// 功能模块加载/启停机制
// core/ 下每个 .ts 文件默认导出一个 CoreModule，删除任意文件不影响运行
import { getConfig, log, setConfig } from './api'

export type Dispose = () => void

export interface ModuleContext {
  getConfig<T>(key: string, def: T): Promise<T>
  setConfig(key: string, val: unknown): Promise<void>
}

/**
 * 预定义设置字段：模块声明后由设置页自动生成控件，
 * 值自动通过 getConfig/setConfig 读写并持久化。
 * type:
 *  - switch  → 滑块开关（布尔）
 *  - text    → 单行文本框（字符串）
 *  - number  → 数字输入（数值）
 *  - select  → 下拉选择（options 提供选项）
 * key 需带模块前缀（如 "redirect.domain"），label 为显示名，hint 为辅助说明。
 */
export interface OptionField {
  key: string
  label: string
  type: 'switch' | 'text' | 'number' | 'select'
  default?: string | number | boolean
  options?: { label: string; value: string }[]
  hint?: string
}

export interface CoreModule {
  description?: string
  enabledByDefault?: boolean
  settings?: OptionField[]              // 设置页声明式字段，自动生成控件
  render?: (host: HTMLElement) => void  // 设置页自定义 UI，复杂场景时替代 settings
  popup?: (host: HTMLElement) => void   // popup 操作 UI，有则 popup 动态加"描述"按钮
  setup(ctx: ModuleContext): Dispose | undefined | Promise<Dispose | undefined>
}

const STORAGE_KEY = 'modules'
const loader = import.meta.glob<CoreModule>('./core/*.ts', { eager: true, import: 'default' })

const known = new Map<string, CoreModule>()
const active = new Map<string, Dispose>()

// 模块 id 直接由文件名派生，保证文件名即模块身份
export function moduleIdFromPath(path: string): string {
  return path.split('/').pop()!.replace(/\.ts$/, '')
}

function createContext(): ModuleContext {
  return { getConfig, setConfig }
}

async function activate(id: string, module: CoreModule): Promise<void> {
  if (active.has(id)) return
  try {
    const dispose = (await module.setup(createContext())) ?? (() => {})
    active.set(id, dispose)
  } catch (err) {
    log.error(`模块 ${id} 启动失败:`, err)
  }
}

async function deactivate(id: string): Promise<void> {
  const dispose = active.get(id)
  if (!dispose) return
  try {
    dispose()
  } catch (err) {
    log.error(`模块 ${id} 清理失败:`, err)
  }
  active.delete(id)
}

function onStorageChanged(changes: Record<string, chrome.storage.StorageChange>, area: string): void {
  if (area !== 'local' || !changes[STORAGE_KEY]) return
  if (known.size === 0) return
  const oldMap = (changes[STORAGE_KEY].oldValue ?? {}) as Record<string, boolean>
  const newMap = (changes[STORAGE_KEY].newValue ?? {}) as Record<string, boolean>
  void (async () => {
    for (const [id, enabled] of Object.entries(newMap)) {
      const module = known.get(id)
      if (!module) continue
      if (enabled && !oldMap[id]) await activate(id, module)
      if (!enabled && oldMap[id]) await deactivate(id)
    }
  })()
}

// 顶层同步注册，保证 service worker 可被 storage 变更唤醒（MV3 要求）
chrome.storage.onChanged.addListener(onStorageChanged)

export async function initModules(): Promise<void> {
  const stored = (await chrome.storage.local.get(STORAGE_KEY))[STORAGE_KEY] ?? {}
  for (const [path, module] of Object.entries(loader)) {
    const id = moduleIdFromPath(path)
    known.set(id, module)
    const enabled = (stored as Record<string, boolean>)[id] ?? module.enabledByDefault ?? false
    if (enabled) await activate(id, module)
  }
}