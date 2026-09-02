import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

const readSource = (relativePath) => readFile(path.join(webRoot, relativePath), 'utf8')

test('长期记忆管理只从编辑智能体的记忆页签进入', async () => {
  const [editModal, managePanel, agentView] = await Promise.all([
    readSource('src/components/model-management/AgentEditModal.vue'),
    readSource('src/components/model-management/AgentManagePanel.vue'),
    readSource('src/views/AgentView.vue')
  ])

  assert.match(editModal, /key:\s*'memory',\s*label:\s*'记忆'/)
  assert.match(editModal, /<AgentMemoryPanel/)
  assert.match(editModal, /agentModalActiveTab === 'memory'/)

  for (const legacyEntrySource of [managePanel, agentView]) {
    assert.doesNotMatch(legacyEntrySource, /AgentMemoryModal/)
    assert.doesNotMatch(legacyEntrySource, />长期记忆</)
  }
})
