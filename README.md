<p align="center">
  <b>简体中文</b> · <a href="README_ZH_TW.md">繁體中文</a> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero.svg" alt="一句话，发往你选的所有平台：你对 AI 助手说一句，它写好各平台文案、适配封面，再逐个平台提交；示意发布单显示 Bilibili 和 YouTube 已核验，抖音已提交待核验，Instagram 发布中，X 排队中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  <b>一句话，发往你选的所有平台。</b><br>
  你的 AI 助手为每个平台写好标题和简介、适配好封面；首次登录之后，CLI 在后台逐个平台提交，每一步都留下回执。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.15.1%20alpha-f59e0b" alt="版本 0.15.1 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <img src="https://img.shields.io/badge/Windows-%E5%B7%B2%E5%AE%9E%E6%B5%8B-555" alt="Windows 已实测">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 许可证"></a>
</p>

<p align="center">
  <a href="#-一句话开始">一句话开始</a> ·
  <a href="#-它能帮你做什么">能做什么</a> ·
  <a href="#-平台支持">平台支持</a> ·
  <a href="#-安全护栏">安全护栏</a> ·
  <a href="#-安装">安装</a> ·
  <a href="#-常见问题">常见问题</a> ·
  <a href="https://whitecarp.github.io/publishops-crosscast/">在线介绍</a>
</p>

> [!IMPORTANT]
> **0.15.1 alpha。** YouTube、抖音、Instagram、X 都已真实发布并读回过，B站也用 bili-cli 上传并核验过，详见[验证记录](docs/VALIDATION.md)。它仍是 alpha：登录会过期，平台也可能临时要求验证，长时间无人值守还没验收。

## 🚀 一句话开始

把下面这段话发给你的 AI 助手（Codex、Claude Code 这类能执行命令的助手都行），把 `final.mp4` 换成你的视频：

```text
请安装 https://github.com/whitecarp/publishops-crosscast
读取 skills/publishops-crosscast/SKILL.md，然后运行 vpub doctor。
把 final.mp4 公开发布到 B站、YouTube、抖音、Instagram 和 X，标题和简介按平台来写。
```

**开始前准备好这几样：**

- **B站**：用 B站 App 扫码登录一次（通过 bili-cli）。
- **YouTube**：CLI 走官方 API；没配置时，能操作浏览器的 AI 助手可以在 YouTube Studio 上发布。YouTube 没有 CLI 后台浏览器路径。在 Google Cloud 启用 YouTube Data API v3，建一个“桌面应用”OAuth 客户端，把下载的 JSON 交给 AI 助手。
- **抖音、Instagram、X**：第一次会打开 CrossCast 专用的官方 Chrome 窗口，你自己扫码或输入密码、验证码；之后 CLI 在后台复用这个登录。
- **X 浏览器路线**：只能用 Media Studio 发布，账号必须已订阅并能进入 Media Studio。
- **电脑上要有 Google Chrome**：没有的话，运行 `vpub setup --install-browser` 安装。

