<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Brain, Trash2 } from 'lucide-vue-next'

import { agentApi } from '@/apis/agent_api'

const props = defineProps({
  agent: { type: Object, required: true },
  active: { type: Boolean, default: false }
})

const loading = ref(false)
const clearing = ref(false)
const items = ref([])
const scope = ref({})
const pagination = reactive({ page: 1, page_size: 20, total: 0 })
const filters = reactive({ kind: 'all', category: 'all' })

const dreamStatuses = {
  completed: { label: 'Dream 已完成', color: 'success' },
  failed: { label: 'Dream 失败', color: 'error' },
  running: { label: 'Dream 运行中', color: 'processing' }
}
const kindLabels = { daily: '每日记忆', digest: '长期摘要' }
const categoryLabels = { personal: '个人事实', procedure: '流程经验', wiki: '知识条目' }

const agentSlug = computed(() => props.agent?.slug || props.agent?.id || props.agent?.agent_id || '')
const dreamStatus = computed(() => {
  const status = scope.value.dream_status
  return status ? dreamStatuses[status] || { label: 'Dream 状态未知', color: 'default' } : null
})

const handleBusy = (error, fallback) => {
  if (error?.response?.status === 409) message.warning('记忆正在使用，请稍后重试')
  else message.error(error.message || fallback)
}

const load = async () => {
  if (!agentSlug.value) return
  loading.value = true
  try {
    const result = await agentApi.listMemories(agentSlug.value, {
      ...filters,
      page: pagination.page,
      page_size: pagination.page_size
    })
    items.value = result.items || []
    Object.assign(pagination, result.pagination || {})
    scope.value = result.scope || {}
  } catch (error) {
    handleBusy(error, '加载长期记忆失败')
  } finally {
    loading.value = false
  }
}

const removeItem = (item) => {
  Modal.confirm({
    title: '删除这条长期记忆？',
    content:
      item.kind === 'daily'
        ? '删除 Daily 记忆不会自动删除已经生成的独立 Digest。'
        : '删除后该记忆将不再参与后续检索。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await agentApi.deleteMemory(agentSlug.value, item.memory_id)
        message.success('记忆已删除')
        await load()
      } catch (error) {
        handleBusy(error, '删除长期记忆失败')
      }
    }
  })
}

const clearAll = () => {
  Modal.confirm({
    title: '清空该智能体的全部长期记忆？',
    content: '只清空你在该智能体下的记忆，不影响对话历史，也不会影响其他用户。此操作不可恢复。',
    okText: '清空',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      clearing.value = true
      try {
        await agentApi.clearMemories(agentSlug.value)
        message.success('长期记忆已清空')
        await load()
      } catch (error) {
        handleBusy(error, '清空长期记忆失败')
      } finally {
        clearing.value = false
      }
    }
  })
}

const changeFilters = () => {
  pagination.page = 1
  load()
}

watch(
  () => [props.active, agentSlug.value],
  ([active], previous = []) => {
    if (agentSlug.value !== previous[1]) {
      items.value = []
      scope.value = {}
      Object.assign(pagination, { page: 1, page_size: 20, total: 0 })
    }
    if (!active) return
    pagination.page = 1
    load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="agent-memory-panel">
    <div class="memory-toolbar">
      <div class="memory-status">
        <Brain :size="16" />
        <span>{{ scope.enabled ? '记忆已启用' : '记忆已关闭，已有数据仍保留' }}</span>
        <a-tag v-if="dreamStatus" :color="dreamStatus.color">{{ dreamStatus.label }}</a-tag>
      </div>
      <a-button danger :loading="clearing" :disabled="!pagination.total" @click="clearAll">
        清空全部
      </a-button>
    </div>

    <div class="memory-filters">
      <a-select v-model:value="filters.kind" style="width: 140px" @change="changeFilters">
        <a-select-option value="all">全部类型</a-select-option>
        <a-select-option value="daily">Daily</a-select-option>
        <a-select-option value="digest">Digest</a-select-option>
      </a-select>
      <a-select v-model:value="filters.category" style="width: 160px" @change="changeFilters">
        <a-select-option value="all">全部分类</a-select-option>
        <a-select-option value="personal">个人事实</a-select-option>
        <a-select-option value="procedure">流程经验</a-select-option>
        <a-select-option value="wiki">知识条目</a-select-option>
      </a-select>
    </div>

    <a-spin :spinning="loading">
      <a-empty v-if="!items.length" description="暂无长期记忆" />
      <div v-else class="memory-list">
        <article v-for="item in items" :key="item.memory_id" class="memory-card">
          <div class="memory-card-main">
            <div class="memory-card-title">
              <span>{{ item.title }}</span>
              <a-tag>
                {{ kindLabels[item.kind] || '未知类型' }} ·
                {{ categoryLabels[item.category] || '未知分类' }}
              </a-tag>
            </div>
            <p>{{ item.summary || '暂无摘要' }}</p>
            <small>{{ item.memory_date || item.updated_at }}</small>
          </div>
          <a-button type="text" danger aria-label="删除记忆" @click="removeItem(item)">
            <Trash2 :size="16" />
          </a-button>
        </article>
      </div>
    </a-spin>

    <a-pagination
      v-if="pagination.total > pagination.page_size"
      v-model:current="pagination.page"
      :page-size="pagination.page_size"
      :total="pagination.total"
      show-less-items
      @change="load"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-memory-panel {
  min-height: 320px;
}

.memory-toolbar,
.memory-status,
.memory-filters,
.memory-card,
.memory-card-title {
  display: flex;
  align-items: center;
}

.memory-toolbar {
  justify-content: space-between;
  gap: 12px;
}

.memory-status,
.memory-filters,
.memory-card-title {
  gap: 8px;
}

.memory-filters {
  margin: 16px 0;
}

.memory-list {
  display: grid;
  gap: 10px;
  margin-bottom: 16px;
}

.memory-card {
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
}

.memory-card-main {
  min-width: 0;
}

.memory-card-title {
  color: var(--gray-900);
  font-weight: 600;
}

.memory-card p {
  margin: 8px 0;
  color: var(--gray-700);
  line-height: 1.55;
}

.memory-card small {
  color: var(--gray-500);
}
</style>
