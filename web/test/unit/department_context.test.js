import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'
import { createDepartmentEpoch, memberActions } from '../../src/utils/departmentContext.js'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

// 单测内隔离 BroadcastChannel：用进程内分发模拟同源标签页，不触网
const broadcastPosted = []
const broadcastInstances = new Set()
globalThis.BroadcastChannel = class BroadcastChannel {
  constructor() {
    this.onmessage = null
    broadcastInstances.add(this)
  }
  postMessage(data) {
    broadcastPosted.push(data)
    for (const channel of broadcastInstances) {
      if (channel !== this && channel.onmessage) channel.onmessage({ data })
    }
  }
  close() {
    broadcastInstances.delete(this)
  }
  unref() {}
}

// =============================================================================
// === 纯函数：epoch gate 与成员操作边界 ===
// =============================================================================

test('切换后拒绝旧请求结果', () => {
  const gate = createDepartmentEpoch()
  const previous = gate.current()
  gate.advance()
  assert.equal(gate.accept(previous), false)
  assert.equal(gate.accept(gate.current()), true)
})

test('部门管理员不能操作管理员角色', () => {
  assert.deepEqual(memberActions('user', 'admin', 'admin'), {
    canAdd: true,
    canRemove: false,
    canEditRole: false
  })
  assert.equal(memberActions('superadmin', 'superadmin', 'admin').canEditRole, true)
  assert.equal(memberActions('user', 'user', 'user').canAdd, false)
})

// =============================================================================
// === user store：世代 / revision / 切换 ===
// =============================================================================

async function withServer(run) {
  // 隔离上一用例遗留的通道监听，避免跨用例消息分发
  broadcastInstances.clear()
  broadcastPosted.length = 0
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-message-api' : null
        },
        load(id) {
          if (id !== '\0test-message-api') return null
          return `export const message = {
            error(value) { globalThis.__departmentContextMessages.push(['error', value]) },
            info(value) { globalThis.__departmentContextMessages.push(['info', value]) },
            success(value) { globalThis.__departmentContextMessages.push(['success', value]) }
          }`
        }
      }
    ]
  })

  try {
    await run(server)
  } finally {
    await server.close()
  }
}

const buildIdentity = (overrides = {}) => ({
  id: 7,
  username: '成员甲',
  uid: 'member-a',
  phone_number: null,
  avatar: null,
  role: 'admin',
  account_role: 'user',
  department_id: 11,
  department_name: '部门A',
  context_revision: 3,
  session_id: 'session-1',
  created_at: '2026-09-17T00:00:00Z',
  last_login: null,
  ...overrides
})

const identityInDepartmentB = (revision) =>
  buildIdentity({
    role: 'user',
    department_id: 12,
    department_name: '部门B',
    context_revision: revision
  })

async function loadUserModules(server) {
  const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  return { authApi, useUserStore }
}

function mockDepartmentApi({ switchResponse, departments } = {}) {
  const calls = { switch: [], departments: 0 }
  const original = {
    switch: authApiMockHolder.api.switchDepartmentContext,
    departments: authApiMockHolder.api.getMyDepartments
  }
  authApiMockHolder.api.getMyDepartments = async () => {
    calls.departments += 1
    return departments || { items: [] }
  }
  authApiMockHolder.api.switchDepartmentContext = async (payload) => {
    calls.switch.push(payload)
    if (switchResponse instanceof Error) throw switchResponse
    return switchResponse
  }
  return {
    calls,
    restore() {
      authApiMockHolder.api.switchDepartmentContext = original.switch
      authApiMockHolder.api.getMyDepartments = original.departments
    }
  }
}

const authApiMockHolder = { api: null }

test('切换成功替换上下文并拒绝迟到的旧 /me 响应', async () => {
  await withServer(async (server) => {
    globalThis.__departmentContextMessages = []
    setActivePinia(createPinia())
    const { authApi, useUserStore } = await loadUserModules(server)
    authApiMockHolder.api = authApi
    const store = useUserStore()

    assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)
    const epochBeforeSwitch = store.departmentEpoch.current()

    const mocks = mockDepartmentApi({
      switchResponse: identityInDepartmentB(4),
      departments: {
        items: [
          { id: 11, name: '部门A', role: 'admin' },
          { id: 12, name: '部门B', role: 'user' }
        ]
      }
    })
    try {
      await store.switchDepartment(12)
    } finally {
      mocks.restore()
    }

    assert.equal(store.departmentId, 12)
    assert.equal(store.departmentName, '部门B')
    assert.equal(store.contextRevision, 4)
    assert.equal(store.userRole, 'user')
    assert.equal(store.departmentEpoch.accept(epochBeforeSwitch), false)
    assert.deepEqual(store.availableDepartments, [
      { id: 11, name: '部门A', role: 'admin' },
      { id: 12, name: '部门B', role: 'user' }
    ])

    // 切换成功后旧 /me 返回：revision 落后于已接收值，必须被拒绝
    let resolveLateMe
    authApi.getCurrentUser = () => new Promise((resolve) => (resolveLateMe = resolve))
    const lateRefresh = store.getCurrentUser()
    resolveLateMe(buildIdentity())
    assert.equal(await lateRefresh, null)
    assert.equal(store.departmentId, 12)
    assert.equal(store.contextRevision, 4)
  })
})

