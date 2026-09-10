import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

import {
  WEKNORA_FILE_STATUS_FILTER_OPTIONS,
  canWeknoraDownloadFile,
  canWeknoraPreviewFile,
  canWeknoraReparseFile,
  getFileStatusView,
  getWeknoraFileStatusView
} from '../../src/utils/knowledge_file_policy.js'
import { buildWeknoraDatabaseRequest } from '../../src/utils/databaseCreateForm.js'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

function extractBlock(source, startMarker, endMarker) {
  const startIndex = source.indexOf(startMarker)
  assert.notEqual(startIndex, -1, `未找到起点 ${startMarker}`)
  const endIndex = source.indexOf(endMarker, startIndex)
  assert.notEqual(endIndex, -1, `未找到终点 ${endMarker}`)
  return source.slice(startIndex, endIndex)
}

test('WeKnora 状态标签映射远端原文,未知状态保留原文不伪装成完成', () => {
  const expectedLabels = {
    pending: '排队',
    processing: '处理中',
    finalizing: '后处理中',
    completed: '完成',
    failed: '失败',
    deleting: '删除中',
    cancelled: '已取消',
    draft: '草稿'
  }
  for (const [status, label] of Object.entries(expectedLabels)) {
    assert.equal(getWeknoraFileStatusView(status).label, label)
  }

  assert.equal(getWeknoraFileStatusView('mystery').label, '未知状态(mystery)')
  assert.equal(getWeknoraFileStatusView('').label, '未知状态()')

  // 内置模式状态视图保持原样
  assert.equal(getFileStatusView('uploaded').label, '待解析')
  assert.equal(getFileStatusView('mystery').label, 'mystery')

  assert.deepEqual(
    WEKNORA_FILE_STATUS_FILTER_OPTIONS.map((option) => option.value),
    Object.keys(expectedLabels)
  )
})

test('WeKnora 重新解析仅对失败/完成的文档开放', () => {
  assert.equal(canWeknoraReparseFile({ status: 'failed' }), true)
  assert.equal(canWeknoraReparseFile({ status: 'completed' }), true)
  assert.equal(canWeknoraReparseFile({ status: 'processing' }), false)
  assert.equal(canWeknoraReparseFile({ status: 'pending' }), false)
  assert.equal(canWeknoraReparseFile({ status: 'failed', is_folder: true }), false)
})

test('WeKnora 建库请求只携带名称、描述与归属部门', () => {
  const superadminRequest = buildWeknoraDatabaseRequest('产品资料库', '部门共用资料', 3)
  assert.deepEqual(superadminRequest, {
    database_name: '产品资料库',
    description: '部门共用资料',
    owning_department_id: 3
  })

  const adminRequest = buildWeknoraDatabaseRequest('产品资料库', '', null)
  assert.deepEqual(adminRequest, {
    database_name: '产品资料库',
    description: ''
  })

  assert.equal('kb_type' in adminRequest, false)
  assert.equal('embedding_model_spec' in adminRequest, false)
  assert.equal('additional_params' in adminRequest, false)
  assert.equal('share_config' in adminRequest, false)
})

