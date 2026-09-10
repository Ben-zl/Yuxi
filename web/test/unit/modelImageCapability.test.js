import assert from 'node:assert/strict'
import test from 'node:test'

import { loadModelImageCapability, resolveModelImageCapability } from '../../src/utils/modelMetadata.js'

const catalog = {
  provider: {
    models: {
      'glm-5': { modalities: { input: ['text'], output: ['text'] } },
      'glm-5v': { modalities: { input: ['text', 'image'], output: ['text'] } }
    }
  }
}

const models = {
  provider: {
    models: [
      { spec: 'provider:glm-5', model_id: 'glm-5', input_modalities: [] },
      { spec: 'provider:glm-5v', model_id: 'glm-5v', input_modalities: [] },
      { spec: 'provider:custom', model_id: 'custom', input_modalities: [] },
      { spec: 'provider:declared', model_id: 'declared', input_modalities: ['text'] }
    ]
  }
}

const customProviderModels = {
  custom: {
    models: [{ spec: 'custom:glm-5', model_id: 'glm-5', input_modalities: [] }]
  }
}

test('模型目录识别文本和视觉模型', () => {
  assert.deepEqual(resolveModelImageCapability(models, catalog, 'provider:glm-5'), {
    known: true,
    supported: false
  })
  assert.deepEqual(resolveModelImageCapability(models, catalog, 'provider:glm-5v'), {
    known: true,
    supported: true
  })
})

test('服务端声明优先形成确定能力，未知自定义模型保持未知', () => {
  assert.deepEqual(resolveModelImageCapability(models, catalog, 'provider:declared'), {
    known: true,
    supported: false
  })
  assert.deepEqual(resolveModelImageCapability(models, catalog, 'provider:custom'), {
    known: false,
    supported: false
  })
})

test('自建供应商可按一致的模型目录能力识别 glm-5 为文本模型', () => {
  assert.deepEqual(resolveModelImageCapability(customProviderModels, catalog, 'custom:glm-5'), {
    known: true,
    supported: false
  })
})


test('模型目录加载失败时仍使用服务端明确的文本能力', async () => {
  const capability = await loadModelImageCapability(
    models,
    'provider:declared',
    async () => {
      throw new Error('catalog unavailable')
    }
  )

  assert.deepEqual(capability, { known: true, supported: false })
})
