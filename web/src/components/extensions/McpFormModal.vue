<template>
  <a-modal
    v-model:open="visible"
    :title="editMode ? '编辑 MCP' : '添加 MCP'"
    @ok="handleFormSubmit"
    :confirmLoading="formLoading"
    @cancel="visible = false"
    :maskClosable="false"
    width="560px"
    class="server-modal"
  >
    <a-form layout="vertical" class="extension-form">
      <a-form-item label="MCP 标识" required class="form-item">
        <a-input
          v-model:value="form.slug"
          placeholder="请输入 MCP 稳定标识，如 my-mcp"
          :disabled="editMode"
        />
      </a-form-item>
      <a-form-item label="MCP 名称" required class="form-item">
        <a-input v-model:value="form.name" placeholder="请输入 MCP 展示名称" />
      </a-form-item>
      <a-form-item label="描述" class="form-item">
        <a-input v-model:value="form.description" placeholder="请输入 MCP 描述" />
      </a-form-item>
      <a-row :gutter="16">
        <a-col :span="12">
          <a-form-item label="传输类型" required class="form-item">
            <a-select v-model:value="form.transport">
              <a-select-option value="streamable_http">streamable_http</a-select-option>
              <a-select-option value="sse">sse</a-select-option>
            </a-select>
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="图标" class="form-item">
            <a-input v-model:value="form.icon" placeholder="输入 emoji，如 🧠" :maxlength="2" />
          </a-form-item>
        </a-col>
      </a-row>
      <template v-if="form.transport === 'streamable_http' || form.transport === 'sse'">
        <a-form-item label="MCP URL" required class="form-item">
          <a-input
            v-model:value="form.url"
            :placeholder="
              editMode && editData?.url_configured && !editData?.url
                ? '已配置（地址已隐藏）'
                : 'https://example.com/mcp'
            "
            @change="urlEdited = true"
          />
        </a-form-item>
        <a-form-item label="HTTP 请求头" class="form-item">
          <a-textarea
            v-model:value="form.headersText"
            placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
            :rows="3"
            @change="headersEdited = true"
          />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="HTTP 超时（秒）" class="form-item">
              <a-input-number
                v-model:value="form.timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="SSE 读取超时（秒）" class="form-item">
              <a-input-number
                v-model:value="form.sse_read_timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </template>
      <a-form-item label="标签" class="form-item">
        <a-select
          v-model:value="form.tags"
          mode="tags"
          placeholder="输入标签后回车添加"
          style="width: 100%"
        />
      </a-form-item>
      <ShareConfigForm
        ref="shareConfigFormRef"
        v-model="form.share_config"
        :allowed-access-levels="allowedShareAccessLevels"
        :require-read-scope="true"
        :disabled="isGlobalScopeReadOnly"
      />
    </a-form>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

const props = defineProps({
  open: { type: Boolean, default: false },
  editMode: { type: Boolean, default: false },
  editData: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'submitted'])

const visible = computed({
  get: () => props.open,
  set: (val) => emit('update:open', val)
})

const formLoading = ref(false)
const urlEdited = ref(false)
const headersEdited = ref(false)
const shareConfigFormRef = ref(null)
const createDefaultShareConfig = () => ({
  version: 2,
  read_scope: {
    access_level: userStore.isSuperAdmin ? 'global' : 'department',
    department_ids:
      !userStore.isSuperAdmin && userStore.departmentId != null
        ? [Number(userStore.departmentId)]
        : [],
    user_uids: []
  },
  manage_scope: null
})

const form = reactive({
  slug: '',
  name: '',
  description: '',
  transport: 'streamable_http',
  url: '',
  headersText: '',
  timeout: null,
  sse_read_timeout: null,
  tags: [],
  icon: '',
  share_config: createDefaultShareConfig()
})
const isGlobalScopeReadOnly = computed(
  () =>
    !userStore.isSuperAdmin &&
    props.editMode &&
    form.share_config?.read_scope?.access_level === 'global'
)
const allowedShareAccessLevels = computed(() =>
  userStore.isSuperAdmin || isGlobalScopeReadOnly.value ? ['global', 'department'] : ['department']
)

watch(
  () => props.open,
  (val) => {
    if (val) {
      urlEdited.value = false
      headersEdited.value = false
    }
    if (val && props.editData) {
      Object.assign(form, {
        slug: props.editData.slug || '',
        name: props.editData.name || '',
        description: props.editData.description || '',
        transport: props.editData.transport || 'streamable_http',
        url: props.editData.url || '',
        headersText: props.editData.headers ? JSON.stringify(props.editData.headers, null, 2) : '',
        timeout: props.editData.timeout,
        sse_read_timeout: props.editData.sse_read_timeout,
        tags: props.editData.tags || [],
        icon: props.editData.icon || '',
        share_config: props.editData.share_config
          ? JSON.parse(JSON.stringify(props.editData.share_config))
          : createDefaultShareConfig()
      })
    } else if (val && !props.editData) {
      Object.assign(form, {
        slug: '',
        name: '',
        description: '',
        transport: 'streamable_http',
        url: '',
        headersText: '',
        timeout: null,
        sse_read_timeout: null,
        tags: [],
        icon: '',
        share_config: createDefaultShareConfig()
      })
    }
  },
  { immediate: true }
)

const handleFormSubmit = async () => {
  try {
    formLoading.value = true
    const shareValidation = shareConfigFormRef.value?.validate()
    if (shareValidation && !shareValidation.valid) {
      message.error(shareValidation.message)
      return
    }
    let headers = null
    if (form.headersText.trim()) {
      try {
        headers = JSON.parse(form.headersText)
      } catch {
        message.error('请求头 JSON 格式错误')
        return
      }
    }
    const data = {
      slug: form.slug,
      name: form.name,
      description: form.description || null,
      transport: form.transport,
      url: form.url || null,
      timeout: form.timeout || null,
      sse_read_timeout: form.sse_read_timeout || null,
      tags: form.tags.length > 0 ? form.tags : null,
      icon: form.icon || null,
      share_config: form.share_config
    }
    if (!data.slug?.trim()) {
      message.error('MCP 标识不能为空')
      return
    }
    if (!data.name?.trim()) {
      message.error('MCP 名称不能为空')
      return
    }
    if (!data.transport) {
      message.error('请选择传输类型')
      return
    }
    const preserveUrl =
      props.editMode &&
      !urlEdited.value &&
      (props.editData?.url_configured || Boolean(props.editData?.url))
    if (['sse', 'streamable_http'].includes(data.transport)) {
      if (!preserveUrl && !data.url?.trim()) {
        message.error('HTTP 类型必须填写 MCP URL')
        return
      }
    }
    if (props.editMode) {
      delete data.slug
      if (preserveUrl) delete data.url
      if (headersEdited.value) data.headers = headers
      const result = await mcpApi.updateMcpServer(props.editData?.resource_id, data)
      if (result.success) {
        message.success('MCP 更新成功')
      } else {
        message.error(result.message || '更新失败')
        return
      }
    } else {
      data.headers = headers
      const result = await mcpApi.createMcpServer(data)
      if (result.success) {
        message.success('MCP 创建成功')
      } else {
        message.error(result.message || '创建失败')
        return
      }
    }
    visible.value = false
    emit('submitted')
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    formLoading.value = false
  }
}
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
</style>
