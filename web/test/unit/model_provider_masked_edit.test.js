import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'

import { createPinia } from 'pinia'
import { createServer } from 'vite'
import { parse } from 'vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

const componentUrl = new URL(
  '../../src/components/model-management/ModelProviderManagePanel.vue',
  import.meta.url
)
const instrumentedUrl = new URL(
  `../../src/components/model-management/.ModelProviderManagePanel.test-${pid}.vue`,
  import.meta.url
)

const exposeBlock = `defineExpose({
  loading,
  stats: providerStats,
  refresh: loadProviders
})`

const instrumentedExposeBlock = `globalThis.__modelProviderFormTestApi = {
  userStore,
  shareConfigFormRef,
  allowedShareAccessLevels,
  isGlobalScopeReadOnly,
  providerForm,
  openCreateProviderModal,
  openEditProviderModal,
  markMaskedProviderFieldChanged,
  buildProviderPayload,
  providers,
  deleteProviderFromEdit
}
defineExpose({})`

async function loadProviderFormLogic() {
  const source = readFileSync(componentUrl, 'utf8')
  const { descriptor } = parse(source)
  const setupSource = descriptor.scriptSetup?.content || ''
  assert.equal(setupSource.includes(exposeBlock), true)
  writeFileSync(
    instrumentedUrl,
    `<script setup>\n${setupSource.replace(exposeBlock, instrumentedExposeBlock)}\n</script>\n<template><div /></template>\n`
  )

  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const component = await server.ssrLoadModule(
      `/src/components/model-management/${instrumentedUrl.pathname.split('/').at(-1)}`
    )
    globalThis.__modelProviderFormTestApi = undefined
    const app = createSSRApp(component.default)
    app.use(createPinia())
    await renderToString(app)
    return globalThis.__modelProviderFormTestApi
  } finally {
    await server.close()
    unlinkSync(instrumentedUrl)
  }
}

test('普通或部门管理员编辑脱敏 Provider 时不覆盖未修改的凭据和高级配置', async () => {
  const form = await loadProviderFormLogic()
  form.openEditProviderModal({
    resource_id: 'provider-resource-1',
    provider_id: 'department-provider',
    display_name: 'Department Provider',
    base_url: 'https://example.com/v1',
    capabilities: ['chat'],
    is_enabled: true,
    share_config: {
      version: 2,
      read_scope: { access_level: 'department', department_ids: ['dep-1'], user_uids: [] },
      manage_scope: null
    }
  })

  assert.equal(form.providerForm.api_key_env, undefined)
  assert.equal(form.providerForm.api_key, undefined)
  assert.equal(form.providerForm.headers_text, undefined)
  assert.equal(form.providerForm.extra_text, undefined)

  const payload = form.buildProviderPayload()
  for (const field of [
    'base_url',
    'embedding_base_url',
    'rerank_base_url',
    'models_endpoint',
    'embedding_models_endpoint',
    'rerank_models_endpoint',
    'api_key_env',
    'api_key',
    'headers_json',
    'extra_json'
  ]) {
    assert.equal(Object.hasOwn(payload, field), false)
  }
})

test('隐藏 Provider URL 仅在显式编辑后提交', async () => {
  const form = await loadProviderFormLogic()
  form.openEditProviderModal({
    resource_id: 'provider-resource-url',
    provider_id: 'sensitive-provider',
    display_name: 'Sensitive Provider',
    base_url_configured: true,
    embedding_base_url_configured: true,
    capabilities: ['chat', 'embedding'],
    share_config: {
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    }
  })
  assert.equal(Object.hasOwn(form.buildProviderPayload(), 'base_url'), false)
  form.providerForm.base_url = 'https://replacement.test/v1'
  form.markMaskedProviderFieldChanged('base_url')
  assert.equal(form.buildProviderPayload().base_url, 'https://replacement.test/v1')
})

test('编辑弹窗删除按 resource_id 定位供应商', () => {
  const source = readFileSync(componentUrl, 'utf8')
  assert.match(source, /providerResourceId\(candidate\) === editingProviderId\.value/)
  assert.doesNotMatch(source, /p\.provider_id === editingProviderId\.value/)
})

