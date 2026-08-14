"""agentscope 承接层：网关侧与 agentscope service 的桥接。

- client：service 的异步 HTTP 客户端
- projection：yuxi 配置 → agentscope 运行时对象的投影
- runner：线程会话保障与一轮对话的触发/事件收集
"""
