import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { parse } from 'vue/compiler-sfc'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('MCP 详情编辑保留隐藏地址，替换和取消正确更新状态', async () => {
  const sourceUrl = new URL('../../src/components/extensions/McpDetailView.vue', import.meta.url)
  const instrumentedUrl = new URL(
    `../../src/components/extensions/.McpDetailView.test-${pid}.vue`,
    import.meta.url
  )
  const { descriptor } = parse(readFileSync(sourceUrl, 'utf8'))
  const source = descriptor.scriptSetup.content
    .replace("import { mcpApi } from '@/apis/mcp_api'", 'const mcpApi = {}')
    .replace(
      "import ExtensionDetailLayout from '@/components/shared/ExtensionDetailLayout.vue'",
      'const ExtensionDetailLayout = {}'
    )
    .replace(
      "import { useRoute, useRouter } from 'vue-router'",
      'const useRoute = () => ({ params: { slug: "resource" } }); const useRouter = () => ({})'
    )
    .replace(
      "import { message, Modal } from 'ant-design-vue'",
      'const message = { error: () => {} }; const Modal = {}'
    )
  writeFileSync(
    instrumentedUrl,
    `<script setup>${source}
globalThis.__mcpDetailUrlTest = { server, editForm, urlEdited, headersEdited, shareConfigFormRef, startEdit, cancelEdit, buildEditPayload, validateEditPayload }
</script><template><div /></template>`
  )
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const component = await vite.ssrLoadModule(
      `/src/components/extensions/${instrumentedUrl.pathname.split('/').at(-1)}`
    )
    const app = createSSRApp(component.default)
    const pinia = createPinia()
    pinia.state.value.user = { userRole: 'superadmin', departmentId: null }
    app.use(pinia)
    await renderToString(app)
    const api = globalThis.__mcpDetailUrlTest
    api.server.value = {
      resource_id: 'resource',
      name: 'Test',
      transport: 'sse',
      url_configured: true
    }
    api.startEdit()
    api.editForm.name = 'Renamed'
    let payload = api.buildEditPayload()
    assert.deepEqual(payload.share_config.read_scope, {
      access_level: 'global',
      department_ids: [],
      user_uids: []
    })
    assert.equal(Object.hasOwn(payload, 'url'), false)
    assert.equal(Object.hasOwn(payload, 'headers'), false)
    assert.equal(api.validateEditPayload(payload), true)
    api.editForm.headersText = '{"Authorization":"replacement"}'
    api.headersEdited.value = true
    payload = api.buildEditPayload()
    assert.deepEqual(payload.headers, { Authorization: 'replacement' })
    api.editForm.url = 'https://replacement.test/mcp?token=fixture'
    api.urlEdited.value = true
    payload = api.buildEditPayload()
    assert.equal(payload.url, api.editForm.url)
    assert.equal(api.validateEditPayload(payload), true)
    api.editForm.url = ''
    assert.equal(api.validateEditPayload(api.buildEditPayload()), false)
    api.cancelEdit()
    api.startEdit()
    assert.equal(Object.hasOwn(api.buildEditPayload(), 'url'), false)
    assert.equal(Object.hasOwn(api.buildEditPayload(), 'headers'), false)
    assert.equal(api.validateEditPayload(api.buildEditPayload()), true)
    api.server.value = { name: 'Missing', transport: 'sse', url_configured: false }
    api.startEdit()
    assert.equal(api.validateEditPayload(api.buildEditPayload()), false)
  } finally {
    await vite.close()
    unlinkSync(instrumentedUrl)
    delete globalThis.__mcpDetailUrlTest
  }
})

