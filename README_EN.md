<p align="center"><img src="assets/hero.svg" alt="PublishOps CrossCast" width="720"></p>

# PublishOps CrossCast

[简体中文](README.md) · [繁體中文](README_ZH_TW.md) · [English](README_EN.md)

**0.3.0 alpha** · Python 3.10–3.12 · [MIT License](LICENSE)

Prepare platform-specific release plans for one video with an AI assistant, then use official platform pages with your review and approval.

**Quick start:** Give this request to your coding assistant:

> Install https://github.com/whitecarp/publishops-crosscast, read `skills/publishops-crosscast/SKILL.md`, and run `vpub doctor`. Prepare a draft first; I will choose platforms, accounts, and visibility before anything proceeds.

```text
User: Prepare a release draft for this video. Do not publish; I will choose the platforms.
AI: I will inspect the file and prepare a plan. You can review the platforms, account, and visibility before I open the official pages.
User: Choose YouTube and keep it private. Show me the title and description first.
AI: The draft is ready. Please review the copy and account; nothing has been submitted.
```

This is an **illustrative workflow**, not a real publication record. CrossCast prepares a plan first. The CLI does not approve content for you or treat a queued task or login link as a published post. A host without browser-control capability cannot complete web-based publishing.

## Platform scope

- **Bilibili:** default is host-operated official web handoff; the upstream `bili` CLI is optional.
- **YouTube:** official web workflow, with an optional YouTube API for explicitly configured capabilities.
- **Douyin:** official web workflow; its standalone backend remains experimental.
- **Instagram and X:** official web workflows operated by the host. Drafts can be prepared; publishing or editing requires the appropriate authorization.
- **TikTok:** support is currently suspended.

There are no general-purpose comments, direct messages, inbox, or creator-analytics commands. See [platform boundaries](docs/PLATFORMS_EN.md).

## Install

Python 3.10–3.12 is required. The Windows installer has been tested; macOS and Linux have not been validated.

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script prints the full path to `vpub.exe` but does not add it to PATH. Use that path directly, or run commands from the virtual environment it creates. Alternatively, install from Git with pip (Git and a compatible Python are required):

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
```

## FAQ

**Do I need to buy a model API?** No additional model API is required for CrossCast itself. Copywriting uses the host conversation model. YouTube API use requires your own Google OAuth setup; host subscriptions, platform features, or optional APIs may have separate costs. Free use is not guaranteed.

**Does it publish automatically?** The default `native` web workflow requires a browser-capable host. Once configured and authorized, the Bilibili CLI or YouTube API path can submit directly from the CLI; check the selected backend and `vpub capabilities`. Without browser tools, an assistant cannot complete web workflows for you.

**Will all five platforms work after installation?** Not necessarily. Each requires the relevant account state, feature access, and host browser capability. Read the [platform boundaries](docs/PLATFORMS_EN.md).

## More

[Usage guide](docs/USAGE_EN.md) · [Platform scope](docs/PLATFORMS_EN.md) · [简体中文](README.md) · [繁體中文](README_ZH_TW.md) · [Online overview](https://whitecarp.github.io/publishops-crosscast/) · [Report an issue](https://github.com/whitecarp/publishops-crosscast/issues)

Credits: [ai-video-lab](https://github.com/whitecarp/ai-video-lab) (development origin and notices) · [bili-cli](https://github.com/kukemc/bili-cli) (upstream Bilibili tool). See [NOTICE](NOTICE.md).
