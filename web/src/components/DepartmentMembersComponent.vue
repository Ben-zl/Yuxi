<template>
  <div class="department-members">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">成员管理</div>
        <p class="section-description">
          管理「{{ userStore.departmentName || '当前部门' }}」的成员与部门内角色；要管理其他部门，请先在左下角切换部门。
        </p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="members.refreshing"
          title="刷新"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: members.refreshing }" />
          </template>
        </a-button>
        <a-button
          v-if="canAddMembers"
          type="primary"
          class="add-btn lucide-icon-btn"
          @click="openAddModal"
        >
          <template #icon><UserPlus :size="16" /></template>
          添加成员
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="members.loading">
        <div v-if="members.error" class="error-message">
          <a-alert type="error" :message="members.error" show-icon>
            <template #action>
              <a-button size="small" danger @click="fetchMembers">重试</a-button>
            </template>
          </a-alert>
        </div>

        <template v-else-if="members.items.length > 0">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="members.items"
              :columns="columns"
              :rowKey="(record) => record.user_id"
              :pagination="false"
              class="settings-table"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'member'">
                  <div class="member-table-cell">
                    <div class="member-meta">
                      <span class="member-name" :title="record.username">{{ record.username }}</span>
                      <span class="member-uid">ID: {{ record.uid }}</span>
                    </div>
                  </div>
                </template>
                <template v-if="column.key === 'role'">
                  <a-select
                    v-if="canEditRole"
                    :value="record.role"
                    size="small"
                    class="role-select"
                    :disabled="members.roleUpdating"
                    @change="(role) => handleRoleChange(record, role)"
                  >
                    <a-select-option value="admin">管理员</a-select-option>
                    <a-select-option value="user">普通成员</a-select-option>
                  </a-select>
                  <span v-else class="role-badge" :class="record.role">
                    <UserStar v-if="record.role === 'admin'" :size="12" />
                    <User v-else :size="12" />
                    <span>{{ getRoleDisplayName(record.role) }}</span>
                  </span>
                </template>
                <template v-if="column.key === 'action'">
                  <a-tooltip v-if="actionsFor(record.role).canRemove" title="移除成员">
                    <a-button
                      type="text"
                      size="small"
                      danger
                      class="action-btn lucide-icon-btn"
                      @click="confirmRemoveMember(record)"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </a-tooltip>
                  <span v-else class="action-placeholder">-</span>
                </template>
              </template>
            </a-table>
          </div>

          <div v-if="members.total > members.pageSize" class="pagination-section">
            <a-pagination
              :current="members.currentPage"
              :page-size="members.pageSize"
              :total="members.total"
              :page-size-options="['20', '50', '100']"
              show-size-changer
              size="small"
              @change="handlePageChange"
            />
          </div>
        </template>

        <div v-else class="empty-state">
          <a-empty description="当前部门暂无成员" />
        </div>
      </a-spin>
    </div>

    <!-- 添加成员弹窗 -->
    <a-modal
      v-model:open="addModal.visible"
      title="添加成员"
      @ok="handleAddSubmit"
      :confirmLoading="addModal.submitting"
      @cancel="addModal.visible = false"
      :maskClosable="false"
      width="480px"
      class="member-modal"
    >
      <a-form layout="vertical" class="member-form">
        <a-form-item label="搜索账号" class="form-item">
          <a-input
            v-model:value="addModal.searchKeyword"
            class="search-input"
            placeholder="输入名称或登录 ID 检索已有账号"
            allow-clear
          >
            <template #prefix><Search :size="16" /></template>
          </a-input>
        </a-form-item>

        <a-form-item label="选择账号" required class="form-item">
          <div class="candidate-list">
            <div v-if="addModal.loading" class="candidate-state">
              <a-spin size="small" />
              <span>正在检索候选账号…</span>
            </div>
            <div v-else-if="addModal.error" class="candidate-state">
              <span class="candidate-error">{{ addModal.error }}</span>
              <a-button size="small" @click="fetchCandidates">重试</a-button>
            </div>
            <div v-else-if="addModal.items.length === 0" class="candidate-state">
              <span>{{
                addModal.searchKeyword.trim() ? '没有匹配的候选账号' : '暂无可添加的候选账号'
              }}</span>
            </div>
            <a-radio-group
              v-else
              v-model:value="addModal.selectedUserId"
              class="candidate-options"
            >
              <a-radio v-for="item in addModal.items" :key="item.user_id" :value="item.user_id">
                <span class="candidate-name">{{ item.username }}</span>
                <span class="candidate-uid">ID: {{ item.uid }}</span>
              </a-radio>
            </a-radio-group>
          </div>
          <div v-if="addModal.items.length > 0 && addModal.total > addModal.items.length" class="help-text">
            已展示前 {{ addModal.items.length }} 个候选，共 {{ addModal.total }} 个，可继续输入缩小范围
          </div>
        </a-form-item>

        <p class="help-text">新成员默认为普通成员角色{{ canEditRole ? '，添加后可调整部门角色' : '' }}。</p>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { departmentApi } from '@/apis'
