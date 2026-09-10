<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { agentTaskApi } from '@/apis/agent_task_api'
import { formatFullDateTime } from '@/utils/time'
import AgentTaskEditModal from '@/components/agent-tasks/AgentTaskEditModal.vue'
import {
  Plus, Play, Power, Archive, Search, SquarePen, Copy,
  ChevronDown, ChevronRight, XCircle, CheckCircle2, Loader2, Ban, Clock
} from '@lucide/vue'

const router = useRouter()

const searchText = ref('')
const searchInputRef = ref(null)
const filterStatus = ref('all')
const filterTrigger = ref('all')
const filterScope = ref('all')
const editModalOpen = ref(false)
const editingTask = ref(null)
const tasks = ref([])
const loading = ref(false)
const expandedTaskId = ref(null)
const executionsByTask = ref({})
const execLoadingByTask = ref({})
const execPageByTask = ref({})
const PAGE_SIZE = 5

onMounted(() => loadTasks())

async function loadTasks() {
  loading.value = true
  try {
    const data = await agentTaskApi.listTasks()
    tasks.value = data.tasks || []
  } catch (e) {
    console.error('加载任务列表失败:', e)
  } finally {
    loading.value = false
  }
}

const filteredTasks = computed(() => {
  let list = tasks.value
  if (searchText.value) {
    const q = searchText.value.toLowerCase()
    list = list.filter((t) => t.name?.toLowerCase().includes(q) || t.agent_name?.toLowerCase().includes(q))
  }
  if (filterStatus.value === 'enabled') list = list.filter((t) => t.enabled && !t.archived_at)
  if (filterStatus.value === 'disabled') list = list.filter((t) => !t.enabled && !t.archived_at)
  if (filterStatus.value === 'archived') list = list.filter((t) => t.archived_at)
  if (filterTrigger.value === 'manual') list = list.filter((t) => !t.schedule?.mode && !t.api_enabled)
  if (filterTrigger.value === 'schedule') list = list.filter((t) => t.schedule?.mode)
  if (filterTrigger.value === 'api') list = list.filter((t) => t.api_enabled)
  if (filterScope.value === 'user') list = list.filter((t) => scopeLabel(t) === '个人')
  if (filterScope.value === 'department') list = list.filter((t) => scopeLabel(t) === '部门')
  return list
})

async function toggleExpand(task) {
  if (expandedTaskId.value === task.id) {
    expandedTaskId.value = null
    return
  }
  expandedTaskId.value = task.id
  execPageByTask.value[task.id] = 0
  await loadExecutions(task.id)
}

async function loadExecutions(taskId) {
  execLoadingByTask.value[taskId] = true
  try {
    const data = await agentTaskApi.listExecutions(taskId)
    const all = (data.executions || []).sort((a, b) => {
      const ta = new Date(a.queued_at || 0).getTime()
      const tb = new Date(b.queued_at || 0).getTime()
      return tb - ta
    })
    executionsByTask.value[taskId] = all
  } catch (e) {
    console.error('加载执行历史失败:', e)
  } finally {
    execLoadingByTask.value[taskId] = false
  }
}

function pagedExecutions(taskId) {
  const all = executionsByTask.value[taskId] || []
  const page = execPageByTask.value[taskId] || 0
  return all.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
}

function totalPages(taskId) {
  const all = executionsByTask.value[taskId] || []
  return Math.max(1, Math.ceil(all.length / PAGE_SIZE))
}

function goPage(taskId, delta) {
  const page = execPageByTask.value[taskId] || 0
  const max = totalPages(taskId) - 1
  execPageByTask.value[taskId] = Math.max(0, Math.min(max, page + delta))
}

function openExecution(task, execution) {
  const url = router.resolve(`/agent-tasks/${task.id}/${execution.id}`).href
  window.open(url, '_blank')
}

function openCreate() {
  editingTask.value = null
  editModalOpen.value = true
}

