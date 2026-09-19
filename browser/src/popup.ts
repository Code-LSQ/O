// 扩展管理 popup：core 模块快捷入口 + 扩展启停，支持列表/网格显示
import type { CoreModule } from './manager'
import { moduleIdFromPath } from './manager'
import { log } from './api'

interface ExtItem {
  id: string
  name: string
  version: string
  iconUrl: string
  enabled: boolean
  mayDisable: boolean
}

interface CoreEntry {
  id: string
  desc: string
  open: () => void
}

type ViewMode = 'list' | 'grid'

const VIEW_KEY = 'popupView'
const listEl = document.getElementById('list')!
const viewBtn = document.getElementById('viewBtn')!
const optionBtn = document.getElementById('optionBtn')!
const moduleView = document.getElementById('moduleView')!
const moduleTitle = document.getElementById('moduleTitle')!
const moduleBody = document.getElementById('moduleBody')!
const backBtn = document.getElementById('backBtn')!

// 声明了 popup 的 core 模块：列表/网格动态加"描述"入口
const coreLoader = import.meta.glob<CoreModule>('./core/*.ts', { eager: true, import: 'default' })

optionBtn.addEventListener('click', () => {
  void chrome.runtime.openOptionsPage().catch((err: unknown) => log.error('打开设置页失败:', err))
})

backBtn.addEventListener('click', () => {
  moduleView.hidden = true
  listEl.hidden = false
})

// 切换到模块操作视图
function openModule(id: string, renderUI: (host: HTMLElement) => void): void {
  moduleTitle.textContent = id
  moduleBody.innerHTML = ''
  renderUI(moduleBody)
  listEl.hidden = true
  moduleView.hidden = false
}

let view: ViewMode = 'list'

function applyView(): void {
  listEl.classList.toggle('view-grid', view === 'grid')
  viewBtn.textContent = view === 'list' ? '网格' : '列表'
  applyGrid()
}

let measureCtx: CanvasRenderingContext2D | null = null

// 用 canvas 测量文本宽度（与网格 name 的 12px 字体一致）
function textWidth(text: string): number {
  if (!measureCtx) {
    const canvas = document.createElement('canvas')
    measureCtx = canvas.getContext('2d')
    if (measureCtx) measureCtx.font = '12px system-ui, "Microsoft YaHei", sans-serif'
  }
  return measureCtx ? measureCtx.measureText(text).width : text.length * 12
}

// 超宽时在空格或英文与其他字符交界处就近中点换行
function wrapName(name: string, maxWidth: number): string {
  if (textWidth(name) <= maxWidth * 1.1) return name
  const mid = Math.floor(name.length / 2)
  let best = -1
  for (let i = 0; i < name.length - 1; i++) {
    const aEng = /[a-z]/i.test(name[i])
    const bEng = /[a-z]/i.test(name[i + 1])
    if (aEng !== bEng && (best === -1 || Math.abs(i - mid) < Math.abs(best - mid))) best = i
  }
  if (best <= 0) return name
  const cut = name[best] === ' ' ? best : best + 1
  return name.slice(0, cut) + '\n' + name.slice(best + 1)
}

// 根据弹窗实际宽度计算列数，并把每格设为正方形（高度=宽度）
function applyGrid(): void {
  const rows = listEl.querySelectorAll<HTMLElement>('.row')
  if (view !== 'grid') {
    listEl.style.gridTemplateColumns = ''
    for (const row of rows) {
      row.style.height = ''
      row.style.borderLeft = ''
      row.style.borderTop = ''
      const nameEl = row.querySelector<HTMLElement>('.name')
      if (nameEl?.dataset.raw) nameEl.textContent = nameEl.dataset.raw
    }
    return
  }
  const avail = listEl.clientWidth
  const cols = Math.max(2, Math.floor(avail / 107))
  listEl.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`
  const cell = Math.floor(avail / cols)
  const maxWidth = cell - 4
  rows.forEach((row) => {
    row.style.height = `${cell}px`
    row.style.borderLeft = 'none'
    row.style.borderTop = 'none'
    const nameEl = row.querySelector<HTMLElement>('.name')
    if (nameEl?.dataset.raw) nameEl.textContent = wrapName(nameEl.dataset.raw, maxWidth)
  })
}

viewBtn.addEventListener('click', () => {
  view = view === 'list' ? 'grid' : 'list'
  void chrome.storage.local.set({ [VIEW_KEY]: view })
  applyView()
})

function buildList(coreEntries: CoreEntry[], items: ExtItem[]): void {
  listEl.innerHTML = ''
  if (items.length === 0 && coreEntries.length === 0) {
    const empty = document.createElement('div')
    empty.className = 'empty'
    empty.textContent = '没有其他扩展'
    listEl.append(empty)
    return
  }
  for (const entry of coreEntries) {
    const row = document.createElement('div')
    row.className = 'row'
    const icon = document.createElement('div')
    icon.className = 'icon icon-letter'
    icon.textContent = entry.id[0]?.toUpperCase() ?? '?'
    const info = document.createElement('div')
    info.className = 'info'
    const title = entry.desc || entry.id
    const name = document.createElement('div')
    name.className = 'name'
    name.textContent = title
    name.dataset.raw = title
    info.append(name)
    const btn = document.createElement('button')
    btn.className = 'desc-btn'
    btn.textContent = '描述'
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation()
      entry.open()
    })
    row.append(icon, info, btn)
    row.addEventListener('click', entry.open)
    listEl.append(row)
  }
  for (const item of items) {
    const row = document.createElement('div')
    row.className = 'row'
    if (!item.enabled) row.classList.add('off')

    const icon = document.createElement('img')
    icon.className = 'icon'
    icon.src = item.iconUrl
    icon.alt = ''
    icon.onerror = () => icon.remove()

    const info = document.createElement('div')
    info.className = 'info'
    const name = document.createElement('div')
    name.className = 'name'
    name.textContent = item.name
    name.dataset.raw = item.name
    const version = document.createElement('div')
    version.className = 'version'
    version.textContent = `v${item.version}`
    info.append(name, version)

    row.addEventListener('click', () => {
      if (!item.mayDisable) return
      const target = !item.enabled
      void chrome.management.setEnabled(item.id, target)
        .then(() => render())
        .catch((err: unknown) => {
          log.error('切换扩展状态失败:', err)
          render()
        })
    })

    row.append(icon, info)
    listEl.append(row)
  }
}

async function render(): Promise<void> {
  const coreEntries: CoreEntry[] = []
  for (const [path, module] of Object.entries(coreLoader)) {
    const popup = module.popup
    if (!popup) continue
    const id = moduleIdFromPath(path)
    coreEntries.push({
      id,
      desc: module.description ?? '',
      open: () => openModule(id, popup),
    })
  }
  const all = await chrome.management.getAll()
  const items = all
    .filter((e) => e.type === 'extension' && e.id !== chrome.runtime.id)
    .map((e) => {
      const icon = e.icons ? [...e.icons].sort((a, b) => b.size - a.size)[0] : undefined
      return {
        id: e.id,
        name: e.name,
        version: e.version,
        iconUrl: icon?.url ?? '',
        enabled: e.enabled,
        mayDisable: e.mayDisable !== false,
      } satisfies ExtItem
    })
    .sort((a, b) => Number(b.enabled) - Number(a.enabled))
  buildList(coreEntries, items)
  applyGrid()
}

async function init(): Promise<void> {
  view = ((await chrome.storage.local.get(VIEW_KEY))[VIEW_KEY] as ViewMode | undefined) ?? 'list'
  applyView()
  await render()
}

void init()