# AgentScope 2.0.7 Yuxi-only 适配

## 目标

Yuxi 将 AgentScope git 依赖从 `c28b985c` 升级到
`7a9ef73662fb96cde89e0eb984bd4a2c6334c07d`，不修改 AgentScope 源码，保留现有
Thread、Session、消息、摘要和存量 Workspace。

## 范围

- 适配 Session 锁内读取和压缩失败保留旧摘要的上游行为。
- 持久化 parent/worker `workspace_id`，以该 ID 管理新目录、旧目录和残留容器。
- 让 AgentScope 原生 Team middleware 负责 `TeamSay` 纠正和 leader 通知；Yuxi 只投影 child Run。
- 按 Anthropic、OpenAI 和 Gemini 的真实口径归一缓存输入，并按 `reply_id` 去重。
- 保持 Yuxi 的 `max_execution_steps -> max_iters` 投影，最大迭代保留文本和失败终态。
- 接受 AgentScope 默认保留最近 5 张图片，不增加管理配置。

不接入 Channel、AgentScope RAG/WebUI、GoalPipeline、Scheduler、预热池和图片数量配置 UI。
保留 `AgentCreate` 与 `_sub_agent_templates` 私有兼容点，但用契约测试锁定且不增加新私有依赖。

## 数据与删除契约

业务库新增 nullable 列：

- `agentscope_thread_sessions.agentscope_workspace_id`
- `agentscope_team_worker_bindings.agentscope_workspace_id`

新 Session 创建后立即读取并保存 ID；旧映射在继续对话或删除前懒回填。Session 不存在且
ID 仍为空时显式失败，不从目录名猜测。

线程删除顺序固定为：

1. 回填 parent 和全部 worker Workspace ID 并提交。
2. 删除 parent Session，让 AgentScope 完成取消、Team 级联和逻辑清理。
3. 调用 `DELETE /yuxi/workspace/thread` 清理缓存实例、容器及新旧目录。
4. 删除 Agent 和 Credential。
5. 停用 worker binding、删除 parent mapping、标记 child Thread 删除。
6. 最后软删除 Yuxi Thread。

物理清理失败时保留 Yuxi Thread、mapping 和 binding 供重试；已删除的 AgentScope Session
不回滚。路径删除使用 `lstat`、规范化 containment 校验并拒绝根目录、路径逃逸和符号链接。

## 用量契约

`CacheInputMode` 固定为：

- `additive`：Anthropic-compatible，输入为 raw + cache creation + cache read。
- `inclusive`：OpenAI-compatible 和 Gemini，raw/prompt 已包含 cached 子集。
- `unknown`：保留 raw input，不展示缓存命中率。

完整的 Yuxi `model_usage` 优先于同一 `reply_id` 的原生 `MODEL_CALL_END`。只有真实观察到
缓存字段时才累计缓存分母，不回填历史缺失用量。Team worker 的 reply generator 不包含
MessageBus 自定义用量事件，因此 child Run 从实际 `Agent.model` 适配器继承缓存输入口径，再归一
原生 `MODEL_CALL_END` 的缓存字段；未知适配器仍保持 `unknown`，不得猜测或虚构命中率。

## Team worker 回报契约

AgentScope 原生 `TeamSay(to=null)` 表示向全队广播。在长期 Team 中，两个 worker 广播完成
回报会互相触发新一轮 reply，Yuxi 又会把每轮 reply 投影成 child Run，最终形成持续拉起循环。

Yuxi 在 worker 运行时用同名工具覆盖原生 `TeamSay`：从 AgentScope 公开 Storage 记录解析
真实 leader，将 `to=null` 归一为 leader，拒绝 peer 目标，并按原生 inbox 锁协议定向投递。
工具结果以 `team_target` 记录实际目标；child Run 只接受 leader 派发消息，peer HintBlock 不进入
模型上下文，也不创建新 Run。leader 的原生工具与 Team middleware 行为保持不变。

Team 保留到 parent Thread 删除后，后续轮次重复调用 `TeamCreate` 时由 Yuxi 幂等返回当前
`team_id`，并提示直接调用 `AgentCreate`；仅 Session 尚无 Team 时才透传 AgentScope 原生创建逻辑。

## 验收 Checklist