test('其他标签页切换后本页旧 /me 响应被拒绝', async () => {
  await withServer(async (server) => {
    setActivePinia(createPinia())
    const { useUserStore } = await loadUserModules(server)
    const { useProjectsStore } = await server.ssrLoadModule('/src/stores/projects.js')
    const store = useUserStore()
    const projectsStore = useProjectsStore()

    assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)
    projectsStore.projects = [{ id: 'project-a', name: '部门A项目' }]
    const epochBefore = store.departmentEpoch.current()

    // 另一标签页切换后被广播触发的刷新：更高 revision 可接受
    assert.equal(store.applySession(identityInDepartmentB(4)), true)

    // 身份更新发现部门变化：同样递增 epoch 并清空部门缓存
    assert.equal(store.departmentEpoch.accept(epochBefore), false)
    assert.deepEqual(projectsStore.projects, [])

    // 相同 revision 的并发响应只接受更新的请求序号
    assert.equal(store.applySession(identityInDepartmentB(4), { requestSeq: 1 }), false)
    assert.equal(store.applySession(identityInDepartmentB(4)), true)

    // 旧 /me 迟到返回：revision 落后，拒绝
    assert.equal(store.applySession(buildIdentity()), false)
    assert.equal(store.departmentId, 12)
    assert.equal(store.contextRevision, 4)
  })
})

test('同凭证双标签页经广播联动刷新且不传播 token', async () => {
  await withServer(async (server) => {
    setActivePinia(createPinia())
    const { authApi, useUserStore } = await loadUserModules(server)
    const pinia1 = createPinia()
    const pinia2 = createPinia()

    setActivePinia(pinia1)
    const tab1 = useUserStore()
    setActivePinia(pinia2)
    const tab2 = useUserStore()

    // 刷新 /me 返回切换后的部门 B（模拟另一标签页已完成切换）
    authApi.getCurrentUser = async () => identityInDepartmentB(4)

    setActivePinia(pinia1)
    assert.equal(tab1.applySession(buildIdentity({ access_token: 'token-a' })), true)
    setActivePinia(pinia2)
    assert.equal(tab2.applySession(buildIdentity({ access_token: 'token-b' })), true)

    // 标签页2刷新到部门B后广播，标签页1应通过重读 /me 跟进
    setActivePinia(pinia2)
    assert.equal(tab2.applySession(identityInDepartmentB(4)), true)

    const deadline = Date.now() + 2000
    while (tab1.departmentId !== 12 || tab2.departmentId !== 12) {
      if (Date.now() > deadline) {
        assert.fail(`双标签页未收敛到部门B: tab1=${tab1.departmentId} tab2=${tab2.departmentId}`)
      }
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
    assert.equal(tab1.departmentName, '部门B')
    assert.equal(tab1.contextRevision, 4)

    // 广播只含会话标识与 revision，不携带 token
    for (const payload of broadcastPosted) {
      assert.equal(payload.type, 'session_updated')
      assert.equal('access_token' in payload, false)
      assert.deepEqual(Object.keys(payload).sort(), ['revision', 'session_id', 'type'])
    }
  })
})

test('更换凭证后旧 /me 响应被拒绝', async () => {
  await withServer(async (server) => {
    setActivePinia(createPinia())
    const { authApi, useUserStore } = await loadUserModules(server)
    const store = useUserStore()

    assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)

    let resolveLateMe
    authApi.getCurrentUser = () => new Promise((resolve) => (resolveLateMe = resolve))
    const lateRefresh = store.getCurrentUser()

    authApi.login = async () => ({
      access_token: 'token-b',
      token_type: 'bearer',
      user_id: 9,
      username: '成员乙',
      uid: 'member-b',
      phone_number: null,
      avatar: null,
      role: 'user',
      account_role: 'user',
      department_id: 12,
      department_name: '部门B',
      context_revision: 1,
      session_id: 'session-2'
    })
    await store.login({ loginId: 'member-b', password: 'password-123' })

    resolveLateMe(buildIdentity({ username: '迟到的成员甲' }))
    assert.equal(await lateRefresh, null)
    assert.equal(store.username, '成员乙')
    assert.equal(store.uid, 'member-b')
    assert.equal(store.sessionId, 'session-2')
  })
})