test('WeKnora 建库弹窗为单步简化表单且不出现类型与模型步骤', () => {
  const source = readSource('../../src/components/knowledge/DatabaseCreateFlowModal.vue')

  const weknoraSection = extractBlock(source, '<section v-if="isWeknoraMode"', '</section>')
  assert.match(weknoraSection, /知识库名称/)
  assert.match(weknoraSection, /归属部门/)
  assert.match(weknoraSection, /知识库描述/)
  assert.doesNotMatch(weknoraSection, /知识库类型/)
  assert.doesNotMatch(weknoraSection, /EmbeddingModelSelector/)
  assert.doesNotMatch(weknoraSection, /chunk_preset_id/)

  // 单步表单:不出现"下一步",直接创建;超级管理员才展示部门下拉
  assert.match(source, /v-if="!isWeknoraMode && currentStep < 2"[\s\S]*?下一步/)
  assert.match(source, /v-if="isWeknoraMode"/)
  assert.match(source, /userStore\.isSuperAdmin[\s\S]*?owningDepartmentId/)
  assert.match(source, /buildWeknoraDatabaseRequest\(/)
})

test('知识库 store 仅在非 WeKnora 模式要求选择知识库类型', () => {
  const source = readSource('../../src/stores/database.js')

  assert.match(source, /if \(!formData\.kb_type && !runtimeCapabilitiesStore\.weknoraBackendEnabled\)/)
})

test('WeKnora 模式知识库页签向普通成员开放,内置模式保持仅管理员', () => {
  const source = readSource('../../src/views/ExtensionsView.vue')

  assert.match(
    source,
    /const knowledgeTabVisible = computed\(\s*\(\) => knowledgeEnabled\.value && \(userStore\.isAdmin \|\| weknoraBackendEnabled\.value\)\s*\)/
  )
  assert.match(source, /v-if="knowledgeTabVisible && activeTab === 'knowledge'"/)
  // 普通成员页签同样按能力裁剪,其他扩展入口保持原有权限
  assert.match(
    source,
    /const userExtensionTabs = computed\(\(\) => \[\s*\.\.\.\(knowledgeTabVisible\.value \? \[\{ key: 'knowledge', label: '知识库' \}\] : \[\]\),\s*\{ key: 'skills', label: '技能' \}\s*\]\)/
  )
  assert.match(source, /userStore\.isAdmin \|\| weknoraBackendEnabled/)

  // 路由守卫仅对知识库详情放开管理员门禁,其余管理员路由不变
  const routerSource = readSource('../../src/router/index.js')
  assert.match(
    routerSource,
    /weknoraBackendEnabled && to\.name === 'ExtensionKnowledgeBaseDetail'/
  )
  assert.match(routerSource, /if \(requiresAdmin && !isAdmin && !knowledgeDetailOpenToMembers\)/)
})

test('WeKnora 知识库详情页签只保留文件管理与检索测试', () => {
  const source = readSource('../../src/views/DataBaseInfoView.vue')

  const weknoraTabsBlock = extractBlock(
    source,
    'if (isWeknoraKb.value) {',
    'if (isMilvus.value) {'
  )
  assert.match(weknoraTabsBlock, /key: 'filetable', label: '文件管理'/)
  assert.match(weknoraTabsBlock, /key: 'query', label: '检索测试'/)
  // 实体图谱页签开放(读取 WeKnora 远端图谱);评估仍隐藏
  assert.match(weknoraTabsBlock, /key: 'graph', label: '知识图谱'/)
  assert.doesNotMatch(weknoraTabsBlock, /key: 'evaluation'/)

  // 文件面板对 WeKnora 托管库开放渲染
  assert.match(source, /v-if="isMilvus \|\| isWeknoraKb"/)

  // 编辑弹窗隐藏检索配置页签与分块策略;内置维护动作与双阶段批量入口隐藏
  assert.match(source, /<a-tab-pane v-if="!isWeknoraKb" key="retrieval" tab="检索配置"/)
  assert.match(source, /v-if="!isConnector && !isWeknoraKb" name="chunk_preset_id"/)
  assert.match(source, /v-if="canManageDatabase && !isWeknoraKb && pendingParseCount > 0"/)
  assert.match(source, /v-if="canManageDatabase && !isWeknoraKb && pendingIndexCount > 0"/)
  assert.match(source, /v-if="canManageDatabase && !isWeknoraKb"[\s\S]*?repairDatabaseStats/)
  assert.match(source, /if \(!kbId\.value \|\| !canManageDatabase\.value \|\| isWeknoraKb\.value\) return/)

  // 绑定待核对徽标由 binding_status=pending_review 驱动
  assert.match(
    source,
    /const bindingPendingReview = computed\(\s*\(\) => isWeknoraKb\.value && database\.value\?\.binding_status === 'pending_review'\s*\)/
  )
  assert.match(source, /v-if="bindingPendingReview"[\s\S]*?绑定待核对/)
})

test('WeKnora 详情页分开消费 can_write_content 与 can_manage', () => {
  const source = readSource('../../src/views/DataBaseInfoView.vue')

  // can_write_content 仅在 WeKnora 库上生效,内容写按钮不再复用 can_manage
  assert.match(source, /const canWriteContent = computed\(\(\) => database\.value\?\.can_write_content === true\)/)
  assert.match(
    source,
    /const contentWritable = computed\(\(\) =>\s*isWeknoraKb\.value \? canWriteContent\.value : canManageDatabase\.value\s*\)/
  )
  assert.match(source, /v-if="contentWritable"[\s\S]*?ref="uploadActionMenuRef"/)
  assert.match(source, /:readonly="!contentWritable"/)

  // 整库管理动作(配置入口)仍由 can_manage 控制
  assert.match(source, /v-if="canManageDatabase"[\s\S]*?aria-label="配置知识库"/)
})

test('WeKnora 模式文件列表隐藏思维导图与本地双阶段批量动作,提供重新解析', () => {
  const source = readSource('../../src/components/FileTable.vue')

  assert.match(
    source,
    /<button\s+v-if="!weknoraMode"[\s\S]*?file-table-mindmap-button[\s\S]*?emit\('mindmap'\)/
  )
  assert.match(source, /<a-button\s+v-if="!weknoraMode"[\s\S]*?handleBatchParse/)
  assert.match(source, /<a-button\s+v-if="!weknoraMode"[\s\S]*?handleBatchIndex/)
  assert.match(source, /v-if="!readonly && weknoraMode && canWeknoraReparseFile\(row\)"/)
  assert.match(source, /重新解析并重建索引/)
  assert.match(source, /旧的分块和向量会被重建覆盖/)
  assert.match(source, /store\.parseFiles\(\[record\.file_id\], \{\}\)/)
  assert.match(
    source,
    /const resolveStatusView = \(status\) =>\s*weknoraMode\.value \? getWeknoraFileStatusView\(status\) : getFileStatusView\(status\)/
  )
  assert.match(
    source,
    /weknoraMode\.value \? WEKNORA_FILE_STATUS_FILTER_OPTIONS : FILE_STATUS_FILTER_OPTIONS/
  )
})

test('WeKnora 模式上传弹窗隐藏 OCR、分块与自动入库并跳过本地 OCR 校验', () => {
  const source = readSource('../../src/components/FileUploadModal.vue')

  assert.match(source, /<div class="auto-index-toggle" v-if="!weknoraMode">/)
  assert.match(source, /v-if="uploadMode !== 'url' && !weknoraMode"[\s\S]*?OCR 引擎/)
  assert.match(source, /<div class="setting-row" v-if="autoIndex && !weknoraMode">/)
  assert.match(source, /hasPdfOrImageFiles && !isOcrEnabled && !weknoraMode/)
  assert.match(source, /uploadMode\.value !== 'url' && !weknoraMode\.value && !validateOcrService\(\)/)
  assert.match(
    source,
    /const buildSubmitParams = \(extras = \{\}\) =>\s*weknoraMode\.value \? \{ \.\.\.extras \} : \{ \.\.\.processingParams\.value, \.\.\.extras \}/
  )
})

test('WeKnora 模式按资源鉴权的知识库端点不再做客户端管理员拦截', async () => {
  const source = readSource('../../src/apis/knowledge_api.js')

  // 详情/文档/检索等端点使用普通认证,由后端按 can_write_content 与读取授权裁决
  for (const fragment of [
    'return apiGet(`/api/knowledge/databases/${kbId}`)',
    'return apiGet(`/api/knowledge/databases/${kbId}/documents${query ? `?${query}` : \'\'}`)',
    'return apiPost(`/api/knowledge/databases/${kbId}/documents/parse`, {',
    'return apiPost(`/api/knowledge/databases/${kbId}/documents`, {',
    "return apiPost('/api/knowledge/files/fetch-url', {"
  ]) {
    assert.ok(source.includes(fragment), `缺少端点声明: ${fragment}`)
  }
  // 整库管理端点仍保持管理员门禁
  assert.match(source, /apiAdminPost\('\/api\/knowledge\/databases', databaseData\)/)
  assert.match(source, /apiAdminDelete\(`\/api\/knowledge\/databases\/\$\{kbId\}`\)/)
})

test('WeKnora 文件详情与下载入口按远端状态裁决且未知状态关闭', () => {
  // completed/draft:可预览解析内容 + 可下载原件
  for (const status of ['completed', 'draft']) {
    assert.equal(canWeknoraPreviewFile({ status }), true, status)
    assert.equal(canWeknoraDownloadFile({ status }), true, status)
  }
  // failed/cancelled:仅可下载原件,不可预览
  for (const status of ['failed', 'cancelled']) {
    assert.equal(canWeknoraPreviewFile({ status }), false, status)
    assert.equal(canWeknoraDownloadFile({ status }), true, status)
  }
  // pending/processing/finalizing/deleting 及未知状态:两者皆不可
  for (const status of ['pending', 'processing', 'finalizing', 'deleting', 'mystery', '']) {
    assert.equal(canWeknoraPreviewFile({ status }), false, status)
    assert.equal(canWeknoraDownloadFile({ status }), false, status)
  }
  // 文件夹一律不可
  assert.equal(canWeknoraPreviewFile({ status: 'completed', is_folder: true }), false)
  assert.equal(canWeknoraDownloadFile({ status: 'completed', is_folder: true }), false)

  // FileTable 按 weknoraMode 切换详情与下载策略
  const source = readSource('../../src/components/FileTable.vue')
  assert.match(
    source,
    /const resolveCanOpenDetail = \(record\) =>\s*weknoraMode\.value \? canWeknoraPreviewFile\(record\) : canOpenFileDetail\(record\)/
  )
  assert.match(
    source,
    /const resolveCanDownload = \(record\) =>\s*weknoraMode\.value \? canWeknoraDownloadFile\(record\) : canDownloadFile\(record\)/
  )
  assert.match(source, /:disabled="lock \|\| !resolveCanDownload\(row\)"/)
  assert.match(source, /if \(!resolveCanOpenDetail\(record\)\)/)
})

test('WeKnora 库 share_config 仅超级管理员可编辑且部门管理员提交不带该字段', () => {
  const source = readSource('../../src/views/DataBaseInfoView.vue')

  assert.match(
    source,
    /const canEditShareConfig = computed\(\s*\(\) => canManageDatabase\.value && \(!isWeknoraKb\.value \|\| userStore\.isSuperAdmin\)\s*\)/
  )
  // 非超级管理员提交体省略 share_config,避免回显值触发后端跨部门校验失败
  assert.match(
    source,
    /if \(!isWeknoraKb\.value \|\| userStore\.isSuperAdmin\) \{\s*updateData\.share_config = editShareConfig\.value\s*\}/
  )
  const updateDataBlock = extractBlock(source, 'const updateData = {', 'if (isDifyKb.value)')
  assert.doesNotMatch(
    updateDataBlock,
    /share_config\s*:/,
    'updateData 初始化不得默认携带 share_config 字段'
  )
})

test('WeKnora 手工文档端点封装按契约提交 title/markdown/parent_id 与增量更新字段', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url, options = {}) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ message: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().token = 'test-token'

    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    await documentApi.createManualDocument('kb_1', {
      title: '团队协作规范',
      markdown: '# 规范\n正文',
      parentId: 'folder_9'
    })
    await documentApi.updateDocument('kb_1', 'doc_2', {
      title: '新标题',
      markdown: '# 新正文'
    })

    assert.equal(requests.length, 2)
    assert.equal(requests[0].url, '/api/knowledge/databases/kb_1/documents/manual')
    assert.equal(requests[0].options.method, 'POST')
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      title: '团队协作规范',
      markdown: '# 规范\n正文',
      parent_id: 'folder_9'
    })

    assert.equal(requests[1].url, '/api/knowledge/databases/kb_1/documents/doc_2')
    assert.equal(requests[1].options.method, 'PUT')
    assert.deepEqual(JSON.parse(requests[1].options.body), {
      title: '新标题',
      markdown: '# 新正文'
    })
  } finally {
    await server.close()
  }
})

