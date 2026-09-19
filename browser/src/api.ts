/* API 和工具模块，防止循环依赖，不导入本地模块。定义程序的桌面端、移动端、浏览器扩展之间的通信接口协议，也负责与其他程序交互。

之后考虑支持从桌面程序发信号启用、禁用浏览器的其他扩展
*/

// 点号嵌套键读写 chrome.storage.local 中的配置
// 如 getConfig('redirect.rules', []) 读取 redirect.rules

async function getAll(): Promise<Record<string, unknown>> {
  return chrome.storage.local.get(null)
}

export async function getConfig<T>(key: string, def: T): Promise<T> {
  const all = await getAll()
  let cur: unknown = all
  for (const part of key.split('.')) {
    if (cur == null || typeof cur !== 'object') return def
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur === undefined || cur === null ? def : (cur as T)
}

export async function setConfig(key: string, val: unknown): Promise<void> {
  const all = await getAll()
  const parts = key.split('.')
  let cur = all
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i]
    if (cur[part] == null || typeof cur[part] !== 'object') cur[part] = {}
    cur = cur[part] as Record<string, unknown>
  }
  cur[parts[parts.length - 1]] = val
  await chrome.storage.local.set(all)
}

// 日志：console 输出，格式对标 Python logging 的
// format="%(asctime)s | %(levelname)s | %(message)s"
// 调用方式：log.info('...')、log.warn('...')、log.error('...')

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function formatTime(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

function writeLog(level: string, message: string, ...args: unknown[]): void {
  const line = `${formatTime(new Date())} | ${level} | ${message}`
  if (level === 'ERROR') console.error(line, ...args)
  else if (level === 'WARN') console.warn(line, ...args)
  else console.log(line, ...args)
}

export const log = {
  info(message: string, ...args: unknown[]): void { writeLog('INFO', message, ...args) },
  warn(message: string, ...args: unknown[]): void { writeLog('WARN', message, ...args) },
  error(message: string, ...args: unknown[]): void { writeLog('ERROR', message, ...args) },
}


// 要自行实现翻译吗？考虑一下，看看别的扩展的做法

