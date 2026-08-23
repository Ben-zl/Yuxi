import assert from 'node:assert/strict'
import test from 'node:test'

import { getToolCallExecutionState } from '../../src/components/ToolCallingResult/toolRegistry.js'

test('工具取消或中断后不再显示为运行中', () => {
  assert.equal(getToolCallExecutionState({ status: 'pending' }), 'running')
  assert.equal(getToolCallExecutionState({ status: 'success' }), 'completed')
  assert.equal(getToolCallExecutionState({ status: 'error' }), 'error')
  assert.equal(getToolCallExecutionState({ status: 'cancelled' }), 'error')
  assert.equal(getToolCallExecutionState({ status: 'interrupted' }), 'error')
})
