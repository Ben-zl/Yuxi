/**
 * 清理模型标题响应，避免把内部推理写入会话标题。
 */
export const normalizeGeneratedTitle = (value, fallback = '') => {
  let title = String(value || '')
    .replace(/<think>[\s\S]*?<\/think>/gi, ' ')
    .replace(/<analysis>[\s\S]*?<\/analysis>/gi, ' ')
    .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, ' ')

  // 某些模型只返回未闭合的推理块，不能把内部判断写入标题。
  title = title.replace(/<(?:think|analysis|reasoning)>[\s\S]*$/i, ' ')
  title = title
    .replace(/^```(?:text|markdown)?\s*|\s*```$/gi, '')
    .replace(/^\s*(?:标题|title)\s*[:：]\s*/i, '')
    .replace(/["“”'']/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  return title || String(fallback || '').replace(/\s+/g, ' ').trim()
}
