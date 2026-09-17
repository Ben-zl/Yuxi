import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { resolveProviderDisplayName } from '../../src/utils/modelProviderDisplay.js'

const embeddingSelector = readFileSync(
  new URL('../../src/components/EmbeddingModelSelector.vue', import.meta.url),
  'utf8'
)
const rerankSelector = readFileSync(
  new URL('../../src/components/RerankModelSelector.vue', import.meta.url),
  'utf8'
)
const chatSelector = readFileSync(
  new URL('../../src/components/ModelSelectorComponent.vue', import.meta.url),
  'utf8'
)

test('模型供应商显示名称优先使用接口返回的 display name', () => {
  assert.equal(
    resolveProviderDisplayName('provider-resource-id', {
      provider_display_name: '金山云星流',
      provider_id: 'ksyun-xingliu'
    }),
    '金山云星流'
  )
  assert.equal(
    resolveProviderDisplayName('provider-resource-id', { display_name: '兼容名称' }),
    '兼容名称'
  )
  assert.equal(resolveProviderDisplayName('provider-resource-id', { name: '旧版名称' }), '旧版名称')
  assert.equal(resolveProviderDisplayName('provider-resource-id', {}), 'provider-resource-id')
})

test('全部模型选择器统一解析供应商显示名称', () => {
  for (const source of [embeddingSelector, rerankSelector, chatSelector]) {
    assert.match(source, /resolveProviderDisplayName\(providerId, providerData\)/)
  }
  for (const source of [embeddingSelector, rerankSelector]) {
    assert.doesNotMatch(source, /<span>\{\{\s*providerId\s*\}\}<\/span>/)
  }
})
