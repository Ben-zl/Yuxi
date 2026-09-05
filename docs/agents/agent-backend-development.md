# 开发智能体后端

本页面向需要在 Yuxi 中新增或维护 Agent 管理元数据的贡献者。它只讲配置能力和运行时投影；实际执行由 AgentScope service 承担，配置字段、权限和运行时上下文分别见[配置智能体](./agents-config.md)和[Agent 运行时上下文](../mechanisms/agent-runtime.md)。

## 后端放在哪里

随服务发布的 Agent 管理后端放在：

```text
backend/package/yuxi/agents/buildin/<your_agent>/
├── __init__.py
├── context.py
└── graph.py
```

`buildin` 包会遍历包含 `__init__.py` 的子目录，发现并注册其中的 `BaseAgent` 子类。`__init__.py` 需要导出该类。`BaseAgent` 只拥有管理端名称、描述、能力和 Context schema，不拥有第二套执行器。

## 最小实现

```python
from yuxi.agents import BaseAgent, BaseContext


class MyAgent(BaseAgent):
    name = "我的智能体"
    description = "用于示例的智能体后端"
    context_schema = BaseContext

```

这个示例只声明管理面的 Context。真实运行时由 `config_projection.py` 读取 Agent、模型供应商、Skills、MCP、知识库和子智能体配置，生成 `RuntimeProjection`，再由 `server.agentscope_main:app` 的 `extra_agent_middlewares` / `extra_agent_tools` 装配 AgentScope Session。

不要在管理后端或路由中直接创建模型、Session、Workspace 或执行状态；不要从浏览器输入、宿主机路径或数据库原始字段直接拼出可执行配置。

## Context 和配置表单

需要让管理员或用户配置 Agent 行为时，在 `context.py` 扩展 `BaseContext`：

```python
from dataclasses import dataclass, field
from yuxi.agents import BaseContext


@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    response_style: str = field(
        default="concise",
        metadata={
            "name": "回答风格",
            "description": "控制回答的详细程度",
            "type": "string",
            "options": ["concise", "detailed"],
        },
    )
```

metadata 会影响 Agent 详情接口和 `AgentRuntimeConfigForm`。不要只在前端添加一个字段，也不要把运行期 ID、worker 身份和权限快照暴露成可保存配置。

新增字段后，沿下面的链路检查：

```text
context_schema
  → get_configurable_items()
  → Agent 详情接口
  → 前端配置表单
  → config_json.context
  → RuntimeProjection
  → AgentScope Session
```

## 中间件和工具

资源权限和默认资源选择在 AgentScope Session 创建前处理；模型提示注入、工具动态开放、文件结果处理、state 更新和观测才适合放入 middleware。内置 Agent 的工具可见性和执行注册分为两层：服务端逐轮装配工具，再由 Skill 激活状态决定是否让模型看到。

优先复用：

- [工具系统](./tools-system.md) 的注册和目标校验；
- [中间件](./middleware.md) 的装配顺序；
- [Skills 管理](./skills-management.md) 的依赖和激活规则；
- [沙盒机制](../mechanisms/sandbox.md) 的文件和命令边界。

新 middleware 不要绕过 `RuntimeProjection`，也不要用 Prompt、前端隐藏或 schema omission 代替后端授权。

## 检查清单

- `BaseAgent` 子类位于可被 `agent_manager` 发现的包中，并被 `__init__.py` 导出；
- `context_schema` 的默认值、字段权限和选项能被前端正确渲染；
- AgentScope `RuntimeProjection` 使用 Yuxi 的模型、工具、文件和 Session 装配入口；
- 工具副作用在执行处验证用户、路径和资源；
- LITE 模式不会因新增导入而初始化知识库、图谱或评估重运行时；
- 新的模型可见输入、状态、文件或协议有正向和负向测试；
- 相关 API、机制和用户文档已更新。

## 源码和测试

- [BaseAgent](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/base.py)
- [Context](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/context.py)
- [AgentScope service](https://github.com/xerrors/Yuxi/blob/main/backend/server/agentscope_main.py)
- [Agent 自动发现](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/__init__.py)
- [Agent unit tests](https://github.com/xerrors/Yuxi/tree/main/backend/test/unit/agents)
- [Agent integration/E2E](https://github.com/xerrors/Yuxi/tree/main/backend/test/e2e)

改变持久配置、权限、模型可见输入、Run 生命周期或文件边界时，先按 [Yuxi Spec Loop](../develop-guides/spec-loop.md) 建立相应的决策和验证范围。
