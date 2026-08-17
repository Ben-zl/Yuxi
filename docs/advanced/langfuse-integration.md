# Langfuse 集成

Yuxi 使用 AgentScope 执行智能体，并通过 OpenTelemetry 将 agent、model 和 tool span 上报到支持 OTLP 的 Langfuse。Langfuse 是可选观测系统，不参与队列、会话、消息或 Run 的事实状态；未配置或暂时不可达时，聊天主链路仍应独立工作。

## 观测链路

`server/agentscope_main.py` 为每轮会话注入 AgentScope `TracingMiddleware`。当 `OTEL_EXPORTER_OTLP_ENDPOINT` 存在时，服务初始化 OpenTelemetry `TracerProvider` 和 HTTP OTLP exporter：

```text
AgentScope ReAct/Team
  -> TracingMiddleware
  -> OpenTelemetry spans
  -> OTLP HTTP exporter
  -> Langfuse
```

默认 service name 为 `yuxi-agentscope`，可以通过 `OTEL_SERVICE_NAME` 覆盖。AgentScope span 会携带其会话和工具语义，适合排查模型耗时、工具调用、错误和 Team 编排。

## 配置

按照 Langfuse 当前的 OpenTelemetry 接入说明准备 OTLP endpoint 和认证 Header，并将以下变量传入 `agentscope-dev`/生产 AgentScope 服务：

```env
OTEL_EXPORTER_OTLP_ENDPOINT=<Langfuse OTLP traces endpoint>
OTEL_EXPORTER_OTLP_HEADERS=<Langfuse OTLP authorization headers>
OTEL_SERVICE_NAME=yuxi-agentscope
```

代码使用 `OTLPSpanExporter()` 读取标准 OpenTelemetry 环境变量。凭据不应写入源码、测试或 Agent workspace。

Yuxi 仍可使用 Langfuse SDK 提交用户反馈 score。需要该能力时同时配置：

```env
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=<public key>
LANGFUSE_SECRET_KEY=<secret key>
LANGFUSE_BASE_URL=<Langfuse base URL>
```

点赞写入 `user-feedback=1`，点踩写入 `user-feedback=0`，原因作为 comment。只有助手消息已经关联有效 `langfuse_trace_id` 时才会远程提交；无 trace id 或 Langfuse 不可用时，本地反馈仍会保存。

## 验证

不要只检查环境变量。发起一条会触发模型和工具的真实对话，然后在 Langfuse 中确认：

- 出现新的 AgentScope agent/generation/tool spans；
- span 归属正确的 session；
- 模型和工具耗时、错误状态可读；
- 未配置 exporter 时，聊天仍正常完成且 AgentRun 正确终态。

容器侧排障先看：

```bash
docker logs agentscope-dev --tail 200
docker exec agentscope-dev env | grep '^OTEL_'
```

输出环境变量时应避免在共享日志或问题报告中展示完整认证 Header。
