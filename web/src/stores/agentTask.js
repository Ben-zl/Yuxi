import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { message } from 'ant-design-vue'
import { agentTaskApi } from '@/apis/agent_task_api'

const ACTIVE_STATUSES = new Set(['queued', 'running', 'interrupted'])

export const useAgentTaskStore = defineStore('agentTask', () => {
  const tasks = ref([])
  const loading = ref(false)
  const currentTask = ref(null)
  const executions = ref([])
  const executionsLoading = ref(false)
  let pollTimer = null

  const hasActiveExecutions = computed(() =>
    executions.value.some((e) => ACTIVE_STATUSES.has(e.status))
  )

  async function loadTasks() {
    loading.value = true
    try {
      const data = await agentTaskApi.listTasks()
      tasks.value = data.tasks || []
    } catch (err) {
      console.error('加载任务列表失败:', err)
    } finally {
      loading.value = false
    }
  }

  async function loadTask(taskId) {
    try {
      currentTask.value = await agentTaskApi.getTask(taskId)
      return currentTask.value
    } catch (err) {
      console.error('加载任务详情失败:', err)
      throw err
    }
  }

  async function loadExecutions(taskId) {
    executionsLoading.value = true
    try {
      const data = await agentTaskApi.listExecutions(taskId)
      executions.value = data.executions || []
    } catch (err) {
      console.error('加载执行历史失败:', err)
    } finally {
      executionsLoading.value = false
    }
  }

  async function createTask(payload) {
    const task = await agentTaskApi.createTask(payload)
    message.success('任务创建成功')
    await loadTasks()
    return task
  }

  async function updateTask(taskId, payload) {
    const task = await agentTaskApi.updateTask(taskId, payload)
    message.success('任务已更新')
    currentTask.value = task
    await loadTasks()
    return task
  }

  async function triggerManual(taskId) {
    const execution = await agentTaskApi.triggerManual(taskId)
    message.success('已触发执行')
    return execution
  }

  async function cancelExecution(executionId) {
    await agentTaskApi.cancelExecution(executionId)
    message.success('已请求取消')
  }

  async function submitApproval(executionId, decisions) {
    await agentTaskApi.submitApproval(executionId, decisions)
    message.success('已提交审批')
  }

  async function previewSchedule(payload) {
    return agentTaskApi.previewSchedule(payload)
  }

  function startPolling(taskId) {
    stopPolling()
    pollTimer = setInterval(async () => {
      await loadExecutions(taskId)
    }, 5000)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // 清空部门内任务缓存并停止轮询；切换部门或上下文失效时调用，不取消已提交任务
  function resetTasks() {
    stopPolling()
    tasks.value = []
    currentTask.value = null
    executions.value = []
    executionsLoading.value = false
  }

  return {
    tasks,
    loading,
    currentTask,
    executions,
    executionsLoading,
    hasActiveExecutions,
    loadTasks,
    loadTask,
    loadExecutions,
    createTask,
    updateTask,
    triggerManual,
    cancelExecution,
    submitApproval,
    previewSchedule,
    startPolling,
    stopPolling,
    resetTasks
  }
})