<details>
<summary>想自己在终端里装？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 检查本机依赖
vpub capabilities  # 查看本机实际能走的发布方式
```

需要 Python 3.10–3.12。这两条命令都不会登录或发布。登录和手动流程见[安装](#-安装)。

</details>

## 🎬 发一条视频是什么样

> **你**：把 final.mp4 公开发到这五个平台，标题口语一点。

| 步骤 | AI 助手和 vpub 在做什么 |
| --- | --- |
| ✍️ 自动写文案 | B站、抖音写简体中文，YouTube、Instagram、X 写英文<br>YouTube 按视频内容选好分类；长度、标签都检查过，不夹带外链 |
| 🖼️ 自动配封面 | 横版：B站 16:10，YouTube、X 16:9<br>竖版：AI 助手自动生成一张竖版图，抖音和 Instagram 共用；视频保持原样 |
| 🧾 自动出方案 | 列清平台、账号、公开范围和 AI 声明，生成确认码 |
| 🔁 先预演 | `--dry-run` 走一遍，不碰平台 |
| 📮 后台提交 | 登录后自动在后台提交视频 |

> **AI 助手**：五个平台都提交好了。已经读回到作品的标成“已核验”，还在审核的，通过后我再更新。Instagram 的封面请在手机上换成那张竖版图。

<sub>流程示意，不是真实发布记录。</sub>

## ✨ 它能帮你做什么

### ✍️ 自动写好每个平台的文案

B站、抖音用简体中文，YouTube、Instagram、X 用英文。视频本身是别的语言时，先保留原文，再附上平台语言的译文；中文内容在 B站、抖音只写简体。文案由你正在用的 AI 助手来写，vpub 给出写作要求并检查长度和格式，不内置模型，也不需要另配模型 API Key。YouTube 的分类按视频内容来选。

### 🖼️ 封面自动适配各平台画布

按各平台画布另存封面，完整保留画面，不裁切、不拉伸、不覆盖原图：B站 16:10，YouTube 16:9，X 通过 Media Studio 设置横版封面。抖音和 Instagram 的竖版封面，由 AI 助手（如 Codex）自动生成一张专用竖版图，两边共用，不会拿横版图加黑边凑数。视频本身不转竖版，保留原比例。Instagram 的封面要在手机上换。

### 🧾 先出方案，再动手

每次发布先生成一份方案：平台、账号、公开范围和 AI 声明都写得清清楚楚，并生成一个确认码。视频、封面或文案一改，确认码就变，旧的确认自动作废，保证发出去的就是你确认过的那一版。`--dry-run` 预演不会碰平台。

### 📮 登录一次，之后后台发布

首次登录之后，CLI 自己完成上传：B站走 bili-cli，YouTube 走官方 API，抖音、Instagram、X 由 CLI 在后台操作专用的 Chrome。B站和 YouTube 还支持定时发布。

### 🔁 中断了也能接着来

上传途中断开，`vpub resume` 接着原来的任务继续：YouTube 从同一个上传会话续传，X 接着已经存好的 Studio 素材。结果不明的提交不会自动再点一次。

### 🔍 每一步都有回执

每个平台的进度都叫它本来的名字：排队中 → 发布中 → 已提交，待核验 → 已核验。只有从平台页面或 API 读回到作品，才算“已核验”；排队、登录链接和本地回执都不等于发布成功。

### ✏️ 发出去还能改

`vpub edit` 原地修改已发布的作品：YouTube 的标题和简介、Instagram 的配文、抖音的竖版封面和章节，X 在出现编辑入口时也能改。不换视频，不改公开范围。

### 💬 评论、点赞和私信

读评论、用评论者的语言回复，点赞、收藏，在抖音、Instagram、X 上读写私信，查看 @ 提醒。每次只做你授权的那一条，不会自动群发。各平台能做的不一样，详见[功能范围](docs/FEATURES.md)。

### 📊 数据和运营汇报

查看单条作品的播放和互动，巡检多个平台的新评论和数据变化，生成一份运营汇报。

### 📧 需要你时发邮件提醒（可选）

平台要你扫码或验证时，可以给你发一封提醒邮件。只支持 Gmail，默认关闭，你主动要求时才开启。

## 🧭 平台支持

| 平台 | 登录 | 发布方式 | 实测记录 |
| --- | --- | --- | --- |
| **Bilibili** | B站 App 扫码（bili-cli） | bili-cli，可定时发布 | ✅ bili-cli |
| **YouTube** | Google 桌面 OAuth 客户端 | 默认走官方 API，可定时发布；也可由 AI 助手操作 YouTube Studio | ✅ API |
| **抖音** | 专用官方 Chrome | CLI 后台浏览器 | ✅ 已发布 |
| **Instagram** | 专用官方 Chrome | CLI 后台浏览器，封面在手机上改 | ✅ 已发布 |
| **X** | 专用官方 Chrome | 只能用 Media Studio（需订阅） | ✅ 已发布 |
| **TikTok** | — | — | ⏸️ 暂停 |

实测记录见[验证记录](docs/VALIDATION.md)。B站是作者直接用 bili-cli 上传的，`vpub capabilities` 里 B站仍标为未实测。这些是过去的真实发布，不代表在你的账号、地区和平台状态下一定成功。

**发之前需要知道**

- **YouTube**：CLI 走官方 API；没配置时，能操作浏览器的 AI 助手可以在 YouTube Studio 上发布。YouTube 没有 CLI 后台浏览器路径。项目还在测试模式时，要先把登录用的账号加进“测试用户”；按 Google 的规定，2020 年 7 月 28 日之后创建、又没通过审核的 API 项目，上传的视频会被锁成私享。
- **X 浏览器路线**：只能用 Media Studio。账号必须已订阅并能进入 Media Studio；未订阅账号的视频上限是 140 秒、512 MB，本工具不支持。
- **Instagram**：视频保留原比例；封面必须在手机上改：打开作品的“编辑封面 → 从相册添加”，选和抖音共用的那张竖版图，再看一眼主页网格的裁切。
- **抖音**：长时间保持登录的稳定性还在验收；网页上传中断后不能续传，要先检查已有草稿。
- **还没有的**：TikTok（暂停）、YouTube 卡片和可点击片尾、自动或批量私信与点赞、完整历史导出。

## 可选 X／Instagram API 路线（实验性）

API 代码维持 MIT 开源，包括 `api_backends.py` 和测试。CrossCast 不设置许可证密钥或付费解锁；平台开发者权限、审核与费用另计。

X／Instagram 默认仍使用已登录的后台浏览器。新增 API 上传路线**目前只通过离线故障注入测试，尚未完成真实账号授权和上传验收**；既有发布实测指的是浏览器路线。API 目前涵盖授权、上传、发布和读回，评论、收藏及私信命令保留原有后端。

- **X API：**创建支持 OAuth 2.0 用户授权的开发者应用，启用所需媒体及帖子权限。把 `client_id`、适用时的 `client_secret` 保存到私人 JSON。开发者 API 权限与计费不同于普通订阅及 Media Studio 权限。
- **Instagram API：**此实现使用 Facebook Login for Business，需要关联 Facebook 公共主页的 Instagram 专业账号。把 `app_id`、`app_secret` 及可选的 `ig_user_id` 保存到私人 JSON；权限及审核取决于 Meta 应用设置。
- 在应用登记 `http://127.0.0.1:8765/callback`，执行对应命令并自行完成官方授权，再在方案对应平台指定 `"method": "api"`。凭证与令牌只保存在私人目录。