function openEdit(task) {
  editingTask.value = task
  editModalOpen.value = true
}

async function triggerTask(task) {
  try {
    await agentTaskApi.triggerManual(task.id)
    message.success('已触发执行')
    await loadTasks()
    if (expandedTaskId.value === task.id) await loadExecutions(task.id)
  } catch (e) {
    message.error(e.message || '触发失败')
  }
}

async function toggleEnabled(task) {
  try {
    const { agentTaskApi } = await import('@/apis/agent_task_api')
    await agentTaskApi.updateTask(task.id, { enabled: !task.enabled })
    message.success('任务已更新')
    await loadTasks()
  } catch (e) {
    message.error(e.message || '操作失败')
  }
}

function archiveTask(task) {
  Modal.confirm({
    title: '归档任务',
    content: `归档后任务不可触发，历史执行保留。确认归档「${task.name}」？`,
    okText: '归档',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await agentTaskApi.updateTask(task.id, { archived: true })
      message.success('任务已归档')
      await loadTasks()
    }
  })
}

function triggerLabel(task) {
  const parts = ['手动']
  if (task.schedule?.mode) parts.push('定时')
  if (task.api_enabled) parts.push('API')
  return parts.join(' · ')
}

function scopeLabel(task) {
  const scope = task.share_config?.read_scope || {}
  if (scope.access_level === 'global') return '全局'
  if (scope.access_level === 'department') return '部门'
  return '个人'
}

function fmtTime(t) {
  if (!t) return '-'
  return formatFullDateTime(t) || '-'
}

const STATUS_MAP = {
  enabled: { color: 'green', text: '启用' },
  disabled: { color: 'default', text: '停用' },
  archived: { color: 'orange', text: '归档' }
}
function statusInfo(task) {
  if (task.archived_at) return STATUS_MAP.archived
  return task.enabled ? STATUS_MAP.enabled : STATUS_MAP.disabled
}

const EXEC_STATUS = {
  queued: { icon: Clock, color: 'var(--gray-500)', label: '排队' },
  running: { icon: Loader2, color: 'var(--main-color)', label: '执行中' },
  interrupted: { icon: Clock, color: 'var(--color-warning-700)', label: '等待审批' },
  succeeded: { icon: CheckCircle2, color: 'var(--color-success-500)', label: '成功' },
  failed: { icon: XCircle, color: 'var(--color-error-500)', label: '失败' },
  cancelled: { icon: Ban, color: 'var(--gray-400)', label: '已取消' },
  skipped: { icon: Ban, color: 'var(--gray-400)', label: '已跳过' },
  missed: { icon: Ban, color: 'var(--gray-400)', label: '已错过' }
}
function execMeta(s) { return EXEC_STATUS[s] || EXEC_STATUS.queued }

function triggerLabelShort(t) {
  return { manual: '手动', api: 'API', schedule: '定时' }[t] || t
}

function apiTriggerUrl(task) {
  return `${window.location.origin}/api/agent-tasks/${task.id}/trigger`
}

async function copyApiUrl(task) {
  const url = apiTriggerUrl(task)
  const key = `key-${Date.now()}`
  try {
    await navigator.clipboard.writeText(`curl -X POST ${url} -H "Authorization: Bearer yxkey_YOUR_API_KEY" -H "Idempotency-Key: ${key}"`)
    message.success('已复制 API 触发命令')
  } catch {
    message.info(`API 触发地址：${url}`)
  }
}
</script>

