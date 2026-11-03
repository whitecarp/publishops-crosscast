<div align="center">

<img src="assets/hero.svg" alt="发布流程概念示意：一句请求，经 AI 助手写文案、配封面后发往五个平台，附示例进度；不是真实发布记录" width="100%">

<br>

<a href="https://github.com/whitecarp/publishops-crosscast/releases"><img src="https://img.shields.io/badge/version-0.16.0%20alpha-f59e0b" alt="版本 0.16.0 alpha"></a> <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12"> <img src="https://img.shields.io/badge/Windows-%E5%B7%B2%E5%AE%9E%E6%B5%8B-555" alt="Windows 已实测"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 许可证"></a>

**简体中文** | [繁體中文](README_ZH_TW.md) | [English](README_EN.md)

</div>

## 💡 这是什么

做完一条视频，还要给每个平台写标题、改封面、一个个上传。**CrossCast 让你的 AI 助手替你做完这些**：你说一句，它写好文案、配好封面，发到 B站、YouTube、抖音、Instagram 和 X。发完之后，改稿、回评论、看数据也能交给它。

<p align="center"><img src="assets/one-message.png" alt="概念插图：一句请求经 AI 助手发往五个平台，不是真实发布记录" width="88%"></p>

> 🎬 五个平台 · 🤖 Codex、Claude Code 都能用 · 🔑 不用另配 API Key · 🛡️ 默认带护栏 · 🆓 免费开源

> **五个平台都有实发记录。** 当前仍是 0.16.0 alpha，有些功能还没在真实账号上试过。各版本与功能的验证范围见[使用说明](docs/GUIDE.md#-能做什么试到哪一步)。

## 🚀 一句话开始

**把下面这段话发给你的 AI 助手**，把 `final.mp4` 换成你的视频，它会自己装好，再带你登录：

```text
请安装 https://github.com/whitecarp/publishops-crosscast
读取 skills/publishops-crosscast/SKILL.md，然后运行 vpub doctor。
把 final.mp4 公开发布到 B站、YouTube、抖音、Instagram 和 X，标题和简介按平台来写。
```

每个平台第一次都要你自己扫码或输入验证码登录一次，之后就在后台发布。

<details>
<summary>或者自己在终端里装</summary>

```bash
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor              # 检查本机缺什么
vpub auth login douyin   # 每个平台登录一次
```

需要 Python 3.10–3.12 和 Google Chrome。Windows 已实测，macOS 和 Linux 尚未验收。完整步骤见[使用说明](docs/GUIDE.md#-安装)。

</details>

## 🎬 发一条视频是什么样

> **你**：把 final.mp4 公开发到这五个平台，标题口语一点。

```text
✍️  写文案    B站、抖音用简体中文，YouTube、Instagram、X 用英文；需翻译时原文在前
🖼️  配封面    按每个平台的尺寸配好，原图不动
🧾  出方案    发到哪、用哪个账号、谁能看，都列清楚
🔁  先预演    走一遍流程，不会真的发布
📮  发出去    后台一个个平台发布，在平台上看到作品才算成功
```

> **AI 助手**：五个平台都提交好了。已经在平台上看到作品的标成“已核验”，还在审核的，通过后我再更新。Instagram 的封面请在手机上换成那张竖版图。

<sub>流程示意，不是真实发布记录。</sub>

## ✨ 它能帮你做什么

### ✍️ 写好每个平台的文案
标题、简介和标签由你的 AI 助手写，CrossCast 检查。B站、抖音用简体中文，YouTube、Instagram、X 用英文；视频语言不同时，先保留原文，再附译文。中文内容在 B站、抖音只用简体。

### 🖼️ 配好封面
按每个平台的尺寸另存一份，不裁切、不拉伸，也不碰原图。抖音和 Instagram 要竖版封面，可以让 AI 助手画一张，也可以从原图重新排版。

### 📮 登录一次，之后后台发布
按平台支持定时发布（Instagram 除外）、存草稿或停在预览、带章节，上传时加入合集或播放列表。中断后可以恢复，但尚未提交的尝试可能需要重新上传。

### ✏️ 发完还能改
改标题、简介、封面和章节，各平台不一样。不是 CrossCast 发的旧作品，认领后也能改。

### 💬 回评论和私信
读评论、只用评论者的语言回复，看 @ 提醒，发单条私信，点赞和收藏，还能置顶、隐藏或删除评论。每次只做你说的那一条，不群发。

### 📊 看数据
看作品列表、播放和互动，也能一次看多个平台的新评论和数据变化。还能告诉你哪些事没做完、下一步做什么。

### 🎛️ B站互动组件
给自己的 B站视频加关联视频、关注按钮、投票和评分。

## 📺 支持的平台

- **B站**：用 B站 App 扫码登录
- **YouTube、抖音**：在 CrossCast 打开的 Chrome 窗口里登录一次
- **Instagram**：同样登录一次；封面要在手机上改，不能定时发布
- **X**：同样登录一次；账号需要已订阅，并能进入 Media Studio

TikTok 暂停支持。注意事项见[使用说明](docs/GUIDE.md#-平台支持)。

## 🆓 免费与付费

上面这些都免费，MIT 开源。付费附加组件还没开售，你现在用不到它；它只多出官方 API 发布、运营报告和邮件提醒这类进阶功能，[完整清单](docs/GUIDE.md#-免费与付费)在使用说明里。

## 🛡️ 安全护栏

用的是你自己的账号，所以默认都开着：

- ⏱️ **有上限**：回复、私信和点赞都有每日上限，不对同一个人重复相同的话，并标注 AI
- 🛑 **碰到风控就停**：平台要求验证、风控或限流时，所有平台都停下，等你处理
- 🔁 **不盲目重发**：提交结果不明时去平台上核对，不自动重新提交
- 🔑 **登录你自己来**：不要把 Cookie、密码或验证码发进对话
- 🖼️ **原素材不动**：封面另存为新文件，原视频和原图保持原样

## ❓ 常见问题

<details>
<summary>发布前还要我再确认吗？</summary>

不用。你说要发布，就是授权：它按你说的平台、账号和公开范围提交。只有内容、账号、平台、受众或任务范围变了，才会再问你。想先看一眼，就说“先别发”。

</details>

<details>
<summary>会不会被封号？</summary>

自动化总有风险。CrossCast 默认限制用量，碰到验证、风控或限流就停下，等你处理。平台也可能把它的专用浏览器当成新设备，要求你验证身份，这要你自己在官方页面完成。

</details>

<details>
<summary>现在能放心用于正式发布吗？</summary>

还在测试阶段。登录会过期，平台也可能临时要求验证，长时间无人值守还没验收。第一次用时，可以让 AI 助手先预演一遍，看过方案再正式发布。

</details>

更多问题见[使用说明](docs/GUIDE.md#-常见问题)。

## 📚 文档

这个仓库专注于打磨软件本身。项目的来历、研究和制作记录见 [agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)。

- [使用说明](docs/GUIDE.md)：每个功能试到哪一步、平台注意事项、安装步骤
- [功能范围](docs/FEATURES.md)：各平台能做什么
- [验证记录](docs/VALIDATION.md)：每个版本的真实测试结果
- [在线介绍](https://whitecarp.github.io/publishops-crosscast/)
- [报告问题](https://github.com/whitecarp/publishops-crosscast/issues)：请先移除账号与私密信息

来源鸣谢：[agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)（开发来源）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 功能，其代码已包含在内）。第三方授权详见 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 许可证](LICENSE) 发布，是独立整合工具，并非任何视频平台的官方产品。
