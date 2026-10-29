<p align="center"><img src="assets/hero.svg" alt="PublishOps CrossCast" width="720"></p>

# PublishOps CrossCast

[简体中文](README.md) · [繁體中文](README_ZH_TW.md) · [English](README_EN.md)

**0.3.0 alpha** · Python 3.10–3.12 · [MIT License](LICENSE)

让 AI 助手把一支视频整理成适配多个平台的发布方案，并在你确认后协助完成官方网页发布。

**一句话开始：** 把下面这段发给你的 AI 编程助手：

> 安装 https://github.com/whitecarp/publishops-crosscast，读取 `skills/publishops-crosscast/SKILL.md`，运行 `vpub doctor`。先帮我准备草稿；由我选择平台、账号和公开范围，再继续。

```text
用户：先准备这支视频的发布草稿，不要发布。平台由我选。
AI：我会检查文件并生成方案。你确认平台、账号和公开范围后，我再打开对应官方页面交给你核对。
用户：选 YouTube，保持私密。先给我看标题和说明。
AI：草稿已准备。请核对文案与账号；当前还没有提交。
```

上面对话是**流程示意**，不是实际发布记录。CrossCast 默认先准备方案；CLI 不会替你确认内容，也不会把待办或登录链接说成已发布。没有可操作浏览器的宿主，无法完成需要网页交互的平台发布。

## 支持范围

- **Bilibili**：通过 `bili` 上游工具；CrossCast 不重实现其全部能力。
- **YouTube**：官方网页流程；可选 YouTube API，用于明确配置的能力。
- **Douyin**：官方网页流程；独立后端仍属实验阶段。
- **Instagram、X**：由宿主协助操作官方网页。可准备草稿；发布或修改需按流程授权。
- **TikTok**：当前暂停支持。

目前没有通用评论、私信、收件箱或创作者数据命令。细节见[平台说明](docs/PLATFORMS.md)。

## 安装

需要 Python 3.10–3.12。Windows 安装脚本已经实测；macOS、Linux 尚未验收。

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会打印 `vpub.exe` 的完整路径，但不会把它加入 PATH。可直接使用该路径，或在安装脚本建立的虚拟环境中运行命令。也可用 pip 从 Git 安装（需要 Git 和兼容 Python）：

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
```

## 常见问题

**要另买模型 API 吗？** 不需要为 CrossCast 本身另买模型 API。AI 文案由宿主对话模型完成。若选择 YouTube API，需自行配置 Google OAuth；宿主订阅、平台功能或可选 API 可能另有费用，本项目不承诺免费。

**它会自动发布吗？** 默认 `native` 网页流程需要可操作浏览器的宿主。配置并授权后，Bilibili CLI 或 YouTube API 路径可由 CLI 直接提交；具体后端以方案与 `vpub capabilities` 为准。没有浏览器工具的助手不能替你完成网页流程。

**安装后就能操作五个平台吗？** 不一定。各平台需要相应账号状态、功能权限和宿主浏览器能力；请先看[平台边界](docs/PLATFORMS.md)。

## 进阶

[使用指南](docs/USAGE.md) · [平台范围](docs/PLATFORMS.md) · [英文](README_EN.md) · [繁體中文](README_ZH_TW.md) · [在线介绍](https://whitecarp.github.io/publishops-crosscast/) · [报告问题](https://github.com/whitecarp/publishops-crosscast/issues)

来源鸣谢：[ai-video-lab](https://github.com/whitecarp/ai-video-lab)（开发来源与说明）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。详见[NOTICE](NOTICE.md)。