import { memberActions } from '@/utils/departmentContext'
import { RefreshCw, Search, Trash2, User, UserPlus, UserStar } from '@lucide/vue'

const userStore = useUserStore()

const columns = [
  { title: '成员', key: 'member', width: '40%' },
  { title: '部门角色', dataIndex: 'role', key: 'role', width: '28%' },
  { title: '操作', key: 'action', width: '32%', align: 'center' }
]

const getRoleDisplayName = (role) => (role === 'admin' ? '管理员' : '普通成员')

// 成员列表状态：加载 / 空结果 / 失败重试三态由 error 与 items 组合表达
const members = reactive({
  loading: false,
  refreshing: false,
  items: [],
  total: 0,
  currentPage: 1,
  pageSize: 20,
  error: null,
  roleUpdating: false
})

// 添加成员弹窗：候选检索同样具备加载 / 空 / 失败重试状态
const addModal = reactive({
  visible: false,
  searchKeyword: '',
  loading: false,
  error: null,
  items: [],
  total: 0,
  selectedUserId: null,
  submitting: false
})

// 目标无关的操作能力（添加、角色编辑）；移除按目标角色逐行判断
const canAddMembers = computed(
  () => memberActions(userStore.accountRole, userStore.userRole, 'user').canAdd
)
const canEditRole = computed(
  () => memberActions(userStore.accountRole, userStore.userRole, 'user').canEditRole
)
const actionsFor = (targetRole) => memberActions(userStore.accountRole, userStore.userRole, targetRole)

// 部门上下文切换后的迟到响应由 base.js 的 epoch 门禁静默丢弃，这里不当作列表失败
const isStaleContextError = (error) => error?.code === 'department_context_stale'

let latestListRequest = 0
const fetchMembers = async () => {
  const departmentId = userStore.departmentId
  if (departmentId == null) return
  const requestId = ++latestListRequest
  try {
    members.loading = true
    const response = await departmentApi.listMembers(departmentId, {
      offset: (members.currentPage - 1) * members.pageSize,
      limit: members.pageSize
    })
    if (requestId !== latestListRequest) return

    members.items = response.items || []
    members.total = response.total || 0
    members.error = null
  } catch (error) {
    if (requestId !== latestListRequest || isStaleContextError(error)) return
    console.error('获取部门成员列表失败:', error)
    members.error = '获取成员列表失败，请重试'
  } finally {
    if (requestId === latestListRequest) members.loading = false
  }
}

const handleRefresh = async () => {
  if (members.refreshing) return
  members.refreshing = true
  try {
    await fetchMembers()
    // fetchMembers 内部捕获失败并写入 members.error，不在此重复弹提示
    if (!members.error) {
      message.success('刷新成功')
    }
  } finally {
    members.refreshing = false
  }
}

const handlePageChange = (page, pageSize) => {
  members.currentPage = pageSize === members.pageSize ? page : 1
  members.pageSize = pageSize
  fetchMembers()
}

// 超级管理员调整部门角色；失败重读后端分页，保证展示与事实一致
const handleRoleChange = async (record, role) => {
  if (role === record.role) return
  const departmentId = userStore.departmentId
  if (departmentId == null) return
  members.roleUpdating = true
  try {
    const updated = await departmentApi.updateMemberRole(departmentId, record.user_id, role)
    record.role = updated.role
    message.success(`已将 "${record.username}" 的部门角色调整为${getRoleDisplayName(updated.role)}`)
    await fetchMembers()
  } catch (error) {
    console.error('修改部门成员角色失败:', error)
    if (!isStaleContextError(error)) {
      message.error(error.message || '修改角色失败，请稍后重试')
      await fetchMembers()
    }
  } finally {
    members.roleUpdating = false
  }
}

