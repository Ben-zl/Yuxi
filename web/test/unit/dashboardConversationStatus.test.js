import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

const readSource = (relativePath) =>
  fs.readFileSync(new URL(`../../${relativePath}`, import.meta.url), 'utf8')

test('Dashboard 将 Conversation active 展示为活跃而不是 Run 进行中', () => {
  const listSource = readSource('src/components/dashboard/ThreadStatsComponent.vue')
  const detailSource = readSource('src/components/dashboard/ThreadDetailDrawer.vue')

  assert.match(listSource, /record\.status === 'active'[\s\S]{0,80}>活跃<\/a-tag>/)
  assert.match(detailSource, /detail\?\.status === 'active'[\s\S]{0,80}>活跃<\/a-tag>/)
  assert.doesNotMatch(listSource, /record\.status === 'active'[\s\S]{0,80}>进行中<\/a-tag>/)
  assert.doesNotMatch(detailSource, /detail\?\.status === 'active'[\s\S]{0,80}>进行中<\/a-tag>/)
})
