/**
 * 部门管理 API
 */

import {
  apiAdminGet,
  apiAdminPost,
  apiAdminDelete,
  apiPatch,
  apiSuperAdminGet,
  apiSuperAdminPost,
  apiSuperAdminPut,
  apiSuperAdminDelete
} from './base'
import { checkAdminPermission } from '@/stores/user'

const BASE_URL = '/api/departments'

/**
 * 获取部门列表（普通管理员可访问）
 * @returns {Promise<Array>} 部门列表
 */
export const getDepartments = () => {
  return apiAdminGet(BASE_URL)
}

/**
 * 获取部门详情
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 部门详情
 */
export const getDepartment = (departmentId) => {
  return apiSuperAdminGet(`${BASE_URL}/${departmentId}`)
}

/**
 * 创建部门
 * @param {Object} data - 部门数据
 * @param {string} data.name - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 创建的部门
 */
export const createDepartment = (data) => {
  return apiSuperAdminPost(BASE_URL, data)
}

/**
 * 更新部门
 * @param {number} departmentId - 部门ID
 * @param {Object} data - 部门数据
 * @param {string} [data.name] - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 更新后的部门
 */
export const updateDepartment = (departmentId, data) => {
  return apiSuperAdminPut(`${BASE_URL}/${departmentId}`, data)
}

/**
 * 删除部门
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 删除结果
 */
export const deleteDepartment = (departmentId) => {
  return apiSuperAdminDelete(`${BASE_URL}/${departmentId}`)
}

// =============================================================================
// === 部门成员管理（当前部门管理员与超级管理员可访问） ===
// =============================================================================

/**
 * 分页列出部门成员
 * @param {number} departmentId - 部门ID
 * @param {{ offset?: number, limit?: number }} [params] - 分页参数，默认 offset=0/limit=20，limit 最大 100
 * @returns {Promise<{ items: Array<{ user_id: number, uid: string, username: string, role: string }>, total: number }>}
 */
export const listMembers = (departmentId, params = {}) => {
  const query = new URLSearchParams({
    offset: String(params.offset ?? 0),
    limit: String(params.limit ?? 20)
  })
  return apiAdminGet(`${BASE_URL}/${departmentId}/members?${query}`)
}

/**
 * 检索可加入部门的已有账号候选；仅返回 user_id/uid/username
 * @param {number} departmentId - 部门ID
 * @param {{ search?: string, offset?: number, limit?: number }} [params] - 检索与分页参数
 * @returns {Promise<{ items: Array<{ user_id: number, uid: string, username: string }>, total: number }>}
 */
export const searchMemberCandidates = (departmentId, params = {}) => {
  const query = new URLSearchParams({
    offset: String(params.offset ?? 0),
    limit: String(params.limit ?? 20)
  })
  if (params.search) query.set('search', params.search)
  return apiAdminGet(`${BASE_URL}/${departmentId}/member-candidates?${query}`)
}

/**
 * 添加已有账号为部门成员，默认普通角色
 * @param {number} departmentId - 部门ID
 * @param {number} userId - 目标账号ID
 * @returns {Promise<{ user_id: number, uid: string, username: string, role: string }>}
 */
export const addMember = (departmentId, userId) => {
  return apiAdminPost(`${BASE_URL}/${departmentId}/members`, { user_id: userId })
}

/**
 * 修改部门成员角色（admin/user）；任命与降级管理员仅超级管理员
 * @param {number} departmentId - 部门ID
 * @param {number} userId - 目标账号ID
 * @param {string} role - 部门角色：admin/user
 * @returns {Promise<{ user_id: number, uid: string, username: string, role: string }>}
 */
export const updateMemberRole = (departmentId, userId, role) => {
  checkAdminPermission()
  return apiPatch(`${BASE_URL}/${departmentId}/members/${userId}`, { role })
}

/**
 * 移除部门成员；成功返回 204 空响应
 * @param {number} departmentId - 部门ID
 * @param {number} userId - 目标账号ID
 * @returns {Promise<string>} 空文本
 */
export const removeMember = (departmentId, userId) => {
  return apiAdminDelete(`${BASE_URL}/${departmentId}/members/${userId}`)
}

export const departmentApi = {
  getDepartments,
  getDepartment,
  createDepartment,
  updateDepartment,
  deleteDepartment,
  listMembers,
  searchMemberCandidates,
  addMember,
  updateMemberRole,
  removeMember
}
