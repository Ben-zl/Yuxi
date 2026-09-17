<template>
  <div class="user-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">用户管理</div>
        <p class="section-description">
          管理系统账号，请谨慎操作。删除账号后该账号将无法登录；部门内角色请在“成员管理”中调整。
        </p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="userManagement.refreshing"
          title="刷新"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: userManagement.refreshing }" />
          </template>
        </a-button>
        <a-button type="primary" @click="showAddUserModal" class="add-btn lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          添加用户
        </a-button>
      </div>
    </div>

    <div class="filter-section">
      <a-input
        v-model:value="userManagement.searchKeyword"
        class="search-input"
        placeholder="搜索用户名 / ID / 手机号"
        allow-clear
      >
        <template #prefix><Search :size="16" /></template>
      </a-input>
      <div class="filter-actions">
        <a-select v-model:value="userManagement.roleFilter" class="filter-select">
          <a-select-option value="">全部身份</a-select-option>
          <a-select-option value="superadmin">超级管理员</a-select-option>
          <a-select-option value="user">普通用户</a-select-option>
        </a-select>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="userManagement.loading">
        <div v-if="userManagement.error" class="error-message">
          <a-alert type="error" :message="userManagement.error" show-icon />
        </div>

        <template v-if="userManagement.users.length > 0">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="userManagement.users"
              :columns="columns"
              :rowKey="(record) => record.id"
              :pagination="false"
              class="settings-table"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'user'">
                  <div class="user-table-cell">
                    <FallbackAvatar
                      :src="record.avatar"
                      :default-src="getUserDefaultAvatarSrc(record)"
                      :name="record.username"
                      :seed="record.uid || record.username"
                      kind="user"
                      :size="28"
                      shape="circle"
                      :alt="record.username"
                      class="user-avatar"
                    />
                    <div class="user-meta">
                      <span class="user-name" :title="record.username">{{ record.username }}</span>
                      <span v-if="record.uid" class="user-uid">ID: {{ record.uid }}</span>
                    </div>
                  </div>
                </template>
                <template v-if="column.key === 'role'">
                  <span class="role-badge" :class="record.role">
                    <UserLock v-if="record.role === 'superadmin'" :size="12" />
                    <UserStar v-else-if="record.role === 'admin'" :size="12" />
                    <User v-else :size="12" />
                    <span>{{ getRoleDisplayName(record.role) }}</span>
                  </span>
                </template>
                <template v-if="column.key === 'phone'">
                  <span class="phone-text">{{ record.phone_number || '-' }}</span>
                </template>
                <template v-if="column.key === 'lastLogin'">
                  <span class="time-text">{{ formatTime(record.last_login) }}</span>
                </template>
                <template v-if="column.key === 'action'">
                  <a-space :size="4">
                    <a-tooltip title="编辑用户">
                      <a-button
                        type="text"
                        size="small"
                        class="action-btn lucide-icon-btn"
                        @click="showEditUserModal(record)"
                      >
                        <SquarePen :size="14" />
                      </a-button>
                    </a-tooltip>
                    <a-tooltip
                      :title="
                        isUserDeleteDisabled(record) ? '不能删除当前用户或超级管理员' : '删除用户'
                      "
                    >
                      <a-button
                        type="text"
                        size="small"
                        danger
                        :disabled="isUserDeleteDisabled(record)"
                        class="action-btn lucide-icon-btn"
                        @click="confirmDeleteUser(record)"
                      >
                        <Trash2 :size="14" />
                      </a-button>
                    </a-tooltip>
                  </a-space>
                </template>
              </template>
            </a-table>
          </div>

          <div v-if="userManagement.total > userManagement.pageSize" class="pagination-section">
            <a-pagination
              :current="userManagement.currentPage"
              :page-size="userManagement.pageSize"
              :total="userManagement.total"
              :page-size-options="['20', '50', '100']"
              show-size-changer
              size="small"
              @change="handlePageChange"
            />
          </div>
        </template>

        <div v-else class="empty-state">
          <a-empty :description="hasActiveFilters ? '没有匹配的用户' : '暂无用户数据'" />
        </div>
      </a-spin>
    </div>

    <!-- 用户表单模态框 -->
    <a-modal
      v-model:open="userManagement.modalVisible"
      :title="userManagement.modalTitle"
      @ok="handleUserFormSubmit"
      :confirmLoading="userManagement.loading"
      @cancel="userManagement.modalVisible = false"
      :maskClosable="false"
      width="480px"
      class="user-modal"
    >
      <a-form layout="vertical" class="user-form">
        <a-form-item label="用户名" required class="form-item">
          <a-input
            v-model:value="userManagement.form.username"
            placeholder="请输入用户名（2-20个字符）"
            @blur="validateAndGenerateUid"
            :maxlength="20"
          />
          <div v-if="userManagement.form.usernameError" class="error-text">
            {{ userManagement.form.usernameError }}
          </div>
          <div
            v-if="userManagement.form.generatedUid && !userManagement.editMode"
            class="help-text"
          >
            登录ID：{{ userManagement.form.generatedUid }}，此ID将用于登录，根据用户名自动生成
          </div>
        </a-form-item>

        <!-- 手机号字段 -->
        <a-form-item label="手机号" class="form-item">
          <a-input
            v-model:value="userManagement.form.phoneNumber"
            placeholder="请输入手机号（可选，可用于登录）"
            :maxlength="11"
          />
          <div v-if="userManagement.form.phoneError" class="error-text">
            {{ userManagement.form.phoneError }}
          </div>
        </a-form-item>

        <template v-if="userManagement.editMode">
          <div class="password-toggle">
            <a-checkbox v-model:checked="userManagement.displayPasswordFields">
              修改密码
            </a-checkbox>
          </div>
        </template>

        <template v-if="!userManagement.editMode || userManagement.displayPasswordFields">
          <a-form-item label="密码" required class="form-item">
            <a-input-password
              v-model:value="userManagement.form.password"
              :placeholder="`请输入密码（至少 ${MIN_PASSWORD_LENGTH} 位）`"
              :minlength="MIN_PASSWORD_LENGTH"
            />
          </a-form-item>

          <a-form-item label="确认密码" required class="form-item">
            <a-input-password
              v-model:value="userManagement.form.confirmPassword"
              placeholder="请再次输入密码"
            />
          </a-form-item>
        </template>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, onMounted, onUnmounted, watch, computed } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { authApi } from '@/apis'
