import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

const chatSource = fs.readFileSync(
  new URL('../../src/components/AgentChatComponent.vue', import.meta.url),
  'utf8'
)
const panelSource = fs.readFileSync(
  new URL('../../src/components/AgentPanel.vue', import.meta.url),
  'utf8'
)

test('AgentChat 将文件面板 Section 与文件系统生命周期传递给 AgentPanel', () => {
  assert.match(chatSource, /:sections="agentPanelSections"/)
  assert.match(chatSource, /:active-section-key="agentPanelActiveSectionKey"/)
  assert.match(chatSource, /:filesystem-visible="agentPanelFilesystemVisible"/)
  assert.match(chatSource, /:filesystem-polling-active="agentPanelFilesystemPollingActive"/)
  assert.match(chatSource, /@activate-section="activateAgentPanelSection"/)
  assert.match(chatSource, /@close-section="closeAgentPanelSection"/)
})

test('打开和关闭文件预览会同步更新 Section 状态', () => {
  assert.match(chatSource, /agentPanelSections\.value = upsertAgentPanelSection\(/)
  assert.match(chatSource, /key: `file:\$\{tab\.previewKey\}`/)
  assert.match(chatSource, /closePanelSectionState\(/)
  assert.match(chatSource, /agentPanelActiveSectionKey\.value = nextSectionState\.activeKey/)
  assert.match(chatSource, /closePanelPreviewTab\(section\.path, section\.previewKey\)/)
  assert.match(chatSource, /:active-preview-key="agentPanelActivePreviewKey"/)
})

test('同路径文件使用 previewKey 隔离，并按旧线程清理缓存', () => {
  assert.match(chatSource, /workspace:\$\{String\(userStore\.uid \|\| 'current'\)\}:\$\{path\}/)
  assert.match(panelSource, /workspaceOwnerId/)
  assert.match(panelSource, /const staleThreadId = previousThreadId \|\| threadId/)
  assert.match(panelSource, /key\.startsWith\(`thread:\$\{staleThreadId\}:`\)/)
  assert.match(panelSource, /key\.startsWith\(`thread:\$\{props\.threadId\}:`\)/)
  assert.match(chatSource, /releasePreviewCacheEntry\(tab\.previewKey\)/)
})

test('AgentPanel 渲染可切换的文件 Section 标签', () => {
  assert.match(panelSource, /class="section-tabs"/)
  assert.match(panelSource, /@click="emit\('activate-section', section\.key\)"/)
  assert.match(panelSource, /@click\.stop="emit\('close-section', section\.key\)"/)
})