test('切换成功清空部门资源缓存并终止流订阅', async () => {
  await withServer(async (server) => {
    setActivePinia(createPinia())
    const { authApi, useUserStore } = await loadUserModules(server)
    authApiMockHolder.api = authApi
    const { useProjectsStore } = await server.ssrLoadModule('/src/stores/projects.js')
    const store = useUserStore()
    const projectsStore = useProjectsStore()

    assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)
    projectsStore.projects = [{ id: 'project-a', name: '部门A项目' }]
    const streamController = new AbortController()
    const unregister = store.registerDepartmentStreamController(streamController)

    const mocks = mockDepartmentApi({
      switchResponse: identityInDepartmentB(4),
      departments: { items: [{ id: 12, name: '部门B', role: 'user' }] }
    })
    try {
      await store.switchDepartment(12)
    } finally {
      mocks.restore()
    }

    assert.deepEqual(projectsStore.projects, [])
    assert.equal(streamController.signal.aborted, true)
    unregister()
  })
})

test('切换失败保留原有效上下文', async () => {
  await withServer(async (server) => {
    setActivePinia(createPinia())
    const { authApi, useUserStore } = await loadUserModules(server)
    authApiMockHolder.api = authApi
    const { useProjectsStore } = await server.ssrLoadModule('/src/stores/projects.js')
    const store = useUserStore()
    const projectsStore = useProjectsStore()

    assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)
    projectsStore.projects = [{ id: 'project-a', name: '部门A项目' }]
    const epochBefore = store.departmentEpoch.current()

    const conflict = new Error('请求冲突，请刷新后重试')
    conflict.status = 409
    const mocks = mockDepartmentApi({ switchResponse: conflict })
    try {
      await assert.rejects(store.switchDepartment(12))
    } finally {
      mocks.restore()
    }

    assert.equal(store.departmentId, 11)
    assert.equal(store.contextRevision, 3)
    assert.equal(store.departmentEpoch.current(), epochBefore)
    assert.deepEqual(projectsStore.projects, [{ id: 'project-a', name: '部门A项目' }])
    assert.equal(store.switchingDepartment, false)
  })
})

// =============================================================================
// === base.js：epoch 门禁、revision 头与切换期间写保护 ===
// =============================================================================

async function loadBaseModules(server) {
  const { apiGet, apiPost } = await server.ssrLoadModule('/src/apis/base.js')
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  return { apiGet, apiPost, useUserStore }
}

function loginAsDepartmentA(store) {
  assert.equal(store.applySession(buildIdentity({ access_token: 'token-a' })), true)
}

test('迟到的资源响应被静默拒绝且不提示错误', async () => {
  await withServer(async (server) => {
    globalThis.__departmentContextMessages = []
    setActivePinia(createPinia())
    const { apiGet, useUserStore } = await loadBaseModules(server)
    const store = useUserStore()
    loginAsDepartmentA(store)

    let resolveFetch
    globalThis.fetch = () => new Promise((resolve) => (resolveFetch = resolve))
    const request = apiGet('/api/projects')

    store.departmentEpoch.advance()
    resolveFetch(
      new Response(JSON.stringify([{ id: 'project-a', name: '部门A项目' }]), {
        headers: { 'content-type': 'application/json' }
      })
    )

    await assert.rejects(request, (error) => {
      assert.equal(error.code, 'department_context_stale')
      return true
    })
    assert.deepEqual(globalThis.__departmentContextMessages, [])
  })
})

test('部门请求携带 X-Department-Revision 且切换期间拒绝写请求', async () => {
  await withServer(async (server) => {
    globalThis.__departmentContextMessages = []
    setActivePinia(createPinia())
    const { apiGet, apiPost, useUserStore } = await loadBaseModules(server)
    const store = useUserStore()
    loginAsDepartmentA(store)

    let fetchCalls = 0
    let capturedHeaders = null
    globalThis.fetch = async (_url, options) => {
      fetchCalls += 1
      capturedHeaders = options.headers
      return new Response(JSON.stringify({ data: [] }), {
        headers: { 'content-type': 'application/json' }
      })
    }

    await apiGet('/api/agent')
    assert.equal(capturedHeaders['X-Department-Revision'], '3')
    assert.equal(capturedHeaders.Authorization, 'Bearer token-a')

    store.switchingDepartment = true
    await assert.rejects(apiPost('/api/agent', { name: '新智能体' }), (error) => {
      assert.equal(error.code, 'department_switching')
      return true
    })
    assert.equal(fetchCalls, 1)
    store.switchingDepartment = false
  })
})
