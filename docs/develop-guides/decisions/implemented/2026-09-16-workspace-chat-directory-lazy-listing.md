# 工作区历史对话目录按需加载

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/workspace_service.py

## 问题

工作区打开 `/agents/chats/` 时，为隐藏没有可见文件的历史会话，会对当前用户每个
Conversation 串行查询 AgentScope 映射并列出 uploads、outputs。真实管理员有 93 个历史会话时，
单次 HTTP 请求耗时 128 至 190 秒，页面在此期间一直显示加载状态。

## 决策

- 非递归打开历史对话根目录时，只读取 PostgreSQL 中当前 UID、当前可见 Conversation 的
  AgentScope 映射，并与历史本地文件目录合并，不访问远端 Workspace。
- 根目录允许显示已经建立 AgentScope 映射但暂时没有文件的会话。用户进入具体会话目录后，
  再读取该会话的 uploads、outputs；空会话返回空目录。
- 递归历史文件查询仍读取远端 Workspace，并一次批量读取线程映射，按最多 16 个线程一组并发，
  避免恢复逐线程映射查询和无界远端并发。
- 单个会话文件读取、下载、只读权限、UID 和 thread_id 授权边界保持不变。

## 替代方案

- 保留逐线程串行请求并只增加前端超时：不能缩短后端执行时间，只会把卡住改成报错。
- 根目录继续远端筛选空会话并提高并发：93 个会话实测仍需约 5.8 秒，并继续随历史数量增长。
- 无界并发列出全部 Workspace：可能同时发出数百个 AgentScope 请求，扩大服务压力和失败面。

## 后果

- 已建立 AgentScope 映射但没有 uploads、outputs 的会话也会出现在历史对话根目录；进入后显示空目录。
- 根目录返回数量可能增加，但不再等待所有远端 Workspace；递归查询保持既有的整体失败语义。
- 递归文件查询仍可能较慢，但使用固定并发上限，且不影响普通目录浏览。

## 验证

| 主张 | 证据 | 结果 |
|---|---|---|
| 根目录不访问 AgentScope | unit 注入会失败的远端列表函数，非递归根目录仍返回映射会话 | Passed |
| 预加载映射不会再次查询 repository | `list_visible_files` 使用显式 mapping 的 unit | Passed |
| 历史目录既有文件、安全与排序语义不回归 | Workspace、thread workspace 与 mapping repository unit | 49 passed |
| 真实历史目录不再卡住 | 93 个历史会话的正式 HTTP `/api/workspace/tree` 请求 | 0.051 秒，200 |
| 具体会话仍按需加载 | 正式 HTTP 展开一个有文件的历史会话 | 0.306 秒，200 |
