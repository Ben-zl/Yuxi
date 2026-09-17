import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canManageCurrentDepartmentMembers,
  memberActions
} from '../../src/utils/departmentContext.js'

// =============================================================================
// === 成员操作边界：按钮可见性与服务器授权保持同一矩阵 ===
// =============================================================================

test('部门管理员不能操作管理员角色', () => {
  assert.deepEqual(
    memberActions('user', 'admin', 'admin'),
    { canAdd: true, canRemove: false, canEditRole: false }
  )
  assert.equal(memberActions('superadmin', 'superadmin', 'admin').canEditRole, true)
  assert.equal(memberActions('user', 'user', 'user').canAdd, false)
})

test('超级管理员可任命降级并移除管理员，部门管理员只能移除普通成员', () => {
  assert.deepEqual(memberActions('superadmin', 'superadmin', 'admin'), {
    canAdd: true,
    canRemove: true,
    canEditRole: true
  })
  assert.deepEqual(memberActions('superadmin', 'superadmin', 'user'), {
    canAdd: true,
    canRemove: true,
    canEditRole: true
  })
  assert.deepEqual(memberActions('user', 'admin', 'user'), {
    canAdd: true,
    canRemove: true,
    canEditRole: false
  })
})

// =============================================================================
// === 设置面板成员管理标签可见性 ===
// =============================================================================

test('成员管理标签仅对有当前部门的管理者可见', () => {
  assert.equal(canManageCurrentDepartmentMembers('superadmin', 'superadmin', true), true)
  assert.equal(canManageCurrentDepartmentMembers('user', 'admin', true), true)
  assert.equal(canManageCurrentDepartmentMembers('user', 'user', true), false)
  // 无当前部门时没有可管理对象，超管也不例外
  assert.equal(canManageCurrentDepartmentMembers('user', 'admin', false), false)
  assert.equal(canManageCurrentDepartmentMembers('superadmin', 'superadmin', false), false)
})
