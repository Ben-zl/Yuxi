/**
 * 部门上下文前端工具：epoch gate 与成员操作边界。
 *
 * epoch gate 用于丢弃部门切换前发出的迟到响应；成员操作函数只做
 * 按钮可见性判断，服务器仍是授权事实 Owner。
 */

/**
 * 创建部门 epoch gate。
 *
 * @returns {{ current: () => number, advance: () => number, accept: (value: number) => boolean }}
 *   current 返回当前 epoch；advance 递增并返回新 epoch；accept 判断
 *   捕获的 epoch 是否仍与当前值一致（一致才允许消费响应结果）。
 */
export function createDepartmentEpoch() {
  let epoch = 0
  return {
    current: () => epoch,
    advance: () => ++epoch,
    accept: (value) => value === epoch
  }
}

/**
 * 判断当前账号对目标成员可执行的操作。
 *
 * @param {string} accountRole 全局账号身份：superadmin/user
 * @param {string} currentRole 操作者在当前部门的有效角色
 * @param {string} targetRole 目标成员在当前部门的角色
 * @returns {{ canAdd: boolean, canRemove: boolean, canEditRole: boolean }}
 */
export function memberActions(accountRole, currentRole, targetRole) {
  const superadmin = accountRole === 'superadmin'
  const admin = superadmin || currentRole === 'admin'
  return {
    canAdd: admin,
    // 部门管理员本人也是admin，禁止移除任何admin同时保护自移除。
    canRemove: superadmin || (admin && targetRole === 'user'),
    canEditRole: superadmin
  }
}

/**
 * 判断设置面板“成员管理”标签对当前身份是否可见。
 *
 * 成员管理只作用于当前部门，超管与当前部门管理员可见；
 * 无当前部门时没有可管理对象，超管也不例外。
 *
 * @param {string} accountRole 全局账号身份：superadmin/user
 * @param {string} currentRole 当前部门有效角色
 * @param {boolean} hasDepartment 是否有当前活动部门
 * @returns {boolean}
 */
export function canManageCurrentDepartmentMembers(accountRole, currentRole, hasDepartment) {
  if (!hasDepartment) return false
  return accountRole === 'superadmin' || currentRole === 'admin'
}
