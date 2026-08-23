<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import { agentTaskApi } from '@/apis/agent_task_api'
import { useAgentStore } from '@/stores/agent'

const route = useRoute()
const agentStore = useAgentStore()
const chatRef = ref(null)
const threadId = ref('')
const agentSlug = ref('')
const loading = ref(true)

async function loadExecution() {
  const executionId = route.params.executionId
  if (!executionId) return
  loading.value = true
  try {
    const data = await agentTaskApi.getExecution(executionId)
    threadId.value = data.thread_id || ''
    agentSlug.value = data.agent_slug || ''
  } catch (e) {
    console.error('加载执行详情失败', e)
    threadId.value = ''
  } finally {
    loading.value = false
  }
}

async function ensureAgentSelected() {
  // 等待 agentStore 完全初始化（不是仅 isInitialized=true，而是 agents 已加载）
  if (!agentStore.isInitialized) {
    await agentStore.initialize()
  }
  if (agentSlug.value && agentSlug.value !== agentStore.selectedAgentId) {
    const agent = (agentStore.agents || []).find((a) => a.slug === agentSlug.value || a.id === agentSlug.value)
    if (agent) {
      await agentStore.selectAgent(agent.id)
    }
  }
}

// 当 threadId 和 chatRef 都就绪时加载线程
let lastLoadedThread = ''
async function waitAgentStoreReady() {
  // 如果 initialize 正在进行，轮询等待完成
  let retries = 0
  while (!agentStore.isInitialized && retries < 50) {
    await agentStore.initialize()
    if (!agentStore.isInitialized) {
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    retries++
  }
}

async function trySelectThread() {
  const tid = threadId.value
  const ref = chatRef.value
  if (!tid || !ref || tid === lastLoadedThread) return
  lastLoadedThread = tid
  try {
    await waitAgentStoreReady()
    await ensureAgentSelected()
    await ref.selectThreadFromRoute(tid)
  } catch (e) {
    console.error('加载线程失败', e)
  }
}

onMounted(async () => {
  await loadExecution()
  await trySelectThread()
})

watch(chatRef, () => { trySelectThread() })

watch(() => route.params.executionId, async (_, oldId) => {
  if (!oldId) return
  if (chatRef.value) {
    await chatRef.value.selectThreadFromRoute('')
  }
  lastLoadedThread = ''
  threadId.value = ''
  await loadExecution()
  await trySelectThread()
})

function handleThreadChange() {
  // 任务执行线程不改变 URL
}
</script>

<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <div class="content">
        <div v-if="loading || !agentStore.isInitialized" class="loading-hint">加载执行线程...</div>
        <AgentChatComponent
          v-show="!loading && agentStore.isInitialized"
          ref="chatRef"
          :single-mode="false"
          run-source="agent_task"
          @thread-change="handleThreadChange"
        />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}
.agent-view-body {
  display: flex;
  flex-direction: row;
  width: 100%;
  flex: 1;
  height: 100%;
  overflow: hidden;
  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
}
.loading-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--gray-400);
  font-size: 14px;
}
</style>
