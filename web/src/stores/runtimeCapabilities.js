import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { discoveryApi } from '@/apis/system_api'

const DISABLED_FEATURES = Object.freeze({ knowledge: false, knowledge_backend: 'builtin', knowledge_backend_ready: false })

function readFeatures(payload) {
  const features = payload?.capabilities?.features ?? {}
  return {
    knowledge: features.knowledge === true,
    knowledge_backend: features.knowledge_backend === 'weknora' ? 'weknora' : 'builtin',
    // 兼容未携带该字段的最小载荷:仅显式 false 表示后端配置不完整
    knowledge_backend_ready: features.knowledge_backend_ready !== false
  }
}

export const useRuntimeCapabilitiesStore = defineStore('runtime-capabilities', () => {
  const features = ref({ ...DISABLED_FEATURES })
  const status = ref('idle')
  const error = ref(null)
  let loadingPromise = null

  const knowledgeEnabled = computed(() => features.value.knowledge)
  const knowledgeBackend = computed(() => features.value.knowledge_backend)
  const weknoraBackendEnabled = computed(() => knowledgeEnabled.value && knowledgeBackend.value === 'weknora')

  async function ensureLoaded() {
    if (status.value === 'ready') {
      return features.value
    }
    if (loadingPromise) return loadingPromise

    status.value = 'loading'
    error.value = null
    loadingPromise = discoveryApi
      .getCapabilities()
      .then((payload) => {
        features.value = readFeatures(payload)
        status.value = 'ready'
        return features.value
      })
      .catch((cause) => {
        features.value = { ...DISABLED_FEATURES }
        error.value = cause
        status.value = 'error'
        console.warn('加载运行时能力失败，已关闭可选能力:', cause)
        return features.value
      })
      .finally(() => {
        loadingPromise = null
      })

    return loadingPromise
  }

  return {
    features,
    status,
    error,
    knowledgeEnabled,
    knowledgeBackend,
    weknoraBackendEnabled,
    ensureLoaded
  }
})