test('WeKnora 模式提供手工文档来源与文档编辑入口,内置模式不出现', () => {
  const uploadSource = readSource('../../src/components/FileUploadModal.vue')
  const tableSource = readSource('../../src/components/FileTable.vue')

  // 手工文档来源仅在 weknora 模式加入来源切换,提交走 manual 端点
  assert.match(uploadSource, /\.\.\.\(weknoraMode\.value\s*\? \[[\s\S]*?value: 'manual'/)
  assert.match(uploadSource, /<div class="manual-area" v-if="uploadMode === 'manual'">/)
  assert.match(uploadSource, /文档标题/)
  assert.match(uploadSource, /Markdown 正文/)
  assert.match(
    uploadSource,
    /await documentApi\.createManualDocument\(kbId\.value, \{\s*title: manualTitle\.value\.trim\(\),\s*markdown: manualMarkdown\.value,\s*parentId: selectedFolderId\.value\s*\}\)/
  )
  assert.match(uploadSource, /uploadMode\.value === 'manual'[\s\S]*?return\s*\n\s*\}/)

  // 行操作"编辑文档"仅 weknora 模式展示,弹窗含标题/描述/Markdown 并走 PUT 端点
  assert.match(tableSource, /v-if="!readonly && weknoraMode && !row\.is_folder"[\s\S]*?编辑文档/)
  assert.match(tableSource, /v-model:open="docEditModalVisible"[\s\S]*?title="编辑文档"/)
  assert.match(tableSource, /docEditForm\.markdown/)
  assert.match(tableSource, /await documentApi\.updateDocument\(store\.kbId, docEditTarget\.value\.file_id, payload\)/)
  // 正文仅在相对回读初值变化时提交,避免误清空远端内容
  assert.match(tableSource, /docEditOriginalMarkdown\.value === null/)
  assert.match(tableSource, /docEditForm\.markdown !== docEditOriginalMarkdown\.value/)
})
