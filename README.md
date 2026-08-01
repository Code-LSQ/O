# O

## [中文](README.md#简体中文) | [English](README.md#English)

## 简体中文

### 项目简介


文档正在完善中……如果没找到想找的内容可以过几天再来。

O 是一个带了一些奇怪功能的快速启动器，使用 PySide6 框架，支持插件扩展。O 其实没有任何含义，仅仅是项目需要一个名字，所以在这里出现，因为我是取名废。你可以理解为 `Open`（开放），`Operation`（操作） 或 `Otiose`（无用）或者其他任何含义。

用户自定义插件（.py）和主题文件（.qss），建议放到 data/user 文件夹下，这里是给用户预留的自定义文件夹。虽然在 src 文件夹可以自定义但在更新后就会覆盖掉，但在更新时会删除 data 以外的文件和文件夹，因此不建议这么做。


当前支持平台为 Windows 8.1+。

Windows 上的启动器已经有很多轮子了，Quicker、uTools、Maye Lite、Rolan、Lucy、Tiny、Flow Launcher。

Maye Lite，我有时候无法启动成功。
uTools，需要安装且会自动更新，同时稍显笨重。
Dawn Launcher，用 Electron ，速度比较慢，资源占用比较大。
Flow Launcher，速度比较慢。
Lucy，不支持高分辨率屏幕。
Quicker 是会员制软件，虽然很好用但免费版翻页麻烦而且图标功能实在不便。
Tiny，软件大小只有 10M，内存占用也小，不过有些细节我不喜欢。

它们都是好软件，不过我都有点用不习惯，最后造了一个自己的轮子。如果你认为这个软件很不好用但又需要类似软件，可以去看看。


提醒您，这是一个非常丑陋、抽象、无语的项目，作者大部分时间毫无计划地想到什么加什么，因为这个项目主要是给作者个人使用的。

Github 上的项目，总会有人尝尝咸淡。感谢您的尝试与奉献。


### 启动器

#### 启动器

程序的主要功能为收纳快捷方式，从而打开 exe、打开文件夹、设置 Python 和 Java 路径后运行 .py 和 .jar、打开网址等。
另外在打包为 exe 程序后，如果没有配置 Python 路径，将会尝试通过程序自身的 Python 解释器运行 .py 脚本，当然，这不是很稳定。
路径支持环境变量如 %UserProfile%
图标可以填一个 exe 文件的路径，会获取它的图标。


#### 环境变量

在设置->环境变量中
C:\SDK\MinGW\   将此文件夹加入 Path
ANDROID_HOME=C:\Android\SDK  将其加入环境变量
这是针对所有项目的环境变量

如果要针对单个程序
使用形如 {env: CLAUDE_CODE_NO_FLICKER=1}  ，每个 {env: } 内填入一个环境变量，用空格` `分隔多个 {env: }。
对于一些程序，可以通过此功能达到修改程序数据目录的效果，如 {enc: AppData=C:\data} 。


#### 进程/服务管理

填写规范，用 | 分隔，有空格要用 "" 包裹，有白名单，免得误操作给系统搞崩溃了。

可以使用资源监视器搜索指定软件有哪些流氓进程，打开方式为 任务管理器->性能->运行新任务右边->资源监视器->句柄搜索

此功能仅针对 .exe 程序，需要管理员权限。适用于百度网盘、WPS等软件的流氓进程，还有 VMware 这种关闭后有好几个服务在运行的。
在用户启动主进程后，设置一个定时器，十秒检查一次主进程状态。

进程管理
检测到用户结束主进程后，结束其附属进程

服务管理
在启动主进程时，先检查附属服务状态，如果不是手动启用，设置其为手动启用，然后启动附属服务。检测到用户结束主进程后，停止附属服务。


格式，使用 " | " 来对各个服务、进程进行分隔，如果服务名有空格应该用 "" 包裹

这里列举一些例子，同时也欢迎各位反馈。
VMware：服务 VMAuthdService | VMnetDHCP | VMUSBArbService | "VMware NAT Service" ，可能还有一个托盘进程
百度网盘：进程 YunDetectService.exe

如果你需要更加强大的处理流氓软件的功能，可以使用 [Sandboxie](https://github.com/sandboxie-plus/Sandboxie/releases)，可以隔离文件和进程到虚拟环境中。



### 插件

#### 插件规范

插件路径为 src/plugin/ ，用户自定义插件路径为 data/user/，其内的每个 .py、.pyd 文件都是一个插件


插件系统
PluginBase（基类） — 定义在 src/plugin.py，所有插件必须继承此类

getAction()，控制插件返回的按钮

如果有需要额外导入的 Python 库依赖，在 .py 文件中用 PluginLib 列表标明。


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


开发新插件

1. 在 plugin/ 下新建 .py 文件
2. 定义类继承 PluginBase，设置 description 属性
3. 实现 getAction() 返回菜单项


#### 工具箱



#### AI

为什么有 AI 功能呢？因为这个项目也是我的毕业设计，虽然后来大改了好几次，还是保留了这部分功能，不过我个人是不喜欢什么都加 AI 的。事实上本项目的 AI 功能也称得上比较难用，只不过调用起来比较方便。

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


### Windows 设置

右键->添加预设项->系统 中有一些 Windows 特定功能

除此之外你可能需要的 Windows 路径
控制面板  C:\Windows\System32\control.exe
注册表编辑器  C:\Windows\regedit.exe
服务  C:\Windows\System32\services.msc
PowerShell C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
Windows 功能  C:\Windows\System32\OptionalFeatures.exe
事件查看器  C:\Windows\System32\eventvwr.msc
资源监视器  C:\Windows\System32\perfmon.exe
系统信息  C:\Windows\System32\msinfo32.exe
系统配置  C:\Windows\System32\msconfig.exe
颜色管理  C:\Windows\System32\colorcpl.exe
计算机管理  C:\Windows\System32\compmgmt.msc
组策略  C:\Windows\System32\gpedit.msc
文件资源管理器  C:\Windows\explorer.exe
任务管理器  C:\Windows\System32\Taskmgr.exe
任务计划程序  C:\Windows\System32\taskschd.msc
系统属性  C:\Windows\System32\SystemPropertiesComputerName.exe
性能选项  C:\Windows\System32\SystemPropertiesPerformance.exe
Windows 工具  C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Administrative Tools
防火墙  C:\Windows\System32\WF.msc
用户账户控制设置  C:\Windows\System32\UserAccountControlSettings.exe
本地安全策略  C:\Windows\System32\secpol.msc
实时字幕  C:\Windows\System32\LiveCaptions.exe



### 问题

已发现但尚未解决的问题包括：
1.
2.


### 计划

0. 在不增加新依赖的情况下尽可能扩展功能
1. 清理代码
2. 优化我丑陋的 UI



可能会做的事有
1. 改用 Nuitka 打包
2. 图片质量比较、图片相似度比较
3. UI 界面配色自定义



### 感谢名单

[OpenList](https://github.com/OpenListTeam/OpenList)


## English



