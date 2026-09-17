import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/apis/auth_api'
import { useAgentStore } from './agent'
import { useProjectsStore } from './projects'
import { useDatabaseStore } from './database'
import { useChatThreadsStore } from './chatThreads'
import { useAgentTaskStore } from './agentTask'
import { createDepartmentEpoch } from '@/utils/departmentContext'

// 同凭证标签页间只广播会话标识与 revision，不广播 token
const DEPARTMENT_CONTEXT_CHANNEL = 'yuxi-department-context'

export const useUserStore = defineStore('user', () => {
  // 状态
  const token = ref(localStorage.getItem('user_token') || '')
  const userId = ref(null)
  const username = ref('')
  const uid = ref('')
  const phoneNumber = ref('')
  const avatar = ref('')
  const userRole = ref('')
  const accountRole = ref('')
  const departmentId = ref(null)
  const departmentName = ref('')
  const contextRevision = ref(null)
  const sessionId = ref(null)
  const availableDepartments = ref([])
  const departmentsLoading = ref(false)
  const departmentsError = ref('')
  const switchingDepartment = ref(false)

  // 部门 epoch：切换或上下文失效时递增，用于丢弃旧部门发出的迟到响应
  const departmentEpoch = createDepartmentEpoch()
  // 登录世代：换凭证时递增，旧世代的身份响应一律拒绝；由 store 本地维护
  let loginGeneration = 0
  // 身份响应请求序号：相同 revision 的并发响应只接受较新的结果
  let identityRequestSeq = 0
  let lastAppliedRequestSeq = 0
  let acceptedRevision = null
  let identityRefreshInFlight = null
  const departmentStreamControllers = new Set()
  let broadcastChannel = null

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superadmin')
  const isSuperAdmin = computed(
    () => accountRole.value === 'superadmin' || userRole.value === 'superadmin'
  )

  function ensureBroadcastChannel() {
    if (broadcastChannel || typeof BroadcastChannel === 'undefined') return broadcastChannel
    try {
      broadcastChannel = new BroadcastChannel(DEPARTMENT_CONTEXT_CHANNEL)
      broadcastChannel.onmessage = (event) => {
        const payload = event?.data
        if (payload?.type !== 'session_updated') return
        // 只信任同会话的标识广播，收到后重读 /me；不从消息或存储复制 token
        if (payload.session_id && payload.session_id === sessionId.value && isLoggedIn.value) {
          void refreshIdentity().catch(() => {})
        }
      }
      if (typeof broadcastChannel.unref === 'function') broadcastChannel.unref()
    } catch {
      broadcastChannel = null
    }
    return broadcastChannel
  }

  function notifySessionUpdated() {
    if (!sessionId.value) return
    try {
      ensureBroadcastChannel()?.postMessage({
        type: 'session_updated',
        session_id: sessionId.value,
        revision: contextRevision.value
      })
    } catch {
      // 广播失败只影响其他标签页的即时性，焦点恢复时仍会重读
    }
  }

  // 标签页恢复焦点时重读身份，避免长期驻留页面持有过期部门
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('focus', () => {
      if (isLoggedIn.value) void refreshIdentity()
    })
  }
  ensureBroadcastChannel()

  function abortDepartmentStreams() {
    for (const controller of departmentStreamControllers) {
      try {
        controller.abort()
      } catch {
        // 重复中止无需处理
      }
    }
    departmentStreamControllers.clear()
  }

  /**
   * 登记部门作用域的流订阅控制器，切换或上下文失效时统一中止。
   * @param {AbortController} controller
   * @returns {() => void} 注销函数，流正常结束时调用
   */
  function registerDepartmentStreamController(controller) {
    departmentStreamControllers.add(controller)
    return () => departmentStreamControllers.delete(controller)
  }

  /**
   * 作废旧部门的一切本地状态：递增 epoch、终止流订阅并清空部门资源缓存。
   * 只影响前端视图与缓存，不取消后端运行或已提交任务。
   */
  function clearDepartmentScopedState() {
    departmentEpoch.advance()
    abortDepartmentStreams()
    useAgentStore().reset()
    useProjectsStore().resetProjects()
    useDatabaseStore().resetDepartmentData()
    useChatThreadsStore().resetThreads()
    useAgentTaskStore().resetTasks()
  }

  /**
   * 统一应用后端身份响应。
   *
   * @param {Object} data - /me 同构身份（含 account_role/role/department_id/
   *   department_name/context_revision/session_id），登录响应额外携带 access_token
   * @param {{ generation?: number, requestSeq?: number, suppressChangeHandling?: boolean }} meta
   *   generation 为请求开始时捕获的登录世代；requestSeq 为请求序号；
   *   suppressChangeHandling 用于已自行完成清理的切换流程
   * @returns {boolean} 是否真正应用；false 表示迟到或旧世代响应被拒绝
   */
  function applySession(data, meta = {}) {
    const generation = meta.generation ?? loginGeneration
    if (generation !== loginGeneration) return false
    const requestSeq = meta.requestSeq ?? ++identityRequestSeq
    const incomingSessionId =
      typeof data.session_id === 'string' && data.session_id ? data.session_id : null
    const incomingRevision =
      typeof data.context_revision === 'number' ? data.context_revision : null

    // 同会话拒绝落后 revision；相同 revision 只接受更新的请求序号
    if (
      incomingSessionId &&
      incomingSessionId === sessionId.value &&
      incomingRevision !== null &&
      acceptedRevision !== null &&
      (incomingRevision < acceptedRevision ||
        (incomingRevision === acceptedRevision && requestSeq <= lastAppliedRequestSeq))
    ) {
      return false
    }

    const hadIdentity = Boolean(uid.value || token.value)
    const previousDepartmentId = departmentId.value
    const previousRole = userRole.value
    const previousRevision = contextRevision.value

    if (data.access_token) {
      token.value = data.access_token
      localStorage.setItem('user_token', data.access_token)
    }
    userId.value = data.user_id ?? data.id ?? userId.value
    username.value = data.username ?? username.value
    uid.value = data.uid ?? uid.value
    phoneNumber.value = data.phone_number || ''
    avatar.value = data.avatar || ''
    userRole.value = data.role || 'user'
    accountRole.value = data.account_role || ''
    departmentId.value = data.department_id || null
    departmentName.value = data.department_name || ''
    contextRevision.value = incomingRevision
    if (incomingSessionId !== sessionId.value) {
      sessionId.value = incomingSessionId
      acceptedRevision = incomingRevision
    } else if (incomingRevision !== null) {
      acceptedRevision = Math.max(acceptedRevision ?? incomingRevision, incomingRevision)
    }
    lastAppliedRequestSeq = requestSeq

    // 身份更新发现部门/角色变化或失效时同样递增 epoch 并清缓存，不限于显式切换
    if (
      hadIdentity &&
      !meta.suppressChangeHandling &&
      (previousDepartmentId !== departmentId.value || previousRole !== userRole.value)
    ) {
      clearDepartmentScopedState()
    }

    if (
      previousRevision !== contextRevision.value ||
      previousDepartmentId !== departmentId.value ||
      previousRole !== userRole.value
    ) {
      notifySessionUpdated()
    }
    return true
  }

  /**
   * 应用新的登录凭证（密码/OIDC/初始化）：递增登录世代后统一装配身份。
   */
  function applyNewCredentials(data) {
    loginGeneration += 1
    acceptedRevision = null
    return applySession(data, { generation: loginGeneration, requestSeq: ++identityRequestSeq })
  }

  async function login(credentials) {
    try {
      const data = await authApi.login(credentials)
      applyNewCredentials(data)
      return true
    } catch (error) {
      console.error('登录错误:', error)
      throw error
    }
  }

  function logout() {
    loginGeneration += 1
    departmentEpoch.advance()
    abortDepartmentStreams()
    token.value = ''
    userId.value = null
    username.value = ''
    uid.value = ''
    phoneNumber.value = ''
    avatar.value = ''
    userRole.value = ''
    accountRole.value = ''
    departmentId.value = null
    departmentName.value = ''
    contextRevision.value = null
    sessionId.value = null
    availableDepartments.value = []
    departmentsError.value = ''
    switchingDepartment.value = false
    acceptedRevision = null

    // 清除 agentStore 状态，确保重新登录时能正确加载数据
    const agentStore = useAgentStore()
    agentStore.reset()

    // 只清除 token
    localStorage.removeItem('user_token')
  }

  /**
   * 显式退出：先撤销服务端会话，再清本地状态。
   * 网络失败仍完成本地退出，但返回 false 表示不宣称服务端已撤销。
   */
  async function logoutSession() {
    let revoked = false
    try {
      await authApi.logoutSession()
      revoked = true
    } catch (error) {
      console.warn('服务端会话撤销失败，仅执行本地退出:', error?.message || error)
    } finally {
      logout()
    }
    return revoked
  }

  async function initialize(admin) {
    try {
      const data = await authApi.initialize(admin)
      applyNewCredentials(data)
      return true
    } catch (error) {
      console.error('初始化管理员错误:', error)
      throw error
    }
  }

  async function checkFirstRun() {
    try {
      const data = await authApi.checkFirstRun()
      return data.first_run
    } catch (error) {
      console.error('检查首次运行状态错误:', error)
      return false
    }
  }

  // 用于API请求的授权头
  function getAuthHeaders() {
    return {
      Authorization: `Bearer ${token.value}`
    }
  }

  /**
   * 直接 fetch 的流式入口使用的请求头：授权头加当前部门 revision。
   */
  function getStreamAuthHeaders() {
    const headers = { ...getAuthHeaders() }
    if (departmentId.value !== null && contextRevision.value !== null) {
      headers['X-Department-Revision'] = String(contextRevision.value)
    }
    return headers
  }

  async function loadAvailableDepartments() {
    departmentsLoading.value = true
    departmentsError.value = ''
    try {
      const data = await authApi.getMyDepartments()
      availableDepartments.value = data?.items || []
      return availableDepartments.value
    } catch (error) {
      // 迟到响应（epoch 已过期）静默丢弃，不当作加载失败
      if (error?.code !== 'department_context_stale') {
        departmentsError.value = '部门列表加载失败'
      }
      return null
    } finally {
      departmentsLoading.value = false
    }
  }

  /**
   * 切换当前登录会话的活动部门。
   * POST 成功后才替换可见上下文；失败保留原有效上下文。
   */
  async function switchDepartment(departmentId) {
    if (!isLoggedIn.value) throw new Error('未登录')
    if (!sessionId.value) throw new Error('当前凭证没有可切换的登录会话')
    if (switchingDepartment.value) throw new Error('部门切换进行中，请稍后')
    if (departmentId === departmentId.value) return true

    switchingDepartment.value = true
    const generation = loginGeneration
    const requestSeq = ++identityRequestSeq
    try {
      const data = await authApi.switchDepartmentContext({
        department_id: departmentId,
        expected_revision: contextRevision.value
      })
      clearDepartmentScopedState()
      applySession(data || {}, { generation, requestSeq, suppressChangeHandling: true })
      await loadAvailableDepartments()
      return data
    } finally {
      switchingDepartment.value = false
    }
  }

  /**
   * 重读身份与可切换部门；带并发去重，供广播/焦点/部门上下文错误共用。
   */
  function refreshIdentity() {
    if (!isLoggedIn.value) return Promise.resolve(null)
    if (identityRefreshInFlight) return identityRefreshInFlight
    identityRefreshInFlight = (async () => {
      try {
        const userData = await getCurrentUser()
        await loadAvailableDepartments()
        return userData
      } finally {
        identityRefreshInFlight = null
      }
    })()
    return identityRefreshInFlight
  }

  // 用户管理功能
  async function getUsers({ pageSize = 100 } = {}) {
    try {
      const users = []
      let skip = 0

      while (true) {
        const batch = await authApi.getUsers({ skip, limit: pageSize })
        users.push(...batch)

        if (batch.length < pageSize) {
          break
        }

        skip += pageSize
      }

      return users
    } catch (error) {
      console.error('获取用户列表错误:', error)
      throw error
    }
  }

  async function createUser(userData) {
    try {
      return await authApi.createUser(userData)
    } catch (error) {
      console.error('创建用户错误:', error)
      throw error
    }
  }

  async function updateUser(userId, userData) {
    try {
      return await authApi.updateUser(userId, userData)
    } catch (error) {
      console.error('更新用户错误:', error)
      throw error
    }
  }

  async function deleteUser(userId) {
    try {
      return await authApi.deleteUser(userId)
    } catch (error) {
      console.error('删除用户错误:', error)
      throw error
    }
  }

  // 验证用户名并生成uid
  async function validateUsernameAndGenerateUid(username) {
    try {
      return await authApi.validateUsername(username)
    } catch (error) {
      console.error('用户名验证错误:', error)
      throw error
    }
  }

  // 上传头像
  async function uploadAvatar(file) {
    try {
      const data = await authApi.uploadAvatar(file)

      // 更新本地头像状态
      avatar.value = data.avatar_url

      return data
    } catch (error) {
      console.error('头像上传错误:', error)
      throw error
    }
  }

  // 获取当前用户信息
  async function getCurrentUser() {
    const generation = loginGeneration
    const requestSeq = ++identityRequestSeq
    try {
      const userData = await authApi.getCurrentUser()
      if (!applySession(userData, { generation, requestSeq })) {
        // 迟到或旧世代响应：结果作废，不污染当前上下文
        return null
      }
      return userData
    } catch (error) {
      console.error('获取用户信息错误:', error)
      throw error
    }
  }

  // 更新个人资料
  async function updateProfile(profileData) {
    const generation = loginGeneration
    const requestSeq = ++identityRequestSeq
    try {
      const userData = await authApi.updateProfile(profileData)
      applySession(userData, { generation, requestSeq })
      return userData
    } catch (error) {
      console.error('更新个人资料错误:', error)
      throw error
    }
  }

  return {
    // 状态
    token,
    userId,
    username,
    uid,
    phoneNumber,
    avatar,
    userRole,
    accountRole,
    departmentId,
    departmentName,
    contextRevision,
    sessionId,
    availableDepartments,
    departmentsLoading,
    departmentsError,
    switchingDepartment,
    departmentEpoch,

    // 计算属性
    isLoggedIn,
    isAdmin,
    isSuperAdmin,

    // 方法
    applySession,
    applyNewCredentials,
    login,
    logout,
    logoutSession,
    initialize,
    checkFirstRun,
    getAuthHeaders,
    getStreamAuthHeaders,
    loadAvailableDepartments,
    switchDepartment,
    refreshIdentity,
    registerDepartmentStreamController,
    getUsers,
    createUser,
    updateUser,
    deleteUser,
    validateUsernameAndGenerateUid,
    uploadAvatar,
    getCurrentUser,
    updateProfile
  }
})

// 检查当前用户是否有管理员权限
export const checkAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isAdmin) {
    throw new Error('需要管理员权限')
  }
  return true
}

// 检查当前用户是否有超级管理员权限
export const checkSuperAdminPermission = () => {
  const userStore = useUserStore()
  return userStore.isSuperAdmin
}