import { Plus, SquarePen, Trash2, User, UserLock, UserStar, RefreshCw, Search } from '@lucide/vue'
import { formatDateTime } from '@/utils/time'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '@/utils/passwordValidation'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

const userStore = useUserStore()

const columns = [
  { title: '用户', key: 'user', width: '34%' },
  { title: '账号身份', dataIndex: 'role', key: 'role', width: '20%' },
  { title: '手机号', dataIndex: 'phone_number', key: 'phone', width: '20%' },
  { title: '最后登录', dataIndex: 'last_login', key: 'lastLogin', width: '16%' },
  { title: '操作', key: 'action', width: '10%', align: 'center' }
]

const getRoleDisplayName = (role) => {
  const map = {
    superadmin: '超级管理员',
    admin: '管理员',
    user: '普通用户'
  }
  return map[role] || role || '普通用户'
}

// 用户管理相关状态
const userManagement = reactive({
  loading: false,
  refreshing: false,
  users: [],
  total: 0,
  searchKeyword: '',
  roleFilter: '',
  currentPage: 1,
  pageSize: 20,
  error: null,
  modalVisible: false,
  modalTitle: '添加用户',
  editMode: false,
  editUserId: null,
  form: {
    username: '',
    generatedUid: '', // 自动生成的uid
    phoneNumber: '', // 手机号
    password: '',
    confirmPassword: '',
    usernameError: '', // 用户名错误信息
    phoneError: '' // 手机号错误信息
  },
  displayPasswordFields: true // 编辑时是否显示密码字段
})

const hasActiveFilters = computed(
  () => Boolean(userManagement.searchKeyword.trim()) || Boolean(userManagement.roleFilter)
)

// 添加验证用户名并生成uid的函数
const validateAndGenerateUid = async () => {
  const username = userManagement.form.username.trim()

  // 清空之前的错误和生成的ID
  userManagement.form.usernameError = ''
  userManagement.form.generatedUid = ''

  if (!username) {
    return
  }

  // 在编辑模式下，不需要重新生成uid
  if (userManagement.editMode) {
    return
  }

  try {
    const result = await userStore.validateUsernameAndGenerateUid(username)
    userManagement.form.generatedUid = result.uid
  } catch (error) {
    userManagement.form.usernameError = error.message || '用户名验证失败'
  }
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }

  // 中国大陆手机号格式验证
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听密码字段显示状态变化
watch(
  () => userManagement.displayPasswordFields,
  (newVal) => {
    // 当取消显示密码字段时，清空密码输入
    if (!newVal) {
      userManagement.form.password = ''
      userManagement.form.confirmPassword = ''
    }
  }
)

