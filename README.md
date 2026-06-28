# O

## [中文](README.md#简体中文) | [English](README.md#English)

## 简体中文

### 项目简介

O，考虑到我是取名废，干脆用这个名字了，原名是 MiniWread，可以理解为极简读写，是一个带了一些奇怪功能的文本编辑器。但是你也可以完全抛弃它的文本编辑器功能来用。

提醒您，这是一个非常丑陋、抽象、无语的项目，作者大部分时间毫无计划地想到什么加什么，因为这个项目主要是给作者个人使用的。

Github 上的项目，总会有人尝尝咸淡。感谢您的尝试与奉献。

功能列表：

主体：


插件：
符号链接管理
文件同步


### 功能

#### AI

为什么有 AI 功能呢？因为这个项目也是我的毕业设计，我个人是不喜欢什么都加 AI 的


#### 快速启动

启动器
文件路径和图标路径两端不要带双引号 ""。工作目录和启动参数可以带。
图标可以填一个 exe 文件的路径，会获取它的图标。

建议使用资源监视器搜索指定软件有哪些流氓进程，路径为 C:\Windows\System32\perfmon.exe


### 插件

#### 插件规范

插件路径为 /plugin/ ，其内的每个 .py 文件都是一个插件


插件系统主要由两部分组成：
1. PluginBase（基类） — 定义在 src/plugin.py，所有插件必须继承此类
2. PluginManager（管理器） — 定义在 src/plugin.py，单例模式，负责扫描、加载、启用/禁用插件


必须定义

属性：


方法：
getAction()，控制插件返回的按钮

如果有需要额外导入的 Python 库依赖，请在 .py 文件中注释。

getPluginManager(main_window=None) -> PluginManager，通过此函数获取管理器实例，首次调用时会自动扫描 plugin/ 目录。


PluginBase — 插件基类

所有插件必须继承 PluginBase 类

实例属性（由管理器自动管理）：
  main_window — 主窗口 QMainWindow 实例
  enabled     — bool，当前是否已启用
  settings    — dict，插件配置数据
可重写的方法（按调用时机排列）：
  初始化阶段：
    initialize() — 插件初始化。
      在 enablePlugin() 中被调用，此时 main_window 已可用。



PluginManager — 插件管理器
关键方法：
scan_plugins(force=False) -> list，扫描 plugin/ 目录，返回所有可用插件名称。

enablePlugin(plugin_name) -> bool，启用指定插件：实例化 -> initialize() -> activate()。如果插件已启用，直接返回 True。

disablePlugin(plugin_name)，禁用指定插件：deactivate() -> cleanup() -> 从内存移除。

loadEnablePlugin()，加载所有已配置为启用状态的插件。

reloadPlugin(plugin_name) -> bool，重新加载单个插件（先禁用，再重新 import 并启用）。

reloadPlugins()，重新加载所有已启用的插件。

isPluginEnabled(plugin_name) -> bool

save_config(config_file: Path)，将 enabled_plugins 和 plugin_configs 保存到 JSON 文件。

load_config(config_file: Path)，从 JSON 文件加载插件配置并加载所有启用的插件。

setMainWindow(main_window)，设置主窗口实例，更新所有已加载插件的 main_window 属性。


配置保存

插件配置存储在主配置文件 /data/config.json 的 Plugin.plugin_name 下：
  {
    "Plugin": {
      "plugin_name": {
        "enabled": true,
        "……"
      }
    }
  }
init_plugins() 会读取 enabled 字段决定是否加载，其余字段作为 plugin_configs 传入。
save_plugin_config() 会重新构建 Plugin 字典：先取 enabled_plugins 中的状态，再合并 plugin_configs。
Plugin.plugin_name 有 enabled 字段，由插件管理器控制。插件在 _save_config() 中请勿直接对整个字典赋值，以免覆盖 enabled 字段。应该使用 update.() 或使用一个单独的子字段保存配置。


#### 开发新插件

1. 在 plugin/ 下新建 .py 文件
2. 定义类继承 PluginBase，设置 description 属性
3. 实现 getAction() 返回菜单项
4. 在 init 中调用 super().init(main_window)，然后加载配置



#### 符号链接

此插件用于管理符号链接。

适用情况
1. C 盘空间紧张，要移动内容到 D 盘。
2. 想把软件数据放在同一个文件夹进行管理。

需要使用管理员权限。使用前建议备份一下文件夹。
注意！请不要对 C:\Windows 等系统文件夹使用！！！

使用说明

支持跨盘符移动，支持环境变量如 %UserProfile%

源路径的文件或文件夹 C:\Users\User\.ollama，
目标路径的文件或文件夹 C:\Tool\AppData\.ollama ，是操作完成后形成的路径，运行前，文件夹 C:\Tool\AppData\ 要存在，但内部不能存在 .ollama 同名文件或文件夹
注意要让源路径和目标路径末尾的文件或文件夹名相同。


几个按钮的具体行为如下

运行具体行为
C:\Users\User\.ollama 会被移动到 C:\Tool\AppData  内部，形成 C:\Tool\AppData\.ollama 的路径，然后在 C:\Users\User\.ollama 创建一个指向 C:\Tool\AppData\.ollama 的符号链接，从而达到访问 C:\Users\User\.ollama 实际上是访问 C:\Tool\AppData\.ollama 的效果。
符号链接的原理导致移动文件夹才能达到减小 C 盘或统一管理文件的目的。

在 C:\Tool\AppData 文件夹存在且内部没有 .ollama 文件夹的情况下，大致等效于下方的 cmd 命令。不过增加了校验和回退。
```
move C:\Users\User\.ollama C:\Tool\AppData
mklink /D C:\Users\User\.ollama C:\Tool\AppData\.ollama
```


恢复具体行为，删除源路径的符号链接，然后把目标路径的文件或文件夹移动回去，如果源路径不是符号链接，会提示是否将其删除然后覆盖内容。

测试具体行为，检测所有保存的符号链接路径，源路径是否存在，是符号链接还是文件、文件夹，目标路径是否存在，是符号链接还是文件、文件夹


#### OpenCC

此插件用于简体中文和繁体中文的互相转换。
需要额外安装 OpenCC 库，如果删除此插件并移除构建脚本内的 '--hidden-import=opencc', 可以使程序大小减少 5M。如果你要这么做，建议自己重新构建一遍。

#### OpenList

OpenList 是一个开源项目，此插件调用 OpenList 的 API 进行文件同步。
需要额外学习 OpenList 的使用。
https://github.com/OpenListTeam/OpenList/releases

##### 排除规则

排除规则是相对于源目录的，是相对路径，/ 和 \ 效果相同

举例，当前目录是 C:\Code ，想排除 C:\Code\SDK\Python
下面两种写法都可以
\SDK\Python\
/SDK/Python/


### 问题

已发现但尚未解决的问题包括：
1.
2.

### 计划

0. 在不增加新依赖的情况下尽可能扩展功能
1. 清理代码



可能会实现的功能有
1. 改用 Nuitka 打包
2. 图片质量比较、图片相似度比较
3. UI 界面配色自定义


#### 其他

语言文件在 O/src/lang/ ，第一个键 "翻译"，它的值表明这个文件对应的语言。

主题文件存放在 O/src/theme/ ，如果主题需要使用白色的最大化图标，将主题名加入 getMaxIcon 函数中的集合。


### 感谢名单

[OpenList](https://github.com/OpenListTeam/OpenList)


## English



