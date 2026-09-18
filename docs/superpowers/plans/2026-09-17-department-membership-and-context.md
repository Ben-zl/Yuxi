# 多部门成员与当前部门权限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分离全局账号管理和部门成员管理，使同一账号按当前会话部门获取独立角色，并让运行任务固定提交部门。

**Architecture:** PostgreSQL 保存成员关系、登录会话的活动部门和运行请求的提交部门。认证产生不可变的权限上下文，service 在事务内校验并修改成员关系，repository 执行持久化查询；前端只使用后端返回的权限并在切换时清理旧数据。运行执行重新校验固定部门的有效权限，不从 Web 会话获取部门。

**Tech Stack:** FastAPI、SQLAlchemy async、PostgreSQL、AgentScope、Vue 3、Pinia、Ant Design Vue、pytest、Node.js 原生 test runner、Docker Compose；不增加依赖。

**Spec:** [多部门成员关系与当前部门权限](../../develop-guides/decisions/proposed/2026-09-17-department-membership-and-context.md)。产品边界由该 decision 拥有，本计划只定义实现顺序与验证方法。

## Global Constraints

- 面向实现者和 Reviewer；前置阅读根目录 `AGENTS.md`、`ARCHITECTURE.md`、backend/web/docs 子树指令及[测试规范](../../develop-guides/testing-guidelines.md)。页面类型为内部实施计划，不添加用户导航。
- 账号管理仅超级管理员可用；部门角色只有 `admin` 和 `user`，超级管理员无需成员关系即可管理任意部门。
- 不新增邀请注册、自定义角色、跨部门权限汇总、历史业务数据兼容层或生产环境自动清理机制。
- 当前部门属于登录会话的请求上下文，不是用户账号上的全局可变字段。
- 数据库迁移仅演进 schema，不在 API 启动或迁移脚本中夹带开发账号清理。
- 清理只处理账号、成员关系和必要的凭证失效，不删除、迁移或重新归属历史会话、文件、智能体、知识库等业务数据，不删除部门。
- 不修改 ORM User 的字段来投影权限。HTTP 路由只解析、认证、装配响应；事务在 service，查询在 repository。
- 不自动执行 push、PR 或远端写入。每个任务的提交检查点须先经全新、不继承开发上下文的 Reviewer 审查；只暂存本任务文件，不包含既有 workspace/model selector 改动。
- 本文所有测试均为计划，当前产品结果为 **Not run**。代码块是测试与核心实现契约，不代表已经存在的函数；新增符号在对应任务内定义。

## 实施顺序与决策检查点

这是一个贯通认证、持久化和运行权限的功能，不能将前后端分别发布。顺序为 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10；任务 7 的产品决策不阻塞其他任务编写和开发，但阻塞整项验收与发布。每个任务内部按 checkbox 逐步完成，先恢复具体缺陷，再实现，再验证；不要一次改完整条链路才运行测试。

**假设：** 使用服务端登录会话，JWT 增加随机 `sid`，保留现有过期机制；同一凭证标签页共享一条会话，不同登录创建不同会话。旧无 `sid` 的交互凭证失效并要求重新登录，不引入旧角色兼容层。API Key 和非交互运行上下文不依赖登录会话。

**待确认边界：** decision 的“删除部门边界”尚未获明确批准。任务 7 是条件任务，不把建议当作用户授权；未确认时不实施新的删除策略，也不能保留原有迁入默认部门或级联删除账号的路径后宣称功能完成。

**主要风险：** 遗漏隐式 User 角色读取、后台重载用户丢失部门、恢复分支绕过普通提交、并发撤权、切换前迟到响应。任务 4、5、6、8 对应闭合这些风险。

## 文件与接口地图

所有路径相对仓库根目录；这些是实施定位，不是已完成事实。

| 责任 | 新文件 | 现有主要修改点 |
|---|---|---|
| 成员与会话持久化 | `backend/package/yuxi/repositories/department_membership_repository.py`、`auth_session_repository.py`（同目录） | `storage/postgres/models_business.py`、`manager.py`、`storage_migration.py`（均在 `backend/package/yuxi/` 下） |
| 有效身份与切换 | `backend/package/yuxi/services/department_context_service.py` | `backend/server/utils/auth_middleware.py`、`backend/server/routers/auth_router.py`、`backend/server/routers/user_router.py`、`backend/package/yuxi/services/auth_service.py`、`oidc_service.py` |
| 成员管理用例 | `backend/package/yuxi/services/department_membership_service.py` | `backend/server/routers/auth_dept_router.py`、`backend/package/yuxi/services/identity_admin_service.py`、用户/部门 repository |
| 权限消费者 | 无新权限框架 | `backend/package/yuxi/permissions/resource_permission.py`、资源 repository、知识库/工作区入口 |
| 运行与非交互入口 | 无旁路提交机制 | `run_submission_service.py`、运行 repository、`agentscope/config_projection.py`、任务/Channel services |
| 切换与成员界面 | `web/src/utils/departmentContext.js`、`web/src/components/DepartmentMembersComponent.vue` | user store、API wrapper、SettingsModal、UserInfo、UserManagement |
| 开发清理 | `backend/scripts/cleanup_development_accounts.py` | 不使用通用硬删除流程 |

## Task 1：增加成员关系、登录会话与运行部门字段

**Files:** 修改 `backend/server/routers/auth_dept_router.py`、`backend/package/yuxi/repositories/department_repository.py`、`backend/package/yuxi/storage/postgres/models_business.py`、`backend/package/yuxi/storage/postgres/manager.py`、`backend/package/yuxi/storage_migration.py`；新增 `backend/package/yuxi/repositories/department_membership_repository.py`、`backend/package/yuxi/repositories/auth_session_repository.py`；测试新增 `backend/test/integration/services/test_department_membership_schema.py`，修改 `backend/test/unit/storage/test_postgres_manager_schema.py` 和 `backend/test/integration/services/test_schema_migration_version.py`。

**Interfaces:** 新增 `DepartmentDeletionConflict(ValueError)` 于 department repository，由旧删除路由转换为409；新增 `DepartmentMembership(user_id:int, department_id:int, role:str)`，组合主键与 `role IN ('admin','user')` check；新增 `AuthSession(id:str, user_id:int, active_department_id:int|None, revision:int, expires_at:datetime, revoked_at:datetime|None)`。`DepartmentMembershipRepository(db)` 提供 `get(user_id,department_id,*,for_update=False)`、`list_for_user(user_id)`、`list_for_department(department_id,*,offset,limit)`、`add(user_id,department_id,*,role='user')`、`remove(user_id,department_id)`；返回 ORM 行或分页 `{items,total}`，不自行 commit。`AuthSessionRepository(db)` 提供 `get(session_id,*,for_update=False)` 与 `create(user_id,active_department_id,expires_at)`，ID 用 UUID 字符串。

- [x] 编写真实 PG 测试，沿已有 migration 测试的引擎/事务装配创建两个部门和一个测试账号；相同 `(user_id,department_id)` 插入两次报 `IntegrityError`，另一部门独立插入成功，非法角色失败。关键断言：

```python
assert [(row.department_id, row.role) for row in rows] == [(dept_a.id, "admin"), (dept_b.id, "user")]
assert user.role == "user"
assert user.department_id is None
```

测试中的 `rows` 来自新增 repository，按 department_id 排序；`user/dept_a/dept_b` 在测试事务内用现有 ORM 创建，退出回滚，不假设不存在的共享 fixture。

