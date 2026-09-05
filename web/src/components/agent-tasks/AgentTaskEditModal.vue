<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { agentApi } from '@/apis/agent_api'
import { useAgentTaskStore } from '@/stores/agentTask'
import { useUserStore } from '@/stores/user'
import { getDepartments } from '@/apis/department_api'
import { Clock, Zap, KeyRound } from '@lucide/vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  task: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'saved'])

const apiTriggerUrl = computed(() => {
  const taskId = props.task?.id
  if (taskId) return `${window.location.origin}/api/agent-tasks/${taskId}/trigger`
  return `${window.location.origin}/api/agent-tasks/{task_id}/trigger`
})

const store = useAgentTaskStore()
const userStore = useUserStore()
const agents = ref([])
const departments = ref([])
const saving = ref(false)
const previewTimes = ref([])

const isEdit = computed(() => !!props.task)
const title = computed(() => (isEdit.value ? '编辑任务' : '创建任务'))

const form = reactive({
  name: '',
  agent_slug: '',
  prompt: '',
  api_enabled: false,
  schedule_enabled: false,
  schedule_mode: 'daily',
  schedule_time: '09:00',
  schedule_weekdays: [1],
  schedule_cron: '*/10 * * * *',
  schedule_timezone: 'Asia/Shanghai',
  share_level: 'user',
  share_departments: [],
  tool_approval_mode: 'always_trust'
})

const WEEKDAYS = [
  { label: '一', value: 1 },
  { label: '二', value: 2 },
  { label: '三', value: 3 },
  { label: '四', value: 4 },
  { label: '五', value: 5 },
  { label: '六', value: 6 },
  { label: '日', value: 0 }
]

const TIMEZONES = [
  'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Singapore', 'Europe/London',
  'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'UTC'
]

watch(
  () => props.open,
  (val) => {
    if (!val) return
    if (props.task) {
      form.name = props.task.name || ''
      form.agent_slug = props.task.agent_slug || ''
      form.prompt = props.task.prompt || ''
      form.api_enabled = props.task.api_enabled || false
      form.tool_approval_mode = props.task.tool_approval_mode || 'always_trust'
      const sched = props.task.schedule
      if (sched && sched.mode) {
        form.schedule_enabled = true
        form.schedule_mode = sched.mode
        form.schedule_timezone = sched.timezone || 'Asia/Shanghai'
        form.schedule_cron = sched.cron || '*/10 * * * *'
        if (sched.mode === 'daily' || sched.mode === 'weekly') {
          const parts = (sched.cron || '').split(/\s+/)
          if (parts.length === 5) {
            const hour = parts[1].padStart(2, '0')
            const minute = parts[0].padStart(2, '0')
            form.schedule_time = `${hour}:${minute}`
            if (sched.mode === 'weekly' && parts[4] !== '*') {
              form.schedule_weekdays = parts[4].split(',').map(Number)
            }
          }
        }
      } else {
        form.schedule_enabled = false
      }
      const scope = props.task.share_config?.read_scope || {}
      form.share_level = scope.access_level || 'user'
      form.share_departments = scope.department_ids || []
    } else {
      Object.assign(form, {
        name: '', agent_slug: '', prompt: '', api_enabled: false,
        schedule_enabled: false, schedule_mode: 'daily', schedule_time: '09:00',
        schedule_weekdays: [1], schedule_cron: '*/10 * * * *', schedule_timezone: 'Asia/Shanghai',
        share_level: 'user', share_departments: [], tool_approval_mode: 'always_trust'
      })
    }
    previewTimes.value = []
  }
)

const agentsLoaded = ref(false)
const departmentsLoaded = ref(false)

async function ensureAgents() {
  if (agentsLoaded.value) return
  agentsLoaded.value = true
  try {
    const data = await agentApi.getAgents()
    agents.value = data.agents || data || []
  } catch (e) {
    console.error('加载智能体列表失败', e)
  }
}

async function ensureDepartments() {
  if (departmentsLoaded.value || !userStore.isAdmin) return
  departmentsLoaded.value = true
  try {
    const data = await getDepartments()
    departments.value = data.departments || data || []
  } catch (e) {
    console.error('加载部门列表失败', e)
  }
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      ensureAgents()
      ensureDepartments()
    }
  }
)

const canShareDepartment = computed(() => userStore.isAdmin || userStore.isSuperAdmin)

async function doPreview() {
  if (!form.schedule_enabled) {
    previewTimes.value = []
    return
  }
  try {
    const payload = buildSchedulePayload()
    if (!payload.schedule?.enabled) return
    const data = await store.previewSchedule(payload)
    previewTimes.value = data.preview || []
  } catch {
    previewTimes.value = []
  }
}

watch(
  () => [form.schedule_enabled, form.schedule_mode, form.schedule_time, form.schedule_weekdays, form.schedule_cron, form.schedule_timezone],
  () => { if (form.schedule_enabled) doPreview() }
)

