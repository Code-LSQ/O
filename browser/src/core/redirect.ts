// 网址重定向模块：把链接中 {...} 包裹的内容替换为目标文本
// 语法：from 中 * 匹配任意内容（原样保留），{...} 为被替换区（恰好一组），
// 括号内文本按字面匹配（{*} 时匹配任意内容），to 为纯替换文本。
// 仅对页面跳转（main_frame）生效；匹配锚定 URL 开头避免误伤 query
import type { CoreModule } from '../manager'
import { getConfig, log, setConfig } from '../api'

interface RedirectRule {
  from: string
  to: string
}

const RULES_KEY = 'redirect.rules'
const ID_BASE = 100000
// 本模块专属动态规则 id 段：应用规则前先整段移除，保证 SW 重启（内存状态丢失）
// 或扩展重载后，Chrome 中残留的旧规则不会引发 "id not unique" 冲突
const REDIRECT_IDS = Array.from({ length: 500 }, (_, i) => ID_BASE + i)

// 解析 from 构造正则与替换串：* → 通配捕获组（替换串中 \n 保留原内容），
// {...} → 被替换区，括号内为字面文本（不占捕获组，直接匹配），替换串放入 to 文本；
// 括号内为 * 时匹配任意内容。其余字符转义为字面。
// 要求以协议开头且恰好一组 {...}，否则视为非法返回 null。
// regexSubstitution 用反斜杠 \1 引用捕获组（DNR 规范，$1 会被当作字面输出）
function buildRule(id: number, rule: RedirectRule): chrome.declarativeNetRequest.Rule | null {
  const from = rule.from.trim()
  const to = rule.to.trim()
  if (!from || !to) return null
  if (!/^https?:\/\//i.test(from)) return null
  if ((from.match(/\{.*?\}/g) ?? []).length !== 1) return null
  let pattern = ''
  let substitution = ''
  let groups = 0
  let i = 0
  while (i < from.length) {
    const ch = from[i]
    if (ch === '*') {
      groups++
      pattern += '([^/?#]*)'
      substitution += `\\${groups}`
      i++
    } else if (ch === '{') {
      const end = from.indexOf('}', i + 1)
      if (end === -1) return null
      const zone = from.slice(i + 1, end)
      // 括号内字面匹配（替换区示意），{*} 时通配，均不占捕获组
      pattern += zone === '*' ? '[^/?#]*' : zone.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      substitution += to
      i = end + 1
    } else {
      pattern += ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      substitution += ch
      i++
    }
  }
  return {
    id,
    priority: 1,
    action: { type: 'redirect', redirect: { regexSubstitution: substitution } },
    condition: {
      regexFilter: `^(?i)${pattern}`,
      resourceTypes: ['main_frame'],
    },
  }
}

// 全量应用规则：先整段移除本模块规则再添加新的，保证与配置一致
async function applyRules(rules: RedirectRule[]): Promise<void> {
  const addRules: chrome.declarativeNetRequest.Rule[] = []
  rules.forEach((rule, i) => {
    const built = buildRule(ID_BASE + i, rule)
    if (built) addRules.push(built)
  })
  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: REDIRECT_IDS,
    addRules,
  })
  log.info(`重定向规则已应用 ${addRules.length} 条`)
}

export default {
  description: '网址重定向',
  // 分组配置编辑器：每组两个输入框（链接模式、替换文本），改动即保存并自动生效
  render(host) {
    void (async () => {
      const rules = (await getConfig<RedirectRule[]>(RULES_KEY, [])).map((r) => ({ ...r }))
      const save = () => {
        void setConfig(RULES_KEY, rules)
      }
      const list = document.createElement('div')
      list.className = 'rule-list'
      const refresh = () => {
        list.innerHTML = ''
        const hint = document.createElement('div')
        hint.className = 'rule-hint'
        hint.textContent = '* 匹配任意内容并保留，{ } 中的内容匹配并替换'
        list.append(hint)
        rules.forEach((rule, index) => {
          const row = document.createElement('div')
          row.className = 'rule-row'

          const from = document.createElement('input')
          from.className = 'rule-input'
          from.placeholder = 'https://*.example.{*}'
          from.value = rule.from

          const arrow = document.createElement('span')
          arrow.className = 'rule-arrow'
          arrow.textContent = '→'

          const to = document.createElement('input')
          to.className = 'rule-input'
          to.placeholder = 'cn'
          to.value = rule.to

          // 校验并实时保存：input 事件每次按键都判定并写入配置，
          // 避免失焦（change）前测试导致规则未保存
          const check = () => {
            const f = from.value.trim()
            const validFrom = /^https?:\/\//i.test(f) && (f.match(/\{.*?\}/g) ?? []).length === 1
            from.classList.toggle('invalid', !validFrom)
            to.classList.toggle('invalid', to.value.includes('*'))
          }
          from.addEventListener('input', () => {
            rule.from = from.value
            check()
            save()
          })
          to.addEventListener('input', () => {
            rule.to = to.value
            check()
            save()
          })

          const del = document.createElement('button')
          del.className = 'rule-del'
          del.textContent = '删除'
          del.addEventListener('click', () => {
            rules.splice(index, 1)
            refresh()
            save()
          })

          row.append(from, arrow, to, del)
          check()
          list.append(row)
        })
        const add = document.createElement('button')
        add.className = 'rule-add'
        add.textContent = '添加规则'
        add.addEventListener('click', () => {
          rules.push({ from: '', to: '' })
          refresh()
        })
        list.append(add)
        host.append(list)
      }
      refresh()
    })()
  },
  async setup(ctx) {
    const onChanged = (
      changes: Record<string, chrome.storage.StorageChange>,
      area: string,
    ): void => {
      // storage.onChanged 只报告被 set 的顶层键，而 RULES_KEY 是点号嵌套键
      // （setConfig 整对象写回，顶层键为 redirect），故检查顶层键
      if (area !== 'local' || !changes['redirect']) return
      void (async () => {
        const rules = await ctx.getConfig<RedirectRule[]>(RULES_KEY, [])
        await applyRules(rules)
      })().catch((err: unknown) => log.error('应用重定向规则失败:', err))
    }
    chrome.storage.onChanged.addListener(onChanged)
    await applyRules(await ctx.getConfig<RedirectRule[]>(RULES_KEY, []))
    return () => {
      chrome.storage.onChanged.removeListener(onChanged)
      void chrome.declarativeNetRequest.updateDynamicRules({ removeRuleIds: REDIRECT_IDS })
        .catch((err: unknown) => log.error('移除重定向规则失败:', err))
    }
  },
} satisfies CoreModule