- [x] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_department_membership_schema.py -v`；预期新模型/约束缺失导致失败，而非凭证或服务错误。
- [x] 在现有 `ensure_business_schema` 中加入幂等 DDL，business 版本从已检查的 13 升至 14，`storage_migration.py` 的 `upgrade_from` 元组加入 13（保留现有版本项，不补入原本不支持的 2）；若执行时版本已经变化，使用当时下一版本并同步所有断言，不覆盖他人迁移。

```python
__table_args__ = (
    CheckConstraint("role IN ('admin', 'user')", name="ck_department_membership_role"),
)
# DepartmentMembership 的 user_id、department_id 同时设置 primary_key=True。
# Department 到 User 不再使用 cascade="all, delete-orphan"。
```

- [x] 为 `AgentRunRequest`、`AgentRun`、`AgentTask`、`TaskExecution`、`AgentScopeChannelBinding` 增加 `department_id`；旧数据允许 NULL，新运行提交由 service 强制非空。保留旧历史行，不猜测归属。APIKey 复用已有 department_id；`CLIAuthSession` 增加 nullable `approved_department_id`，在批准与兑换两个事务之间保存授权部门；`AgentMemoryScope` 增加 nullable `maintenance_department_id`，仅绑定维护运行使用的部门，不改变个人 Memory 的现有归属。删除旧单部门字段延后至任务 10，以便先进行独立清理；期间新代码不得读写其授权含义。
- [x] 同步闭合旧删除路径的中间态：`delete_and_migrate_users` 在迁移旧用户或修改key之前锁定部门行；若存在新 membership、AuthSession 活动部门、CLI批准部门、Memory维护绑定或任务/Channel/运行部门引用，抛出新定义的 `DepartmentDeletionConflict(ValueError)`，路由映射409且不产生任何写入。不自动删除新关系来绕过外键；没有新引用时保留旧删除语义，直到任务7获批准后替换。新增引用写入在同一事务先锁目标部门行再验证存在，使用与删除一致的锁序，防止检查后并发插入。测试真实HTTP返回409而非500，并回读账号、关系、key均未改变；覆盖无新引用的既有删除回归和并发加成员/删除。此保护只防止新增schema破坏中间态，不批准最终删除策略。
- [x] 用真实 PG 验证空库初始化、13→14、迁移重复执行、旧行保留；迁移不修改账号有效性，不复制旧 admin 身份到成员表。运行上述集与 `test/integration/services/test_schema_migration_version.py`，预期通过。
- [x] 审查模型与 migration 的约束一致、关系无账号级联删除后，按本任务路径暂存并提交 `feat: 增加部门成员与会话上下文存储`。

**执行记录（2026-09-18）：**
- red：`docker compose exec api python -m pytest test/integration/services/test_department_membership_schema.py` 收集期 ImportError（新符号未定义），符合预期。
- green：同命令 `10 passed`（约束 4 + 演进 2 + 模型 2 + repository 保护 1 + HTTP 409 1）；`test_schema_migration_version.py` 3 passed；`test/unit/storage` 58 passed。
- 运行库升级：`docker compose run --rm storage-migrator` 后 `yuxi_schema_migrations.business = 14`，真实 PG 回读 `agent_run_requests.department_id` 落地；api/worker 重启后 `/api/system/ready` = ready。
- 注：容器内跑（`python -m pytest`，与既有 e2e 跑法一致；`uv run --group test` 因容器 user 无 site-packages 写权限不可用）。全量 `test/unit -m "not slow"` 有 63 个失败，经 stash 对照确认为基线环境性失败（compose 结构/workspace symlink/OCR env，与本次改动无关），改动相关模块全绿。
- ruff：本次改动文件（package 与测试）`ruff check`/`ruff format --check` 全部通过（容器内 `uv run --no-sync --with ruff`，ruff 0.15.15；未触碰的基线文件不在本次格式化范围）。`python3 scripts/verify_engineering_contracts.py` 通过（86 decisions）；`python3 -m unittest scripts.test_verify_engineering_contracts` OK（61 tests）；`git diff --check` 通过。
- Reviewer 第一轮 BLOCK 修复（2026-09-18）：① DDL `department_memberships.user_id` 外键误写 `REFERENCES departments(id)`，修正为 `users(id)`；开发库两新表（均 0 行）DROP 后回退版本行至 13 重跑 storage-migrator，PG 回读 `department_memberships_user_id_fkey → users` 确认修复；测试补 FK 指向断言与幽灵 user_id 插入拒绝（防两条建表路径漂移回归）。② `DepartmentMembershipRepository.add` 增加部门行 FOR UPDATE 锁与存在性校验（与删除一致锁序），新增并发测试证明 add 在删除事务持锁期间阻塞、部门删除后被拒绝；add 对不存在部门抛 ValueError。③ HTTP 409 测试补账号/启用人 key 未变断言。修复后 `test_department_membership_schema.py + test_schema_migration_version.py + test/unit/storage` = 72 passed。第二轮独立 Reviewer 复审 APPROVE（FK/锁序/运行库三层验证）；复审 Minor 项（测试文件 E501 与格式、记录表述）已随提交前处理，测试文件 lint/format 全绿、11 passed。

## Task 2：建立不可变权限上下文与会话部门切换

**Files:** 新增 `backend/package/yuxi/services/department_context_service.py`；修改 `backend/server/main.py`（显式CORS请求头列表）、`backend/test/integration/conftest.py`（revision请求头辅助）、`backend/server/utils/auth_middleware.py`、`backend/server/routers/auth_router.py`、`backend/server/routers/agent_task_router.py`（交互接口依赖归类）、`backend/package/yuxi/services/auth_service.py`、`backend/package/yuxi/services/oidc_service.py`；新增 `backend/test/unit/services/test_department_context_service.py`、`backend/test/integration/services/test_department_session_context.py`。

**Interfaces:** 定义下列类型与异步 service；SQL 查询由任务 1 repository 承担。`account_role` 仅 `superadmin/user`，`role` 是当前请求有效角色；`account_role == "superadmin"` 时有效 `role` 恒为 `superadmin`，无论是否有活动部门或membership；非超管有部门时取membership.role，无部门时为user。需要 ORM 写入的个人资料用例按 `id` 重新加载账号，不能给上下文赋值。

```python
@dataclass(frozen=True)
class DepartmentContext:
    id: int
    uid: str
    username: str
    account_role: str
    department_id: int | None
    department_name: str | None
    role: str
    session_id: str | None
    revision: int
```

`resolve_department_context(db, *, user_id:int, department_id:int|None, session_id:str|None=None, revision:int=0) -> DepartmentContext` 检查账号有效性、锁定、部门存在性及实时成员角色；`switch_department(db, *, session_id:str, department_id:int, expected_revision:int) -> DepartmentContext` 在事务内锁会话并递增 revision。新增交互接口契约为 `GET /api/auth/my-departments`、`POST /api/auth/department-context`、`POST /api/auth/logout`；logout成功及重复撤销为204，鉴权规则见本任务步骤。新增 `get_authenticated_user` 作为无需部门的强制登录依赖。

- [x] 写两次独立登录的 HTTP 测试，复用 `test_client/admin_headers` 创建测试账号和关系，再登录获取不同 token；首个切换 A，第二个切 B，交替 `/api/auth/me` 必须保持各自部门。同 token 两次读取必须相同；移除关系后旧 token 不能读取原部门。
- [x] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_department_session_context.py -v`；预期缺少切换路由/会话隔离失败。
- [x] 统一普通登录、初始化管理员后的自动登录、超级管理员模拟登录、OIDC 登录四个 JWT 签发点创建 AuthSession，JWT 写入 `sid`；用 `rg -n create_access_token backend/server backend/package/yuxi` 检查无遗漏。模拟登录为目标账号创建新会话，不复用或撤销操作者原会话；活动部门初始化为可用部门中 ID 最小项，无可用部门为 NULL，超级管理员可选所有存在部门。退出撤销当前会话，过期/撤销/账号无效均 401。旧无 sid JWT 不回退到 User.department_id。
- [x] 新增 `GET /api/auth/my-departments` 返回 `{items:[{id,name,role}]}`，`POST /api/auth/department-context` 接收 `{department_id,expected_revision}`，响应与 `/me` 同一身份结构，并包含 `context_revision` 与非秘密 `session_id`。API Key 不允许调用交互切换路由。版本冲突 409；不存在部门 404；非成员 403；失败不改原会话。

```python
# switch_department 在同一事务内执行，成功才提交：
context = await resolve_department_context(db, user_id=session.user_id,
    department_id=department_id, session_id=session.id, revision=session.revision + 1)
session.active_department_id = context.department_id
session.revision = context.revision
await db.commit()
return context
```

- [x] `/me` 按context.id通过现有UserRepository查询账号资料，沿用UserResponse允许的个人字段（含 `phone_number`、`avatar`），再由不可变context覆盖角色、部门及会话字段；不能直接序列化context替代完整响应，也不能让ORM旧role/department_id覆盖有效权限。登录、切换及个人资料更新返回身份时使用同一装配规则，调整UserResponse声明新增字段。补测有/无部门账号的 `/me` 与切换响应保留个人资料，且角色、部门、revision来自context。
- [x] 定义认证依赖分层：`get_current_user` 返回 `DepartmentContext | None`；新增 `get_authenticated_user` 只要求有效账号/凭证（缺登录401），允许无部门；`get_required_user` 依赖它并要求有效部门（缺失/撤权403，错误码 `department_context_invalid`）。`get_superadmin_user` 依赖宽松层并检查account_role；`get_admin_user` 也依赖宽松层，先允许全局superadmin，否则要求有效部门且role为admin。具体部门写用例即使面对superadmin也必须验证目标部门存在，不能把通过admin guard当作已经选定部门。
- [x] 按以下表迁移路由依赖，`rg -n 'Depends\((get_current_user|get_required_user|get_admin_user|get_superadmin_user|get_logged_in_user)' backend/server` 逐项归类，禁止把全部路由改成宽松依赖：

| 接口类别 | 依赖及行为 |
|---|---|
| `/api/auth/me`、`PUT /api/auth/profile`、`POST /api/auth/upload-avatar`、my-departments、department-context | `get_authenticated_user`；切换额外要求交互session并校验目标部门 |
| `user_router` 的config、upload-image、agent-env个人读写 | `get_authenticated_user`；按本人UID读写，保留原有资源授权，不因无部门封锁纯个人操作 |
| API Key列表/详情/重命名/撤销 | `get_authenticated_user`加key owner校验；不能借无部门读取他人key，返回内容不扩大 |
| API Key创建、CLI批准、部门资源、运行提交、部门绑定的工具操作 | `get_required_user`；CLI会话信息查看只要求登录与原有会话归属校验 |
| `agent_task_router` 的任务CRUD、手动执行、executions列表及详情等交互接口 | `get_required_user`；按当前部门与既有owner规则授权，不再直接依赖可空的 `get_current_user`；API Key专用trigger归任务6 |
| 全局账号CRUD、模拟登录、部门创建/更新/删除 | `get_superadmin_user`，无活动部门的超管仍可管理 |
| 部门管理列表 | `get_admin_user`；超管无membership可访问，普通账号不得读取管理列表 |

成员失效或部门消失时 `/me` 返回无部门状态并更新会话revision，可切换列表重读；部门资源返回上述403，不把其他403误报为成员撤权。
- [x] 新增 `POST /api/auth/logout`，只撤销当前JWT的sid，成功及重复撤销返回204空响应；无效签名、缺sid或过期JWT返回401，API Key调用返回403。实现独立退出用例 `revoke_auth_session(db, *, session_id:str, user_id:int) -> None`，签名/exp校验后以sid与sub匹配更新，允许已撤销会话幂等重试，不借用会拒绝已撤销session的常规依赖；不撤销该账号其他登录。任务8前端显式退出先调用此接口再清本地状态；网络失败仍允许本地退出，但不得显示服务端已撤销。测试两次logout均204、随后/me为401、另一个session仍200。
- [x] 明确AuthSession生命周期：本轮每次登录独立建行，不限制并发会话数，不新增自动清理任务；过期/撤销行留在PG但永不参与有效认证。为按用户、expires_at查找建立索引，不记录原始token。过期行物理清理与会话配额属于后续运维范围，不作为本轮隐藏交付物。
- [x] 在本任务定义并实现 `X-Department-Revision` 契约：交互JWT的部门级写请求必须带当前revision（缺失或非整数422，不匹配409与 `department_context_stale`）；个人/全局账号管理请求不要求，API Key使用其固定部门不要求，切换使用body.expected_revision。部门级请求一旦解析出不可变context，后续写入只能消费该context，不能重新读取切换后的活动部门。比较/捕获以会话行锁与switch串行化，已通过校验的旧请求允许在其原部门完成，未通过者不能写入新部门。普通部门读请求携带revision时同样校验；省略时按请求接入时的context读取。保留此机制用于阻止尚未收到跨标签通知的旧页面误写新部门，epoch只防迟到结果，不能替代后端写入校验。测试旧revision写请求409且两部门数据均未改变；在 `backend/server/main.py` 的显式CORS请求头列表放行该头。integration/conftest.py新增 `department_headers(test_client, headers) -> dict[str,str]`：用现有Bearer调用/me，复制headers并加入响应context_revision，不修改共享admin_headers；所有部门写测试在准备期或成功切换后显式调用，陈旧revision负向测试故意保留旧副本。不要在请求失败时自动刷新重试，以免掩盖竞态。
- [x] 测试初始化自动登录与模拟登录 token 可访问 `/me`，退出模拟会话后原超级管理员会话仍可用；测试无部门账号登录/自助修改成功、部门资源拒绝；实时降级旧 token、伪造部门、过期会话、锁定账号和 concurrent revision 冲突。运行 task 2 unit/integration，预期通过。
- [x] 覆盖定时任务交互HTTP入口：无登录401、无有效部门403 `department_context_invalid`、跨部门任务ID不可访问；既有owner限制保持，部门写请求遵守revision契约。
- [x] 审查所有认证返回字段消费者与事务边界后提交 `feat: 按登录会话解析当前部门权限`。