// 移除确认框持有引用：部门切换时立即作废，防止旧部门页面的确认操作
// 携带新部门 ID 与 revision 误删另一部门的成员关系
let removeConfirmRef = null
const invalidateRemoveConfirm = () => {
  if (removeConfirmRef) {
    removeConfirmRef.destroy()
    removeConfirmRef = null
  }
}

// 移除前确认目标姓名；打开时捕获部门与 epoch，确认时校验未被切换
const confirmRemoveMember = (member) => {
  const openDepartmentId = userStore.departmentId
  const openEpoch = userStore.departmentEpoch.current()
  if (openDepartmentId == null) return
  removeConfirmRef = Modal.confirm({
    title: '确认移除成员',
    content: `确定要将成员 "${member.username}"（ID: ${member.uid}）移出当前部门吗？仅解除本部门成员关系，不影响其账号与其他部门。`,
    okText: '移除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      removeConfirmRef = null
      // 打开后部门被切换（含其他标签页广播联动）或上下文已失效：作废本次操作
      if (
        openDepartmentId !== userStore.departmentId ||
        !userStore.departmentEpoch.accept(openEpoch)
      ) {
        message.error('部门上下文已变化，本次移除已取消')
        return
      }
      try {
        members.loading = true
        await departmentApi.removeMember(openDepartmentId, member.user_id)
        message.success(`已移除成员 "${member.username}"`)
        if (members.items.length === 1 && members.currentPage > 1) {
          members.currentPage -= 1
        }
        await fetchMembers()
      } catch (error) {
        console.error('移除部门成员失败:', error)
        if (!isStaleContextError(error)) {
          message.error(error.message || '移除失败，请稍后重试')
        }
        members.loading = false
      }
    },
    onCancel() {
      removeConfirmRef = null
    }
  })
}

let candidateRequestTimer = null
let latestCandidateRequest = 0
const fetchCandidates = async () => {
  const departmentId = userStore.departmentId
  if (departmentId == null) return
  const requestId = ++latestCandidateRequest
  try {
    addModal.loading = true
    const response = await departmentApi.searchMemberCandidates(departmentId, {
      search: addModal.searchKeyword.trim(),
      offset: 0,
      limit: 20
    })
    if (requestId !== latestCandidateRequest) return

    addModal.items = response.items || []
    addModal.total = response.total || 0
    addModal.error = null
    if (!addModal.items.some((item) => item.user_id === addModal.selectedUserId)) {
      addModal.selectedUserId = null
    }
  } catch (error) {
    if (requestId !== latestCandidateRequest || isStaleContextError(error)) return
    console.error('检索候选账号失败:', error)
    addModal.error = '候选账号检索失败，请重试'
  } finally {
    if (requestId === latestCandidateRequest) addModal.loading = false
  }
}

const openAddModal = () => {
  addModal.searchKeyword = ''
  addModal.items = []
  addModal.total = 0
  addModal.error = null
  addModal.selectedUserId = null
  addModal.visible = true
  fetchCandidates()
}

const handleAddSubmit = async () => {
  const departmentId = userStore.departmentId
  if (departmentId == null) return
  if (addModal.selectedUserId == null) {
    message.warning('请先选择要添加的账号')
    return
  }
  addModal.submitting = true
  try {
    const member = await departmentApi.addMember(departmentId, addModal.selectedUserId)
    message.success(`已将 "${member.username}" 加入当前部门，角色为普通成员`)
    addModal.visible = false
    await fetchMembers()
  } catch (error) {
    console.error('添加部门成员失败:', error)
    if (!isStaleContextError(error)) {
      message.error(error.message || '添加失败，请稍后重试')
    }
  } finally {
    addModal.submitting = false
  }
}

watch(
  () => addModal.searchKeyword,
  () => {
    if (!addModal.visible) return
    if (candidateRequestTimer) clearTimeout(candidateRequestTimer)
    candidateRequestTimer = setTimeout(() => fetchCandidates(), 300)
  }
)

// 部门切换后重读当前部门成员；离开部门时清空旧部门视图
watch(
  () => userStore.departmentId,
  (newDepartmentId) => {
    invalidateRemoveConfirm()
    members.currentPage = 1
    if (newDepartmentId == null) {
      members.items = []
      members.total = 0
      return
    }
    fetchMembers()
  }
)

onMounted(() => {
  fetchMembers()
})

