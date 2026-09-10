import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue'

const source = readFileSync(new URL('../../src/components/agent-tasks/AgentTaskEditModal.vue', import.meta.url), 'utf8')
const previewSource = source.slice(source.indexOf('const canShareDepartment'), source.indexOf('function buildPayload'))

function setupPreview(t) {
  const form = reactive({ schedule_enabled: true, schedule_mode: 'weekly', schedule_time: '09:00', schedule_weekdays: [1], schedule_timezone: 'Asia/Shanghai', schedule_cron: '' })
  const props = reactive({ open: true })
  const previewTimes = ref([])
  const calls = []
  const scope = effectScope()
  t.after(() => scope.stop())
  const context = { form, props, previewTimes, computed, watch, ref, userStore: {}, store: {
    previewSchedule: (payload) => new Promise((resolve, reject) => calls.push({ payload: JSON.parse(JSON.stringify(payload)), resolve, reject }))
  } }
  const doPreview = scope.run(() => runInNewContext(`${previewSource}; doPreview`, context))
  return { form, props, previewTimes, calls, doPreview }
}

const settle = async () => { await Promise.resolve(); await nextTick() }

test('增加星期会刷新预览，清空后重新选择也能恢复', async (t) => {
  const { form, calls, previewTimes } = setupPreview(t)
  form.schedule_weekdays.push(2)
  await nextTick()
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].payload.schedule.weekdays, [1, 2])
  calls[0].resolve({ preview: ['monday', 'tuesday'] })
  await settle()
  form.schedule_weekdays = []
  await nextTick()
  assert.equal(previewTimes.value.length, 0)
  assert.equal(calls.length, 1)
  form.schedule_weekdays.push(3)
  await nextTick()
  assert.equal(calls.length, 2)
  calls[1].resolve({ preview: ['wednesday'] })
  await settle()
  assert.equal(previewTimes.value[0], 'wednesday')
})

test('较早的预览响应不能覆盖最新选择', async (t) => {
  const { doPreview, calls, previewTimes } = setupPreview(t)
  const older = doPreview()
  const newer = doPreview()
  calls[1].resolve({ preview: ['latest'] })
  await newer
  calls[0].resolve({ preview: ['stale'] })
  await older
  assert.equal(previewTimes.value[0], 'latest')
})

test('关闭弹窗后未完成响应不能恢复预览', async (t) => {
  const { doPreview, props, calls, previewTimes } = setupPreview(t)
  const pending = doPreview()
  props.open = false
  await nextTick()
  calls[0].resolve({ preview: ['stale'] })
  await pending
  assert.equal(previewTimes.value.length, 0)
})