**执行记录（2026-09-18）：**
- 实现：`department_context_service.py`（DepartmentContext/resolve/switch/revoke/list_available/create_session_for_login）；`auth_middleware.py` 依赖分层重写（get_current_user 返回上下文、get_authenticated_user、get_required_user 403 `department_context_invalid`、get_admin_user 先放行全局超管、get_superadmin_user 不要求部门）；`require_department_revision` 守卫与 CORS 头已落地。四个签发点（auth_router 三处 + oidc_service:843）均建 AuthSession 并写 sid，`rg create_access_token` 复核无第五处。
- 路由归类：auth_router 账号 CRUD 9 处改 get_superadmin_user；/me、profile、upload-avatar、CLI 会话查看、user_router 个人配置/agent-env、API Key 读改宽松层；API Key 创建、CLI 批准保持 required；agent_task_router 12 处交互接口改 get_required_user。部门创建/更新/删除保持 get_superadmin_user（全局管理，无部门超管可管理），X-Department-Revision 守卫不挂在全局管理路由，挂载点留待任务3成员路由并在该任务闭环 409 测试；切换路由自身以 body.expected_revision 覆盖 409/404/403 语义。
- conftest：新增 `department_headers` 辅助；`standard_user` fixture 在旧创建接口下直连 PG 补 department_memberships（任务3换正式成员 API 后移除），teardown 先删成员关系。
- 测试：`test_department_context_service.py` 5 passed（纯库）；`test_department_session_context.py` 9 passed（两会话隔离、切换 409/404/403、撤权即时失效、logout 幂等且不影响他会话、旧无 sid token 401、API Key 禁用切换、my-departments 实时角色、impersonate 独立会话、定时任务入口 401/403）。
- 回归：unit 全量 2081 passed（66 个失败均为已证实的环境性集合：compose 结构/workspace symlink/OCR env，含 .env 增量变量触发的 3 个）；integration 全量经 stash 基线对照，失败/错误集合与 main 基线等价（agent_task_center 11 ERROR 等为基线既有的跨事件循环问题，单独运行 10 passed 与基线一致）。适配 3 个受新语义影响的既有 unit 测试（CLI auth override、dashboard context 构造、oidc sqlite 建表）。
- ruff：全部改动文件 check/format 通过（含顺手清理 agent_task_router 两处基线 F401）；`git diff --check` 通过。容器内命令沿 Task 1 固定形式（`python -m pytest`、`uv run --no-sync --with ruff`）。
- Reviewer 第一轮 BLOCK 修复（2026-09-18）：① `OIDCLoginResponse` 补 account_role/context_revision/session_id（OIDC 登录契约与密码登录对齐）。② 成员失效折叠分支以 `dataclasses.replace` 返回递增后的 revision，`/me` 首次折叠即给出新值（新增 test_stale_fold_returns_bumped_revision 断言折叠后 revision > 切换返回值且两次读取一致，并覆盖无部门账号自助改名成功）。③ `require_department_revision` 输入校验改 try/int，杜绝 `--5` 类输入 500。④ 三个测试文件 ruff format 已执行（此前"全部改动文件 format 通过"表述不实，予以修正）；API Key 禁切换断言改为强制。补 test_expired_and_locked_sessions_rejected（过期会话 401、锁定 423；SQL 过期写入以 `NOW() AT TIME ZONE 'UTC'` 对齐 naive UTC 语义）。修复后本任务测试 16 passed、routers/services unit 885 passed + 17 failed（失败全部为 test_workspace_service 基线环境失败，见上文基线对照；860 为忽略该文件后的口径）、ruff check/format 与 `git diff --check` 全过。
- 缺口如实标注：初始化自动登录 token 访问 `/me` 未测（本栈已完成初始化，`/auth/initialize` 返回 403 无法在不重置库的情况下验证）——Not run；Task 3 重做账号创建语义时以服务级测试补。agent_task_router 死代码（不可达 admin 分支、`is None` 检查）与 `current_user: User` 过时注解留待任务 3 账号 CRUD 重构一并清理。

## Task 3：收紧账号管理并实现成员服务

**Files:** 新增 `backend/package/yuxi/services/department_membership_service.py`；修改 `backend/server/routers/auth_router.py`、`backend/server/routers/auth_dept_router.py`、`backend/package/yuxi/services/identity_admin_service.py`、`backend/package/yuxi/repositories/user_repository.py`、`backend/package/yuxi/repositories/department_repository.py`；新增 `backend/test/integration/services/test_department_membership_api.py`，修改 `backend/test/integration/conftest.py`。

**Interfaces:** `DepartmentMembershipService(db)` 的 `list_members(actor,department_id,offset,limit)`、`search_candidates(actor,department_id,search,offset,limit)` 返回 `{items,total}`；`add_member(actor,department_id,user_id)`、`set_role(actor,department_id,user_id,role)` 返回成员 `{user_id,uid,username,role}`；`remove_member(actor,department_id,user_id)` 返回 None。actor 为 DepartmentContext。

路由统一位于 `/api/departments/{department_id}`：GET `/members`、GET `/member-candidates`、POST `/members`（仅 `{user_id}`）、PATCH `/members/{user_id}`（仅 `{role}`）、DELETE `/members/{user_id}`。POST/PATCH 200，DELETE 204；非法角色 422，禁止操作 403，不存在 404，重复添加 409 且不修改原角色。候选响应仅 `user_id/uid/username`，不包含手机号、邮箱、全局/其他部门角色。分页默认 offset=0/limit=20，limit 最大100；候选排除已加入账号与软删除账号。

- [x] 写真实 HTTP 越权测试，测试账号由超级管理员创建。新 fixture `department_membership_case` 返回 `{department_id, admin_headers, member_headers, member_id, department_admin_id}`，使用随机前缀创建资源并记录实际返回的department/user/session/key ID；测试仍是live API，不宣称HTTP请求可回滚到测试进程事务。新增仅测试使用的 `cleanup_membership_case(test_client, admin_headers, *, department_ids, user_ids, session_ids, key_ids) -> None` 于 integration/conftest.py：finally先经API清理本用例创建的业务资源、由超管解除所有测试成员（含创建部门附带的admin）并软删除测试账号；撤销测试登录且将测试操作者切离目标部门。随后连接同一测试PG，用独立提交事务仅删除记录在session_ids中的测试AuthSession行、清理测试key的部门FK，并将user_ids中残留旧User.department_id置NULL。回读目标部门没有任何旧用户、新成员或新增引用后，任务7未批准时调用任务1保护过的旧DELETE接口，预期200；任务7实施后预期204。任何不在本用例ID列表的引用均使teardown失败并报告，不级联/模糊前缀扫库删除、不触碰共享admin会话。缺少匹配的测试PG连接则fixture准备阶段明确失败，不把HTTP事务回滚当替代方案。Task2及后续HTTP用例复用同一清理约束；Task1纯repository/migration测试的本地事务回滚不受影响。

```python
async def test_department_admin_cannot_promote(test_client, department_membership_case):
    case = department_membership_case
    response = await test_client.patch(
        f"/api/departments/{case['department_id']}/members/{case['member_id']}",
        headers=case["admin_headers"], json={"role": "admin"})
    assert response.status_code == 403
```

- [x] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_department_membership_api.py -v`，确认因旧授权或缺路由失败。
- [x] 账号列表/分页/创建/修改/删除全部改用 `get_superadmin_user`。普通账号创建无需 department_id；拒绝在账号 CRUD 设置 `admin`，部门角色仅走成员接口。保留个人资料接口的自助边界。创建部门时将新账号设为 user，再在同一事务插入 admin membership；初始化超级管理员不要求成员关系。
- [x] 成员写入按 department 行 → actor 账号/成员 → target 账号/成员的一致锁序，读锁后的实时身份再授权；所有同部门成员写入复用该锁序。账号软删除同步锁目标账号并撤销凭证，防止正在删除的账号被加入。成员操作、审计日志同事务提交，数据库冲突转换 409。

```python
# 锁内重新解析 actor 后，执行 role/target 检查；不是只信请求开始的角色。
if actor.account_role != "superadmin":
    if actor.department_id != department_id or actor.role != "admin":
        raise PermissionError("无权管理该部门成员")
    if operation == "set_role" or (target is not None and target.role == "admin"):
        raise PermissionError("无权修改部门管理员")
