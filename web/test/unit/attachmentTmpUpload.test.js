import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, effectScope, ref, watch } from 'vue'

const source = readFileSync(
  new URL('../../src/components/AttachmentTmpUploadModal.vue', import.meta.url),
  'utf8'
).match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')

function createModal(t, overrides = {}) {
  const events = []
  const errors = []
  const calls = []
  const threadApi = {
    uploadTmpAttachment: async (file) => ({
      file_name: file.name,
      file_type: file.type,
      file_size: file.size,
      bucket_name: 'test-bucket',
      object_name: `tmp/original/${file.name}`,
      parse_supported: true,
      parse_methods: ['rapid_ocr']
    }),
    parseTmpAttachment: async (payload) => {
      calls.push(['parse', payload])
      return { parsed_object_name: 'tmp/parsed/example.md' }
    },
    confirmTmpThreadAttachments: async (threadId, payload) => {
      calls.push(['confirm', threadId, payload])
      return { attachments: [{ file_id: 'file-1' }] }
    },
    ...overrides
  }
  const scope = effectScope()
  t.after(() => scope.stop())
  const modal = scope.run(() => runInNewContext(`${source}\n;({ uploadFile, handleParse, handleConfirm, fileItems, confirming, getErrorMessage })`, {
    computed, ref, watch, threadApi,
    defineProps: () => ({ open: true, threadId: 'thread-1' }),
    defineEmits: () => (...args) => events.push(args),
    useConfigStore: () => ({ config: {} }),
    message: { error: (value) => errors.push(value), success: () => {} }
  }))
  return { modal, events, errors, calls }
}

const file = { name: 'example.log', type: 'text/plain', size: 10 }

test('上传后确认保留后端必填文件名和存储桶', async (t) => {
  const { modal, calls, events } = createModal(t)
  await modal.uploadFile(file)
  await modal.handleConfirm()
  const [, threadId, [payload]] = calls[0]
  assert.equal(threadId, 'thread-1')
  assert.equal(payload.file_name, file.name)
  assert.equal(payload.bucket_name, 'test-bucket')
  assert.equal(payload.object_name, 'tmp/original/example.log')
  assert.equal(payload.file_type, file.type)
  assert.ok(events.some(([name]) => name === 'added'))
})

test('解析请求携带文件名和存储桶，确认使用解析后的对象', async (t) => {
  const { modal, calls } = createModal(t)
  await modal.uploadFile(file)
  await modal.handleParse(modal.fileItems.value[0])
  await modal.handleConfirm()
  assert.equal(calls[0][1].file_name, file.name)
  assert.equal(calls[0][1].bucket_name, 'test-bucket')
  assert.equal(calls[0][1].parse_method, 'rapid_ocr')
  assert.equal(calls[1][2][0].parsed_object_name, 'tmp/parsed/example.md')
})

test('422 显示安全文本并保留附件供重试', async (t) => {
  const { modal, errors, events } = createModal(t, {
    confirmTmpThreadAttachments: async () => {
      throw Object.assign(new Error('请求参数验证失败'), {
        response: { data: { detail: [{ loc: ['body', 'attachments'], msg: 'Field required' }] } }
      })
    }
  })
  await modal.uploadFile(file)
  await modal.handleConfirm()
  assert.equal(errors[0], '请求参数验证失败')
  assert.equal(modal.fileItems.value[0].status, 'uploaded')
  assert.equal(modal.confirming.value, false)
  assert.equal(events.length, 0)
  assert.equal(modal.getErrorMessage({ response: { data: { detail: [] } } }, '添加附件失败'), '添加附件失败')
  assert.equal(modal.getErrorMessage({ response: { data: { detail: '上传失败' } } }), '上传失败')
})
