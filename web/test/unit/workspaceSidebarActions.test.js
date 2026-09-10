import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import * as Vue from 'vue'
import { compileTemplate } from 'vue/compiler-sfc'

const source = readFileSync(new URL('../../src/views/WorkspaceView.vue', import.meta.url), 'utf8')
const sidebar = source.match(/<WorkspaceSidebar\b[\s\S]*?\/>/)[0]
const compiled = compileTemplate({ source: sidebar, filename: 'WorkspaceView.vue', id: 'sidebar-actions' })
assert.equal(compiled.errors.length, 0)
const renderCode = compiled.code
  .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => `const {${names.replace(/ as /g, ': ')}} = Vue`)
  .replace('export function render', 'function render')
const render = runInNewContext(`${renderCode}; render`, {
  Vue: { ...Vue, resolveComponent: (name) => name }
})

function renderSidebar({ readonly = false, uploading = false, sourceKey = 'personal' } = {}) {
  let pickerClicks = 0
  const state = {
    activeSourceKey: Vue.ref(sourceKey),
    isReadonlyWorkspacePath: Vue.ref(readonly),
    uploadingFile: Vue.ref(uploading),
    uploadInputRef: Vue.ref({ value: 'old-selection', click: () => pickerClicks++ }),
    newDirectoryName: Vue.ref('old-name'),
    createDirectoryModalVisible: Vue.ref(false)
  }
  const handlers = ['openUploadFilePicker', 'openCreateDirectoryModal'].map((name) => {
    const start = source.indexOf(`const ${name} =`)
    return source.slice(start, source.indexOf('\n}\n', start) + 2)
  }).join('\n')
  const actions = runInNewContext(`${handlers}; ({ openUploadFilePicker, openCreateDirectoryModal })`, state)
  const vnode = render({
    ...Vue.proxyRefs(state), ...actions,
    currentPath: '/', databases: [], loadingDatabases: false, userStore: { uid: 'test-user' },
    selectPersonalWorkspace() {}, selectDatabase() {}, selectWorkspacePath() {}
  }, [])
  return { vnode, state, pickerClicks: () => pickerClicks }
}

test('侧栏上传事件打开页面文件选择器', () => {
  const { vnode, state, pickerClicks } = renderSidebar()
  assert.equal(typeof vnode.props.onUploadFile, 'function')
  vnode.props.onUploadFile()
  assert.equal(pickerClicks(), 1)
  assert.equal(state.uploadInputRef.value.value, '')
})

test('侧栏新建文件夹事件打开并清空页面对话框', () => {
  const { vnode, state } = renderSidebar()
  assert.equal(typeof vnode.props.onCreateDirectory, 'function')
  vnode.props.onCreateDirectory()
  assert.equal(state.createDirectoryModalVisible.value, true)
  assert.equal(state.newDirectoryName.value, '')
})

test('侧栏传递只读、非个人空间及上传中状态', () => {
  assert.equal(renderSidebar().vnode.props.disabled, false)
  assert.equal(renderSidebar({ readonly: true }).vnode.props.disabled, true)
  assert.equal(renderSidebar({ sourceKey: 'database:test' }).vnode.props.disabled, true)
  assert.equal(renderSidebar({ uploading: true }).vnode.props.uploading, true)
  const { vnode, pickerClicks } = renderSidebar({ uploading: true })
  vnode.props.onUploadFile()
  assert.equal(pickerClicks(), 0)
})