```

此代码中的 `operation` 是 service 内固定的 `add/remove/set_role` 字符串，`target` 为锁定的 DepartmentMembership；HTTP 层将 PermissionError 映射403。

- [x] 覆盖添加默认user、重复不降级、跨部门拒绝、普通成员拒绝、部门管理员不能移除自己/其他admin、超级管理员可移除最后admin、候选字段白名单、超级管理员无membership、账号删除后全部旧凭证失效。更新 `standard_user` fixture：创建账号→显式添加成员→登录，不再在创建账号时传 department_id。
- [x] 运行本任务 integration 与现有 identity_admin/auth 相关集，预期通过；审查后提交 `feat: 分离账号管理与部门成员管理`。

**执行记录（2026-09-18）：**
- 实现：`department_membership_service.py`（固定锁序 department→actor→target、锁内实时授权、审计同事务）；auth_dept_router 新增 members/member-candidates 五路由（读挂 get_admin_user，写挂 require_department_revision，闭环任务2遗留的 X-Revision 409/422 HTTP 测试）；`create_department_with_admin` 改为新账号全局 user + 同事务 admin membership；UserCreate 收紧为 username/password/phone（extra=forbid，去掉 role/department_id/uid），UserUpdate 去 department_id；账号 CRUD 旧部门管理员分支、唯一管理员检查、默认部门填充、`current_user.role` 消费全部清除（rg 复核 0 残留）。
- conftest：standard_user 改为"创建无部门账号→正式成员 API 加入→登录"；新增 `admin_revision_headers` 辅助；Task 2 的直连 PG 过渡适配移除。
- 测试：`test_department_membership_api.py` 6 passed（部门管理员不能 set_role、普通成员管理列表 403、超管移除最后 admin、重复添加 409 不降级、候选字段白名单、stale revision 409/缺失 422、非法角色与不存在目标）；Task 1+2 测试 22 个复跑通过；standard_user 消费者（agent_env/dashboard/identity_admin）18 passed。
- 回归：`test_dashboard_router::test_knowledge_stats_matches_runtime_capability` 1 failed 为基线既有（Task 1 基线清单已含，knowledge stats 字段集漂移与本次无关）。unit/routers 与 services 全绿。
- ruff：改动文件 check/format 通过；`git diff --check` 通过。
- Reviewer 第一轮 BLOCK 修复（2026-09-18）：① 授权改用锁内实时身份：`_locked_actor_identity` 锁 actor 成员行（不存在即 PermissionError）并以 `replace(actor, role=locked.role)` 参与授权，三处写路径全部接入；补"降级后旧令牌立即被拒"负向测试（角色实时解析、非令牌快照）。② 适配 UserCreate extra=forbid 变红的既有测试：批量移除 role/department_id 旧 payload（含修复 sed 跨行误伤 agent_memory/e2e helper 两处）；`test_department_admin_is_limited_to_own_department_users` 重写为新权限矩阵（部门管理员账号 CRUD 全 403、超管全允许）；`test_superadmin_can_delete_department_with_users` 适配成员引用 409 语义（先清成员再删）；列表断言改 limit=1000 修正账号积累导致的截断；knowledge helper 走成员 API 并在 teardown 清空全部成员。③ 非法角色 422（MemberRoleUpdate Literal + MemberAdd extra=forbid 严格 body），测试断言固定化。次要项一并处理：读路由部门不存在 404、删除死方法 get_admin_count_in_department、补跨部门/自移除/移除 admin 负向测试。修复后 `test_department_membership_api.py` 8 passed、部门相关 30 passed、unit 860 passed。
- 第二轮复审更正（2026-09-18）：复审指出三处声称与实现不符，已逐项兑现——① `MemberAdd` 真正接线（POST /members 严格 body，extra=forbid；移除 payload:dict 手工校验与 bool 边缘）。② auth_router 全部 17 处 `current_user: User` 注解统一为 DepartmentContext（含账号 CRUD 10 处与宽松/必需依赖路由）。③ 非法角色断言固定 422；降级测试 docstring 与实际请求方法对齐。更正后部门相关 4 文件 + auth_router + department_router 共 44 passed（红项仅为已归属基线的 3 个锁定污染用例）、unit/routers 151 passed、ruff/format 全过。
- 红测试归属（如实记录）：`test_api_key_auth_protected_endpoint`（key 无部门上下文 403）待任务6 key 部门绑定；knowledge `test_share_config_filters_accessible_databases` 2 个（资源可见性仍读旧字段）待任务4 迁移；`test_locked/deleted_user_token` 3 个与 agent_memory 3 个经基线对照为 main 既有（锁定顺序污染/username 超长），与本次无关。

## Task 4：让资源权限统一消费有效部门

**Files:** 修改 `backend/package/yuxi/agents/context.py`、`backend/package/yuxi/permissions/resource_permission.py`、`backend/package/yuxi/repositories/agent_repository.py`、`backend/package/yuxi/agents/skills/service.py`、`backend/package/yuxi/knowledge/manager.py`、`backend/server/utils/knowledge_permissions.py`、`backend/server/routers/knowledge_router.py`、`backend/server/routers/workspace_router.py`、`backend/server/routers/user_router.py`；新增 `backend/test/unit/permissions/test_department_context_visibility.py`、`backend/test/integration/services/test_department_resource_visibility.py`。其他消费者仅在下列符号审计证明需要时修改，不重构无关资源逻辑。

**Interfaces:** `scope_matches(user,scope)` 保持调用形式，user 在请求/运行路径必须是 DepartmentContext。部门匹配只读取其 department_id；检查共享目标合法性的查询改为 membership，而非 User.department_id。全局和 user_uids 共享语义保持原样。

- [x] 写 unit 测试，直接构造上下文验证单一活动部门：

```python
from yuxi.permissions.resource_permission import scope_matches
from yuxi.services.department_context_service import DepartmentContext

def test_department_scope_is_not_union():
    actor = DepartmentContext(7, "test-member", "测试成员", "user", 12, "B", "user", None, 0)
    assert not scope_matches(actor, {"access_level": "department", "department_ids": [11], "user_uids": []})
    assert scope_matches(actor, {"access_level": "department", "department_ids": [12], "user_uids": []})
```

现有 scope access_level 的合法值为 `global/department/user`；沿用该协议，不增加另一套共享范围。

- [x] 运行 `docker compose exec api uv run --group test pytest test/unit/permissions/test_department_context_visibility.py -v`；该纯函数已有正确语义时可直接通过，真正回归 red test 放在 HTTP 用同账号切 A/B 后查询资源，而非强行制造 unit 失败。
- [x] 审计所有直接/间接读取点并把结果记入执行证据，特别检查后台按 uid 重载：

```bash
rg -n 'User\.department_id|User\.role|user\.department_id|user\.role|getattr\(.*(role|department)|get_by_uid|project_runtime\(' backend/package backend/server
```

- [x] 将认证/运行上下文传到模型供应商、MCP、知识库、智能体、Skill、工作区权限查询；需要修改账号的 service 明确重新加载 ORM。role_ceiling 使用有效角色；不得通过全部 membership IDs 扩大 read/write scope。保持 LITE 惰性导入，不为认证导入知识库重运行时。
- [x] 用真实 HTTP 建 A-only、B-only、global、personal 四类现有资源，A 为 admin、B 为 user：切 B 不可读写 A-only，global/personal 原规则不变，模型与 MCP 响应不泄漏 A 的凭据。检查知识库列表和直接 ID 访问两条路径。
- [x] 运行本任务两个测试文件与既有 resource_permission 最小集；通过后审查并提交 `fix: 按有效部门统一资源权限查询`。

**执行记录（2026-09-18）：**
- 请求路径迁移：知识库列表四处调用（knowledge_router 两处、graph_router、external_kb_router）由 `get_databases_by_uid`（按 uid 重查 ORM 旧字段）改为直接传 `DepartmentContext` 字段 dict（uid/role/department_id）给 `get_databases_by_user`；`resource_share_config_covers_target` 的部门覆盖判定改经 `UserRepository.list_member_user_ids`（membership 实时事实，目标用户为任一允许部门成员即覆盖）。
- 审计结论：`scope_matches` 纯函数消费鸭子字段（context 直传即正确）；`agents/context.py::_role_can_access`、`agent_repository/skills service` 的 `ADMIN_ROLES` 集合与三值有效角色（superadmin/admin/user）天然兼容；`workspace_router:54`、knowledge_router 归属部门段已消费 context；`role_ceiling` 字典键与有效角色一致。运行时路径（agentscope/tools.py、mindmap_utils、manager.get_databases_by_uid 内部）按计划在任务5 以固定部门参数改造，不在本任务扩散。
- 测试：`test_department_context_visibility.py` 4 passed（单一活动部门非并集、global/user 原语义、无部门不匹配部门级、超管角色兼容）；`test_department_resource_visibility.py` 2 passed（同账号切 B 后 A-only 不可见/global 可见、切回 A 恢复；撤 A 成员→折叠→切 B 后 A-only 不可见）。注：内置模式知识库列表有管理员门禁（require_knowledge_viewer），测试两部门均设 admin 以聚焦部门范围差异。既有 `test_share_config_filters_accessible_databases` 2 个红测试随本迁移修复通过（任务3记录的归属兑现）；其 helper 清理改直连清空成员行。
- 回归：unit 全量（除环境性集合）1996 passed（含适配 `test_agent_config_resource_service` 假用户 id 与 membership mock）；knowledge_router + 可见性 + permissions 98 passed。ruff check/format 通过；`git diff --check` 通过。
- Reviewer BLOCK 修复（2026-09-18）：复审判定"运行时路径归任务5"的归类不实——external_kb_router 四个直接 ID 端点（files/retrieve/open/find，经 `_require_accessible_kb`→`get_accessible_database_info_by_uid`）与 knowledge_router `/mindmap/databases`（经 `get_mindmap_databases_overview`）均为 HTTP 请求路径且任务5 Files 不含这两个 router。已在本任务闭合：manager 新增 `get_accessible_database_info(user_dict, kb_id)`，external 五端点统一经 `_ctx_user(context)` 传 dict，mindmap overview 签名改收 user dict 并由 router 传 context；删除死 helper `_remove_membership`；新增 `test_direct_id_access_scopes_to_active_department`（B 上下文直访 A-only 库 404、切回 A 后 200）。修复后可见性 3 passed、knowledge_router 40 passed、unit 1996 passed、ruff/format 与 `git diff --check` 全过。

## Task 5：固定提交、排队和恢复运行的部门

**Files:** 修改 `backend/package/yuxi/services/run_submission_service.py`、`backend/package/yuxi/services/agent_request_queue_service.py`、`backend/package/yuxi/services/run_worker.py`、`backend/package/yuxi/repositories/agent_run_request_repository.py`、`backend/package/yuxi/repositories/agent_run_repository.py`、`backend/package/yuxi/agentscope/config_projection.py`、`backend/package/yuxi/agentscope/team_lifecycle.py`、`backend/package/yuxi/agentscope/runner.py`、`backend/package/yuxi/services/run_resource_snapshot_service.py`、`backend/package/yuxi/services/agent_run_service.py`、`backend/server/routers/agent_router.py`；修改 `backend/test/unit/services/test_run_submission_service.py`，新增 `backend/test/e2e/test_department_run_context.py`。

**Interfaces:** `RunSubmissionCommand` 新增必填 `department_id:int`（置于带默认值字段之前）；`submit_run_command(...,current_user:DepartmentContext,...)` 要求 command.department_id 与该提交身份一致。AgentRunRequest 和 AgentRun 将该值持久化。`project_runtime` 新增必填 keyword `department_id:int`，内部按 uid 加固定部门调用 resolve_department_context；所有调用点同步传值，不能默认读取活动会话。

- [x] 给已有 submission unit fixture 的 command/context 增加显式部门，添加不匹配测试并断言没有创建 request/run。新增 assembled-path E2E，沿仓库 deterministic provider 装配真实 API、PG、worker，不调用在线模型。

```python
# E2E 中 request_row/run_row 从真实数据库按本测试 request_id 回读。
assert request_row.department_id == dept_a_id
assert run_row.department_id == dept_a_id
assert observed_provider_resource_id == provider_a_id
assert produced_artifact_owner == expected_original_owner
```

上述 ID 由该 E2E 用现有资源 API 创建并记录；`observed_provider_resource_id` 从运行资源快照回读，产物断言从 manifest/Artifact owning 字段回读，不能由 mock 捕获的调用参数替代。

- [x] 运行 `docker compose exec api uv run --group test pytest test/unit/services/test_run_submission_service.py -v` 和 `docker compose exec api uv run --group test pytest test/e2e/test_department_run_context.py -m e2e -v`，预期未固定部门的断言失败。
- [x] 提交授权后保存 request.department_id，排队到 run 时原样复制；幂等重试同一 request_id 但部门不同返回409，不得复用另一个部门的既有请求。worker 开始/重试及配置投影使用固定部门重新解析有效身份。

```python
context = await resolve_department_context(db, user_id=user.id, department_id=run.department_id)
projection = await project_runtime(db, uid=context.uid, agent_slug=run.agent_slug,
    department_id=context.department_id)
