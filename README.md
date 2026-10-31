<p align="center">
  <b>简体中文</b> · <a href="README_ZH_TW.md">繁體中文</a> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero.svg" alt="一句话，发往你选的所有平台：你对 AI 助手说一句，它写好各平台文案、适配封面，再逐个平台提交；示意发布单显示 Bilibili 和 YouTube 已核验，抖音已提交待核验，Instagram 发布中，X 排队中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  一句话，发往你选的所有平台。<br>
  AI 助手写好各平台的标题、简介与封面，再逐个平台直接提交，每一步都有回执。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="版本 0.3.0 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 许可证"></a>
</p>

<p align="center">
  <a href="#交给-ai-助手">交给 AI 助手</a> ·
  <a href="#协作方式">协作方式</a> ·
  <a href="#平台范围">平台范围</a> ·
  <a href="#安装">安装</a> ·
  <a href="#费用与隐私">费用与隐私</a> ·
  <a href="#常见问题">常见问题</a> ·
  <a href="docs/USAGE.md">使用指南</a>
</p>

> [!IMPORTANT]
> **0.3.0 alpha。** 你说要发布，就是授权：CrossCast 按你说的平台、账号和公开范围逐个提交，但不承诺五个平台全自动发布。默认的网页流程需要一个能操作浏览器的 AI 宿主；没有这类宿主时，助手只能准备素材和交接说明。

## 交给 AI 助手

最简单的开始方式：把下面这段发给你的 AI 编程助手，并把 final.mp4 换成你的视频文件。

```text
请安装 https://github.com/whitecarp/publishops-crosscast
读取 skills/publishops-crosscast/SKILL.md，然后运行 vpub doctor。
把 final.mp4 公开发布到 B站、YouTube、抖音、Instagram 和 X，标题和简介按平台来写。
```

<details>
<summary><b>接下来大概会怎样？</b>（流程示意，不是实际发布记录）</summary>

```text
你：把 final.mp4 公开发到 B站和 YouTube，标题口语一点。
AI：好的。我会按平台写标题和简介、适配封面，然后逐个平台提交。B站需要你扫码登录一次。
你：扫好了。
AI：B站已提交，读回到作品，已核验；YouTube 已提交，正在核验。
```

CLI 不会把待办、登录链接或本地回执说成“已发布”。最终结果以平台页面读回或平台 API 为准。

</details>

## 协作方式

一次发布涉及四个角色，各自的边界是固定的：

| 角色 | 负责什么 | 边界 |
| --- | --- | --- |
| **你** | 说一句要发的平台、账号与公开范围；需要时扫码或输入验证码 | 这句话就是授权；范围变了会再问你 |
| **AI 宿主** | 读取 `SKILL.md`，按平台撰写标题、说明和标签；有浏览器能力时按你的授权操作官方网页 | 没有浏览器工具时，无法完成网页发布 |
| **`vpub` CLI** | 生成并检查发布方案、适配封面画布、`--dry-run` 预演、保存本地状态与回执 | 不执行浏览器登录，也不替你确认内容 |
| **官方平台** | 账号登录、最终提交与公开 | 平台功能、格式限制与收费以当日官方页面为准 |

已有授权只在原范围内沿用；内容、账号、平台、受众或任务范围发生变化时，会重新请你确认。

## 平台范围

| 平台 | 默认路径 | 可选路径 | 状态 |
| --- | --- | --- | --- |
| **Bilibili** | 宿主操作官方网页 | 上游 `bili` CLI | 可用 |
| **YouTube** | 官方网页交接 | YouTube Data API（需自行配置 OAuth） | 可用 |
| **Douyin** | 宿主协助官方网页 | 独立后端 | 网页可用；独立后端为实验功能 |
| **Instagram** | 宿主操作官方网页，可只存草稿 | 无独立 API | 可用 |
| **X** | 宿主操作官方网页，可只存草稿 | 无独立 API | 可用 |
| **TikTok** | — | — | 暂停支持，CLI 会拒绝登录和发布请求 |

安装成功不等于已登录或已获发布权限，实际后端请用 `vpub capabilities` 查看。目前**没有**通用的评论、回复、私信、收件箱或创作者数据命令。完整边界见[平台说明](docs/PLATFORMS.md)。

## 安装

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

`--dry-run` 不会上传。`response.json` 由宿主模型按 `request.json` 中的模板撰写。每一步的含义、封面处理和草稿模式见[使用指南](docs/USAGE.md)。

</details>

## 费用与隐私

- **模型**：CrossCast 本身不需要另购模型 API，文案由宿主对话模型完成。
- **可能的费用**：宿主订阅、平台功能，以及可选的 YouTube API（需自备 Google OAuth，配额与审核由 Google 决定）可能另有费用或限制。本项目不承诺免费或无限使用。
- **本地数据**：账户会话、任务、设置和回执保存在本机应用数据目录，请不要提交到 GitHub。分享清单、截图或日志前，先移除账号标识、作品 ID、私有路径和凭证。
- **敏感信息**：不要在对话、日志或仓库中提供 Cookie、验证码、密码、令牌或 OAuth 文件。

## 常见问题

<details>
<summary><b>它会自动发布吗？</b></summary>

默认 `native` 网页流程需要能操作浏览器的宿主；你说要发布，它就按授权逐个平台提交。配置并授权后，Bilibili CLI 或 YouTube API 路径可由 CLI 直接提交；具体后端以方案与 `vpub capabilities` 为准。

</details>

<details>
<summary><b>安装后就能操作五个平台吗？</b></summary>

不一定。每个平台都需要相应的账号状态、功能权限和宿主浏览器能力。请先看[平台边界](docs/PLATFORMS.md)。

</details>

<details>
<summary><b>要另买模型 API 吗？</b></summary>

不需要为 CrossCast 本身另买模型 API。只有选择 YouTube API 路径时，才需要自行配置 Google OAuth；其他费用见[费用与隐私](#费用与隐私)。

</details>

<details>
<summary><b>提交结果不确定怎么办？</b></summary>

先核对同一任务和平台页面，不要盲目重试上传。本地回执可用 `vpub receipts` 查看，但它本身不代替平台证据。

</details>

## 文档与鸣谢

| | |
| --- | --- |
| 📘 [使用指南](docs/USAGE.md) | 安装检查、发布方案、文案与封面、草稿与回执 |
| 🧭 [平台范围](docs/PLATFORMS.md) | 各平台路径、通用不支持项与重要边界 |
| 🌐 [在线介绍](https://whitecarp.github.io/publishops-crosscast/) | 项目概览页 |
| 🐞 [报告问题](https://github.com/whitecarp/publishops-crosscast/issues) | 问题反馈（请先移除账号与私密信息） |

来源鸣谢：[ai-video-lab](https://github.com/whitecarp/ai-video-lab)（开发来源与说明）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。第三方授权详见 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 许可证](LICENSE) 发布，是独立整合工具，并非任何视频平台的官方产品。