<template>
  <div class="agent-tasks-page">
    <div class="page-header">
      <h2 class="page-title">任务中心</h2>
      <button class="btn-primary" @click="openCreate">
        <Plus :size="16" /> 创建任务
      </button>
    </div>

    <div class="toolbar">
      <div class="search-box">
        <Search :size="16" class="search-icon" />
        <input ref="searchInputRef" v-model="searchText" placeholder="搜索任务名或智能体" class="search-input" />
        <button
          v-if="searchText"
          type="button"
          class="search-clear"
          aria-label="清空搜索"
          title="清空搜索"
          @click="searchText = ''; searchInputRef?.focus()"
        ><XCircle :size="16" /></button>
      </div>
      <a-select v-model:value="filterStatus" style="width: 120px" placeholder="全部状态" allow-clear>
        <a-select-option value="all">全部状态</a-select-option>
        <a-select-option value="enabled">启用中</a-select-option>
        <a-select-option value="disabled">已停用</a-select-option>
        <a-select-option value="archived">已归档</a-select-option>
      </a-select>
      <a-select v-model:value="filterTrigger" style="width: 130px" placeholder="全部触发方式" allow-clear>
        <a-select-option value="all">全部触发方式</a-select-option>
        <a-select-option value="manual">仅手动</a-select-option>
        <a-select-option value="schedule">含定时</a-select-option>
        <a-select-option value="api">含 API</a-select-option>
      </a-select>
      <a-select v-model:value="filterScope" style="width: 120px" placeholder="全部范围" allow-clear>
        <a-select-option value="all">全部范围</a-select-option>
        <a-select-option value="user">个人</a-select-option>
        <a-select-option value="department">部门</a-select-option>
      </a-select>
    </div>

    <div class="task-table-wrap">
      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="!filteredTasks.length" class="empty-state">
        <p>暂无任务，点击「创建任务」开始</p>
      </div>
      <table v-else class="task-table">
        <thead>
          <tr>
            <th class="th-expand"></th>
            <th>名称</th>
            <th>智能体</th>
            <th>触发方式</th>
            <th>下次运行</th>
            <th>队列</th>
            <th>可见范围</th>
            <th>状态</th>
            <th>API 触发</th>
            <th class="th-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="task in filteredTasks" :key="task.id">
            <tr class="task-row" @click="toggleExpand(task)">
              <td class="cell-expand">
                <button class="expand-btn">
                  <ChevronRight v-if="expandedTaskId !== task.id" :size="16" />
                  <ChevronDown v-else :size="16" />
                </button>
              </td>
              <td class="cell-name">{{ task.name }}</td>
              <td>{{ task.agent_name || task.agent_slug || '-' }}</td>
              <td class="cell-trigger">{{ triggerLabel(task) }}</td>
              <td class="cell-time">{{ fmtTime(task.schedule?.next_run_at) }}</td>
              <td class="cell-queue">{{ task.queue_length || 0 }}</td>
              <td>{{ scopeLabel(task) }}</td>
              <td>
                <span class="status-badge" :class="`status-${statusInfo(task).color}`">{{ statusInfo(task).text }}</span>
              </td>
              <td class="cell-api">
                <button
                  v-if="task.api_enabled && !task.archived_at"
                  class="api-copy-btn"
                  title="复制 API 触发地址"
                  @click.stop="copyApiUrl(task)"
                ><Copy :size="14" /></button>
                <span v-else class="cell-muted">-</span>
              </td>
              <td class="cell-actions" @click.stop>
                <div class="action-buttons">
                  <button v-if="!task.archived_at" class="action-btn" title="立即执行" @click="triggerTask(task)"><Play :size="15" /></button>
                  <button v-if="!task.archived_at" class="action-btn" :title="task.enabled ? '停用' : '启用'" @click="toggleEnabled(task)"><Power :size="15" /></button>
                  <button v-if="!task.archived_at" class="action-btn" title="编辑" @click="openEdit(task)"><SquarePen :size="15" /></button>
                  <button v-if="!task.archived_at" class="action-btn action-danger" title="归档" @click="archiveTask(task)"><Archive :size="15" /></button>
                </div>
              </td>
            </tr>
            <!-- 展开的执行记录行 -->
            <tr v-if="expandedTaskId === task.id" class="exec-panel-row">
              <td colspan="10">
                <div class="exec-panel">
                  <div v-if="execLoadingByTask[task.id]" class="exec-loading">加载执行记录...</div>
                  <div v-else-if="!(executionsByTask[task.id] || []).length" class="exec-empty">暂无执行记录</div>
                  <div v-else class="exec-table-wrap">
                    <table class="exec-table">
                      <thead>
                        <tr>
                          <th>触发方式</th>
                          <th>执行身份</th>
                          <th>状态</th>
                          <th>触发时间</th>
                          <th>完成时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="exec in pagedExecutions(task.id)"
                          :key="exec.id"
                          class="exec-row"
                          @click="openExecution(task, exec)"
                        >
                          <td class="exec-trigger">{{ triggerLabelShort(exec.trigger_type) }}</td>
                          <td class="exec-principal">{{ exec.execution_principal_uid?.slice(0, 8) || '-' }}</td>
                          <td>
                            <span class="exec-status-wrap">
                              <component :is="execMeta(exec.status).icon" :size="14" :style="{ color: execMeta(exec.status).color }" :class="{ spin: exec.status === 'running' }" />
                              <span :style="{ color: execMeta(exec.status).color }">{{ execMeta(exec.status).label }}</span>
                            </span>
                          </td>
                          <td class="exec-time-cell">{{ fmtTime(exec.queued_at) }}</td>
                          <td class="exec-time-cell">{{ fmtTime(exec.finished_at) }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <!-- 分页 -->
                    <div v-if="totalPages(task.id) > 1" class="exec-pager">
                      <button class="page-btn" :disabled="(execPageByTask[task.id] || 0) === 0" @click="goPage(task.id, -1)">上一页</button>
                      <span class="page-info">{{ (execPageByTask[task.id] || 0) + 1 }} / {{ totalPages(task.id) }}</span>
                      <button class="page-btn" :disabled="(execPageByTask[task.id] || 0) >= totalPages(task.id) - 1" @click="goPage(task.id, 1)">下一页</button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <AgentTaskEditModal
      v-model:open="editModalOpen"
      :task="editingTask"
      @saved="loadTasks()"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-tasks-page {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: var(--page-padding);
  max-width: 1200px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--gray-900);
  margin: 0;
}
.btn-primary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--main-color);
  color: var(--gray-0);
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  &:hover { opacity: 0.9; }
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.search-box {
  position: relative;
  flex: 1 1 240px;
  min-width: 0;
  .search-icon {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--gray-400);
  }
}
.search-input {
  width: 100%;
  height: 36px;
  padding: 0 36px 0 34px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  font-size: 13px;
  outline: none;
  background: var(--gray-0);
  &:focus { border-color: var(--main-color); }
}
.search-clear {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-400);
  cursor: pointer;
  &:hover { color: var(--gray-700); }
  &:focus-visible { outline: 2px solid var(--main-color); }
}
.filter-select {
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  font-size: 13px;
  background: var(--gray-0);
  outline: none;
  cursor: pointer;
}
.task-table-wrap {
  background: var(--gray-0);
  border-radius: 10px;
  border: 1px solid var(--gray-100);
  overflow-x: auto;
}
.task-table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
  table-layout: fixed;
  thead th {
    padding: 10px 12px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    color: var(--gray-500);
    border-bottom: 1px solid var(--gray-100);
    background: var(--gray-10);
  }
  tbody td {
    padding: 10px 12px;
    font-size: 13px;
    color: var(--gray-700);
    border-bottom: 1px solid var(--gray-50);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .task-row {
    cursor: pointer;
    &:hover { background: color-mix(in srgb, var(--main-color) 4%, var(--gray-0)); }
  }
  .exec-panel-row {
    display: table-row;
    > td {
      padding: 0;
      border-bottom: 1px solid var(--gray-100);
      background: var(--gray-10);
    }
    .exec-panel {
      display: block;
    }
  }
}
.th-expand {
  width: 32px;
}
.cell-expand {
  width: 32px;
  text-align: center;
}
.expand-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  cursor: pointer;
  color: var(--gray-400);
}
.cell-name {
  font-weight: 600;
  color: var(--gray-900);
  min-width: 120px;
}
.cell-agent {
  color: var(--gray-600);
  min-width: 80px;
}
.cell-trigger {
  font-size: 12px;
  color: var(--gray-500);
  min-width: 80px;
}
.cell-time {
  font-family: monospace;
  font-size: 12px;
  color: var(--gray-400);
  min-width: 120px;
}
.cell-queue {
  text-align: center;
}
.cell-api {
  text-align: center;
}
.cell-muted {
  color: var(--gray-300);
}
.api-copy-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  cursor: pointer;
  color: var(--gray-600);
  &:hover { border-color: var(--main-color); color: var(--main-color); }
}
.cell-scope {
  font-size: 12px;
  color: var(--gray-500);
  min-width: 40px;
}
.cell-actions {
  position: sticky;
  right: 0;
  background: var(--gray-0);
}
.task-table thead .th-actions {
  width: 160px;
  position: sticky;
  right: 0;
  z-index: 1;
}
.action-buttons {
  display: flex;
  gap: 4px;
}
.action-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  cursor: pointer;
  color: var(--gray-600);
  &:hover { border-color: var(--main-color); color: var(--main-color); }
  &.action-danger:hover { border-color: var(--color-error-500); color: var(--color-error-500); }
}
.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  min-width: 36px;
  text-align: center;
  &.status-green { background: var(--color-success-50); color: var(--color-success-700); }
  &.status-default { background: var(--gray-100); color: var(--gray-600); }
  &.status-orange { background: var(--color-warning-50); color: var(--color-warning-700); }
}
/* 执行记录展开面板 */
.exec-panel {
  padding: 8px 12px 8px 40px;
}
.exec-loading,
.exec-empty {
  padding: 16px 0;
  text-align: center;
  color: var(--gray-400);
  font-size: 13px;
}
.exec-table-wrap {
  border-radius: 8px;
  border: 1px solid var(--gray-100);
  overflow: hidden;
}
.exec-table {
  width: 100%;
  border-collapse: collapse;
  thead th {
    padding: 8px 12px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    color: var(--gray-500);
    border-bottom: 1px solid var(--gray-100);
    background: var(--gray-10);
  }
  tbody td {
    padding: 8px 12px;
    font-size: 13px;
    color: var(--gray-700);
    border-bottom: 1px solid var(--gray-50);
  }
  tr:last-child td {
    border-bottom: none;
  }
}
.exec-row {
  cursor: pointer;
  &:hover { background: color-mix(in srgb, var(--main-color) 4%, var(--gray-0)); }
}
.exec-trigger {
  font-weight: 500;
  color: var(--gray-800);
}
.exec-principal {
  font-size: 12px;
  font-family: monospace;
  color: var(--gray-500);
}
.exec-status-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-weight: 500;
}
.exec-time-cell {
  font-size: 12px;
  font-family: monospace;
  color: var(--gray-500);
  white-space: nowrap;
}
.exec-pager {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px 0;
  font-size: 12px;
  color: var(--gray-500);
}
.page-btn {
  padding: 2px 8px;
  border: 1px solid var(--gray-200);
  border-radius: 4px;
  background: var(--gray-0);
  cursor: pointer;
  color: var(--gray-600);
  &:disabled { opacity: 0.4; cursor: default; }
  &:hover:not(:disabled) { border-color: var(--main-color); color: var(--main-color); }
}
.page-info {
  font-size: 12px;
  color: var(--gray-500);
}
.empty-state,
.loading-state {
  padding: 60px 20px;
  text-align: center;
  color: var(--gray-400);
  font-size: 14px;
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