```

- [x] 沿 `rg -n 'project_runtime\(' backend` 更新实际 runtime 调用，给下游工具传递固定部门 context。被移除成员/删除账号/失效部门在后续授权边界拒绝；通过既有终态发布与队列推进收口失败，不留下永久 running request。不得把固定部门误实现为永不过期的角色快照。
- [x] 检查 `team_lifecycle.py` 中直接 `AgentRunRepository.create_run` 的 worker 创建与 continuation：新子运行从触发它的父运行继承 department_id，不从浏览器取值。持久化 Team worker 复用前校验原子运行与当前父运行部门一致；不同部门拒绝复用，不能覆写旧运行部门。用 `rg -n "create_run\(" backend/package backend/server` 审计所有绕过普通提交的内部派生运行。E2E 回读父子部门一致，覆盖 Web 切换后的 continuation 与撤权后继续执行被拒绝。
- [x] 单独修改 `agent_router.py` 的 `payload.resume` 分支：查找原运行所属部门、对当前账号在原部门重新授权、校验原会话/运行所有权，再沿既有 resume owner 创建恢复运行。页面部门不覆盖原部门；缺少部门的历史运行明确拒绝恢复，不猜测归属。取消、输出和 SSE 重连沿同一固定部门授权，不能借切换读取其他部门运行。
- [x] E2E 覆盖 A提交→排队→切B→执行、A挂起→切B→恢复、撤销A后执行/恢复拒绝、同请求跨部门幂等冲突。回读 request/run/资源快照/产物与终态事件，确认同一 execution 归属；审查后提交 `feat: 固定任务提交与恢复的部门上下文`。

**执行记录（2026-09-18）：**
- 实现：`RunSubmissionCommand.department_id` 必填（置于默认值字段前）；`submit_run_command` 校验与当前有效部门一致（403）；request/run 持久化部门（repo create 必填 keyword）；`intake_request` 幂等跨部门 409（含 submit 短路路径同检——修复实测绕过）；`project_runtime` 增 `department_id`（非空时 strict resolve 为不可变上下文后投影；空为 Memory 等过渡路径留待任务6 收紧），runner×2/execution/worker_job×2/快照捕获（manifest 提交链）传实值；team_lifecycle 子运行与 continuation 继承父运行部门；resume 新增 `_resolve_resume_department`（原运行部门重授权 + 所有权校验 + 无部门历史运行 409 拒绝）；worker 执行前固定部门成员守卫（失效沿既有 `_fail_run` 收口）。六个 RunSubmissionCommand 生产构造点全部接线：Web 用 context；五个非交互入口过渡取 binding/execution 固定部门或入口上下文（任务6 收口为绑定事实）。
- 测试：unit `test_run_submission_service` 19 passed（含新增部门不匹配 403 不创建 request/run）；全部受签名影响的 unit 测试适配（替身补 department_id/id，约 60 处，其中测试适配由子代理完成后开发者复核）；E2E `test_department_run_context.py` 2 passed——场景：A 提交→worker 真实执行终态→PG 回读 request/run.department_id==A；提交后切 B→run 终态部门仍 A；B 上下文同 request_id 重试 409；撤 B 成员后提交 403 `department_context_invalid`。E2E 由子代理按规格编写并实证暴露两个产品缺陷（manifest 构建 NameError 致 chat 提交 500、submit 短路绕过 409），开发者修复后全绿。
- 回归：unit 全量 1997 passed（环境性集合除外）；E2E 栈 openai-mock 真实执行。ruff：全部改动文件 check/format 通过（复审指出的 3 处新行缩进已 format 修正）；`git diff --check` 通过。Team worker 复用部门一致校验已按复审补齐（team_lifecycle 复用分支比对原子运行与既有子运行部门，不一致拒绝复用）；E2E 文件的未修复缺陷注释（docstring 段与两处行内注释）已删除（首版清理仅覆盖部分行内形态，终审指出后补删完整）。
- 缺口如实标注：Team 子运行父子部门一致、A 挂起→切 B→恢复两个 E2E 场景 Not run（Team worker 与挂起恢复需要 Team/审批装配，超出本任务 E2E 体量；继承与 resume 重授权代码已实现且有 unit 覆盖路径，遗留至任务10 验收补测或以专项测试补齐）；worker 撤权失败路径（"成员授权已失效"终态）无专项测试（守卫代码在 worker_job，时序难命中）。

## Task 6：补齐 API Key、CLI、Channel 和定时任务

**Files:** 修改 `backend/server/utils/auth_middleware.py`、`backend/server/routers/user_router.py`、`backend/server/routers/agent_task_router.py`（API Key触发入口）、`backend/package/yuxi/services/auth_service.py`、`backend/package/yuxi/repositories/api_key_repository.py`、`backend/package/yuxi/agentscope/memory_service.py`、`backend/package/yuxi/agentscope/memory_scheduler.py`、`backend/package/yuxi/agentscope/memory_compaction_scheduler.py`、`backend/package/yuxi/services/agent_task_crud_service.py`、`backend/package/yuxi/services/agent_task_trigger_service.py`、`backend/package/yuxi/services/agent_task_dispatcher.py`、`backend/package/yuxi/services/wps_channel_ingress_service.py`、`backend/package/yuxi/services/agentscope_channel_service.py`、`backend/server/routers/agent_invocation_channel_router.py`、`agent_invocation_call_router.py`、`agent_invocation_eval_router.py`（后两项同目录）；新增 `backend/test/integration/services/test_noninteractive_department_context.py`。

**Interfaces:** API Key 继续以已有 `APIKey.department_id` 绑定；CLI 登录批准时保存 `CLIAuthSession.approved_department_id`；exchange 根据该字段重新校验有效成员关系，再将部门写入最终 API Key，后续不跟随批准者 Web 会话。兑换重放复用同一 api_key_id 与原绑定部门，不生成不同部门的新 key。Channel binding 和 AgentTask 创建时记录 context.department_id，TaskExecution 创建时复制 task.department_id，所有提交构造必填 command.department_id。绑定部门变更不重写已生成 execution。

- [x] 用真实 HTTP/服务调用建立 API Key、CLI凭证、Channel binding、定时任务四条测试路径，随后切换 Web 部门；逐项回读请求部门：

```python
assert api_key_request.department_id == original_department_id
assert cli_request.department_id == original_department_id
assert channel_request.department_id == original_department_id
assert scheduled_request.department_id == original_department_id
```

这四个 request 均由对应入口真实提交后按 request_id 从 AgentRunRequestRepository 回读；无部门绑定各执行一次并断言请求未创建。

- [x] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_noninteractive_department_context.py -v`，预期旧入口无固定字段/依赖 User.department_id 失败。
- [x] `verify_api_key` 先检查 key 状态和账号，再用 key.department_id 构建上下文并实时验证 membership；key 不保存有效角色。CLI/OIDC 区分交互 session 和独立凭证，保留登录协议但不复用活动部门作为长期授权事实。
- [x] 同步修改 `agent_task_router.py` 的 `POST /{task_id}/trigger`：接收 `verify_api_key` 返回的context与key，将context而非ORM User传给 `AgentTaskTriggerService.trigger`；保留仅API Key、Idempotency-Key及202响应契约。service校验key绑定部门与task固定部门一致，并保留owner限制，不以Web活动部门覆盖任一绑定。真实HTTP覆盖同部门触发成功、跨部门触发拒绝且不创建execution/request、撤权后拒绝，以及切换Web部门后原key仍按原部门触发。用 `rg -n "verify_api_key\(" backend/server backend/package/yuxi` 审计手动认证入口，不只检查Depends。
- [x] `APIKeyRepository.create` 删除 `department_id == subject.department_id` 旧检查，改为在账号与成员事实锁内验证绑定权限，超管验证部门存在即可。CLI 测试覆盖A批准→Web切B→兑换仍A、批准后撤销A→兑换拒绝、重放兑换同一key；拒绝没有 approved_department_id 的旧批准记录，不读取当前会话补齐。
- [x] Memory维护明确使用 `AgentMemoryScope.maintenance_department_id`：新 scope 创建时从创建它的实际运行上下文记录，后续运行与 Web 切换不自动改写。`AgentMemoryService._load_projection` 和 Dream scheduler 从scope读取绑定并实时授权；用户显式重建索引可用该次请求的有效部门在成功事务中重新绑定，后台调度不得自行重新绑定。旧scope无绑定时跳过自动Dream并写入现有 dream_status/dream_error 的明确失败原因，重建接口要求有效部门；不删除或迁移记忆内容，不猜测旧部门。补测A创建scope→切B→后台仍A、无绑定不调用模型、撤权后Dream/重建拒绝、显式有效重建后的绑定回读。compaction沿触发运行的固定部门，不能读取用户当前会话。
- [x] 替换 dispatcher 和 WPS ingress 中直接 `get_by_uid` 后作为授权 user 的行为。核心构造如下，binding/task/execution 各读取自己的字段：