test('编辑时仅提交显式修改的脱敏字段，显式清空仍保留清空语义', async () => {
  const form = await loadProviderFormLogic()
  form.openEditProviderModal({
    resource_id: 'provider-resource-2',
    provider_id: 'shared-provider',
    display_name: 'Shared Provider',
    base_url: 'https://example.com/v1',
    capabilities: ['chat'],
    is_enabled: true
  })

  form.providerForm.api_key = 'replacement-key'
  form.markMaskedProviderFieldChanged('api_key')
  form.providerForm.api_key_env = ''
  form.markMaskedProviderFieldChanged('api_key_env')
  form.providerForm.headers_text = '{"X-Team":"platform"}'
  form.markMaskedProviderFieldChanged('headers_json')

  const payload = form.buildProviderPayload()
  assert.equal(payload.api_key, 'replacement-key')
  assert.equal(payload.api_key_env, null)
  assert.deepEqual(payload.headers_json, { 'X-Team': 'platform' })
  assert.equal(Object.hasOwn(payload, 'extra_json'), false)
})

test('新增 Provider 继续提交完整凭据和高级配置字段', async () => {
  const form = await loadProviderFormLogic()
  form.openCreateProviderModal()

  const payload = form.buildProviderPayload()
  assert.equal(payload.api_key_env, null)
  assert.equal(payload.api_key, null)
  assert.deepEqual(payload.headers_json, {})
  assert.deepEqual(payload.extra_json, {})
})

test('部门管理员新建默认本部门，超级管理员默认全局，并在每次打开时重置', async () => {
  const form = await loadProviderFormLogic()
  form.userStore.userRole = 'admin'
  form.userStore.departmentId = 12
  form.openCreateProviderModal()
  assert.deepEqual(form.buildProviderPayload().share_config, {
    version: 2,
    read_scope: { access_level: 'department', department_ids: [12], user_uids: [] },
    manage_scope: null
  })
  assert.deepEqual(form.allowedShareAccessLevels.value, ['department'])
  form.userStore.userRole = 'superadmin'
  form.openCreateProviderModal()
  assert.deepEqual(form.buildProviderPayload().share_config.read_scope, {
    access_level: 'global',
    department_ids: [],
    user_uids: []
  })
  assert.deepEqual(form.allowedShareAccessLevels.value, ['global', 'department'])
})

test('范围编辑保留原始资源且不提交脱敏凭据，取消后重新打开恢复原范围', async () => {
  const form = await loadProviderFormLogic()
  form.userStore.userRole = 'superadmin'
  const provider = {
    resource_id: 'scoped-resource',
    provider_id: 'scoped',
    share_config: {
      version: 2,
      read_scope: { access_level: 'department', department_ids: [21], user_uids: [] },
      manage_scope: { access_level: 'department', department_ids: [21], user_uids: [] }
    }
  }
  form.openEditProviderModal(provider)
  assert.deepEqual(form.buildProviderPayload().share_config, provider.share_config)
  form.providerForm.share_config.read_scope.department_ids.push(22)
  assert.deepEqual(provider.share_config.read_scope.department_ids, [21])
  assert.deepEqual(form.buildProviderPayload().share_config.read_scope.department_ids, [21, 22])
  assert.equal(Object.hasOwn(form.buildProviderPayload(), 'api_key'), false)
  form.openEditProviderModal(provider)
  assert.deepEqual(form.buildProviderPayload().share_config, provider.share_config)
})

test('部门管理员查看 global 范围不被改写，表单校验失败禁止构建提交', async () => {
  const form = await loadProviderFormLogic()
  form.userStore.userRole = 'admin'
  form.openEditProviderModal({
    resource_id: 'global-resource',
    share_config: {
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    }
  })
  assert.equal(form.isGlobalScopeReadOnly.value, true)
  assert.equal(form.buildProviderPayload().share_config.read_scope.access_level, 'global')
  form.shareConfigFormRef.value = {
    validate: () => ({ valid: false, message: '读取权限至少需要选择一个部门' })
  }
  assert.throws(() => form.buildProviderPayload(), /至少需要选择一个部门/)
})
