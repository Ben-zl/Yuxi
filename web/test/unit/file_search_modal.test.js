import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../../src/components/modals/FileSearchModal.vue', import.meta.url),
  'utf8'
)

test('文件搜索输入后防抖自动查询且回车可立即查询', () => {
  assert.match(source, /const SEARCH_DEBOUNCE_MS = \d+/)
  assert.match(source, /watch\(keyword, scheduleSearch\)/)
  assert.match(source, /searchTimer = setTimeout\([\s\S]*handleSearch\(\)[\s\S]*SEARCH_DEBOUNCE_MS/)
  assert.match(source, /clearTimeout\(searchTimer\)/)
  assert.match(source, /@keydown\.enter\.prevent="handleSearch"/)
})

test('清空关键词和关闭弹窗会取消待执行搜索并清理结果', () => {
  assert.match(source, /const resetSearchResults = \(\) => \{[\s\S]*results\.value = \[\][\s\S]*hasSearched\.value = false/)
  assert.match(source, /if \(!query \|\| !props\.kbId\) \{[\s\S]*resetSearchResults\(\)/)
  assert.match(source, /\(nextOpen\) => \{[\s\S]*resetState\(\)[\s\S]*if \(!nextOpen\) \{/)
})
