"""事件系统模块 - 行为触发机制

设计阶段

基于"行为（触发器）→ 启动器工具"的自动化模型。

行为（Trigger）产生事件，EventManager 匹配规则，调用 runItem(tool) 执行启动器工具。
动作复用启动器已有的工具项，不新建 Action 类。

行为易于扩展：插件在 initialize() 中 registerTrigger 注册新行为类型。

使用方式：
    from src.core.event import getEventManager
    mgr = getEventManager()
    mgr.startup()                # 启动所有已注册行为
    mgr.emit("clipboard", ...)   # 广播事件
    mgr.on("clipboard", handler) # 插件直连订阅
"""

# ─── 事件载荷 ───────────────────────────────────────────────
# Event:
#   name    str   "clipboard" | "hotkey" | "cron" | "startup" | "file_open" | 自定义
#   text    str   主载荷，一律文本（文件→路径，URL→字符串）
#   type    str   "text" | "url" | "file" | "image" | "raw"，仅供条件过滤
#   extra   dict  附加信息（可选）
#   time    float 时间戳
#   source  str   来源触发器名
#
# 配置结构（data/config.json）:
#   "Automation": {
#     "rules": [{
#       "name": str,          # 规则名
#       "enabled": bool,
#       "trigger": {"type": str, ...},   # 行为类型 + 参数
#       "condition": {"mode": str, ...}, # 条件过滤（可选）
#       "tool": str,          # 启动器工具显示名
#       "cooldown": int       # 冷却秒数
#     }]
#   }

# ─── 类层次 ───────────────────────────────────────────────
# EventManager（Singleton）     事件分发 + 规则匹配 + 注册表
#   emit / on / registerTrigger / matchAndRun / startup / shutdown / loadRules / saveRules
#
# TriggerBase                   行为基类
#   name / description / params / start / stop
#
# 内置行为：
#   ClipboardTrigger    剪贴板变化（文本/URL/文件/图片）
#   HotkeyTrigger       全局快捷键
#   CronTrigger         定时（复用 CronTask）
#   StartupTrigger      程序启动
#   FileOpenTrigger     文件打开

# ─── 参数 schema（自描述） ─────────────────────────────────
# TriggerBase.params / 条件 params 的格式：
#   {"param_name": {"type": str, "label": str, "default": any, ...}}
#   type: select | text | number | bool | regex
#   select 类型可带 source="Edit.engine" 从 config 动态获取选项
#
# 设置页根据 params 自动渲染表单 + 校验。
# 加新行为只需写一个 TriggerBase 子类，UI 零改动。

# ─── 条件（condition） ────────────────────────────────────
# {"mode": "none"}              无条件
# {"mode": "regex", "pattern": str}   正则匹配
# {"mode": "contains", "keyword": str} 包含关键词
# {"mode": "startswith", "prefix": str} 前缀匹配
# {"mode": "url_only"}          仅 URL
# {"mode": "min_len", "min": int}      最小长度

# ─── 占位符模板 ──────────────────────────────────────────
# 复用 argsPlaceholder，扩展：
#   {text}   事件主载荷
#   {select} 编辑器选中文本
#   {time}   当前时间戳
#   {date}   当前日期

# ─── 防自身循环 ──────────────────────────────────────────
# ClipboardTrigger._self_written = False
# ClipboardAction 写入剪贴板时置 True → ClipboardTrigger 检测到时跳过
# 与 GlobalHotkeyListener._is_pasting 机制一致
