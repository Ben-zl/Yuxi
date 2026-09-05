<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { CirclePlus, Pencil, RefreshCw, RotateCw, Trash2, X } from '@lucide/vue'

import { agentChannelApi } from '@/apis/agent_channel_api'

const props = defineProps({
  agentSlug: { type: String, required: true }
})

const loading = ref(false)
const saving = ref(false)
const items = ref([])
const runtime = reactive({})
const formOpen = ref(false)
const editingId = ref(null)

const emptyForm = () => ({
  name: '',
  app_id: '',
  app_secret: '',
  allow_from_text: '',
  group_reply_policy: 'mention_only',
  enabled: true
})
const form = reactive(emptyForm())

const channelCount = computed(() => items.value.length)

const loadRuntime = async (records) => {
  await Promise.all(
    records.map(async (item) => {
      try {
        const response = await agentChannelApi.status(item.id)
        runtime[item.id] = response.data?.runtime || { state: 'unknown' }
      } catch (error) {
        runtime[item.id] = { state: 'unavailable', last_error: error.message }
      }
    })
  )
}

const load = async () => {
  if (!props.agentSlug) return
  loading.value = true
  try {
    const response = await agentChannelApi.list()
    items.value = (response.data || []).filter((item) => item.agent_slug === props.agentSlug)
    await loadRuntime(items.value)
  } catch (error) {
    message.error(error.message || '加载协作渠道失败')
  } finally {
    loading.value = false
  }
}

const closeForm = () => {
  formOpen.value = false
  editingId.value = null
  Object.assign(form, emptyForm())
}

const openCreate = () => {
  editingId.value = null
  Object.assign(form, emptyForm())
  formOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name,
    app_id: item.app_id,
    app_secret: '',
    allow_from_text: (item.allow_from || []).join('\n'),
    group_reply_policy: item.group_reply_policy,
    enabled: item.enabled
  })
  formOpen.value = true
}

const payload = () => {
  const data = {
    name: form.name.trim(),
    agent_slug: props.agentSlug,
    app_id: form.app_id.trim(),
    allow_from: form.allow_from_text
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean),
    group_reply_policy: form.group_reply_policy
  }
  if (!editingId.value) data.enabled = form.enabled
  if (form.app_secret) data.app_secret = form.app_secret
  return data
}

const save = async () => {
  if (!form.name.trim() || !form.app_id.trim()) {
    message.warning('请完整填写渠道名称和 App ID')
    return
  }
  if (!editingId.value && !form.app_secret) {
    message.warning('创建渠道必须填写 App Secret')
    return
  }

  saving.value = true
  try {
    const response = editingId.value
      ? await agentChannelApi.update(editingId.value, payload())
      : await agentChannelApi.create(payload())
    closeForm()
    await load()
    if (response.data?.sync_status === 'synced') message.success('协作渠道已同步')
    else message.warning('配置已保存，但同步未完成')
  } catch (error) {
    message.error(error.message || '保存协作渠道失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  try {
    await agentChannelApi.setEnabled(item.id, enabled)
    await load()
  } catch (error) {
    message.error(error.message || '更新渠道状态失败')
  }
}

const reconcile = async (item) => {
  try {
    await agentChannelApi.reconcile(item.id)
    await load()
  } catch (error) {
    message.error(error.message || '重试同步失败')
  }
}

const remove = (item) => {
  Modal.confirm({
    title: `删除 ${item.name}`,
    content: '将清理 AgentScope 中的渠道、专用 Agent 和模型凭据；已有对话历史会保留。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await agentChannelApi.remove(item.id)
      if (editingId.value === item.id) closeForm()
      await load()
      message.success('协作渠道已删除')
    }
  })
}

const statusColor = (state) =>
  ({
    connected: 'green',
    synced: 'green',
    connecting: 'blue',
    pending: 'blue',
    retrying: 'blue',
    error: 'red',
    failed: 'red',
    unavailable: 'red'
  })[state] || 'default'

watch(
  () => props.agentSlug,
  () => {
    closeForm()
    load()
  },
  { immediate: true }
)

defineExpose({ loading, refresh: load })
</script>