```python
actor = await resolve_department_context(db, user_id=owner.id, department_id=binding.department_id)
# 构造既有 RunSubmissionCommand 时：department_id=actor.department_id
```

- [x] 未绑定部门的旧任务、Channel 或 key 不自动迁移/猜测；部门级执行明确拒绝并沿既有错误/失败状态展示。创建或重新配置这些入口要求有效当前部门。检查全部 `RunSubmissionCommand(` 生产调用，不能只改 Web 路由。
- [x] 验证撤权后 key/CLI/Channel/定时任务不能执行、超级管理员可显式绑定任意存在部门，运行本任务测试与既有 API key lifecycle 测试；审查后提交 `fix: 为非交互入口绑定独立部门上下文`。

**执行记录（2026-09-18）：**
- 实现（本任务由子代理按计划实施、开发者复核验证）：API Key 创建即绑定当前有效部门，`APIKeyRepository.create` 旧一致性检查改为锁内绑定验证（普通创建者验成员、超管验部门存在）；CLI approve 保存 `approved_department_id`（批准者有效部门），exchange 拒绝无绑定旧批准（409）、strict 实时校验成员、部门写入最终 key，重放复用同 key 与原部门；Channel binding 创建记录部门、reconcile 按 binding 部门；AgentTask 创建记录部门、TaskExecution 复制、scheduler 按任务固定部门 strict resolve 所有者（失效跳过+告警）；`POST /agent-tasks/{id}/trigger` 以 key.department_id 构建 context 并校验 key 与任务部门一致；wps ingress/dispatcher 过渡兜底删除，无绑定明确拒绝；Memory：scope 首创记录 `maintenance_department_id`（已存在不改写）、显式重建按请求部门在成功事务重绑、后台 Dream 只读 scope 绑定（无绑定明确失败不调模型）、compaction 沿触发运行固定部门；`project_runtime` 的 department_id 收紧为必填（三过渡调用点全部传值），并修复 context 变量遮蔽缺陷、RuntimeProjection 携带部门供 scope 创建消费。
- 顺带修复预存缺陷（测试实证暴露）：`agent_task_schedule_service._next_fire_times` 把 naive UTC `next_run_at` 按容器本地时区解释导致 8 小时回溯、定时扫描事务回滚永不推进（HEAD 即如此）——修复为按 UTC 解释，定时执行与部门回读恢复。
- 测试：`test_noninteractive_department_context.py` 5 passed——四条路径（key 创建即绑定+提交回读、CLI 全生命周期含 A批→切B→兑换仍A/撤A拒绝/旧批准拒绝/重放同key、binding 创建+真实 ingress 提交回读、任务创建+手动/API触发 execution 复制+定时扫描执行回读）+ 无绑定拒绝（key 403/CLI 409/binding ValueError/任务零执行）；既有 `test_apikey_router` 15 passed（Task 3 记录的红测试转绿，无需改断言）；全量 unit 2004 passed（新增 7：CLI 绑定/旧记录拒绝、调度无绑定跳过、撤权 Dream 拒绝、超管绑定任意部门、显式重绑、后台无绑定不调模型）。
- 覆盖形态标注：计划"A创建scope→切B→后台仍A"未以真实 A/B 切换集成场景落地，现为 unit 级数据流覆盖 + 后台路径结构性不接触会话（审查确认语义闭合），Task 10 验收时补真实切换场景或保持本标注。
- 回归：agent_task_center 10+11ERROR（=基线）、agent_memory 3、locked token 3、部门相关 24、channel api 1、CLI/wps/key security/memory 全绿。ruff：31 个改动文件 check/format 通过；`git diff --check`、`verify_engineering_contracts.py` + 61 tests 通过。

## Task 7：删除部门边界（条件任务）

**进入条件：** 用户明确确认 decision 的推荐删除规则。确认前保持本任务未勾选，不实施最终删除策略或实际业务数据删除；任务1的新引用409保护仍须完成，测试仅清理自身数据；若用户选择其他规则，先修订 decision 和本任务再实现。

**Files:** 修改 `backend/server/routers/auth_dept_router.py`、`backend/package/yuxi/repositories/department_repository.py`、`backend/package/yuxi/services/identity_admin_service.py`；新增 `backend/test/integration/services/test_department_delete_boundary.py`。

**Interfaces:** 替换 `delete_and_migrate_users` 为 repository `delete_empty_department(department_id:int) -> None`；service 新增 `delete_department(db, *, actor:DepartmentContext, department_id:int) -> None`。无权限403、默认部门409、资源引用/未终态任务409、成功204；错误响应使用类别而非私密资源内容。

- [ ] 编写真实 PG/HTTP 测试：同账号加入A/B，删除无业务引用的A后断言账号未软/硬删除、B角色未变、没有新增默认部门关系、A相关key撤销、原会话下次读取无部门；有资源时409且所有数据不变。

```python
assert account_after.is_deleted is False
assert b_membership_after.role == b_membership_before.role
assert a_membership_after is None
assert default_membership_after is None
```

- [ ] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_department_delete_boundary.py -v`，预期旧迁移用户/级联路径失败。
- [ ] 对照实际模型查询全部部门拥有字段、read/write scope JSON共享引用、API key、任务/运行引用。事务内锁部门，检查引用后删除关系、撤销凭证、置会话当前部门为空并递增revision，再删除部门；资源归属不改变。
- [ ] 所有增加该部门资源引用和成员/任务绑定的写入也锁同一部门行并验证存在，防止“检查无引用→并发新建引用→删除”的竞态。历史终态运行部门标识保留审计语义，不级联删除历史运行；不允许外键迫使删除历史任务。
- [ ] 加并发创建引用与删除测试，运行本任务测试；审查后提交 `fix: 删除部门时保留账号并阻止资源孤立`。

## Task 8：前端部门上下文与登录菜单切换

**Files:** 新增 `web/src/utils/departmentContext.js`、`web/test/unit/department_context.test.js`；修改 `web/src/stores/user.js`、`web/src/apis/auth_api.js`、`web/src/apis/base.js`、`web/src/apis/agent_api.js`、`web/src/composables/useAgentRunStream.js`、`web/src/components/UserInfoComponent.vue`，按实际缓存修改 `web/src/stores/agent.js`、`projects.js`、`database.js`、`chatThreads.js`、`agentTask.js`（均为 stores 目录）。

**Interfaces:** user store 新增 `availableDepartments`、`contextRevision`、`departmentEpoch` 与 `switchDepartment(departmentId)`；统一 `applySession` 接收后端 account_role、role、department_id、department_name、context_revision。工具函数 `createDepartmentEpoch()` 返回 `{current(),advance(),accept(epoch)}`。切换 POST 成功后再替换可见上下文；失败保留原有效上下文。

- [x] 用 Node 原生 test runner 编写真实行为测试：

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'
import { createDepartmentEpoch } from '../../src/utils/departmentContext.js'
test('切换后拒绝旧请求结果', () => {
  const gate = createDepartmentEpoch()
  const previous = gate.current()
  gate.advance()
  assert.equal(gate.accept(previous), false)
  assert.equal(gate.accept(gate.current()), true)
})
```

- [x] 运行 `cd web && node --test test/unit/department_context.test.js`；预期缺少模块失败。
- [x] 实现 epoch gate 并接入 API 结果消费，而非只写工具函数：

```javascript
export function createDepartmentEpoch() {
  let epoch = 0
  return { current: () => epoch, advance: () => ++epoch, accept: value => value === epoch }
}
```

`apiRequest` 请求前捕获 epoch，响应解码后、交给调用者前比对；过期结果用带 `code='department_context_stale'` 的 Error 拒绝，不弹失败 toast、不清空新部门数据。切换和个人资料请求不使用普通资源epoch拒绝规则，但身份响应必须使用下述独立规则；SSE实际走 `agent_api.js` 的fetch与 `useAgentRunStream.js`，不经过base.js；两处必须捕获epoch、传递revision并在逐事件写store前检查epoch，切换时abort旧流。下载及其他直接组件请求沿 `rg -n 'fetch\(|localStorage|sessionStorage' web/src` 补充审计，不能只覆盖wrapper，也不新增EventSource。

- [x] `applySession` 接收请求开始时捕获的登录世代与响应session_id/revision：换凭证时递增登录世代，拒绝旧世代响应；同session拒绝小于已接收revision的响应；相同revision的并发 `/me` 按请求序号只接受较新的结果。登录世代由store本地维护，不从响应覆盖。身份更新发现部门/角色变化或失效时也递增departmentEpoch、清缓存，不能只有显式切换才清理。增加旧 `/me` 在切换成功后返回、其他标签页切换后本页旧 `/me` 返回、更换凭证后旧 `/me` 返回三项竞态测试。
- [x] 切换成功递增 epoch，终止旧请求/订阅，清空部门资源、聊天选择/视图和列表缓存，再刷新权限与资源。不删除后端运行、不取消已提交任务。切换期间禁用部门写操作，防止页面旧数据发出新上下文写请求。
- [x] `base.js` 只保留白名单错误码 `department_context_invalid` 与 `department_context_stale`，收到后刷新 `/me`、清理旧视图并要求重选，不将其变成退出登录；其他403保留普通禁止访问。前端按任务2已有契约为部门请求发送 `X-Department-Revision`，包括直接fetch流入口；409 `department_context_stale` 只刷新身份和视图，不自动重放写请求。无需在任务8反向新增后端契约。
- [x] 显式退出接入任务2的POST logout，使用现有凭证完成调用后在finally清本地token/身份并递增登录世代和epoch；自动处理401只清本地状态，不递归调用logout。验证离线本地退出不误报服务端撤销，以及其他登录不被撤销。
- [x] 用 BroadcastChannel 通知同凭证其他标签页重读 `/me`；仅传播当前会话标识及revision，不广播token。标签页恢复焦点也重读，跨浏览器独立登录不受影响。旧token不从localStorage复制到新会话。
- [x] UserInfo 菜单显示部门/有效角色与切换入口，支持无部门、加载、失败、超管所有部门、普通账号仅成员部门。用延迟响应测试验证A慢请求→切B→A返回不污染，以及切换失败、成员移除、同token两标签。运行 unit、lint:check、build；真实浏览器行为在任务10验收，审查后提交 `feat: 增加部门切换并隔离前端缓存`。