```sh
vpub auth login x --method api --client-json /private/x-client.json
vpub auth login instagram --method api --client-json /private/instagram-client.json
```

这两条 API 路线尚不应用自定义封面、定时发布或草稿。X 处理等待有上限，恢复时检查原媒体。Instagram 已完成的容器不重传；中断后如果服务端未返回确认的偏移量就停止，实际偏移量行为仍待账号实测。发布结果不明确时，多次恢复也不重发；取得作品 ID 后先落盘再读回。账号改变会停止原任务。

协议参考：[X 分段上传](https://docs.x.com/x-api/media/quickstart/media-upload-chunked)、[Meta 内容发布](https://developers.facebook.com/docs/instagram-platform/content-publishing/)。

## 🔒 安全护栏

用的是你自己的账号，所以这些规则默认就开着：

- **你说了算**：你说要发布就是授权，平台、账号和公开范围都按你说的来，不会因为某个平台已登录就顺手加上。内容、账号、平台、受众或任务范围变了，会再问你。
- **登录你自己来**：扫码、密码和验证码都在官方页面由你本人完成。不要把 Cookie、密码、验证码或令牌发进对话、日志或仓库。
- **不重复上传**：同一个视频在同一个平台账号只提交一次；结果不明时只读回核对，不会再传一遍。
- **碰到风控就停**：遇到验证码、风控或限流，发布立刻停下，等你处理。
- **一次只做一件事**：同一时间只有一个发布任务在跑；评论和私信每次只发你授权的那一条。
- **原素材不动**：适配后的封面另存为新文件，原视频和原图保持原样。

## 🤝 谁负责什么

- **你**：说清要发的平台、账号和公开范围；第一次登录时扫码或输入验证码，在手机上改 Instagram 封面。这句话就是授权；范围变了会再问你。
- **AI 助手**：读取 `SKILL.md`，按平台写文案和回复，生成竖版封面图，替你运行 vpub。
- **vpub**：生成并检查方案、适配封面，首次登录后在后台调用 bili-cli、官方 API 和专用 Chrome 完成提交，保存回执并读回核对；不替你登录，也不替你确认内容。
- **平台**：负责账号登录、审核、最终发布与公开；功能、格式限制和收费以当日官方页面为准。

## 📦 安装

需要 Python 3.10–3.12 和 Google Chrome。Windows 安装脚本已经实测；macOS、Linux 尚未验收。

**Windows 安装脚本**

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会打印 `vpub.exe` 的完整路径，但不会把它加入 PATH。可以直接使用该路径，或在脚本建立的虚拟环境中运行命令。

**通过 pip 从 Git 安装**（需要 Git 和兼容的 Python）

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
```

**登录各平台**

```sh
vpub auth login youtube --method api --client-json /path/to/client.json
vpub auth login douyin
vpub auth login instagram
vpub auth login x
vpub auth status all
```

YouTube 的 OAuth 客户端 JSON 和登录令牌只留在本机，不要提交到 GitHub。

<details>
<summary><b>想自己动手？看一眼手动流程</b></summary>

```sh
vpub init --video final.mp4 --language zh-Hans --out publish.json --platforms bilibili,douyin,youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub covers prepare localized.json --source cover.jpg --portrait portrait.jpg --out-dir covers --out ready.json
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm <确认码>
vpub verify RECEIPT_KEY
```

`--dry-run` 不会上传。`response.json` 由 AI 助手按 `request.json` 中的模板撰写。中断后用 `vpub resume RECEIPT_KEY` 接着做，用 `vpub receipts` 查看所有回执。

</details>

## 💰 费用与隐私

- **模型**：CrossCast 本身不需要另购模型 API，文案由你正在用的 AI 助手完成。
- **可能的费用**：AI 助手的订阅、X 的订阅（发布要用 Media Studio），以及 YouTube API 的配额（由 Google 决定）。本项目不承诺免费或无限使用。
- **本地数据**：登录会话、任务、设置和回执都保存在本机应用数据目录，请不要提交到 GitHub。分享清单、截图或日志前，先移除账号标识、作品 ID、私有路径和凭证。
- **敏感信息**：不要在对话、日志或仓库中提供 Cookie、验证码、密码、令牌或 OAuth 文件。

## ❓ 常见问题

<details>
<summary><b>发布前还要我再确认吗？</b></summary>

不用。你说要发布，就是授权：它按你说的平台、账号和公开范围提交。只有内容、账号、平台、受众或任务范围变了，才会再问你。想先看一眼，就说“先别发”，或者用 `--dry-run` 预演。

</details>

<details>
<summary><b>为什么 YouTube 一定要配 OAuth 客户端？</b></summary>

CLI 通过官方 API 发布到 YouTube，而 API Key 本身不能授权上传，必须用“桌面应用”OAuth 客户端在 Google 页面授权一次。授权在你自己的电脑上完成，令牌只留在本机。

</details>

<details>
<summary><b>X 没订阅能发吗？</b></summary>

不能。默认浏览器路线只通过 Media Studio 发布 X，账号必须已订阅并能进入 Media Studio。付了费或登录了，不代表 Media Studio 已经开通，上传前会先核对。

</details>

<details>
<summary><b>Instagram 的封面为什么要在手机上改？</b></summary>

实测下来，要在手机 App 里“编辑封面 → 从相册添加”才能换上和抖音共用的竖版图。AI 助手会准备好这张图，手机上的操作由你完成。

</details>

<details>
<summary><b>能发不是 AI 生成的视频吗？</b></summary>

能。告诉 AI 助手视频是不是 AI 生成的：是的话，各平台都会勾上 AI 生成内容声明；不是的话，就按普通视频发布，不加 AI 标注。

</details>

<details>
<summary><b>只在终端里运行 vpub，能直接发出去吗？</b></summary>

能。首次登录之后，CLI 自己在后台完成上传，不需要 AI 助手一步步操作浏览器。登录过期或平台要求验证时，需要你回到官方页面处理。

</details>

<details>
<summary><b>提交结果不确定怎么办？</b></summary>

用 `vpub verify` 读回核对，或用 `vpub resume` 接着原来的任务，不要盲目重新上传。本地回执可以用 `vpub receipts` 查看，但它本身不代替平台证据。

</details>

## 📚 文档与鸣谢

| | |
| --- | --- |
| 🧭 [功能范围](docs/FEATURES.md) | 各平台能做什么、用什么方式做 |
| ✅ [验证记录](docs/VALIDATION.md) | 每个版本的真实发布与测试结果 |
| 🐦 [X Media Studio 流程](docs/X_GUIDE.md) | X 的上传、封面与发布步骤 |
| 🌐 [在线介绍](https://whitecarp.github.io/publishops-crosscast/) | 项目概览页 |
| 🐞 [报告问题](https://github.com/whitecarp/publishops-crosscast/issues) | 问题反馈（请先移除账号与私密信息） |

来源鸣谢：[agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)（开发来源）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。第三方授权详见 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 许可证](LICENSE) 发布，是独立整合工具，并非任何视频平台的官方产品。