test('MCP 脱敏地址编辑保留、显式替换和新建校验', async (t) => {
  const sourceUrl = new URL('../../src/components/extensions/McpFormModal.vue', import.meta.url)
  const instrumentedUrl = new URL(
    `../../src/components/extensions/.McpFormModal.test-${pid}.vue`,
    import.meta.url
  )
  const { descriptor } = parse(readFileSync(sourceUrl, 'utf8'))
  const source = descriptor.scriptSetup.content
    .replace(
      "import { message } from 'ant-design-vue'",
      'const message = globalThis.__mcpUrlTest.message'
    )
    .replace(
      "import { mcpApi } from '@/apis/mcp_api'",
      'const mcpApi = globalThis.__mcpUrlTest.api'
    )
  writeFileSync(
    instrumentedUrl,
    `<script setup>${source}
globalThis.__mcpUrlTest.form = { form, urlEdited, headersEdited, handleFormSubmit }
</script><template><div /></template>`
  )
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const component = await server.ssrLoadModule(
      `/src/components/extensions/${instrumentedUrl.pathname.split('/').at(-1)}`
    )
    const row = {
      resource_id: 'mcp-resource',
      slug: 'same',
      name: 'Original',
      transport: 'streamable_http',
      url_configured: true
    }
    const cases = [
      { name: '隐藏地址未编辑仍可更新名称', edit: row },
      { name: '普通地址未编辑也省略 URL', edit: { ...row, url: 'https://example.test/mcp' } },
      {
        name: '显式替换隐藏地址',
        edit: row,
        replacement: 'https://replacement.test/mcp?token=new'
      },
      { name: '显式清空已有地址被拒绝', edit: row, replacement: '', invalid: true },
      { name: '未配置地址不能空值保存', edit: { ...row, url_configured: false }, invalid: true },
      { name: '新建必须填写地址', invalid: true },
      { name: '新建包含地址和 slug', replacement: 'https://new.test/mcp' }
    ]
    for (const scenario of cases) {
      await t.test(scenario.name, async () => {
        const calls = []
        const errors = []
        globalThis.__mcpUrlTest = {
          message: { error: (text) => errors.push(text), success: () => {} },
          api: {
            updateMcpServer: async (id, payload) => {
              calls.push({ id, payload })
              return { success: true }
            },
            createMcpServer: async (payload) => {
              calls.push({ payload })
              return { success: true }
            }
          }
        }
        const app = createSSRApp(component.default, {
          open: true,
          editMode: Boolean(scenario.edit),
          editData: scenario.edit || null
        })
        const pinia = createPinia()
        pinia.state.value.user = { userRole: 'superadmin', departmentId: null }
        app.use(pinia)
        await renderToString(app)
        const { form, urlEdited, headersEdited, handleFormSubmit } = globalThis.__mcpUrlTest.form
        form.name = 'Renamed'
        if (!scenario.edit) form.slug = 'new-mcp'
        if (Object.hasOwn(scenario, 'replacement')) {
          form.url = scenario.replacement
          urlEdited.value = true
        }
        if (scenario.edit && scenario.name === '显式替换隐藏地址') {
          form.headersText = '{"Authorization":"replacement"}'
          headersEdited.value = true
        }
        await handleFormSubmit()
        if (scenario.invalid) {
          assert.equal(calls.length, 0)
          assert.deepEqual(errors, ['HTTP 类型必须填写 MCP URL'])
          return
        }
        assert.deepEqual(errors, [])
        assert.equal(calls.length, 1)
        const { id, payload } = calls[0]
        assert.equal(payload.name, 'Renamed')
        assert.equal(Object.hasOwn(payload, 'slug'), !scenario.edit)
        if (scenario.edit) assert.equal(id, 'mcp-resource')
        if (Object.hasOwn(scenario, 'replacement')) assert.equal(payload.url, scenario.replacement)
        else assert.equal(Object.hasOwn(payload, 'url'), false)
        if (scenario.edit && scenario.name === '显式替换隐藏地址') {
          assert.deepEqual(payload.headers, { Authorization: 'replacement' })
        } else if (scenario.edit) {
          assert.equal(Object.hasOwn(payload, 'headers'), false)
        }
      })
    }
  } finally {
    await server.close()
    unlinkSync(instrumentedUrl)
    delete globalThis.__mcpUrlTest
  }
})

test('部门管理员新建 MCP 默认共享给本部门并提交 share_config v2', async () => {
  const sourceUrl = new URL('../../src/components/extensions/McpFormModal.vue', import.meta.url)
  const instrumentedUrl = new URL(
    `../../src/components/extensions/.McpFormModal.scope-${pid}.vue`,
    import.meta.url
  )
  const { descriptor } = parse(readFileSync(sourceUrl, 'utf8'))
  const source = descriptor.scriptSetup.content
    .replace(
      "import { message } from 'ant-design-vue'",
      'const message = { error: () => {}, success: () => {} }'
    )
    .replace(
      "import { mcpApi } from '@/apis/mcp_api'",
      'const mcpApi = { createMcpServer: async (payload) => { globalThis.__mcpScopePayload = payload; return { success: true } } }'
    )
  writeFileSync(
    instrumentedUrl,
    `<script setup>${source}
globalThis.__mcpScopeTest = { form, handleFormSubmit }
</script><template><div /></template>`
  )
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const component = await vite.ssrLoadModule(
      `/src/components/extensions/${instrumentedUrl.pathname.split('/').at(-1)}`
    )
    const app = createSSRApp(component.default, { open: true })
    const pinia = createPinia()
    pinia.state.value.user = { userRole: 'admin', departmentId: 42 }
    app.use(pinia)
    await renderToString(app)
    const api = globalThis.__mcpScopeTest
    assert.deepEqual(api.form.share_config.read_scope, {
      access_level: 'department',
      department_ids: [42],
      user_uids: []
    })
    api.form.slug = 'department-mcp'
    api.form.name = 'Department MCP'
    api.form.url = 'https://example.test/mcp'
    await api.handleFormSubmit()
    assert.deepEqual(globalThis.__mcpScopePayload.share_config, api.form.share_config)
  } finally {
    await vite.close()
    unlinkSync(instrumentedUrl)
    delete globalThis.__mcpScopeTest
    delete globalThis.__mcpScopePayload
  }
})
