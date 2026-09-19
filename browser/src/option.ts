// 设置页：列出 core 功能模块，启停 + 展开配置项（声明式 OptionField 或自定义 render）
import type { CoreModule, OptionField } from './manager'
import { moduleIdFromPath } from './manager'
import { getConfig, setConfig } from './api'

const STORAGE_KEY = 'modules'
const loader = import.meta.glob<CoreModule>('./core/*.ts', { eager: true, import: 'default' })

const modulesEl = document.getElementById('modules')!

async function readState(): Promise<Record<string, boolean>> {
  return ((await chrome.storage.local.get(STORAGE_KEY))[STORAGE_KEY] ?? {}) as Record<string, boolean>
}

// 滑块开关（与设置页样式配套）
function makeSwitch(enabled: boolean, onChange: (checked: boolean) => void): HTMLElement {
  const label = document.createElement('label')
  label.className = 'switch'
  const checkbox = document.createElement('input')
  checkbox.type = 'checkbox'
  checkbox.checked = enabled
  const slider = document.createElement('span')
  slider.className = 'slider'
  checkbox.addEventListener('change', () => onChange(checkbox.checked))
  label.append(checkbox, slider)
  return label
}

// 按声明生成字段控件，值读写自动持久化到点号配置
async function renderFields(fields: OptionField[], host: HTMLElement): Promise<void> {
  for (const field of fields) {
    const value = await getConfig(field.key, field.default)
    const row = document.createElement('div')
    row.className = 'field'
    const label = document.createElement('div')
    label.className = 'label'
    label.textContent = field.label
    row.append(label)
    if (field.hint) {
      const hint = document.createElement('div')
      hint.className = 'hint'
      hint.textContent = field.hint
      row.append(hint)
    }
    if (field.type === 'switch') {
      row.append(makeSwitch(Boolean(value), (checked) => {
        void setConfig(field.key, checked)
      }))
    } else if (field.type === 'select') {
      const select = document.createElement('select')
      select.className = 'select'
      for (const opt of field.options ?? []) {
        const option = document.createElement('option')
        option.value = opt.value
        option.textContent = opt.label
        select.append(option)
      }
      select.value = String(value)
      select.addEventListener('change', () => {
        void setConfig(field.key, select.value)
      })
      row.append(select)
    } else {
      const input = document.createElement('input')
      input.className = 'input'
      input.type = field.type === 'number' ? 'number' : 'text'
      input.value = String(value)
      input.addEventListener('change', () => {
        void setConfig(field.key, field.type === 'number' ? Number(input.value) : input.value)
      })
      row.append(input)
    }
    host.append(row)
  }
}

function buildList(state: Record<string, boolean>): void {
  modulesEl.innerHTML = ''
  const list = Object.entries(loader).sort(([pa], [pb]) =>
    moduleIdFromPath(pa).localeCompare(moduleIdFromPath(pb)),
  )
  if (list.length === 0) {
    const empty = document.createElement('div')
    empty.className = 'empty'
    empty.textContent = '没有功能模块'
    modulesEl.append(empty)
    return
  }
  for (const [path, module] of list) {
    const id = moduleIdFromPath(path)
    const mod = document.createElement('div')
    mod.className = 'mod'

    // 设置面板：启用后自动展开并渲染内容，关闭后收起；已启用的模块打开设置页即展开
    const enabled = state[id] ?? module.enabledByDefault ?? false
    const hasPanel = !!(module.render || module.settings?.length)
    const panel = document.createElement('div')
    panel.className = 'settings-panel'
    const renderContent = () => {
      if (panel.dataset.rendered) return
      panel.dataset.rendered = '1'
      if (module.render) module.render(panel)
      else if (module.settings?.length) void renderFields(module.settings, panel)
    }
    if (enabled && hasPanel) renderContent()
    else panel.hidden = true

    const row = document.createElement('div')
    row.className = 'row'
    row.append(makeSwitch(enabled, async (checked) => {
      const next = await readState()
      await chrome.storage.local.set({ [STORAGE_KEY]: { ...next, [id]: checked } })
      if (checked && hasPanel) {
        panel.hidden = false
        renderContent()
      } else {
        panel.hidden = true
      }
    }))

    const info = document.createElement('div')
    info.className = 'info'
    const name = document.createElement('div')
    name.className = 'name'
    name.textContent = module.description ?? id
    info.append(name)
    row.append(info)

    mod.append(row, panel)
    modulesEl.append(mod)
  }
}

async function render(): Promise<void> {
  buildList(await readState())
}

void render()