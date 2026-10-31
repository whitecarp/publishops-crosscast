<p align="center">
  <b>简体中文</b> · <a href="README_ZH_TW.md">繁體中文</a> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero.svg" alt="一句话，发往你选的所有平台：你对 AI 助手说一句，它写好各平台文案、适配封面，再逐个平台提交；示意发布单显示 Bilibili 和 YouTube 已核验，抖音已提交待核验，Instagram 发布中，X 排队中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  <b>一句话，发往你选的所有平台。</b><br>
  你的 AI 助手为每个平台写好标题和简介、适配好封面，再逐个平台提交，每一步都留下回执。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="版本 0.3.0 alpha">
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
> **0.3.0 alpha。** 实测记录来自[实验室运营版](https://github.com/whitecarp/agentic-work-lab/tree/main/tools/publishops-crosscast)（0.15.1）和作者的实际使用：YouTube、Instagram、X、抖音都在官方网页上真实发布并读回过；B站由作者直接用上游 bili-cli 上传过，本版的 B站可选路线调用的就是它。X 发布需要账号能进入 Media Studio。运营版的评论、私信、数据巡检等功能不在本版里，本版也不承诺五个平台全自动发布。

## 🚀 一句话开始

把下面这段话发给你的 AI 助手（Codex、Claude Code 这类能执行命令的助手都行），把 `final.mp4` 换成你的视频：

```text
请安装 https://github.com/whitecarp/publishops-crosscast
读取 skills/publishops-crosscast/SKILL.md，然后运行 vpub doctor。
把 final.mp4 公开发布到 B站、YouTube、抖音、Instagram 和 X，标题和简介按平台来写。
```

平台和公开范围可以按需修改。第一次登录各平台时，扫码、密码和验证码由你本人在官方页面完成。

<details>
<summary>想自己在终端里装？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 检查本机依赖
vpub capabilities  # 查看本机实际能走的发布方式
```

需要 Python 3.10–3.12。这两条命令都不会登录或发布。Windows 安装脚本和手动流程见[安装](#-安装)。

</details>

## 🎬 发一条视频是什么样

> **你**：把 final.mp4 公开发到 B站和 YouTube，标题口语一点。

| 步骤 | AI 助手和 vpub 在做什么 |
| --- | --- |
| ✍️ 自动写文案 | AI 助手自动写好：B站用简体中文，YouTube 用英文，并按视频内容选好分类；长度、标签自动检查，不夹带外链 |
| 🖼️ 自动配封面 | 自动按 B站 16:10、YouTube 16:9 适配，完整保留画面，原图不动 |
| 🧾 自动出方案 | 自动列清平台、账号、公开范围和 AI 声明，生成方案哈希 |
| 🔁 先预演 | `--dry-run` 走一遍，不碰平台 |
| 📮 逐个发 | YouTube：官方页面上传 → 读回到作品 → **已核验**<br>B站：等你扫码登录 → 上传完成 → **已提交，待核验** |

> **AI 助手**：YouTube 已发布，读回到了作品。B站已提交，等平台审核通过、读回到作品后，我再标成“已核验”。

<sub>流程示意，不是真实发布记录。</sub>

## ✨ 它能帮你做什么

### ✍️ 自动写好每个平台的文案

B站、抖音用简体中文，YouTube、Instagram、X 用英文；要中英双语时说一声。文案由你正在用的 AI 助手来写，vpub 给出写作要求并检查长度和格式，不内置模型，也不需要另配模型 API Key。YouTube 的分类按视频内容来选，不会一律塞进同一个分类。

### 🖼️ 封面自动适配各平台画布

按各平台的画布另存一份封面，完整保留画面，不裁切、不拉伸、不覆盖原图。平台预览仍可能裁切，提交前看一眼。发 Instagram 直接用原视频，不转码；竖版封面可以和抖音共用一张。

### 🧾 先出方案，再动手

每次发布先生成一份方案：平台、账号、公开范围和 AI 声明都写得清清楚楚，并算出一个方案哈希。视频、封面或文案一改，哈希就变，旧的确认自动作废。`--dry-run` 预演不会碰平台。

### 📮 逐个平台提交

默认由 AI 助手在官方网页上传。B站也可以走 bili-cli、YouTube 也可以走官方 API，由 CLI 直接提交，这两条路还支持定时发布。Instagram 和 X 可以只传到预览或存成草稿，不点“发布”。

### 🔍 每一步都有回执

每个平台的进度都叫它本来的名字：排队中 → 发布中 → 已提交，待核验 → 已核验。只有从平台页面或 API 读回到作品，才算“已核验”；排队、登录链接和本地回执都不等于发布成功。

### ✏️ 发出去还能改

走网页方式发出的作品，可以原地改标题、简介和标签，或者换封面，不用重新上传视频。

### 📧 需要你时发邮件提醒（可选）

平台要你扫码或验证时，可以发一封邮件提醒你。默认关闭；目前适配 Codex 的 Gmail / Outlook 发信工具。

## 🧭 平台支持

| 平台 | 默认方式 | 可选方式 | 实测记录 |
| --- | --- | --- | --- |
| **Bilibili** | AI 助手操作官方网页 | bili-cli，可定时发布 | ✅ bili-cli |
| **YouTube** | AI 助手操作官方网页 | 官方 API（自备 OAuth），可定时发布 | ✅ 网页、API |
| **抖音** | AI 助手协助官方网页 | 独立后端（实验功能） | ✅ 网页 |
| **Instagram** | AI 助手操作官方网页，可只存草稿 | — | ✅ 网页 |
| **X** | AI 助手操作官方网页，可只存草稿 | — | ✅ 网页 |
| **TikTok** | — | — | ⏸️ 暂停 |

“实测记录”来自实验室运营版的[验证文档](https://github.com/whitecarp/agentic-work-lab/blob/main/tools/publishops-crosscast/docs/VALIDATION.md)和作者的实际使用，是过去的真实发布，不代表本版重新验收过，也不代表在你的账号、地区和平台状态下一定成功。B站是作者直接用 bili-cli 上传的，本版经 vpub 调用这一步还没单独实测。`vpub capabilities` 目前只把 YouTube、Instagram、X 标为已实测，还没同步这份记录。

**发之前需要知道**

- **B站、抖音**：目前只能发声明为 AI 生成内容的视频；抖音的自定义封面只能走网页。
- **YouTube API**：未通过 Google 审核的 API 项目，上传的视频会被限制为私享。
- **Instagram**：网页和主页网格的封面裁切可能不同，有时要在手机上选封面。
- **X**：发布需要账号能进入 Media Studio（需要订阅）；未订阅账号的视频上传上限是 140 秒、512 MB。本工具不设置自定义封面。
- **TikTok**：暂停支持，CLI 会拒绝 TikTok 的登录和发布请求。

**通用边界**

- 安装成功不等于已登录或已有发布权限；本机实际能走哪条路，以 `vpub capabilities` 为准。
- 网页方式需要能操作浏览器的 AI 助手；没有这类助手时，只能准备素材和交接说明。
- 定时发布只在 B站 bili-cli（2 小时到 15 天内）和 YouTube API（需设为私密）两条路上可用；网页方式只支持立即发布。
- 目前没有评论、回复、私信、收件箱或创作者数据命令。完整边界见[平台说明](docs/PLATFORMS.md)。

## 🔒 安全护栏

用的是你自己的账号，所以这些规则默认就开着：

- **你说了算**：你说要发布就是授权，平台、账号和公开范围都按你说的来，不会因为某个平台已登录就顺手加上。内容、账号、平台、受众或任务范围变了，会再问你。
- **登录你自己来**：扫码、密码和验证码都在官方页面由你本人完成。不要把 Cookie、密码、验证码或令牌发进对话、日志或仓库。
- **不重复上传**：同一个视频在同一个平台账号只提交一次；结果不明时只核对原来的任务，不会再传一遍。
- **碰到风控就停**：遇到验证码、风控或限流，发布立刻停下，等你处理；用 `vpub stop` 也能随时手动暂停。
- **原素材不动**：适配后的封面另存为新文件，原视频和原图保持原样。

## 🤝 谁负责什么

- **你**：说清要发的平台、账号和公开范围，需要时扫码或输入验证码。这句话就是授权；范围变了会再问你。
- **AI 助手**：读取 `SKILL.md`，按平台写文案；有浏览器能力时，按你的授权操作官方网页。没有浏览器工具，就无法完成网页发布。
- **vpub**：生成并检查方案、适配封面、预演、保存本地状态与回执；不登录账号，也不替你确认内容。
- **平台**：负责账号登录、最终提交与公开；功能、格式限制和收费以当日官方页面为准。

## 📦 安装

需要 Python 3.10–3.12。Windows 安装脚本已经实测；macOS、Linux 尚未验收。

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
vpub capabilities
```

`doctor` 检查本机依赖，`capabilities` 显示实际后端与验证边界；两者都不会登录或发布。

<details>
<summary><b>想自己动手？看一眼手动流程</b></summary>

```sh
vpub init --video final.mp4 --language zh-Hans --out publish.json --platforms youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub plan localized.json
vpub publish localized.json --dry-run
```

`--dry-run` 不会上传。`response.json` 由 AI 助手按 `request.json` 中的模板撰写。每一步的含义、封面处理和草稿模式见[使用指南](docs/USAGE.md)。

</details>

## 💰 费用与隐私

- **模型**：CrossCast 本身不需要另购模型 API，文案由你正在用的 AI 助手完成。
- **可能的费用**：AI 助手的订阅、平台功能，以及可选的 YouTube API（需自备 Google OAuth，配额与审核由 Google 决定）可能另有费用或限制。本项目不承诺免费或无限使用。
- **本地数据**：账户会话、任务、设置和回执保存在本机应用数据目录，请不要提交到 GitHub。分享清单、截图或日志前，先移除账号标识、作品 ID、私有路径和凭证。
- **敏感信息**：不要在对话、日志或仓库中提供 Cookie、验证码、密码、令牌或 OAuth 文件。

## ❓ 常见问题

<details>
<summary><b>发布前还要我再确认吗？</b></summary>

不用。你说要发布，就是授权：它按你说的平台、账号和公开范围提交。只有内容、账号、平台、受众或任务范围变了，才会再问你。想先看一眼，就说“先别发”，或者用 `--dry-run` 预演。

</details>

<details>
<summary><b>能发不是 AI 生成的视频吗？</b></summary>

YouTube、Instagram 和 X 可以，按普通视频发布，不加 AI 标注。B站和抖音这一版只支持声明为 AI 生成内容的视频，普通视频在生成方案时会被拒绝。

</details>

<details>
<summary><b>只在终端里运行 vpub，能直接发出去吗？</b></summary>

默认的网页方式不能：CLI 没法操作浏览器，只能准备方案和交接说明，网页上的步骤要由能操作浏览器的 AI 助手完成。B站 bili-cli 和 YouTube API 两条路配置好之后，可以由 CLI 直接提交。

</details>

<details>
<summary><b>进了队列或有了回执，就算发出去了吗？</b></summary>

不算。排队只说明任务记下了；本地回执记录每一步的证据，但它本身不代替平台证据。有没有发布成功，以平台页面或 API 读回为准。

</details>

<details>
<summary><b>安装后就能操作五个平台吗？</b></summary>

不一定。每个平台都需要相应的账号状态、功能权限和 AI 助手的浏览器能力。请先看[平台说明](docs/PLATFORMS.md)。

</details>

<details>
<summary><b>要另买模型 API 吗？</b></summary>

不需要为 CrossCast 本身另买模型 API。只有选择 YouTube API 路径时，才需要自行配置 Google OAuth；其他费用见[费用与隐私](#-费用与隐私)。

</details>

<details>
<summary><b>提交结果不确定怎么办？</b></summary>

先核对同一个任务和平台页面，不要盲目重新上传。本地回执可以用 `vpub receipts` 查看，但它本身不代替平台证据。

</details>

## 📚 文档与鸣谢

| | |
| --- | --- |
| 📘 [使用指南](docs/USAGE.md) | 安装检查、发布方案、文案与封面、草稿与回执 |
| 🧭 [平台说明](docs/PLATFORMS.md) | 各平台路径、通用不支持项与重要边界 |
| 🌐 [在线介绍](https://whitecarp.github.io/publishops-crosscast/) | 项目概览页 |
| 🐞 [报告问题](https://github.com/whitecarp/publishops-crosscast/issues) | 问题反馈（请先移除账号与私密信息） |

来源鸣谢：[ai-video-lab](https://github.com/whitecarp/ai-video-lab)（开发来源与说明）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。第三方授权详见 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 许可证](LICENSE) 发布，是独立整合工具，并非任何视频平台的官方产品。