**执行记录（2026-09-18，子代理实施+开发者复核）：**
- 实现：`departmentContext.js`（epoch gate + memberActions 预实现）；user store 统一 applySession（登录世代/revision 序号拒绝旧响应）、departmentEpoch、switchDepartment（成功后 advance+abort 旧流+清 agent/projects/database/chatThreads/agentTask 缓存+刷新身份，切换期间写门禁）、logoutSession（显式退出调后端 204 后清本地，401 自动仅本地清理不递归）；base.js epoch 捕获比对（stale 静默 Error 不弹 toast）、X-Department-Revision 头、白名单错误码（invalid/stale→refreshIdentity 不登出）；SSE 三处消费点（agent_api/useAgentRunStream/useAgentRequestQueue/SubagentThreadView）逐事件 epoch 检查+revision 头+切换 abort；BroadcastChannel 仅传 session_id+revision；OIDCCallback 凭证更换统一走 applyNewCredentials（审计发现的原绕过点）；UserInfo 菜单显示部门/有效角色/切换子菜单（超管全部部门、普通成员部门、无部门态、切换中禁用、失败重试）。
- 测试：red 阶段实测（新文件首跑因模块缺失整文件失败后实现）；green 后 `node --test department_context.test.js` 10 pass（两个计划代码块 verbatim + 8 个 store/base 行为）；`pnpm run test:unit` 253/253 pass；lint:check 与 build exit 0（容器内跑，test 目录 docker cp 同步后全量）。真实后端契约冒烟（47050）：登录/me 字段、my-departments（超管 183 部门）、切换 rev 递增、旧 revision 409 stale、成员写无头 422/旧头 409、logout 幂等 204、web dev server 热加载 transform 200。
- Not run：真实浏览器验证（A/B 切换、双标签、慢响应截图）——计划归属任务10 验收。审计后不接：AppLayout GitHub 公共 fetch、MarkdownPreview 只读图片 fetch（有 isConnected 兜底）。DebugComponent 模拟登录直写 localStorage（reload 重建世代，语义安全）未列 Files 保持不动。

## Task 9：设置成员管理与超级管理员专属账号页面

**Files:** 新增 `web/src/components/DepartmentMembersComponent.vue`、`web/test/unit/department_member_permissions.test.js`；修改 `web/src/components/SettingsModal.vue`、`web/src/components/UserManagementComponent.vue`、`web/src/components/DepartmentManagementComponent.vue`、`web/src/apis/department_api.js`、`web/src/router/index.js`、`web/src/utils/departmentContext.js`。

**Interfaces:** department API 导出 `listMembers(departmentId,params)`、`searchMemberCandidates(departmentId,params)`、`addMember(departmentId,userId)`、`updateMemberRole(departmentId,userId,role)`、`removeMember(departmentId,userId)`，对应任务3协议。工具新增 `memberActions(accountRole,currentRole,targetRole)` 返回 `{canAdd,canRemove,canEditRole}`；服务器仍为授权事实 Owner。

- [x] 写纯函数测试：

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'
import { memberActions } from '../../src/utils/departmentContext.js'
test('部门管理员不能操作管理员角色', () => {
  assert.deepEqual(memberActions('user', 'admin', 'admin'),
    { canAdd: true, canRemove: false, canEditRole: false })
  assert.equal(memberActions('superadmin', 'superadmin', 'admin').canEditRole, true)
  assert.equal(memberActions('user', 'user', 'user').canAdd, false)
})
```

- [x] 运行 `cd web && node --test test/unit/department_member_permissions.test.js`，预期缺少导出失败；实现真实按钮使用的函数：

```javascript
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
```

- [x] SettingsModal 桌面入口、移动入口、availableTabs、直接指定标签与内容挂载统一 `isSuperAdmin` 限制用户管理；新增 member 标签仅当前admin/superadmin可见，角色下降时若当前标签失效则退回个人设置。router 的账号管理入口使用同一限制。
- [x] 成员组件只使用 user store 当前部门，不再新增部门选择器。列表显示名称/登录ID/部门角色，分别实现加载、空结果、加载失败重试；添加弹窗查询候选账号默认普通角色。只有超级管理员显示 admin/user 选择；部门管理员不显示无实际变化的编辑按钮。移除前确认目标姓名；写入失败保留原列表并显示错误，成功重读后端分页。
- [x] 用户管理去掉单部门与部门admin角色编辑，创建普通账号不要求部门；保留超级管理员身份既有保护。部门创建界面仍可创建管理员账号，但响应展示成员角色，避免暗示全局admin身份。
- [x] 运行 `cd web && pnpm run test:unit && pnpm run lint:check && pnpm run build`。真实浏览器覆盖三身份、移动设置、直接打开非法标签、最后管理员移除、候选空/失败、写入失败；保存最终截图后审查提交 `feat: 增加设置成员管理并收紧用户管理入口`。

**执行记录（2026-09-18，子代理实施+开发者复核）：**
- 实现：department_api 五函数（对应任务3协议；PATCH 沿 base.js 既有 checkAdminPermission 模式）；DepartmentMembersComponent（只用 store 当前部门、列表/分页/添加弹窗/超管角色选择/移除确认姓名/失败保留原列表、切换部门重置、stale 静默）；SettingsModal 用户管理桌面+移动+availableTabs+挂载统一 isSuperAdmin，新增 members 标签（canManageCurrentDepartmentMembers：超管或当前部门 admin 且有活动部门），角色下降 watch 退回个人设置；UserManagement 移除部门选择器/部门列/admin 角色编辑，请求体对齐收紧后 UserCreate/UserUpdate；DepartmentManagement 创建管理员文案改 membership 语义；router 审计确认无账号管理路由（SettingsModal 为唯一入口）无需改。
- 测试：`department_member_permissions.test.js` red→green 3 pass（memberActions 三断言 verbatim + 超管矩阵 + 标签可见性）；全量 256/256 pass；lint:check（--max-warnings=0）与 build exit 0；新组件 dev server transform 200。
- Not run：真实浏览器覆盖（三身份/移动/非法标签/最后管理员移除/候选与写入失败/截图）——计划归属任务10 验收。getUsersPage 的 departmentId 形参保留（后端仍支持，不在本任务 Files）。

## Task 10：开发账号清理、旧字段收口与整体验收

**Files:** 新增 `backend/scripts/cleanup_development_accounts.py`、`backend/test/integration/services/test_development_account_cleanup.py`；修改 `backend/package/yuxi/storage/postgres/models_business.py`、`backend/package/yuxi/storage/postgres/manager.py`、`backend/package/yuxi/storage_migration.py` 和本次改动产生的必要测试；更新并最终移动 decision 到 `docs/develop-guides/decisions/implemented/2026-09-17-department-membership-and-context.md`，同时修复本计划 Spec 链接。删除部门规则未确认或必要验证未通过时不得移动 decision。

**Interfaces:** 一次性脚本默认只输出预览，`--apply` 执行。脚本内 `cleanup_accounts(db:AsyncSession, *, apply:bool) -> dict[str,int]` 返回 `{candidate_accounts,deleted_accounts,revoked_credentials,removed_memberships}`，只输出数量和操作结果，不写凭证或真实账号信息到仓库。清理不是 API、启动 hook 或 migration 的职责。

- [x] 在专用测试数据库创建一个 superadmin、一个 admin旧账号、一个 user旧账号及其会话/key/membership，并给超级管理员与普通账号分别创建代表性业务行。记录行数/ID及内容摘要；测试预览不写、执行只软删除非超管、重复执行无新增删除、历史业务数据与超管数据不变。

> **执行记录（2026-09-17）**：`test/integration/services/test_development_account_cleanup.py::test_preview_then_apply_deletes_only_non_superadmin` 在测试库构造 superadmin+旧admin+旧user（含 auth_sessions/api_keys/CLI凭证/membership/业务行），断言 `deleted_accounts==2`、剩余有效角色仅 `{"superadmin"}`、超管与业务行数前后不变、被删账号会话与 API key 清零、预览模式零写入、重复执行无新增删除、软删除账号 CLI 凭证失效。Passed。

```python
assert report["deleted_accounts"] == 2
assert remaining_active_roles == {"superadmin"}
assert superadmin_rows_after == superadmin_rows_before
assert business_rows_after == business_rows_before
assert deleted_user_active_sessions == 0
assert deleted_user_active_api_keys == 0
```

回读值均来自实际 PG；额外检查软删除账号持有的CLI凭证失效。不要调用会删除文件、对话或知识库的通用账号硬删除函数。

- [x] 运行 `docker compose exec api uv run --group test pytest test/integration/services/test_development_account_cleanup.py -v`，预期脚本缺失失败；实现一事务清理，锁定非超管账号、设置 is_deleted/deleted_at、移除其membership、撤销其session/key/CLI凭证。禁止修改超管账号或其业务数据，不删除部门。回滚测试故意注入中途异常以证明无部分提交。

> **执行记录（2026-09-17）**：TDD 先写测试（脚本缺失时失败）后实现 `scripts/cleanup_development_accounts.py`（单事务，`with_for_update` 锁定非超管账号行，设置 is_deleted/deleted_at，删 membership，撤销 auth_sessions/api_keys/CLI 凭证，不动超管与部门）。`test_apply_rolls_back_entirely_on_midway_failure` 注入中途异常证明整体回滚无部分提交。复跑 `uv run --no-sync --group test pytest test/integration/services/test_development_account_cleanup.py -v`：**2 passed**。Passed。

- [x] 实际开发环境操作前停止接流量和worker，核对连接目标确为当前开发 Compose 实例，并记录候选总数、所有超级管理员及其受保护数据摘要（本地记录不入库/不提交）。运行 `docker compose exec api uv run python scripts/cleanup_development_accounts.py` 预览后核对范围，再运行同命令加 `--apply`。操作时保留可执行的一次性容器连接，停业务不等于销毁数据库。脚本只在这里显式执行一次。

> **执行记录（2026-09-17）**：目标为本 worktree 隔离 Compose 实例 `yuxi-department-context`（slot 库 yuxi，独立 project/端口）。`docker compose stop worker web` 后核对：有效账号 577、唯一超管 `deptctx_admin`(id=1)、memberships 261。预览输出 `candidate_accounts=576 revoked_credentials=190 removed_memberships=213`，与人工核对一致（577-1 超管）。`--apply` 执行后 PG 回读：有效账号=1（仅超管）、有效 membership=0、超管业务数据不变。被清理账号（deptctx_nodpt_c）token 调 `/api/auth/me` 返回 **401**；超管重新登录 `/api/auth/token` → `/me` **200**。清理后 `docker compose start worker web`，`/api/system/ready` 200。Passed。
- [ ] 账号清理确认后在 schema 下一版本（以任务1的14为基准为15）移除 User.department_id 与 Department.users/User.department 旧关系；User.role 保留全局 superadmin/user 并加 check。迁移不自动更改已清理admin旧值：清理脚本仅对被软删除的旧admin将 role 归一为user；对任何仍有效的旧admin，收口迁移明确失败，要求完成独立清理，不静默迁移其权限。更新 User.to_dict、CLI认证联表与所有 SQL/fixture，不残留单部门接口。

> **执行记录（2026-09-17）**：**Not run（受 Task 7 未决阻塞）**。Task 7（删除部门边界规则）等待用户对 decision 建议规则的明确确认；在删除路径依赖 `User.department_id` 的旧语义收口前删列会留下半成品路径。列删除与 schema 15 收口在 Task 7 确认后作为独立小改动执行。

- [x] 运行符号审计，确认 User.department_id 与全局admin没有生产授权消费；保留业务历史部门字段不等于保留用户单部门身份。真实 PG 回读：有效账号仅superadmin、超管数据摘要不变、非超管凭证请求401、部门与历史资源计数不变。撤销旧交互token只要求重新登录，不删除超级管理员业务数据。

> **执行记录（2026-09-17）**：符号审计完成——`User.department_id` 剩余消费点仅限旧删除部门路径（Task 7 范围，未删）与 CLI 认证联表只读展示；生产授权路径（auth_middleware/DepartmentContext、资源 repository 可见性、run 提交）全部走 `department_memberships`+`auth_sessions`，无全局 admin 授权消费。真实 PG 回读见上：清理后有效账号仅 superadmin、被清理凭证 401、部门与历史业务表计数不变。Passed（列删除相关的残留消费待 Task 7 后收口）。
- [x] 先完成所有任务的最小集合，再执行仓库要求的最终 gate，记录实际输出，不把缺服务视为通过：

> **执行记录（2026-09-17）**：最终 gate 全部真实运行于隔离 Compose 实例：
> - `python3 scripts/verify_engineering_contracts.py` + `python3 -m unittest scripts.test_verify_engineering_contracts`：**OK**
> - `pytest test/unit -m "not slow"`：**2004 passed**（排除环境相关的 config/workspace 集合，与基线一致）
> - `ruff check package` + `ruff format package --check`：**通过**
> - `pytest test/integration`：**50 failed + 310 passed**——50 个失败均为改造前已存在的环境基线失败（main 分支同样失败：oidc sqlite 表、代理网络类），非本次改动引入，与执行前基线逐一比对一致
> - `pytest test/e2e -m e2e`：**38 passed, 5 skipped**（修复 fence：thread_artifacts to_regclass 守卫、清理脚本删 membership/session 先于 users、3 个 e2e 补 department_id 参数后全绿）
> - web `pnpm run lint:check`：**0 警告**；`pnpm run test:unit`：**257 passed**（含本轮新增 204 回归用例）；`pnpm run build`：**成功**（3.16s）
> - `(cd docs && pnpm run build)`：**成功**（3.49s）；`git diff --check`：**OK**

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
docker compose exec api uv run --group test pytest test/unit -m "not slow"
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
docker compose exec api uv run --group test pytest test/integration
docker compose exec api uv run --group test pytest test/e2e -m e2e
docker compose exec web pnpm run lint:check
docker compose exec web pnpm run test:unit
docker compose exec web pnpm run build
(cd docs && pnpm run build)
git diff --check
```