// 监听手机号输入变化
watch(
  () => userManagement.form.phoneNumber,
  (newPhone) => {
    userManagement.form.phoneError = ''

    if (newPhone && !validatePhoneNumber(newPhone)) {
      userManagement.form.phoneError = '请输入正确的手机号格式'
    }
  }
)

let filterRequestTimer = null
watch(
  () => [userManagement.searchKeyword, userManagement.roleFilter],
  () => {
    userManagement.currentPage = 1
    if (filterRequestTimer) clearTimeout(filterRequestTimer)
    filterRequestTimer = setTimeout(() => fetchUsers(), 250)
  }
)

// 格式化时间显示
const formatTime = (timeStr) => formatDateTime(timeStr)

const getUserDefaultAvatarSrc = (user) => (user.uid ? generatePixelAvatar(user.uid) : '')

const isUserDeleteDisabled = (user) =>
  user.id === userStore.userId ||
  (user.role === 'superadmin' && userStore.userRole !== 'superadmin')

let latestUserRequest = 0
const fetchUsers = async () => {
  const requestId = ++latestUserRequest
  try {
    userManagement.loading = true
    const pageSize = Number(userManagement.pageSize)
    const response = await authApi.getUsersPage({
      offset: (userManagement.currentPage - 1) * pageSize,
      limit: pageSize,
      search: userManagement.searchKeyword.trim(),
      role: userManagement.roleFilter
    })
    if (requestId !== latestUserRequest) return

    const maxPage = Math.max(1, Math.ceil(response.total / pageSize))
    if (userManagement.currentPage > maxPage) {
      userManagement.currentPage = maxPage
      await fetchUsers()
      return
    }

    userManagement.users = response.items
    userManagement.total = response.total
    userManagement.error = null
  } catch (error) {
    if (requestId !== latestUserRequest) return
    console.error('获取用户列表失败:', error)
    userManagement.error = '获取用户列表失败'
  } finally {
    if (requestId === latestUserRequest) userManagement.loading = false
  }
}

const handlePageChange = (page, pageSize) => {
  if (filterRequestTimer) clearTimeout(filterRequestTimer)
  userManagement.currentPage = pageSize === userManagement.pageSize ? page : 1
  userManagement.pageSize = pageSize
  fetchUsers()
}

// 刷新用户列表
const handleRefresh = async () => {
  if (userManagement.refreshing) return
  userManagement.refreshing = true
  try {
    await fetchUsers()
    message.success('刷新成功')
  } catch (error) {
    console.error('刷新失败:', error)
    message.error('刷新失败')
  } finally {
    userManagement.refreshing = false
  }
}