onUnmounted(() => {
  if (candidateRequestTimer) clearTimeout(candidateRequestTimer)
  latestListRequest += 1
  latestCandidateRequest += 1
})
</script>

<style lang="less" scoped>
.department-members {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 16px;

    .header-content {
      flex: 1;
      min-width: 0;

      .section-title {
        font-size: 16px;
        font-weight: 500;
        color: var(--gray-900);
        line-height: 1.4;
        margin: 12px 0 12px;
      }

      .section-description {
        font-size: 14px;
        color: var(--gray-600);
        line-height: 1.4;
        margin: 0;
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;

      .refresh-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }

        .spin {
          animation: spin 1s linear infinite;
        }

        :deep(.ant-btn-loading-icon) {
          color: var(--gray-600);
        }
      }
    }
  }

  .content-section {
    overflow: hidden;

    .error-message {
      padding: 16px 24px;
    }

    .empty-state {
      padding: 60px 20px;
      text-align: center;
    }

    .settings-table-wrapper {
      border: 1px solid var(--gray-150);
      border-radius: 8px;
      overflow: hidden;
      background: var(--gray-0);

      :deep(.ant-table) {
        background: transparent;
        font-size: 13px;
      }

      :deep(.ant-table-thead > tr > th) {
        background: var(--gray-50);
        color: var(--gray-500);
        font-weight: 500;
        font-size: 12px;
        padding: 9px 14px;
        border-bottom: 1px solid var(--gray-150);
        white-space: nowrap;

        &::before {
          display: none !important;
        }
      }

      :deep(.ant-table-tbody > tr > td) {
        padding: 10px 14px;
        color: var(--gray-800);
        border-bottom: 1px solid var(--gray-100);
        transition: background 0.15s ease;
      }

      :deep(.ant-table-tbody > tr:last-child > td) {
        border-bottom: none;
      }

      :deep(.ant-table-tbody > tr:hover > td) {
        background: var(--gray-25) !important;
      }

      .member-table-cell {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
        max-width: 100%;

        .member-meta {
          display: flex;
          flex-direction: column;
          min-width: 0;

          .member-name {
            font-weight: 500;
            color: var(--gray-900);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 13px;
            line-height: 18px;
          }

          .member-uid {
            font-size: 11px;
            color: var(--gray-400);
            line-height: 14px;
            font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
          }
        }
      }

      .role-select {
        width: 120px;
      }

      .role-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 500;
        line-height: 16px;
        background: var(--gray-100);
        color: var(--gray-600);

        &.admin {
          background: var(--main-30);
          color: var(--main-color);
        }
      }

      .action-placeholder {
        color: var(--gray-300);
      }

      .action-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 26px;
        height: 26px;
        border-radius: 6px;
        color: var(--gray-400);
        transition: all 0.15s ease;

        &:hover:not(:disabled) {
          background: var(--gray-100);
          color: var(--gray-800);
        }

        &.ant-btn-dangerous:hover:not(:disabled) {
          background: var(--color-error-50, #fff2f0);
          color: var(--color-error-500, #ff4d4f);
        }
      }
    }

    .pagination-section {
      display: flex;
      justify-content: flex-end;
      margin-top: 16px;
    }
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.member-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px 16px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 17px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-body) {
    padding: 20px 24px 24px;
  }

  .member-form {
    .form-item {
      margin-bottom: 16px;

      :deep(.ant-form-item-label) {
        padding-bottom: 6px;

        label {
          font-weight: 600;
          font-size: 13px;
          color: var(--gray-800);
        }
      }
    }

    .help-text {
      color: var(--gray-600);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }
  }

  .candidate-list {
    width: 100%;
    max-height: 240px;
    overflow-y: auto;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    padding: 8px 12px;

    .candidate-state {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 16px 0;
      color: var(--gray-500);
      font-size: 13px;

      .candidate-error {
        color: var(--color-error-500);
      }
    }

    .candidate-options {
      display: flex;
      flex-direction: column;
      gap: 4px;
      width: 100%;

      :deep(.ant-radio-wrapper) {
        display: flex;
        align-items: center;
        padding: 6px 4px;

        .candidate-name {
          font-size: 13px;
          color: var(--gray-900);
          font-weight: 500;
          margin-right: 8px;
        }

        .candidate-uid {
          font-size: 11px;
          color: var(--gray-400);
          font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
        }
      }
    }
  }
}
</style>
