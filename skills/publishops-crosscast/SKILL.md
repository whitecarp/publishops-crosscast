---
name: publishops-crosscast
description: Operate PublishOps CrossCast through the vpub CLI for multi-platform video publishing, metadata, comments, collections, metrics and resumable operations. Use for CrossCast requests involving Bilibili, Douyin, YouTube, Instagram or X; not general browsing or unrelated account actions.
---

# PublishOps CrossCast

Use the installed `vpub` CLI. Read `vpub --help`, `vpub capabilities`, and the relevant subcommand's `--help` before constructing unfamiliar commands. If the command is missing, use the installation instructions in the user's CrossCast checkout; do not invent installer URLs or claim a successful installation without running the CLI.

## Login and execution

- Reuse CrossCast's own saved account sessions. Bilibili uses the included bili-cli, whose `bili` command is also available directly; YouTube uses an AI assistant on YouTube Studio by default, or OAuth and the official API with the paid add-on; Douyin, Instagram and X use dedicated browser profiles controlled by the CLI.
- First login, expired sessions and platform identity challenges require the user to complete the official login. Do not request passwords, PINs, cookies or authorization codes in chat. Do not import the host browser's credentials.
- X publishing requires both a subscription and actual Media Studio access. TikTok is not supported. Check reported capabilities and account-specific limits before an operation.
- Execute only the requested actions, preserving existing visibility, schedules and unrelated content. Existing user authorization persists; do not add a redundant review step. Ask only when a required target or scope is missing.

## Content rules

- Follow the video's source language, separately from the documentation language. Bilibili/Douyin: Chinese content gets Simplified Chinese; other languages get the original language plus Simplified Chinese. YouTube/Instagram/X: English content gets English; other languages get the original language plus English.
- Replies use only the commenter's language. Reports use the user's language, normally Chinese or English. Generate metadata from actual source materials; do not invent events, claims, responses or attribution.
- Use the production cover for the correct episode. Douyin and Instagram share the approved portrait cover; respect safe areas. Preserve Instagram's original video aspect ratio; do not insert a cover frame or force a portrait canvas. Explain any remaining mobile cover adjustment.
- Gmail notifications are optional, off by default, and require an explicit user request and a connected Gmail send tool. Claim with that tool's exact name, call it once with the returned `message` (or `arguments` when given), then record its response. Do not substitute Outlook, SMTP or SMS.

## Recovery and complete reads

With the paid add-on, use `resume KEY` for new YouTube API upload sessions; never recreate an uncertain upload. Use `read-all start/resume/status` for supported API lists and official collection pagination. Preserve partial checkpoints, distinguish explicit end proof from a stalled page, and retain bounded-history limits on other browser feeds. Browser commands share one background session per account, and a second command waits for the first; do not start a second process on the same profile.

`resume` may complete never-started YouTube cover/playlist steps for the original video; uncertain writes remain verification-only. Partial browser replays retain prior rows until a complete fresh pass. Kernel locks release on exit, but marker cleanup requires a confirmed dead owner; do not remove active/unknown locks or browser data.

## Receipts and verification

- Start with `vpub status` when picking work back up or asked what is unfinished: it reads local records only and gives the exact next command for each attempt. Run `vpub cleanup` to preview reclaimable video copies, and add `--apply` only when the user wants disk space back.
- Use exact platform/work/comment/collection IDs from reads. A matching title alone is insufficient. Distinguish private saved collections from public work series.
- Use stable request IDs when provided by the command. After interruption, reuse the original request or its read-only verify/resume command. Never blindly repeat a publish, comment, message, delete or collection mutation.
- Treat `UNVERIFIED`, partial reads, pending platform review and test fixtures honestly. A successful click or zero exit code alone is not proof of a persisted result. Read back through the CLI and retain the receipt.
- Do not clear saved lists, delete videos, contact unrelated accounts or enable recurring operations merely to test a feature. Controlled live tests must fit the user's authorized scope and restore their temporary changes.
- Keep credentials, private messages, raw client logs and account-specific diagnostics out of public GitHub commits. Report meaningful outcomes and remaining limits with verified links.

The Codex client chooses its progress wording. The skill's display name is PublishOps CrossCast; do not promise an exact progress sentence or a custom client UI.

`vpub init --ai-generated yes|no` (default `yes`) sets every platform's AI-content declaration. Ordinary videos use `no` and are published without an AI label. Ask the user when it is unclear; do not guess.
