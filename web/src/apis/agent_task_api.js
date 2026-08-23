import { apiGet, apiPost, apiPatch } from './base'

const BASE_URL = '/api/agent-tasks'
const EXEC_URL = '/api/agent-task-executions'

export const agentTaskApi = {
  listTasks: async () => apiGet(BASE_URL),
  createTask: async (payload) => apiPost(BASE_URL, payload),
  getTask: async (taskId) => apiGet(`${BASE_URL}/${taskId}`),
  updateTask: async (taskId, payload) => apiPatch(`${BASE_URL}/${taskId}`, payload),
  previewSchedule: async (payload) => apiPost(`${BASE_URL}/schedule-preview`, payload),
  triggerManual: async (taskId) => apiPost(`${BASE_URL}/${taskId}/executions`, {}),
  listExecutions: async (taskId) => apiGet(`${BASE_URL}/${taskId}/executions`),
  getExecution: async (executionId) => apiGet(`${EXEC_URL}/${executionId}`),
  submitApproval: async (executionId, decisions) =>
    apiPost(`${EXEC_URL}/${executionId}/approval`, { decisions }),
  cancelExecution: async (executionId) => apiPost(`${EXEC_URL}/${executionId}/cancel`, {})
}
