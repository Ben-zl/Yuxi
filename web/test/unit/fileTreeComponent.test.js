import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

const source = fs.readFileSync(
  new URL('../../src/components/FileTreeComponent.vue', import.meta.url),
  'utf8'
)

test('自定义文件夹标题首次展开时会加载异步子节点', () => {
  assert.match(
    source,
    /const handleNodeClick = async \(data\) => \{[\s\S]*?if \(!isExpanded && props\.loadData\) \{[\s\S]*?await internalLoadData\(data\)/
  )
})