- [x] `uv lock --check` 与 AgentScope 版本导入检查通过。
- [x] 私有 `AgentCreate` 构造、Schema、白名单与 default 移除契约测试通过。
- [x] Schema、Workspace ID 创建/回填、路径安全和删除重试测试通过。
- [x] Team 原生 middleware 真实顺序测试通过，每个 child Run 只有一个终态。
- [x] Anthropic/OpenAI/Gemini 单来源、双来源和中断用量测试通过。
- [x] Team worker 原生 Anthropic 缓存字段 additive 归一和重复 `TeamCreate` 幂等单测通过。
- [x] 最大迭代同时保留文本和 `exceed_max_iters` 失败事实。
- [x] 独立数据卷完成 2.0.6 -> 2.0.7 升级和 2.0.7 -> 2.0.6 回滚演练。
- [x] 真实浏览器验证普通聊天、Team 单一 child 终态和 MiniMax 缓存展示。
- [x] 真实服务验证最大迭代、审批、取消恢复、MCP、协议续传和缓存用量。
- [x] 删除浏览器 Team Thread 后，PostgreSQL、Workspace、Docker 容器和页面状态一致。
- [ ] 最大迭代的浏览器提示、外部执行页面和 Redis 断连后的 Team 自动恢复仍需补验。

## 真实环境验证记录

- 运行版本为 `agentscope 2.0.7.post1`，`api-dev`、`worker-dev`、`agentscope-dev` 和
  `web-dev` 挂载当前 Yuxi checkout。
- MiniMax-M3 真实流式调用通过；两轮浏览器对话观察到第二轮缓存命中率 `99.59%`、
  Thread 累计 `50.27%`，页面显示 `50.3%`，后续累计显示 `66.8%`。
- Team 真实浏览器链路中 parent/child Run 均为 completed，用量均为 complete，child Redis
  Stream 只有一个 `end`；截图位于 `output/playwright/agentscope-207-team-real.png`。
- 双 worker 真实 API E2E 中两个 worker 均调用原始参数 `TeamSay(to=null)`；parent 和两个 child
  Run 均 completed，数据库最终只有 2 个 child Run 和 2 次 `TeamSay`，线程删除成功。单 worker
  与双 worker 用例在同一 pytest 进程连续通过。
- 在原问题 Thread `93c32e06-0344-490c-bcc5-369adb5ffddc` 继续真实浏览器对话，强制再次调用
  `TeamCreate` 后，`TeamCreate`、`AgentCreate`、`TeamDelete` 均一次成功，parent/child Run 均
  completed。新 child Run 按 `additive` 记录输入 `28,761`、缓存读取 `19,101`、命中率
  `66.41%`，Redis Stream 仅 1 个 `end`；页面线程累计缓存命中率显示 `90.3%`。
- 原问题 Thread `42588b21-6f02-4caf-b373-65e240788f12` 保留故障期间 2 个 binding、
  41 个历史 child Run 和 40 次历史 `TeamSay`；修复后连续 10 秒观测均未增长。历史消息不自动
  删除，后续 worker 回报使用新的定向协议。
- 最大迭代通过真实 Compose 服务链路验证：`max_iters=1` 调用 mock HTTP 模型并执行
  `list_kbs`，结果保留非空文本，Run 和 Redis `end` 均为 failed，错误类型为
  `exceed_max_iters`。
- 删除 Team Thread 后，parent mapping 已删除、worker binding 已停用，worker workspace
  目录和 `agentscope.workspace.id` 标签容器均不存在，页面回到新建对话。

## 已知限制

AgentScope 原生 `WakeupDispatcher` 在 Redis 短暂断连时会退出订阅循环，但 `/health` 仍返回
正常。健康状态下 Team 可完成；断连后需要重启 AgentScope 服务才能继续消费已排队 wakeup。
该问题不修改 AgentScope 源码无法在本批次根治，因此不得把 Team 的 Redis 断连恢复报告为
通过。生产接入前需要上游增加重连，或另行设计 Yuxi 侧可观测的进程级恢复机制。

验证容器必须挂载 `/Users/zhanglingbin/Documents/work/github/yuxi`。未配置 provider 记录为
未验证，不得报告为通过。本任务不包含 push、PR、部署或任何 GitHub 远端写入。
