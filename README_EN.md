<p align="center">
  <a href="README.md">简体中文</a> · <a href="README_ZH_TW.md">繁體中文</a> · <b>English</b>
</p>

<p align="center">
  <img src="assets/hero-en.svg" alt="One message, every platform you choose: you tell your AI assistant once, it writes each platform's copy, fits covers and submits platform by platform; the example release plan shows Bilibili and YouTube verified, Douyin submitted and unverified, Instagram publishing and X queued" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  <b>One message. Every platform you choose.</b><br>
  Your AI assistant writes each platform's title and description, fits the covers, then submits platform by platform and keeps a receipt for every step.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="Version 0.3.0 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <img src="https://img.shields.io/badge/Windows-tested-555" alt="Windows tested">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#-start-with-one-message">Quick start</a> ·
  <a href="#-what-it-does-for-you">What it does</a> ·
  <a href="#-platform-support">Platforms</a> ·
  <a href="#-safety-rails">Safety rails</a> ·
  <a href="#-install">Install</a> ·
  <a href="#-faq">FAQ</a> ·
  <a href="https://whitecarp.github.io/publishops-crosscast/?lang=en">Website</a>
</p>

> [!IMPORTANT]
> **0.3.0 alpha.** The real-world test records come from the [full operations edition in the lab](https://github.com/whitecarp/agentic-work-lab/tree/main/tools/publishops-crosscast) (0.15.1) and the author's own use: YouTube, Instagram, X and Douyin were published for real on their official websites and read back. Bilibili uploads were made by the author directly with the upstream bili-cli, which this edition's optional Bilibili route calls. Publishing to X requires an account with Media Studio access. The operations edition's comment, message and analytics features are not part of this edition, and this edition does not promise fully automatic posting to all five platforms.

## 🚀 Start with one message

Send this to your AI assistant (any assistant that can run commands, such as Codex or Claude Code), replacing `final.mp4` with your video:

```text
Please install https://github.com/whitecarp/publishops-crosscast
Read skills/publishops-crosscast/SKILL.md, then run vpub doctor.
Publish final.mp4 publicly to Bilibili, YouTube, Douyin, Instagram, and X, with titles and descriptions written for each platform.
```

Change the platforms and visibility as you like. The first time you sign in to each platform, you scan QR codes and enter passwords and verification codes yourself on the official page.

<details>
<summary>Prefer to install it yourself in a terminal?</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # check local dependencies
vpub capabilities  # see which publishing routes actually work on this machine
```

Requires Python 3.10–3.12. Neither command signs in or publishes. See [Install](#-install) for the Windows installer and the manual flow.

</details>

## 🎬 What publishing one video looks like

> **You**: Publish final.mp4 publicly to Bilibili and YouTube, and keep the titles casual.

| Step | What the AI assistant and vpub do |
| --- | --- |
| ✍️&nbsp;Auto&nbsp;copy | Written automatically: Simplified Chinese for Bilibili, English for YouTube with a category chosen from the video; lengths and tags checked automatically, no external links |
| 🖼️&nbsp;Auto&nbsp;covers | Fitted automatically: 16:10 for Bilibili and 16:9 for YouTube, the full image kept, the original untouched |
| 🧾&nbsp;Auto&nbsp;plan | Built automatically: platforms, accounts, visibility and AI disclosure listed, plus a plan hash |
| 🔁&nbsp;Rehearse | One `--dry-run` pass that never touches a platform |
| 📮&nbsp;Submit | YouTube: upload on the official page → read back the work → **Verified**<br>Bilibili: wait for your QR sign-in → upload finished → **Submitted, unverified** |

> **AI assistant**: YouTube is published and the work was read back. Bilibili is submitted; I'll mark it "Verified" once it passes review and the work is read back.

<sub>An illustration, not a real publication record.</sub>

## ✨ What it does for you

### ✍️ Copy written for every platform, automatically

Simplified Chinese for Bilibili and Douyin, English for YouTube, Instagram and X; ask if you want both languages. Your current AI assistant writes the words, while vpub supplies the writing brief and checks lengths and formats. There is no built-in model and no extra model API key. YouTube categories are chosen from the video's content instead of one default for everything.

### 🖼️ Covers fitted to each platform's canvas, automatically

A separate cover is saved for each platform's canvas with the whole image kept: no cropping, stretching or overwriting the original. Platform previews may still crop, so take a look before submitting. Instagram gets the original video without transcoding, and can share a portrait cover with Douyin.

### 🧾 A plan before anything moves

Every release starts with a plan that spells out platforms, accounts, visibility and AI disclosure, along with a plan hash. Change the video, cover or copy and the hash changes, so an old confirmation no longer applies. A `--dry-run` rehearsal never touches a platform.

### 📮 Submitted one platform at a time

By default your AI assistant uploads on the official websites. Bilibili can also go through bili-cli and YouTube through the official API, both submitted directly from the CLI and both able to schedule. Instagram and X can stop at a preview or a saved draft without pressing "Post".

### 🔍 A receipt for every step

Each platform's progress keeps its real name: Queued → Publishing → Submitted, unverified → Verified. Only a work read back from the platform's page or API counts as "Verified"; a queued task, a sign-in link or a local receipt is not a successful publication.

### ✏️ Edit after posting

Works published through the web workflow can have their title, description and tags edited in place, or their cover replaced, without uploading the video again.

### 📧 Email when it needs you (optional)

When a platform needs you to scan a QR code or verify, CrossCast can send you an email reminder. It is off by default and currently works with Codex's Gmail / Outlook send tools.

## 🧭 Platform support

| Platform | Default route | Optional route | Tested |
| --- | --- | --- | --- |
| **Bilibili** | AI assistant on the official website | bili-cli, can schedule | ✅&nbsp;bili-cli |
| **YouTube** | AI assistant on the official website | Official API (your own OAuth), can schedule | ✅&nbsp;Web,&nbsp;API |
| **Douyin** | AI-assisted official website | Standalone backend (experimental) | ✅&nbsp;Web |
| **Instagram** | AI assistant on the official website, drafts supported | — | ✅&nbsp;Web |
| **X** | AI assistant on the official website, drafts supported | — | ✅&nbsp;Web |
| **TikTok** | — | — | ⏸️&nbsp;Suspended |

The "Tested" column comes from the operations edition's [validation record](https://github.com/whitecarp/agentic-work-lab/blob/main/tools/publishops-crosscast/docs/VALIDATION_EN.md) and the author's own use. These are past real publications: they do not mean this edition was re-validated, or that publishing will succeed for your account, region and platform status. Bilibili uploads were made directly with bili-cli; calling it through vpub has not been tested on its own. `vpub capabilities` currently marks only YouTube, Instagram and X as tested and has not caught up with this record.

**Know before you post**

- **Bilibili and Douyin**: only videos declared as AI-generated content can be published for now; Douyin custom covers need the web route.
- **YouTube API**: videos uploaded by an API project that has not passed Google's audit are restricted to private.
- **Instagram**: covers may crop differently on the website and the profile grid; sometimes you need to pick the cover on your phone.
- **X**: publishing requires an account with Media Studio access (a subscription); accounts without it are limited to 140-second, 512 MB video uploads. This tool does not set a custom cover.
- **TikTok**: suspended; the CLI rejects TikTok sign-in and publish requests.

**General boundaries**

- A successful install does not mean you are signed in or allowed to publish; `vpub capabilities` shows which routes actually work on your machine.
- The web route needs an AI assistant that can operate a browser; without one, it can only prepare materials and handoff notes.
- Scheduling works only on Bilibili's bili-cli route (2 hours to 15 days ahead) and the YouTube API route (set to private); the web route publishes immediately.
- There are no comment, reply, direct-message, inbox or creator-analytics commands. See the [platform boundaries](docs/PLATFORMS_EN.md) for details.

## 🔒 Safety rails

It's your own account, so these rules are on by default:

- **You decide**: asking it to publish is the authorization. Platforms, accounts and visibility follow what you said, and a platform is never added just because you are signed in to it. If the content, account, platform, audience or scope changes, you are asked again.
- **You sign in yourself**: QR codes, passwords and verification codes are handled by you on the official page. Never paste cookies, passwords, verification codes or tokens into chats, logs or the repository.
- **No duplicate uploads**: the same video is submitted only once to the same platform account; if a result is unclear, only the original task is checked, and nothing is uploaded again.
- **Stops at risk controls**: on a verification code, risk control or rate limit, publishing stops and waits for you; `vpub stop` pauses everything at any time.
- **Originals untouched**: fitted covers are saved as new files; your original video and image stay as they are.

## 🤝 Who does what

- **You**: say which platforms, accounts and visibility to publish to, and scan QR codes or enter codes when asked. Your message is the authorization; you are asked again if the scope changes.
- **AI assistant**: reads `SKILL.md` and writes copy for each platform; with browser tools, it operates the official websites under your authorization. Without browser tools, web publishing cannot be completed.
- **vpub**: builds and checks plans, fits covers, rehearses, and keeps local state and receipts; it does not sign in to accounts or approve content for you.
- **Platform**: handles sign-in, final submission and visibility; features, format limits and fees follow the official page on the day.

## 📦 Install

Requires Python 3.10–3.12. The Windows installer has been tested; macOS and Linux have not been validated.

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

`doctor` checks local dependencies and `capabilities` shows actual backends and verification boundaries; neither signs in nor publishes.

<details>
<summary><b>Prefer to drive it yourself? A look at the manual flow</b></summary>

```sh
vpub init --video final.mp4 --language en --out publish.json --platforms youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub plan localized.json
vpub publish localized.json --dry-run
```

`--dry-run` does not upload. Your AI assistant writes `response.json` following the template inside `request.json`. See the [usage guide](docs/USAGE_EN.md) for each step, cover preparation and draft-only mode.

</details>

## 💰 Costs and privacy

- **Model:** CrossCast itself needs no extra model API; your current AI assistant writes the copy.
- **Possible costs:** your AI assistant's subscription, platform features and the optional YouTube API (your own Google OAuth; quotas and review are set by Google) may carry separate costs or limits. Free or unlimited use is not promised.
- **Local data:** account sessions, tasks, settings and receipts live in the local application data directory. Do not commit them to GitHub. Before sharing manifests, screenshots or logs, remove account identifiers, work IDs, private paths and credentials.
- **Secrets:** never paste cookies, verification codes, passwords, tokens or OAuth files into chats, logs or the repository.

## ❓ FAQ

<details>
<summary><b>Do I have to approve each release?</b></summary>

No. Asking it to publish is the authorization: it submits with the platforms, accounts and visibility you named, and only asks again if the content, account, platform, audience or scope changes. To look first, say "don't post yet", or run a `--dry-run`.

</details>

<details>
<summary><b>Can I publish videos that aren't AI-generated?</b></summary>

On YouTube, Instagram and X, yes: they are published as ordinary videos without an AI label. On Bilibili and Douyin, this edition only supports videos declared as AI-generated content; an ordinary video is rejected when the plan is built.

</details>

<details>
<summary><b>If I only run vpub in a terminal, can it publish?</b></summary>

Not with the default web route: the CLI cannot operate a browser, so it can only prepare plans and handoff notes, and the web steps need an AI assistant with browser tools. Once configured, the Bilibili bili-cli and YouTube API routes can submit directly from the CLI.

</details>

<details>
<summary><b>Does a queued task or a receipt mean it was published?</b></summary>

No. Queued only means the task was recorded. Local receipts record the evidence for each step, but they are not platform evidence on their own. Whether a publication succeeded is decided by a readback from the platform's page or API.

</details>

<details>
<summary><b>Will all five platforms work right after installation?</b></summary>

Not necessarily. Each platform needs the right account state, feature access and an AI assistant with browser tools. Read the [platform boundaries](docs/PLATFORMS_EN.md) first.

</details>

<details>
<summary><b>Do I need to buy a model API?</b></summary>

Not for CrossCast itself. You only need your own Google OAuth setup if you choose the YouTube API route; see [Costs and privacy](#-costs-and-privacy) for other costs.

</details>

<details>
<summary><b>What if a submission result is unclear?</b></summary>

Check the same task and the platform page first; do not blindly upload again. `vpub receipts` shows the local ledger, but a receipt on its own is not platform evidence.

</details>

## 📚 Docs and credits

| | |
| --- | --- |
| 📘 [Usage guide](docs/USAGE_EN.md) | Install checks, release plans, copy and covers, drafts and receipts |
| 🧭 [Platform boundaries](docs/PLATFORMS_EN.md) | Routes per platform, unsupported features, key boundaries |
| 🌐 [Website](https://whitecarp.github.io/publishops-crosscast/?lang=en) | Project overview page |
| 🐞 [Report an issue](https://github.com/whitecarp/publishops-crosscast/issues) | Feedback (remove account and private details first) |

Credits: [ai-video-lab](https://github.com/whitecarp/ai-video-lab) (development origin and notices) · [bili-cli](https://github.com/kukemc/bili-cli) (upstream Bilibili tool). Third-party licenses are listed in [NOTICE](NOTICE.md). CrossCast is released under the [MIT License](LICENSE) as an independent integration tool, not an official product of any video platform.