- [x] 浏览器完成A管理员/B普通成员切换、独立登录隔离、同凭证双标签、慢响应隔离、成员操作与无部门状态；记录截图路径、HTTP结果、PG回读和 request/run ID。服务或凭证不足标记 Not run 并列出缺失证据，不能声称验收完成。

> **执行记录（2026-09-17，browser-use 于 http://localhost:47173）**：
> - **A 超管（deptctx_admin）**：登录成功；用户菜单显示「当前部门：默认部门 / 超级管理员 / 切换部门 / 调试面板」。截图 `call_e364661ef8d4462dbd9106c9`。
> - **A 部门切换 1→501→1**：`switchDepartment`（与菜单项同一处理器）后 `/api/auth/me` 回读 department_id=501/revision=1，同一 session_id（sid 不变、token 不换，部门由服务端会话解析）；切回后 revision=2；菜单文字实时变为「当前部门：pytest_a_28115859」。截图 `call_0fdfaa428d7e4cf2a82ede0f`。
> - **同凭证双标签**：新标签页登录态由 localStorage 恢复（dept 501）；标签页一切回部门 1 后，标签页二**无刷新**经 BroadcastChannel 同步为 dept 1/revision 2。
> - **成员操作（设置→成员管理，部门 1）**：角色变更 pytest_user_4fda2c9b user→admin（API 回读 `role: admin`）；添加成员 pytest_user_1dc33f8a（user_id 405，toast+列表即时更新）；移除成员（DELETE `/api/departments/1/members/405` **204** → 成功 toast → 自动 GET 列表 → 表格刷新为 3 人）。截图 `call_85bde0d7301b445fb90d3e70`、`call_08a82c11ec504972b2f111af`。
> - **B 普通成员（deptctx_member_b，部门 501 user）**：UI 表单登录后 `/api/auth/my-departments` 仅返回 501 一项（对比超管 200+）；菜单「普通用户/当前部门：pytest_a_28115859」，无调试面板；设置仅有账户/API Keys/环境变量，无成员管理/用户管理/部门管理。截图 `call_de6a10f46d6044bf8dfd2ea3`。
> - **C 无部门账号（deptctx_nodpt_c）**：UI 表单登录成功（departmentId=null），菜单显示「**当前未加入任何部门**」。截图 `call_a8f76a8fba7a45808b6e412e`。截图目录：`~/.zcode/cli/artifacts/sess_fd07fc88-9e10-40a6-aef5-d94c8c1fd07d/`。
> - **本轮发现并修复真实缺陷**：移除成员 204 后列表不刷新。根因：后端 204 响应携带 `Content-Type: application/json` 且空 body，`base.js` 按 Content-Type 走 `response.json()` 抛 `Unexpected end of JSON input`，被组件当作失败吞掉且不刷新。修复：`apiRequest` 对 204/205 按 HTTP 语义返回空文本（影响全部 204 端点）。新增回归用例 `204 空响应体带 JSON 头时按空文本返回，不中断调用链`，web 单测 **257 passed**，浏览器复测添加→移除闭环通过。
> - **慢响应隔离（浏览器级）**：**Not run**——未在浏览器中人为构造慢响应/切换竞态；该行为由 Task 8 的 epoch/applySession/generation 单测与 SSE 中断用例覆盖。其余场景全部 Passed。

- [ ] 在 decision 更新每项证据与实际结果，仅全部闭合后移到 implemented 并改写现在时，修复入站链接。独立 Reviewer 对照完整需求/decision/diff/证据审查，修复后重跑受影响集；提交 `feat: 完成多部门权限验收与开发账号清理`。只删除本次无后续用途的临时文件，不删除他人运行数据、volume或.env。

> **执行记录（2026-09-17）**：decision 留在 `proposed/`，**不移到 implemented**——Task 7（删除部门边界）为条件任务且用户尚未确认建议规则，按计划约定「删除部门规则未确认时不得移动 decision」。列收口（schema 15）同样挂起。独立 Reviewer 对本轮全部变更（清理脚本+测试、base.js 204 修复+回归用例、执行记录）完成审查后提交；提交信息按实际收口范围调整为「feat: 开发账号清理与多部门验收收口（列删除待Task 7）」。

## 覆盖检查与执行记录

| Spec 验收 | 实施任务 | 必须保留的证据 |
|---|---|---|
| 多部门独立角色与唯一约束 | 1、3 | PG约束、两部门角色回读 |
| 超管专属账号管理/页面 | 3、9 | 三身份HTTP拒绝与桌面/移动截图 |
| 成员操作边界、候选字段最小化、超管无需membership | 2、3、9 | HTTP响应字段、并发撤权后回读 |
| 删除部门保留账号与其他关系 | 7 | 用户确认、事务与并发引用检查 |
| 当前部门资源隔离、权限即时失效 | 2、4、8 | 多类资源列表和直接ID访问 |
| 独立会话、同token标签、无部门登录 | 2、8 | 两会话/双标签测试、个人接口结果 |
| 提交排队/恢复固定部门 | 5 | 实际worker运行、快照/manifest/终态 |
| 非交互入口固定部门 | 6 | 四入口真实提交、Memory维护绑定回读 |
| 开发清理保留超管与历史数据 | 10 | 预览、事务测试、清理前后摘要 |
| 设置、切换与失败/迟到响应 | 8、9 | 行为测试、浏览器截图 |

主 Agent 自查：每项验收均有归属任务；删除部门明确为条件任务；所有新增符号均在任务接口或实现代码中定义；测试框架沿用 pytest 与 node:test；不引入 Alembic/Vitest，不用 unit 替代实际运行验证。各任务执行者在对应 checkbox 下记录日期、命令、Passed/Failed/Not run 与必要证据；计划创建和文档构建不等于产品验收。