function buildSchedulePayload() {
  const schedule = { enabled: form.schedule_enabled, timezone: form.schedule_timezone }
  if (!form.schedule_enabled) return { schedule }
  if (form.schedule_mode === 'daily') {
    Object.assign(schedule, { mode: 'daily', time: form.schedule_time })
  } else if (form.schedule_mode === 'weekly') {
    Object.assign(schedule, { mode: 'weekly', time: form.schedule_time, weekdays: form.schedule_weekdays })
  } else {
    Object.assign(schedule, { mode: 'cron', cron: form.schedule_cron })
  }
  return { schedule }
}

function buildPayload() {
  const payload = {
    name: form.name,
    agent_slug: form.agent_slug,
    prompt: form.prompt,
    api_enabled: form.api_enabled,
    tool_approval_mode: form.tool_approval_mode
  }
  if (canShareDepartment.value) {
    if (form.share_level === 'department') {
      payload.share_config = { access_level: 'department', department_ids: form.share_departments }
    } else {
      payload.share_config = { access_level: 'user' }
    }
  }
  Object.assign(payload, buildSchedulePayload())
  return payload
}

async function handleSave() {
  if (!form.name.trim()) return message.warning('请输入任务名称')
  if (!form.agent_slug) return message.warning('请选择智能体')
  if (!form.prompt.trim()) return message.warning('请输入提示词')
  saving.value = true
  try {
    const payload = buildPayload()
    if (isEdit.value) {
      await store.updateTask(props.task.id, payload)
    } else {
      await store.createTask(payload)
    }
    emit('saved')
    emit('update:open', false)
  } catch (e) {
    message.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function handleClose() {
  emit('update:open', false)
}
</script>

<template>
  <Modal
    :open="open"
    :title="title"
    :width="640"
    :okText="isEdit ? '保存' : '创建'"
    :okButtonProps="{ loading: saving }"
    :cancelButtonProps="{ disabled: saving }"
    @ok="handleSave"
    @cancel="handleClose"
  >
    <div class="task-edit-form">
      <!-- 基本信息 -->
      <div class="form-section">
        <div class="section-title"><Clock :size="16" /> 基本信息</div>
        <div class="form-row">
          <label>任务名称</label>
          <input v-model="form.name" class="form-input" placeholder="为任务起一个名称" />
        </div>
        <div class="form-row">
          <label>智能体</label>
          <a-select v-model:value="form.agent_slug" placeholder="选择智能体" style="width: 100%">
            <a-select-option v-for="a in agents" :key="a.slug" :value="a.slug">{{ a.name }}</a-select-option>
          </a-select>
        </div>
        <div class="form-row">
          <label>提示词</label>
          <textarea v-model="form.prompt" class="form-textarea" rows="3" placeholder="每次执行时发送给智能体的指令"></textarea>
        </div>
      </div>

      <!-- 触发方式 -->
      <div class="form-section">
        <div class="section-title"><Zap :size="16" /> 触发方式</div>
        <div class="form-row">
          <label>API 触发</label>
          <a-switch v-model:checked="form.api_enabled" size="small" />
          <span class="form-hint">启用后可通过 API Key 异步触发</span>
        </div>
        <div v-if="form.api_enabled" class="api-info-box">
          <div class="api-info-label">API 触发地址</div>
          <div class="api-info-url">{{ apiTriggerUrl }}</div>
          <div class="api-info-hint">
            请求需携带 <code>Authorization: Bearer yxkey_你的API密钥</code> 和
            <code>Idempotency-Key: 自定义幂等键</code>，成功返回 202 和 execution_id。
          </div>
        </div>
        <div class="form-row">
          <label>定时触发</label>
          <a-switch v-model:checked="form.schedule_enabled" size="small" />
        </div>
      </div>

      <!-- 定时配置 -->
      <div v-if="form.schedule_enabled" class="form-section">
        <div class="section-title"><Clock :size="16" /> 定时配置</div>
        <div class="form-row">
          <label>模式</label>
          <a-radio-group v-model:value="form.schedule_mode" size="small">
            <a-radio-button value="daily">每天</a-radio-button>
            <a-radio-button value="weekly">每周</a-radio-button>
            <a-radio-button value="cron">Cron</a-radio-button>
          </a-radio-group>
        </div>
        <div v-if="form.schedule_mode === 'daily'" class="form-row">
          <label>执行时间</label>
          <input v-model="form.schedule_time" type="time" class="form-input form-time" />
        </div>
        <div v-if="form.schedule_mode === 'weekly'" class="form-row">
          <label>星期</label>
          <div class="weekday-picker">
            <button
              v-for="d in WEEKDAYS"
              :key="d.value"
              class="weekday-btn"
              :class="{ active: form.schedule_weekdays.includes(d.value) }"
              @click="form.schedule_weekdays.includes(d.value)
                ? form.schedule_weekdays = form.schedule_weekdays.filter(v => v !== d.value)
                : form.schedule_weekdays.push(d.value)"
            >{{ d.label }}</button>
          </div>
        </div>
        <div v-if="form.schedule_mode === 'weekly'" class="form-row">
          <label>执行时间</label>
          <input v-model="form.schedule_time" type="time" class="form-input form-time" />
        </div>
        <div v-if="form.schedule_mode === 'cron'" class="form-row">
          <label>Cron 表达式</label>
          <input v-model="form.schedule_cron" class="form-input" placeholder="*/10 * * * *" />
          <span class="form-hint">5 段 POSIX Cron，最短间隔 5 分钟</span>
        </div>
        <div class="form-row">
          <label>时区</label>
          <a-select v-model:value="form.schedule_timezone" style="width: 100%">
            <a-select-option v-for="tz in TIMEZONES" :key="tz" :value="tz">{{ tz }}</a-select-option>
          </a-select>
        </div>
        <div v-if="previewTimes.length" class="preview-box">
          <div class="preview-label">未来 5 次执行时间</div>
          <div v-for="(t, i) in previewTimes" :key="i" class="preview-item">{{ t }}</div>
        </div>
      </div>

      <!-- 权限与审批 -->
      <div class="form-section">
        <div class="section-title"><KeyRound :size="16" /> 权限与审批</div>
        <div v-if="canShareDepartment" class="form-row">
          <label>可见范围</label>
          <a-radio-group v-model:value="form.share_level" size="small">
            <a-radio value="user">个人</a-radio>
            <a-radio value="department">部门</a-radio>
          </a-radio-group>
        </div>
        <div v-if="canShareDepartment && form.share_level === 'department'" class="form-row">
          <label>共享部门</label>
          <a-select
            v-model:value="form.share_departments"
            mode="multiple"
            style="width: 100%"
            placeholder="选择部门"
            :options="departments.map(d => ({ label: d.name, value: d.id }))"
          />
        </div>
        <div class="form-row">
          <label>工具审批</label>
          <a-radio-group v-model:value="form.tool_approval_mode" size="small">
            <a-radio value="always_trust">
              <span class="approval-label">完全信任</span>
              <span class="approval-hint">智能体自主执行所有工具调用</span>
            </a-radio>
            <a-radio value="default">
              <span class="approval-label">请求审批</span>
              <span class="approval-hint">每次工具调用需执行身份批准</span>
            </a-radio>
          </a-radio-group>
        </div>
      </div>
    </div>
  </Modal>
</template>

<style lang="less" scoped>
.task-edit-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 4px 0;
}
.form-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  color: var(--gray-800);
}
.form-row {
  display: flex;
  align-items: center;
  gap: 12px;
  label {
    width: 80px;
    flex-shrink: 0;
    font-size: 13px;
    color: var(--gray-600);
  }
  .form-hint {
    margin-left: 8px;
    font-size: 12px;
    color: var(--gray-400);
    line-height: 32px;
  }
}
.form-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  font-size: 13px;
  background: var(--gray-0);
  outline: none;
  &:focus {
    border-color: var(--main-color);
  }
}
.form-time {
  max-width: 180px;
}
.form-textarea {
  flex: 1;
  padding: 8px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
  background: var(--gray-0);
  outline: none;
  resize: vertical;
  &:focus {
    border-color: var(--main-color);
  }
}
.weekday-picker {
  display: flex;
  gap: 4px;
}
.weekday-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  font-size: 13px;
  cursor: pointer;
  color: var(--gray-600);
  &.active {
    background: var(--main-color);
    color: var(--gray-0);
    border-color: var(--main-color);
  }
}
.preview-box {
  padding: 10px 12px;
  background: var(--gray-10);
  border-radius: 6px;
  .preview-label {
    font-size: 12px;
    color: var(--gray-500);
    margin-bottom: 4px;
  }
  .preview-item {
    font-size: 12px;
    color: var(--gray-700);
    font-family: monospace;
  }
}
.api-info-box {
  margin-left: 92px;
  padding: 10px 12px;
  background: var(--gray-10);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  .api-info-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--gray-600);
    margin-bottom: 4px;
  }
  .api-info-url {
    font-size: 12px;
    font-family: monospace;
    color: var(--main-color);
    word-break: break-all;
    margin-bottom: 6px;
  }
  .api-info-hint {
    font-size: 12px;
    color: var(--gray-500);
    line-height: 1.5;
    code {
      padding: 1px 4px;
      background: var(--gray-100);
      border-radius: 3px;
      font-size: 11px;
      font-family: monospace;
      color: var(--gray-700);
    }
  }
}
.approval-label {
  font-weight: 500;
  color: var(--gray-800);
}
.approval-hint {
  display: block;
  font-size: 12px;
  color: var(--gray-400);
  line-height: 1.4;
  margin-top: 2px;
}
</style>
