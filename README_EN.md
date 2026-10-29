<p align="center">
  <a href="README.md">简体中文</a> · <a href="README_ZH_TW.md">繁體中文</a> · <b>English</b>
</p>

<p align="center">
  <img src="assets/hero-en.svg" alt="How the handoff works: you choose and review, the AI host writes copy and operates the browser with your authorization, the vpub CLI builds and checks the plan, and submission happens on the official platform page" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  Let an AI assistant turn one video into release plans tailored to each platform.<br>
  You choose the platforms, accounts, and visibility, review everything, and then finish on the official pages.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="Version 0.3.0 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-14b8a6" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#hand-it-to-your-ai-assistant">Quick start</a> ·
  <a href="#how-the-handoff-works">How it works</a> ·
  <a href="#platform-scope">Platforms</a> ·
  <a href="#install">Install</a> ·
  <a href="#costs-and-privacy">Costs &amp; privacy</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="docs/USAGE_EN.md">Usage guide</a>
</p>

> [!IMPORTANT]
> **0.3.0 alpha.** CrossCast prepares plans and handoffs. It does not approve content for you and does not promise fully automatic posting to all five platforms. The default web workflow needs an AI host that can operate a browser; without one, the assistant can only prepare materials and handoff notes.

## Hand it to your AI assistant

The easiest way to start: send this, as is, to your AI coding assistant.

```text
Please install https://github.com/whitecarp/publishops-crosscast
Read skills/publishops-crosscast/SKILL.md, then run vpub doctor.
Prepare a draft first: I will choose the platforms, accounts, and visibility before anything proceeds.
```

<details>
<summary><b>What happens next?</b> (illustrative workflow, not a real publication record)</summary>

```text
You: Prepare a release draft for this video. Do not publish; I will choose the platforms.
AI: I will inspect the file and prepare a plan. You can review the platforms, account, and visibility before I open the official pages.
You: Choose YouTube and keep it private. Show me the title and description first.
AI: The draft is ready. Please review the copy and account; nothing has been submitted.
```

The CLI never treats a queued task, login link, or local receipt as "published". Final results are confirmed by reading back the platform page or the platform API.

</details>

## How the handoff works

Every release involves four roles, each with a fixed boundary:

| Role | Responsible for | Boundary |
| --- | --- | --- |
| **You** | Choosing platforms, accounts, and visibility; reviewing on the official page and deciding whether to submit | You see and approve the plan before anything is submitted |
| **AI host** | Reading `SKILL.md`; writing titles, descriptions, and tags per platform; operating official web pages with your authorization when it has browser tools | Cannot complete web publishing without browser tools |
| **`vpub` CLI** | Building and checking release plans, fitting covers to platform canvases, `--dry-run` rehearsals, keeping local state and receipts | Does not perform browser login or approve content for you |
| **Official platform** | Account login, final submission, and visibility | Features, format limits, and fees follow the official page on the day |

Existing authorization carries over only within its original scope. If the content, account, platform, audience, or task scope changes, you will be asked to confirm again.

## Platform scope

| Platform | Default route | Optional route | Status |
| --- | --- | --- | --- |
| **Bilibili** | Host-operated official web | Upstream `bili` CLI | Available |
| **YouTube** | Official web handoff | YouTube Data API (your own OAuth setup) | Available |
| **Douyin** | Host-assisted official web | Standalone backend | Web available; standalone backend experimental |
| **Instagram** | Host-operated official web, draft-only supported | No standalone API | Available |
| **X** | Host-operated official web, draft-only supported | No standalone API | Available |
| **TikTok** | — | — | Suspended; the CLI rejects login and publish requests |

A successful install does not mean you are logged in or have publishing permission; run `vpub capabilities` to see the actual backends. There are currently **no** general-purpose comment, reply, direct-message, inbox, or creator-analytics commands. See the [platform boundaries](docs/PLATFORMS_EN.md) for details.

## Install

Python 3.10–3.12 is required. The Windows installer has been tested; macOS and Linux have not been validated.

**Windows installer**

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script prints the full path to `vpub.exe` but does not add it to PATH. Use that path directly, or run commands from the virtual environment the script creates.

**pip from Git** (requires Git and a compatible Python)

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub capabilities
```

`doctor` checks local dependencies and `capabilities` shows actual backends and verification boundaries; neither logs in nor publishes.

<details>
<summary><b>Prefer to drive it yourself? A look at the manual flow</b></summary>

```sh
vpub init --video final.mp4 --language en --out publish.json --platforms youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub plan localized.json
vpub publish localized.json --dry-run
```

`--dry-run` does not upload. The host model writes `response.json` following the template inside `request.json`. See the [usage guide](docs/USAGE_EN.md) for each step, cover preparation, and draft-only mode.

</details>

## Costs and privacy

- **Model:** CrossCast itself needs no additional model API; copywriting uses the host conversation model.
- **Possible costs:** Host subscriptions, platform features, and the optional YouTube API (your own Google OAuth; quotas and review are set by Google) may carry separate costs or limits. Free or unlimited use is not promised.
- **Local data:** Account sessions, tasks, settings, and receipts live in the local application data directory. Do not commit them to GitHub. Before sharing manifests, screenshots, or logs, remove account identifiers, work IDs, private paths, and credentials.
- **Secrets:** Never paste cookies, verification codes, passwords, tokens, or OAuth files into conversations, logs, or the repository.

## FAQ

<details>
<summary><b>Does it publish automatically?</b></summary>

The default `native` web workflow requires a browser-capable host and your review and authorization before submission. Once configured and authorized, the Bilibili CLI or YouTube API route can submit directly from the CLI; check the selected backend and `vpub capabilities`.

</details>

<details>
<summary><b>Will all five platforms work right after installation?</b></summary>

Not necessarily. Each platform needs the relevant account state, feature access, and host browser capability. Read the [platform boundaries](docs/PLATFORMS_EN.md) first.

</details>

<details>
<summary><b>Do I need to buy a model API?</b></summary>

Not for CrossCast itself. You only need your own Google OAuth setup if you choose the YouTube API route; see [costs and privacy](#costs-and-privacy) for other costs.

</details>

<details>
<summary><b>What if a submission result is unclear?</b></summary>

Check the same task and the platform page first; do not blindly retry the upload. `vpub receipts` shows the local ledger, but a receipt on its own is not platform evidence.

</details>

## Docs and credits

| | |
| --- | --- |
| 📘 [Usage guide](docs/USAGE_EN.md) | Install checks, release plans, copy and covers, drafts and receipts |
| 🧭 [Platform scope](docs/PLATFORMS_EN.md) | Routes per platform, unsupported features, key boundaries |
| 🌐 [Online overview](https://whitecarp.github.io/publishops-crosscast/) | Project overview page |
| 🐞 [Report an issue](https://github.com/whitecarp/publishops-crosscast/issues) | Feedback (remove account and private details first) |

Credits: [ai-video-lab](https://github.com/whitecarp/ai-video-lab) (development origin and notices) · [bili-cli](https://github.com/kukemc/bili-cli) (upstream Bilibili tool). Third-party licenses are listed in [NOTICE](NOTICE.md). CrossCast is released under the [MIT License](LICENSE) as an independent integration tool, not an official product of any video platform.
