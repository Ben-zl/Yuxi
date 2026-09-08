/**
 * 清理模型标题响应，避免把内部推理写入会话标题。
 */
export const normalizeGeneratedTitle = (value, fallback = '') => {
  let title = String(value || '').trim()
  const closeRegex = /<\/(?:(?:mm:)?think|analysis|reasoning)>/gi
  const openRegex = /<(?:(?:mm:)?think|analysis|reasoning)>/gi
  const closeMatches = [...title.matchAll(closeRegex)]

  // 标题响应可能把推理、正文和 MiniMax 的异常结束标记混在一起；
  // 最后一个结束标记之后才是用户可见标题。
  if (closeMatches.length) {
    const lastClose = closeMatches.at(-1)
    title = title.slice(lastClose.index + lastClose[0].length)
  } else {
    const opening = openRegex.exec(title)
    if (opening) title = title.slice(0, opening.index)
  }

  title = title
    .replace(closeRegex, ' ')
    .replace(openRegex, ' ')
    .replace(/^```(?:text|markdown)?\s*|\s*```$/gi, '')
    .replace(/^\s*(?:标题|title)\s*[:：]\s*/i, '')
    .replace(/["“”'']/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  return title || String(fallback || '').replace(/\s+/g, ' ').trim()
}
