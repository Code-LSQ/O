## 开源项目


[Sandboxie-Plus](https://github.com/sandboxie-plus/Sandboxie/releases)，这个开源沙盒软件可以说是轻量级的虚拟机了。
文档地址： https://sandboxie-plus.github.io/sandboxie-docs/zh-CN/    https://github.com/sandboxie-plus/sandboxie-docs/blob/main/README_zh-CN.md

官方介绍：Sandboxie 是一款基于沙箱的 Windows 隔离软件，可以让你在无需担心文件或注册表被未授权更改的情况下，运行不受信任的应用程序。

VMware是硬件级别的虚拟，Sandboxie-Plus是操作系统级别的虚拟。Sandboxie-Plus可以将用户所做的更改都隔离到一个虚拟环境中，与VMware不同的是，它只虚拟出运行程序需要用到的文件。

VMware的一个 Windows 10 虚拟机，在未精简的情况下占用18G到25G的存储空间，但是可能只安装了两三个流氓软件。而使用Sandboxie-Plus安装流氓软件，只需要软件本体和数据再加上几十到几百M的存储空间。CPU和内存占用，Sandboxie-Plus也要低于VMware的虚拟机（如果你是精简和调试系统的高手那就另说）。

主要用途是测试软件、安装运行环境、应用多开、安装国产流氓软件、玩游戏。不过我用Sandboxie-Plus主要是防止一些软件和游戏安装奇奇怪怪的东西和在我的文件夹里到处拉屎，同时保护我的注册表。

不过需要注意的是，Sandboxie-Plus的功能并没有VMware那么强大，有些软件在虚拟机中可以运行，到Sandboxie-Plus中会报错。而且Sandboxie-Plus也需要更强的解决问题的能力。

另外，Sandboxie-Plus支持便携式安装，只要复制即可使用



[OpenList](https://github.com/OpenListTeam/OpenList/releases)，集中管理云盘等多种存储的软件，可以免登录下载和上传文件，可以加密文件内容。另外很多人用 OpenList 的 WebDAV 来免下载看视频。

配合 [Rclone](https://github.com/rclone/rclone/releases) 可以挂载云盘为本地磁盘。详情可看 [OpenList Desktop](https://github.com/OpenListTeam/OpenList-Desktop/releases) 。


另外它可以跨网盘转移文件。但是嘛……从百度网盘复制到其他网盘的速度相当感人。同样，从其他网盘复制到115网盘也很感人，会受上传和下载速度的限制。

注：挂载 Google Drive 等云盘需要开虚拟网卡或写配置，否则连不上。


下载器，说实话单论功能我认为迅雷确实是最强的，就是太流氓，可以考虑去吾爱破解等论坛下绿色精简版，平时我用的更多的还是开源下载器。
[Ghost Downloader](https://github.com/XiaoYouChR/Ghost-Downloader-3/releases)，功能最多，支持磁力、ed2k、Youtube 视频下载、bilibili 视频下载、兼容 Aria2 RPC，更新频繁，内存占用适中。
[Motrix](https://github.com/agalwood/Motrix/releases)，很好用，外观也还行，可惜内存占用大，还停更了，内存溢出的bug没人修，支持磁力
[Gopeed](https://github.com/GopeedLab/gopeed/releases)，功能强大，外观不太行。


[LunaTranslator](https://github.com/HIllya51/LunaTranslator/releases)，一款翻译软件（视觉小说翻译器），可以用来学外语，主要被用来玩生肉（未翻译）游戏。主要通过 HOOK 来提取游戏文本，也可以通过 OCR 翻译。

[Venera](https://github.com/venera-app/venera/releases)，漫画聚合阅读软件，可惜停更了。

[Czkawka](https://github.com/qarmin/czkawka/releases)，重复文件查找软件，我用来清理重复和高相似度图片，Windows 系统建议下载那个 Krokiet-linux.exe ，这 exe 的命名有点无语……

[Dism++](https://github.com/Chuyu-Team/Dism-Multi-language/releases)，Windows 系统优化软件。
[Optimizer](https://github.com/hellzerg/optimizer/releases)，Windows 系统优化软件

[图吧工具箱WinUI3](https://github.com/luolangaga/tubatools/releases)，使用 C# 写的图吧工具箱 WinUI3 版本，页面更美观，功能更多更全。

[JSLinux](https://bellard.org/jslinux/index.html) 和 [v86](https://copy.sh/v86/)，浏览器里的 32 位 Linux 、Windows 2000 虚拟机。



## 加密备份


[OpenList](https://github.com/OpenListTeam/OpenList/releases)，文档地址   https://doc.oplist.org/

它可以集中管理很多网盘、加密文件内容以及跨网盘传输文件（有速率限制）。


你可以使用本项目尝试进行加密备份，具体流程之后补充，也可以使用以下方案。

TaoSync   https://github.com/dr34m-cn/taosync/releases

使用 OpenList + TaoSync + 中国移动云盘作为加密备份的解决方案。当然你可以用别的网盘，另外没会员的话，慎用115，上传失败概率最大。我是有中国移动的卡所以有2T个人云和2T家庭云。

具体的设置我感觉不用仔细写，OpenList 和 TaoSync 并不是非常难用。但是要像我这样做的话，先去仔细了解一下选项且看完文档。

首先用 OpenList 挂载中国移动云盘，注意个人云和家庭云的区别，家庭云建立敏感词文件夹会失败，如果你不想改文件夹名字，也不想开启文件夹名加密或混淆，那就不要使用家庭云！

然后将你想要同步的本地文件夹挂载为 OpenList 的本地存储，最好是一个，这样方便。如果你已经分散在多个文件夹且不好修改，也可以直接挂载整个磁盘，之后用文件夹过滤，或者使用 mklink 来改变目录结构，使其在同一文件夹。

然后添加 OpenList 的 Crypt 驱动的存储，这个看教程，注意路径别搞错了（注：如果你要用到搜索功能，我建议禁用 Crypt 的索引以及它所在网盘的索引，不禁用也行，但要想好了再搞，因为传输文件后更改 Crypt 配置会导致文件无法被解密）。

最后设置 TaoSync ，可以选择定时同步、手动同步、仅新增、全同步、过滤文件夹等设置，注意过滤要点添加才能生效。设置完了就可以开始尝试同步了。

完成后，建议使用一两个 TXT 文件测试一下，看看从 Crypt 的文件夹下载的和直接从云盘下载的是不是非加密和加密的。


说说我测试的过程吧，我先是使用 OpenList + Rclone，使用Rclone的加密存储，将本地文件加密备份到云盘。但是测试了115网盘、123云盘和中国移动云盘，在传输 2G及以上的文件时均总是失败，最终放弃。

然后我决定使用 OpenList 本身的 Crypt 的加密功能，再使用 TaoSync 来进行同步。我同样测试了 115网盘、123云盘和中国移动云盘，115网盘最慢，失败概率也最大，中国移动云盘速度最快，失败概率最低，123云盘速度和失败概率适中。（注意，样本少所以偶然性强，你可以自己测）


附上我的一键启动脚本和一键关闭脚本。软件和浏览器的路径改成自己的。另外我进入文件夹再启动是为了防止它在其他位置产生数据文件，直接用绝对路径启动会导致其数据在 C:\Users\Li 下。

```
@echo off

cd C:\Li\Tool\Cloud\OpenList
start /B openlist.exe server
start C:\Li\Tool\Extra\CentBrowser\chrome.exe http://127.0.0.1:5244
cd C:\Li\Tool\Cloud\TaoSync
start /B taoSync.exe
start C:\Li\Tool\Extra\CentBrowser\chrome.exe http://127.0.0.1:8023

echo 按任意键终止程序
pause

taskkill /f /im openlist.exe
taskkill /f /im taoSync.exe
echo 已终止程序
pause

```

如果你有加密同步的需求，也可以使用 Rclone、Duplicati、FileGee 。

Rclone 的缺点是比较难用，而且我用它不知道为什么会上传失败。本来我折腾了 Rclone 几天，最后白折腾了……

Duplicati，我没具体用过，支持的网盘和协议都还行，不过对国内的网盘，大部分需要用OpenList转成 WebDAV 再加密同步，我干脆选择了 TaoSync 。

FileGee，个人用户免费使用，其选项中有压缩为zip和添加密码，不过支持的网盘比较少，而且我没用过。支持的网盘有百度网盘、OneDrive、Dropbox、Goolge Drive等。如果有百度网盘会员或在国外，可以考虑使用。

另外的加密软件如 Cryptomator 这种，由于本身并非实时加密传输文件，需要额外的磁盘空间来存放加密后的文件，不做推荐。如果你本地磁盘空间很大也可以考虑。

另外使用 OpenList 和 Rclone，通过 WebDAV 可以将网盘挂载为本地磁盘。可以使用 https://github.com/OpenListTeam/OpenList-Desktop/releases  ，不过稳定性稍差。

暂时就写这么多，剩下的之后补。




## 杂类

### 压缩软件

推荐使用 [7-Zip](https://sparanoid.com/lab/7z/)、[7-Zip 增强版](https://github.com/mcmilk/7-Zip-zstd/releases)、[WinRAR](https://github.com/n2far2000/winrarsc) 。

个人不建议使用什么 360压缩、2345好压乃至其他要会员的压缩软件。谁懂在别人的电脑上看到压缩软件会员弹窗和它的支付二维码的力竭感。


### Qt6 中文异常

Qt6 中文显示异常，有锯齿，模糊不清。Windows 系统，且屏幕分辨率大于等于 2k 高概率发生。

在软件文件夹下找到存放 Qt6Core.dll 等文件的文件夹，如果有 qt.conf，用文本编辑器打开，直接在内容里加上

```
[Platforms]
WindowsArguments=fontengine=freetype
```

如果 qt.conf 本身已经有一个 [Platforms] ，只要在其下加上 WindowsArguments=fontengine=freetype 。

如果没有 qt.conf ，新建一个 qt.conf ，写上代码块的内容，注意保存为 UTF-8 。

如果是 PySide6 或 PyQt6 程序，`os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"` 或 `QFont.setHintingPreference(QFont.HintingPreference.PreferNoHinting)` 后者的副作用应该更小。


### 云盘

注意，我这里表达的看法仅代表个人体验，如果有不同意见还请谅解。

先说结论，如果你有大容量需求而对速度要求不高，不准备开会员，建议选择115网盘，淘宝买空间卡。

如果你不准备开会员，我推荐123云盘。在所有我用过的不开会员的网盘中，123云盘的速度是最快的，前提是配合油猴插件和脚本使用 https://github.com/QingJ01/123pan_unlock ，蓝奏云除外，不过它好像只适合分享，而且我没怎么用，不做讨论。

如果你要开会员，并且不在乎有没有永久空间的话，有磁力需求可以考虑115，否则还是选择百度网盘，虽然它是毒瘤，但是毕竟很多资源都在百度，最后我才推荐123云盘。


**各大网盘体验**

此处仅讨论国内适合使用的网盘。


**123云盘**

初始容量 2TB ，在现阶段冠绝群雄。

非会员下载速度几乎是最快的，也是目前我使用体验最好的云盘。

说起来，123云盘有个其他网盘数据转入，年费SVIP会员、3年及以上VIP会员和长期VIP会员可申请，有4TB固态或20TB机械的临时云桌面分配给你，用于暂存数据。

非会员网页端每日流量1GB，安卓客户端无限制，Windows客户端有限制。建议使用模拟器或油猴脚本。油猴脚本项目地址： https://github.com/QingJ01/123pan_unlock

文件夹自动备份需要会员。不过也可以用第三方软件搭配123云盘完成自动备份。



**115网盘**

初始容量 15GB 。如果你需要大容量永久空间，建议淘宝买空间卡，截止本文初次发布时，大约9元每TB永久空间。

Windows客户端是垃圾浏览器，很难用，非常垃圾。另外几个人反馈说客户端没有文件完整性校验，下载时有小概率损坏，上传则未知。网页端和安卓端会互相挤掉登录。鸿蒙端很久都不更新且功能极度缺失。网页端有文件上传大小限制，而我用第三方软件上传时失败了好几次。

Windows端似乎没有文件夹自动备份功能。安卓端有文件夹自动备份功能，这点在安卓上的云盘上倒是很少见。

我买过一个月的 115VIP， 它的磁力下载功能似乎是很强的。如果你有这方面的需求可以考虑入手。推荐价为一年会员100元或115元，多年会员价格低于100元每年。



**百度网盘**

初始容量 105GB，如果是早年注册的应该是 2TB，不过 365 天不登录就会变成 105GB。

这个网盘我相信不用多说，而且我不是深度使用者。但我是不会推荐。可以使用解析站或其他手段来下载。推荐项目： https://github.com/dongyubin/Baidu-VIP

资源最多，非会员限速在所有网盘里应该是最严重的（没有会员可以考虑去淘宝、拼多多买一日会员，然后拼命下）。另外，我认为它是客户端广告最严重的网盘。

文件夹自动备份功能应该有，但我未体验过。

对了，百度网盘Windows客户端有一个叫 YunDetectService.exe 的后台进程，关闭客户端后不会停止，有点流氓，因为它似乎没给你开启和关闭的选择。

**福利：**
百度500G空间，持续30天，每个月都能领
https://pan.baidu.com/comps/view/MV84NTZfMTAzMF8yODU2X29ubGluZQ==



**夸克网盘**

初始容量 10GB。值得一提的是，在淘宝买 VIP 充值卡比在客户端直接充 VIP 更便宜。可以签到领空间，但非会员连续签7天也只能得到 240MB，注意是 MB 。非会员一年签满大约 12GB。会员一年签满大约 120GB。我的评价是浪费时间。

支持文件夹自动备份，且能选择策略。

**福利：**

三个月VIP，进入夸克高考，随便填成绩，保存后即有。这个我领了，但是你看到这篇文章时可能已经不能领了。

三个月VIP，需要是大学生或教师，通过教育认证领取，发布时仍然可领。

这两个不冲突。



**阿里云盘**
初始容量 105GB。
其他内容我未深度体验。



**中国移动云盘**

初始容量个人云10GB，家庭云10GB。但是这两个说实话不能简单地相加。

个人感觉使用较为繁琐，说几个比较抽象的点，根目录的好几个文件夹无法删除，家庭云塞犄角旮旯里。家庭云有敏感词检测，无法创建一些文件夹和文件名（如色情相关）。不支持直接将个人云的文件上传到家庭云，要么分享，要么重新上传。家庭云可一键转存到个人云。另外同步盘和挂载盘我是用不明白，感觉可以整合和优化。

个人云文件夹自动备份选项少，家庭云不支持文件夹自动备份。

有的套餐赠送2T个人云和2T家庭云，如果不花钱，也还能用。签到也能换会员，大概，不过我不建议花时间去搞这个。


另外：123云盘、中国移动云盘，至少这两个网盘的安装包可以直接用7-Zip或其他压缩软件解压，然后再解压内部的 .7z 文件，免安装直接打开。建议放到 Sandboxie 里，再创建快捷方式。别的网盘我没试。

有一个算是隐性的属性，那就是有些网盘，在回收站内的内容不计算进总容量，如百度网盘，但有些网盘是算的如阿里云盘。对于不算的，可以使用回收站大法来回倒腾（即把回收站当成有期限的大容量存放空间）。


**备份**

为什么我上面谈到文件夹自动备份，因为数据安全不能寄希望于设备可靠性，得靠备份。

另外，如果担心隐私泄露，需要加密备份，上面也写了。
 
