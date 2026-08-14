# 09 — Skills 渐进披露

**What to build:** Skills 按新机制生效：yuxi Skill 表为唯一配置来源，经配置投影（03）同步为 agentscope 的 skill 源；模型按需渐进披露（浏览→激活），Skill 的依赖工具与 MCP 声明随之映射放出。安装一个 Skill 后，对话中智能体能在需要时激活它并使用其依赖工具与 MCP。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 08 — MCP 接入

**Status:** ready-for-agent

- [ ] 管理员在现有界面安装/管理 Skill，行为不变，无需第二处配置
- [ ] 对话中按需激活 Skill，未激活时不占用上下文（渐进披露语义成立）
- [ ] Skill 的依赖工具与 MCP 声明随激活放出、随会话边界收回（依赖 08 的 MCP 通道验证）
- [ ] Skill 依赖闭包（skill 依赖 skill）在新机制下等价展开
