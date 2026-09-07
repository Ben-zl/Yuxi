import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

const source = fs.readFileSync(
  new URL('../../src/components/AgentChatComponent.vue', import.meta.url),
  'utf8'
)

test('不使用项目时不会把 __auto__ 哨兵发送为真实 project_id', () => {
  assert.match(
    source,
    /projectId: props\.projectId === AUTO_PROJECT_ID \? null : props\.projectId/
  )
})