<template>
  <div class="wps-channel-panel">
    <div class="channel-toolbar">
      <div>
        <div class="channel-title-row">
          <h3>WPS 协作渠道</h3>
          <a-tag v-if="channelCount">{{ channelCount }}</a-tag>
        </div>
        <p>将当前智能体接入 WPS 私聊或群聊。</p>
      </div>
      <div class="toolbar-actions">
        <a-tooltip title="刷新连接状态">
          <a-button class="icon-button" :loading="loading" aria-label="刷新连接状态" @click="load">
            <RefreshCw v-if="!loading" :size="15" />
          </a-button>
        </a-tooltip>
        <a-button v-if="!formOpen" type="primary" class="lucide-icon-btn" @click="openCreate">
          <CirclePlus :size="15" />
          新增渠道
        </a-button>
      </div>
    </div>

    <div v-if="formOpen" class="channel-editor">
      <div class="editor-heading">
        <div>
          <h4>{{ editingId ? '编辑渠道' : '新增渠道' }}</h4>
          <span>{{ editingId ? 'App Secret 留空时保持原凭据' : '凭据只用于建立 WPS 连接' }}</span>
        </div>
        <a-tooltip title="关闭表单">
          <a-button class="icon-button" aria-label="关闭表单" @click="closeForm">
            <X :size="15" />
          </a-button>
        </a-tooltip>
      </div>

      <a-form layout="vertical" class="channel-form">
        <a-form-item label="渠道名称" required>
          <a-input v-model:value="form.name" placeholder="例如：客服协作群" />
        </a-form-item>
        <a-form-item label="App ID" required>
          <a-input v-model:value="form.app_id" autocomplete="off" />
        </a-form-item>
        <a-form-item
          class="full-width"
          :label="editingId ? 'App Secret（留空表示不轮换）' : 'App Secret'"
          :required="!editingId"
        >
          <a-input-password v-model:value="form.app_secret" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="群聊触发">
          <a-radio-group v-model:value="form.group_reply_policy" button-style="solid">
            <a-radio-button value="mention_only">仅 @ 应用</a-radio-button>
            <a-radio-button value="all">全部消息</a-radio-button>
          </a-radio-group>
        </a-form-item>
        <a-form-item class="full-width" label="允许的发送者 ID">
          <a-textarea
            v-model:value="form.allow_from_text"
            :rows="3"
            placeholder="每行一个；留空表示不限制"
          />
        </a-form-item>
        <a-form-item v-if="!editingId" label="创建后启用">
          <a-switch v-model:checked="form.enabled" />
        </a-form-item>
      </a-form>

      <div class="editor-actions">
        <a-button @click="closeForm">取消</a-button>
        <a-button type="primary" :loading="saving" @click="save">保存渠道</a-button>
      </div>
    </div>

    <a-spin v-else :spinning="loading">
      <a-empty v-if="!items.length" :image="false" description="当前智能体尚未配置 WPS 渠道">
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <CirclePlus :size="15" />
          新增渠道
        </a-button>
      </a-empty>

      <div v-else class="channel-list">
        <article v-for="item in items" :key="item.id" class="channel-item">
          <div class="channel-item-main">
            <div class="channel-name-line">
              <strong>{{ item.name }}</strong>
              <a-tag :color="statusColor(item.sync_status)">{{ item.sync_status }}</a-tag>
              <a-tag :color="statusColor(runtime[item.id]?.state)">
                {{ runtime[item.id]?.state || 'unknown' }}
              </a-tag>
            </div>
            <span class="channel-app-id">{{ item.app_id }}</span>
            <span v-if="item.last_error || runtime[item.id]?.last_error" class="error-text">
              {{ item.last_error || runtime[item.id]?.last_error }}
            </span>
          </div>

          <div class="channel-item-actions">
            <a-switch
              size="small"
              :checked="item.enabled"
              :aria-label="item.enabled ? '禁用渠道' : '启用渠道'"
              @change="(value) => toggleEnabled(item, value)"
            />
            <a-tooltip title="编辑">
              <a-button class="icon-button" aria-label="编辑渠道" @click="openEdit(item)">
                <Pencil :size="15" />
              </a-button>
            </a-tooltip>
            <a-tooltip title="重试同步">
              <a-button class="icon-button" aria-label="重试同步" @click="reconcile(item)">
                <RotateCw :size="15" />
              </a-button>
            </a-tooltip>
            <a-tooltip title="删除">
              <a-button danger class="icon-button" aria-label="删除渠道" @click="remove(item)">
                <Trash2 :size="15" />
              </a-button>
            </a-tooltip>
          </div>
        </article>
      </div>
    </a-spin>
  </div>
</template>

<style lang="less" scoped>
.wps-channel-panel {
  min-height: 100%;
  color: var(--gray-900);
}

.channel-toolbar,
.editor-heading,
.channel-item,
.editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.channel-toolbar {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--gray-150);

  h3,
  p {
    margin: 0;
  }

  h3 {
    font-size: 15px;
    font-weight: 600;
    line-height: 22px;
  }

  p {
    margin-top: 3px;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 18px;
  }
}

.channel-title-row,
.toolbar-actions,
.channel-name-line,
.channel-item-actions {
  display: flex;
  align-items: center;
  gap: 7px;
}

.channel-editor {
  padding-top: 18px;
}

.editor-heading {
  margin-bottom: 16px;

  h4,
  span {
    margin: 0;
  }

  h4 {
    font-size: 14px;
    font-weight: 600;
    line-height: 20px;
  }

  span {
    display: block;
    margin-top: 2px;
    color: var(--gray-500);
    font-size: 12px;
  }
}

.channel-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 14px;

  .full-width {
    grid-column: 1 / -1;
  }
}

.editor-actions {
  justify-content: flex-end;
  padding-top: 4px;
}

.channel-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 16px;
}

.channel-item {
  min-height: 76px;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.channel-item-main {
  min-width: 0;
}

.channel-name-line {
  flex-wrap: wrap;

  strong {
    font-size: 13px;
    font-weight: 600;
  }
}

.channel-app-id,
.error-text {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.channel-app-id {
  color: var(--gray-500);
}

.error-text {
  max-width: 390px;
  color: var(--color-error-700);
}

.channel-item-actions {
  flex-shrink: 0;
}

.icon-button {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  padding: 0;
}

:deep(.ant-empty) {
  margin: 72px 0;
}

:deep(.ant-form-item) {
  margin-bottom: 14px;
}

@media (max-width: 768px) {
  .channel-toolbar,
  .channel-item {
    align-items: flex-start;
    flex-direction: column;
  }

  .channel-form {
    grid-template-columns: minmax(0, 1fr);

    .full-width {
      grid-column: auto;
    }
  }

  .channel-item-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
