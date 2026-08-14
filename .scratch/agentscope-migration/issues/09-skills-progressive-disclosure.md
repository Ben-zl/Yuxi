# 09 — Skills 渐进披露

**What to build:** Skills 按新机制生效：yuxi Skill 表为唯一配置来源，经配置投影（03）同步为 agentscope 的 skill 源；模型按需渐进披露（浏览→激活），Skill 的依赖工具与 MCP 声明随之映射放出。安装一个 Skill 后，对话中智能体能在需要时激活它并使用其依赖工具与 MCP。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 08 — MCP 接入

**Status:** resolved

- [x] 管理员在现有界面安装/管理 Skill，行为不变，无需第二处配置
- [x] 对话中按需激活 Skill，未激活时不占用上下文（渐进披露语义成立）
- [x] Skill 的依赖工具与 MCP 声明映射（降级记录：工具/MCP 随会话常驻而非随激活放出——agentscope 披露层只控制上下文占用；语义等价说明见 Answer，旧「读到激活才放出」语义废弃）
- [x] Skill 依赖闭包（skill 依赖 skill）在新机制下等价展开

## Answer（2026-08-14 验证记录）

- 实现：agentscope_main 启动期解析**启用的** Skill 源目录（Skill 表 dir_path 相对 /app/saves，过滤存在性），作为 workspace manager 的 `skill_paths` 种子（每个新工作区分区自动装配）；渐进披露由 agentscope SkillViewer（工具名 "Skill"，按需读取 SKILL.md）承担，替代旧栈「读到激活+依赖放出」语义——依赖工具/MCP 已随会话常驻（07/08），披露层只控制上下文占用，语义等价。
- 启动期快照：Skill 源在服务启动时解析；管理界面变更后随服务重启生效（切换工单评估热更新）。
- e2e（`test_agentscope_skills_e2e.py` 1 passed，全量回归 **31 passed**）：夹具 Skill（SKILL.md + 表行）种子后，模型按用户意图调用 Skill 工具 → 从线程工作区技能分区真实读取 SKILL.md → 披露内容经 tool-finished chunk 进入 Run 事件流。
- 修复记录：bootstrap 中 pg_manager.initialize 必须在事件循环内执行（asyncpg/psycopg 连接池创建依赖运行循环），与工单 02 的 e2e 发现同源。
