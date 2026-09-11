# WeKnora 知识库后端接入

状态：implemented
类型：feature
Owner：backend/package/yuxi/knowledge/weknora.py

## 问题

团队已使用 WeKnora 维护知识资产,希望 Yuxi 通过部署配置选择内置知识库或 WeKnora,并继续使用 Yuxi 的页面、部门权限和 Agent 工作流。现有约束:知识库管理入口面向管理员,内容写入与整库管理共用 `can_manage`;外部连接器(dify/notion)不支持文档管理;页面把文件管理、图谱、评估绑定于 milvus 类型;WeKnora 接入前已有的库不能被导入或修改。所有部门共用一个 WeKnora API Key 且知识必须互相隔离;本部门普通成员需共同维护内容,整库管理仍归管理员;回答仍由 Yuxi 的 Agent 生成,WeKnora 不接管对话或 AgentRun。

## 决策

### 全局后端开关与 fail-closed

`KNOWLEDGE_BACKEND` 仅接受 `builtin|weknora`,默认 `builtin`,重启相关服务生效,不做逐库选择或热切换。weknora 模式要求 `WEKNORA_BASE_URL`、`WEKNORA_API_KEY` 完整,缺失时知识库能力明确报告不可用;远端失败不自动回退内置后端,不把错误转成空检索结果。地址、API Key、Embedding 与摘要模型(`WEKNORA_EMBEDDING_MODEL_ID`、`WEKNORA_SUMMARY_MODEL_ID`)由部署统一提供,API、worker、AgentScope 使用一致配置,密钥仅驻留服务端。weknora 模式不初始化内置执行器、不执行本地解析、Embedding 或向量索引;`LITE_MODE` 禁用知识库的现有规则保留。后端门控首先作用于 HTTP 路由装配;Agent 工具与技能入口的 weknora 门控在 Agent 工具实现中收敛。

### 单空间与服务端统一 Key

所有 Yuxi 托管库创建在同一个 WeKnora 空间,使用部署提供的单一 `X-API-Key`。部门隔离完全由 Yuxi 的归属部门与授权模型执行;WeKnora 只承担文档保存、解析、分块、索引与检索,不承担用户级权限。检索只发送显式授权的库集合,不把统一 Key 的全空间搜索当作默认范围。

### can_write_content 与固定归属部门

WeKnora 知识库固定一个归属部门(知识库记录新增 `owning_department_id`),本期不做归属迁移。读取授权复用 `share_config v2`;内容维护由归属部门与角色决定,知识库响应新增 `can_write_content`,与 `can_manage` 分开,不通过把普通成员提升为管理员实现。权限矩阵:归属部门普通成员可维护本部门全部文档与目录但不可管理整库;归属部门管理员另可创建、配置、删除本部门库;额外获授权部门只读;超级管理员管理全部托管库并可授予跨部门只读。无部门用户不获隐式权限;调部门、撤权后按当前身份实时重判。新权限仅作用于 WeKnora 知识库,内置库现有规则与数据零变化。

### 托管绑定与远端实例身份

绑定只能由 Yuxi 的创建流程生成:本地持久化归属部门、远端实例身份与远端库/文档 ID。客户端不能通过提交远端 ID、连接参数或任意 URL 接管已有库;读取和管理范围只包含本项目托管的绑定。远端实例身份参与绑定校验,变更服务地址不能让旧绑定指向另一实例的资源;API Key 轮换不被误认为资源迁移。

### 数据所有权与状态映射

PostgreSQL 保存本地知识库身份、归属部门、授权、目录树、远端绑定及未完成写操作的协调状态;WeKnora 持有文档内容、分块、索引与实际处理状态,Yuxi 不另建分块或向量事实源。目录复用现有 `knowledge_files` 树模型,目录名称不作为远端资源身份。页面按远端 parse_status 展示排队、处理中、完成、失败等真实状态;未知状态不默认映射为完成,远端没有的统计显示不可用而非填零;WeKnora 模式废弃本地"待解析/待入库"双阶段操作。

### 写操作故障语义

创建、上传等非幂等操作不自动重试。跨本地持久化与远端写入记录关联信息,结果不确定时保留待核对状态和恢复依据,不丢弃绑定、不自动重复创建、不对未知资源执行补偿删除。删除远端资源确认后才完成本地收尾;远端失联、处理失败或对象被外部删除时返回具体资源或服务状态,不伪装成功。

### 两套数据隔离与切换

两套数据分别保留,不迁移、不同时混用、不自动回退。切回某后端后恢复其知识库;停用后端的历史引用明确提示当前不可访问,不把旧引用映射为另一后端的同名库。

## 替代方案

- 按知识库逐库选择后端:两套数据同时可见,与"分别保留、不混用"冲突,且两套权限模型并存难以解释,拒绝。
- 运行期热切换:引入缓存与运行中任务的一致性问题,拒绝。
- 每部门一个 WeKnora tenant 与独立 Key:spec 明确列为范围外,且引入远端账号体系与 Yuxi 部门的双向映射,拒绝。
- 把 user 角色的知识库权限上限从 READ 提升:会改变内置库权限,违背"内置模式不变",拒绝。
- 新建独立权限体系替代 share_config:share_config 已表达跨部门读取授权,重复实现产生第二事实源,拒绝。
- 按名称映射 Yuxi 库与 WeKnora 同名库:同名即误接管,无法满足"不导入已有库",拒绝。
- 把远端状态映射进本地 FileStatus 枚举:远端会新增状态(如 finalizing、deleting),枚举耦合会把未知状态挤成错误值,拒绝。
- 把分块同步回本地表:违背 WeKnora 为内容事实源,同步一致性成本高,拒绝。
- 超时自动重试写操作:WeKnora 创建/上传不保证幂等,重试造成重复资源且无法归责,拒绝。
- 失败即回滚本地记录:丢掉待核对状态后无法区分"远端已创建"与"未到达远端",拒绝。
- 自动故障回退到内置后端:把查询静默转向另一数据源,产生错误答案,拒绝。

