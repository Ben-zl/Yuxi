import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../../src/components/AgentChatComponent.vue', import.meta.url), 'utf8')
const panelSource = readFileSync(new URL('../../src/components/AgentPanel.vue', import.meta.url), 'utf8')

test('恢复 0.7.2 侧边栏关键交互接线', () => {
  assert.match(source, /ConversationProcessGroupComponent/)
  assert.match(source, /collapseIntermediate: conv\?\.status !== 'streaming'/)
  assert.match(source, /replyElapsedLabel/)
  assert.match(source, /ContextUsageRing/)
  assert.match(source, /@click="toggleStatePanel"/)
  assert.match(source, /threadDraftSession\.switchThread/)
  assert.match(source, /@click="openPanelPreview\(file\)"/)
  assert.match(source, /statePanelMaxHeightStyle/)
  assert.match(source, /isAgentPanelMaximized/)
})

test('ContextUsageRing 位于输入区右侧并且 Debug 面板受权限控制', () => {
  const ringIndex = source.indexOf('<ContextUsageRing')
  const rightSlotIndex = source.indexOf('template #actions-right-extra')
  assert.ok(ringIndex > rightSlotIndex)
  assert.equal(source.includes('v-if="messageDebugEnabled"'), true)
  assert.match(source, /messageDebugEnabled = computed\(\(\) => infoStore\.debugMode && userStore\.isSuperAdmin\)/)
  assert.ok(source.indexOf('class="input-model-selector"', ringIndex) > ringIndex)
  assert.match(panelSource, /<MessageDebugPanel :messages="messages" \/>/)
})