// 打开添加用户模态框
const showAddUserModal = () => {
  userManagement.modalTitle = '添加用户'
  userManagement.editMode = false
  userManagement.editUserId = null
  userManagement.form = {
    username: '',
    generatedUid: '',
    phoneNumber: '',
    password: '',
    confirmPassword: '',
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = true
  userManagement.modalVisible = true
}

// 打开编辑用户模态框
const showEditUserModal = (user) => {
  userManagement.modalTitle = '编辑用户'
  userManagement.editMode = true
  userManagement.editUserId = user.id
  userManagement.form = {
    username: user.username,
    generatedUid: user.uid || '', // 编辑模式显示现有的uid
    phoneNumber: user.phone_number || '',
    password: '',
    confirmPassword: '',
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = false // 默认不显示密码字段
  userManagement.modalVisible = true
}

// 处理用户表单提交
const handleUserFormSubmit = async () => {
  try {
    // 简单验证
    if (!userManagement.form.username.trim()) {
      message.error('用户名不能为空')
      return
    }

    // 验证用户名长度
    if (
      userManagement.form.username.trim().length < 2 ||
      userManagement.form.username.trim().length > 20
    ) {
      message.error('用户名长度必须在 2-20 个字符之间')
      return
    }

    // 验证手机号
    if (userManagement.form.phoneNumber && !validatePhoneNumber(userManagement.form.phoneNumber)) {
      message.error('请输入正确的手机号格式')
      return
    }

    if (userManagement.displayPasswordFields) {
      if (!userManagement.form.password) {
        message.error('密码不能为空')
        return
      }

      if (!isPasswordLongEnough(userManagement.form.password)) {
        message.error(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`)
        return
      }

      if (userManagement.form.password !== userManagement.form.confirmPassword) {
        message.error('两次输入的密码不一致')
        return
      }
    }

    userManagement.loading = true

    // 与收紧后的 UserCreate/UserUpdate 对齐：仅 username/password/phone_number
    if (userManagement.editMode) {
      const updateData = {
        username: userManagement.form.username.trim()
      }

      // 添加手机号字段
      if (userManagement.form.phoneNumber) {
        updateData.phone_number = userManagement.form.phoneNumber
      }

      // 如果显示了密码字段并且填写了密码，才更新密码
      if (userManagement.displayPasswordFields && userManagement.form.password) {
        updateData.password = userManagement.form.password
      }

      await userStore.updateUser(userManagement.editUserId, updateData)
      message.success('用户更新成功')
    } else {
      // 创建普通账号，不要求部门；部门角色由成员管理维护
      const createData = {
        username: userManagement.form.username.trim(),
        password: userManagement.form.password
      }

      // 添加手机号字段（如果填写了）
      if (userManagement.form.phoneNumber) {
        createData.phone_number = userManagement.form.phoneNumber
      }

      await userStore.createUser(createData)
      message.success('用户创建成功，可在“成员管理”中将其加入部门')
    }

    // 重新获取用户列表
    await fetchUsers()
    userManagement.modalVisible = false
  } catch (error) {
    console.error('用户操作失败:', error)
    message.error(error.message || '操作失败，请稍后重试')
  } finally {
    userManagement.loading = false
  }
}

// 删除用户
const confirmDeleteUser = (user) => {
  // 自己不能删除自己
  if (user.id === userStore.userId) {
    message.error('不能删除自己的账户')
    return
  }

  // 确认对话框
  Modal.confirm({
    title: '确认删除用户',
    content: `确定要删除用户 "${user.username}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.deleteUser(user.id)
        message.success('用户删除成功')
        // 重新获取用户列表
        await fetchUsers()
      } catch (error) {
        console.error('删除用户失败:', error)
        message.error(error.message || '删除失败，请稍后重试')
      } finally {
        userManagement.loading = false
      }
    }
  })
}

// 在组件挂载时获取用户列表
onMounted(async () => {
  await fetchUsers()
})

onUnmounted(() => {
  if (filterRequestTimer) clearTimeout(filterRequestTimer)
  latestUserRequest += 1
})
</script>

<style lang="less" scoped>
.user-management {
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

  .filter-section {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;

    .search-input {
      width: 300px;
      max-width: 100%;

      :deep(.ant-input-prefix) {
        color: var(--gray-500);
        margin-right: 6px;
      }
    }

    .filter-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      margin-left: auto;
    }

    .filter-select {
      width: 150px;
    }
  }

  @media (max-width: 640px) {
    .filter-section {
      align-items: stretch;

      .search-input,
      .filter-actions {
        width: 100%;
      }

      .filter-actions {
        margin-left: 0;
      }

      .filter-select {
        flex: 1;
        min-width: 0;
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

      .user-table-cell {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
        max-width: 100%;

        .user-avatar {
          flex-shrink: 0;
        }

        .user-meta {
          display: flex;
          flex-direction: column;
          min-width: 0;

          .user-name {
            font-weight: 500;
            color: var(--gray-900);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 13px;
            line-height: 18px;
          }

          .user-uid {
            font-size: 11px;
            color: var(--gray-400);
            line-height: 14px;
            font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
          }
        }
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

        &.superadmin {
          background: rgba(217, 119, 6, 0.08);
          color: #d97706;
        }

        &.admin {
          background: var(--main-30);
          color: var(--main-color);
        }
      }

      .time-text {
        color: var(--gray-600);
        font-size: 12px;
      }

      .phone-text {
        font-size: 12px;
        color: var(--gray-700);
        font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
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

.user-modal {
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

  .user-form {
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

    .error-text {
      color: var(--color-error-500);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .help-text {
      color: var(--gray-600);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .password-toggle {
      margin-bottom: 16px;
      padding: 12px 16px;
      background: var(--gray-25);
      border-radius: 8px;
      border: 1px solid var(--gray-100);

      :deep(.ant-checkbox-wrapper) {
        font-weight: 500;
        color: var(--gray-700);
        font-size: 13px;
      }
    }
  }
}
</style>
