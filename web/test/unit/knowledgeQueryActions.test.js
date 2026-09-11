import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../../src/components/QuerySection.vue', import.meta.url),
  'utf8'
)

test('生成示例操作常驻搜索按钮旁且建议区不保留重复入口', () => {
  const actionBlock = source.slice(
    source.indexOf('<div class="query-action-buttons">'),
    source.indexOf('</div>', source.indexOf('<div class="query-action-buttons">'))
  )
  const suggestionsBlock = source.slice(
    source.indexOf('<div v-else-if="showQuerySuggestions"'),
    source.indexOf('</template>')
  )

  assert.match(actionBlock, /v-if="canGenerateQuestions"/)
  assert.match(actionBlock, /class="generate-examples-button"/)
  assert.match(actionBlock, /:loading="generatingQuestions"/)
  assert.match(actionBlock, /@click="generateSampleQuestions\(false\)"/)
  assert.ok(actionBlock.indexOf('generate-examples-button') < actionBlock.indexOf('search-button'))
  assert.doesNotMatch(suggestionsBlock, /重新生成/)
  assert.doesNotMatch(suggestionsBlock, /@click="\(\) => generateSampleQuestions\(false\)"/)
})
