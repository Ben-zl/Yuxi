import { apiAdminDelete, apiAdminGet, apiAdminPost, apiAdminPut } from './base'

const BASE_URL = '/api/agent-channels'

export const agentChannelApi = {
  list: () => apiAdminGet(BASE_URL),
  create: (payload) => apiAdminPost(BASE_URL, payload),
  update: (id, payload) => apiAdminPut(`${BASE_URL}/${encodeURIComponent(id)}`, payload),
  remove: (id) => apiAdminDelete(`${BASE_URL}/${encodeURIComponent(id)}`),
  reconcile: (id) => apiAdminPost(`${BASE_URL}/${encodeURIComponent(id)}/reconcile`, {}),
  setEnabled: (id, enabled) =>
    apiAdminPost(`${BASE_URL}/${encodeURIComponent(id)}/${enabled ? 'enable' : 'disable'}`, {}),
  status: (id) => apiAdminGet(`${BASE_URL}/${encodeURIComponent(id)}/status`)
}
