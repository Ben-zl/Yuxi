import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

import { normalizeGeneratedTitle } from '../../src/utils/conversationTitle.js'

const source = fs.readFileSync(
  new URL('../../src/components/AgentChatComponent.vue', import.meta.url),
  'utf8'
)

test('标题生成优先使用当前对话模型，避免旁路调用错误的 fast_model', () => {
  assert.match(
    source,
    /agentApi\.generateTitle\(\s*autoTitle,\s*modelSpec \|\| configStore\.config\?\.fast_model/
  )
})

test('标题生成不使用完整 think 块，并保留最终标题', () => {
  assert.equal(normalizeGeneratedTitle('<think>内部判断</think>性能监控日报'), '性能监控日报')
})

test('模型只返回未闭合 think 时回退到用户请求', () => {
  assert.equal(normalizeGeneratedTitle('<think>内部判断：用户想要分析性能', '性能分析日报'), '性能分析日报')
})

test('标题清理不会把标题前缀和 Markdown 标记带入会话列表', () => {
  assert.equal(normalizeGeneratedTitle('```text\n标题：性能分析\n```'), '性能分析')
})

test('MiniMax 异常结束标记后的标题才展示给用户', () => {
  assert.equal(
    normalizeGeneratedTitle('<think>内部判断</think>任务已委派。 </mm:think>性能监控日报'),
    '性能监控日报'
  )
})