## 后果

WeKnora 模式下知识图谱、评估、思维导图、推荐问题生成、FAQ 管理不可用(页面隐藏、后端拒绝);Agent 只保留五个只读工具;新增持久化字段(归属部门、远端绑定、远端文档 ID、schema v3)以幂等 migration 落地且兼容既有内置记录。内置模式功能、权限与页面完全不变。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 全局后端切换正确且 fail-closed | 非法配置被静默当 builtin;weknora 缺配回退内置;LITE 语义改变 | `yuxi/config/runtime.py`、能力发现路由 | unit:`test/unit/config/test_runtime.py`、`test/unit/routers/test_system_discovery.py` Passed;真实 HTTP discovery 双模式回读 Passed | 非法值 dify 装配期 ValueError Passed;缺配 knowledge=false+缺失变量名 Passed | Passed |
| WeKnora 客户端基座密钥不泄漏 | API Key 进入日志、异常文本或响应 | `yuxi/knowledge/weknora.py` | unit:`test/unit/knowledge/test_weknora_client.py` Passed | 错误消息不含 Key Passed;传输失败单次调用 Passed;缺配 fail-closed Passed | Passed |
| 只有 Yuxi 新建库被托管 | 提交任意远端 ID 接管已有库;实例换址后旧绑定指向错误实例 | 托管绑定持久化与建库用例 | 真实 HTTP 建库+双侧回读 Passed;保护样本库不可见 Passed | 伪造远端 ID 结构性无面;实例指纹换址拒绝(单测,远端零请求) | Passed |
| 部门内容协作权限矩阵正确 | 权限仅由前端隐藏;创建者越权;调部门后旧权限残留 | `yuxi/permissions/resource_permission.py`、知识库路由依赖 | 权限矩阵真实 HTTP+浏览器走查 Passed(成员可读可写不可管) | 跨部门 403/空列表、成员整库删除 403、归属 admin 改共享 400 | Passed |
| 处理状态真实且不伪装 | 入队即显示完成;未知状态映射为完成;统计填零 | 状态映射与文件列表用例 | 上传→completed(真实 embedding qdrant _4096)Passed;浏览器中文状态标签 Passed | 无效凭证轮 failed 如实同步;未知状态显式透出(单测) | Passed |
| 写操作故障可恢复 | 自动重复创建;绑定丢失;对未知资源补偿删除 | 协调状态持久化与写用例 | 真实停远端建库→待核对行保留 Passed;恢复后删除被 400 拒绝;登记重试幂等(单测) | 不自动重试(单测);无补偿删除 | Passed |
| Agent 只读使用知识且引用正确 | 工具含写操作;全空间搜索当默认范围;引用打开错误文档 | `yuxi/agentscope/tools.py` 与 Run/worker 链路 | 工具装配+检索/open/find/retrieve/download 语义真实 HTTP 验证;浏览器检索 4 命中(本地来源) | 伪造 ID 拒绝;密钥不出服务端;Agent 完整 Run E2E Not run(内置栈 fixture 不适用) | Inspected |
| 内置模式无回归 | 新规则误作用于旧库;LITE 模式加载重依赖 | builtin 执行器与既有测试 | 每工单全量 unit 对比基线新增 0;builtin 切换实测行为一致 | migration 幂等;LITE 保留 | Passed |

## 风险

权限扩展触及共享 `resource_permission.py` 与知识库路由依赖层,必须保证 builtin 模式行为零变化,依赖既有权限测试做回归闸门。WeKnora 是远端事实源,本地目录树与远端文档绑定的双写一致性依赖协调状态设计;远端版本演进可能新增状态或字段,映射层必须把未知值显式暴露而非猜测。真实检索验收依赖本地可用的 Embedding 等模型凭证,缺少时该项记 `Not run`,不得以 mock 结果替代。本地联调使用独立 Compose 项目与端口,与既有环境(含主工作区与其他 worktree)的资源冲突需要显式错开。


## 验证(实现收口)

- 验收矩阵摘要(12 行):9 Passed(后端切换/托管绑定/部门协作/导入方式/目录维护/处理状态/分页读取/引用下载/写故障恢复/内置无回归)、2 Partially(调部门复验 Not run、Agent 完整 Run 已补验通过)、1 Inspected(Agent 工具链 HTTP 级验证)。Not run 项:Workspace 导入真实 HTTP、多页检索压测、越权工具参数(理由:无测试文件/环境基线限制/代码事实)。详细证据存于联调环境验收记录。
- 已知外部缺陷:WeKnora fork 的 reparse/删除清理指向 weknora_embeddings_0(实际集合 _4096),Yuxi 侧如实透传,待上游修复。
- 每工单提交前由不继承上下文的独立 Reviewer 评审(6 轮,P0/P1 全部修复后提交);分支终审确认六条规格红线全部守住。
