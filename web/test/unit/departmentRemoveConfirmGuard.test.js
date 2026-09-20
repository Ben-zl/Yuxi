import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import * as Vue from 'vue'

const source = readFileSync(
  new URL('../../src/components/DepartmentMembersComponent.vue', import.meta.url),
  'utf8'
)

/** 从组件源码截取函数块（起于 marker，止于首层配对大括号结尾）。 */
function extractBlock(marker) {
  const start = source.indexOf(marker)
  assert.ok(start > 0, `未找到 ${marker}`)
  let depth = 0
  let i = source.indexOf('=', start)
  for (; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    if (source[i] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(start, i + 1)
    }
  }
  assert.fail('代码块未闭合')
}

const refDecl = 'let removeConfirmRef = null'
const invalidateBlock = extractBlock('const invalidateRemoveConfirm =')
const confirmBlock = extractBlock('const confirmRemoveMember =')

function buildHarness() {
  const state = {
    removeConfirmRef: null,
    destroyed: 0,
    removed: [],
    errors: [],
    confirmOptions: null,
    Vue,
    Modal: {
      confirm(options) {
        state.confirmOptions = options
        return { destroy() { state.destroyed += 1; state.removeConfirmRef = null } }
      }
    },
    message: { success() {}, error(msg) { state.errors.push(msg) } },
    userStore: {
      departmentId: 1,
      departmentEpoch: { current: () => 0, accept: (v) => v === 0 }
    },
    departmentApi: { removeMember: async (deptId, userId) => { state.removed.push([deptId, userId]) } },
    members: Vue.reactive({ loading: false, items: [], total: 0, currentPage: 1, pageSize: 20 }),
    isStaleContextError: () => false,
    fetchMembers: async () => {}
  }
  const code = [refDecl, invalidateBlock, confirmBlock, '({ invalidateRemoveConfirm, confirmRemoveMember })'].join('\n')
  state.api = runInNewContext(code, state)
  return state
}

test('组件在部门切换 watch 中接线作废调用', () => {
  const watchStart = source.indexOf('watch(\n  () => userStore.departmentId,')
  assert.ok(watchStart > 0, '缺少部门切换 watch')
  const watchBody = source.slice(watchStart, source.indexOf('\n)', watchStart) + 2)
  assert.ok(
    watchBody.includes('invalidateRemoveConfirm()'),
    '部门切换 watch 必须作废移除确认框'
  )
})

test('invalidateRemoveConfirm 销毁已打开的移除确认框且幂等', () => {
  const s = buildHarness()
  s.api.confirmRemoveMember({ username: 'u1', uid: 'u1', user_id: 9 })
  assert.equal(s.destroyed, 0)
  s.api.invalidateRemoveConfirm()
  assert.equal(s.destroyed, 1)
  s.api.invalidateRemoveConfirm()
  assert.equal(s.destroyed, 1, '无确认框时为空操作')
})

test('确认时部门已切换则拒绝发请求并提示', async () => {
  const s = buildHarness()
  s.api.confirmRemoveMember({ username: 'u1', uid: 'u1', user_id: 9 })
  s.userStore.departmentId = 2 // 打开后另一标签切换到 B
  await s.confirmOptions.onOk()
  assert.deepEqual(s.removed, [], '部门已切换时不得发出移除请求')
  assert.ok(s.errors.some((m) => m.includes('已取消')), '必须提示操作取消')
})

test('确认时 epoch 已推进则拒绝发请求', async () => {
  const s = buildHarness()
  s.api.confirmRemoveMember({ username: 'u1', uid: 'u1', user_id: 9 })
  s.userStore.departmentEpoch = { current: () => 1, accept: (v) => v === 1 }
  await s.confirmOptions.onOk()
  assert.deepEqual(s.removed, [], 'epoch 失配时不得发出移除请求')
})

test('部门与 epoch 均未变化时按打开时的部门发请求', async () => {
  const s = buildHarness()
  s.api.confirmRemoveMember({ username: 'u1', uid: 'u1', user_id: 9 })
  await s.confirmOptions.onOk()
  assert.deepEqual(s.removed, [[1, 9]], '使用打开确认框时捕获的部门')
})
