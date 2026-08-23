import assert from 'node:assert/strict'
import test from 'node:test'

import { MessageProcessor } from '../../src/utils/messageProcessor.js'

test('执行后取消的完整问答仍可组成历史对话', () => {
  const conversations = MessageProcessor.convertServerHistoryToMessages([
    { type: 'human', content: '查询性能', delivery_status: 'cancelled' },
    { type: 'ai', content: '已生成部分结果', delivery_status: 'complete' }
  ])

  assert.equal(conversations.length, 1)
  assert.equal(conversations[0].status, 'finished')
  assert.deepEqual(
    conversations[0].messages.map((message) => message.content),
    ['查询性能', '已生成部分结果']
  )
})

test('交付物只归属于调用 present_artifacts 的对话', () => {
  const artifactConversation = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'present_artifacts',
            tool_call_result: { content: '已将交付物展示给用户' },
            args: JSON.stringify({
              filepaths: [
                '/workspace/outputs/bubble_sort.py',
                '/home/gem/user-data/outputs/bubble_sort.js'
              ]
            })
          },
          {
            function: { name: 'present_artifacts' },
            status: 'success',
            args: { filepaths: ['/home/gem/user-data/outputs/bubble_sort.py'] }
          }
        ]
      }
    ]
  }
  const laterConversation = {
    messages: [{ type: 'human', content: '运行 Python 的' }]
  }

  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(artifactConversation), [
    '/home/gem/user-data/outputs/bubble_sort.py',
    '/home/gem/user-data/outputs/bubble_sort.js'
  ])
  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(laterConversation), [])
})
