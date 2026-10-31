# X: CLI Media Studio publishing

[繁體中文](X_GUIDE.md) · **English**

**X publishing supports subscribed users whose accounts can actually access Media Studio.**

This requirement exists because non-subscribers are limited to **140-second, 512 MB** video uploads and cannot use the Media Studio media-management and custom-thumbnail workflow on which this tool depends. CrossCast uploads videos and sets production thumbnails through Media Studio, so its X backend currently supports subscription accounts with that access. Login or payment alone does not establish access; the CLI checks the library entry and managed account before selecting media. [Official upload limits](https://help.x.com/en/using-x/x-videos) · [Media Studio access](https://help.x.com/en/using-x/media-studio).

**0.3.1 alpha:** `vpub auth login x` opens the official login window once. Later `publish` commands reuse CrossCast's dedicated profile headlessly: check account → upload to Media Studio → apply the production thumbnail → fill exact source-aware post text → submit once → read back the original status. This is a scripted official website workflow, not an X API client.

```sh
vpub auth login x
vpub auth status x
vpub init --video final.mp4 --language zh-Hans --platforms x --out x.json
# Host prepares source-aware copy (English only, or original plus English) and the production cover.
vpub plan x.json
vpub publish x.json --confirm CURRENT_PLAN_HASH
vpub verify RECEIPT_KEY
vpub works x
```

Use `expected_identity` for the intended handle. Media Studio access is checked before upload, including the managed account identity. If processing or a selector fails before Post, `resume KEY` can reopen the same saved media; after Post it only verifies. Existing receipts prevent another upload. `--draft-only` stops at preview, which is not a guaranteed persistent post draft. This route uses current account audience and does not alter protected-account settings.

Production cover upload and episode-two publication were observed; [validation](VALIDATION_EN.md) records the repairs and limits. Custom thumbnails require actual Media Studio access; a subscription alone is not a guarantee. Native AI-label control was not observed in Studio, so this route retains the explicit AI-generated caption disclosure.

## Eligibility and official limits

This project currently supports subscription accounts with actual Media Studio access. A free account's short-video allowance does not make it a supported publishing route here. X's general web-upload guide lists 140 seconds and 512 MB for non-subscribers; Studio thumbnail and media-management features require the appropriate access. [General upload limits](https://help.x.com/en/using-x/x-videos) · [Media Studio access](https://help.x.com/en/using-x/media-studio).

The official legacy tier table excludes Media Studio from Basic and includes it in Premium/Premium+. Other newer plans also exist; the actual account entitlement is decisive. Do not promise support for every paid plan. [Official tiers](https://help.x.com/en/using-x/x-premium). Checked 2026-10-30.

## Historical native-browser guide

The following records describe earlier host-operated tests; current CLI support is stated above.

[繁體中文](X_GUIDE.md) · **English**

This release provides **in-app-browser handoff**: sign-in URL, one post body, account checks, media snapshots, single submission and receipt validation. A host agent must operate the official site. One X publication has now been read back in the signed-in UI; a standalone API publisher and full-playback acceptance remain incomplete.

**Live test:** Ready media, English text and AI disclosure survived a saved-draft reopen. The user then explicitly authorized publication; the same draft was submitted and the original status read back. It was not deleted. Self-only tests must use draft-only mode, which forbids final submission until explicit authorization changes the mode.

```sh
vpub onboard --platforms x
vpub init --video final.mp4 --out x-publish.json --platforms x
# Review identity and text
vpub plan x-publish.json
vpub publish x-publish.json --confirm CURRENT_PLAN_HASH
```

Set `expected_identity` to the handle visible in the signed-in profile, without `@`. Confirm the account in the same browser before `browser begin`; let the user choose the sign-in method.

- **Text:** `plan.platforms.x.post_text` combines title, description and hashtags. `ai_generated: true` adds an AI disclosure. Use the complete generated `post_text` without silently rewriting or truncating it.
- **Audience:** Account/current UI audience applies; this is not an individual private-video switch and does not change protected-account settings.
- **Limits:** Check the website's weighted character counter, media validation and account limits; do not treat Python string length as X's counter. Inspect duration/size/format first, stop on mismatch, and do not silently trim or assume paid privileges.
- **Submit:** Wait for processing, check exact text/audience, submit once; inspect the original post after an uncertain result.
- **Receipt:** Read back the status URL, numeric ID, original handle, exact `post_text` and source fields. Validation rejects a different author, mismatched ID or altered disclosure/text.

X's web-upload guide currently lists 140 seconds and 512 MB for non-subscribers, with different paid limits; validate the actual account UI as well. [Official source](https://help.x.com/en/using-x/x-videos) (checked 2026-10-29).

Discovery, work listings, analytics, comments and inbox are not unified X commands yet. Do not describe this handoff as a full X CLI.

## Custom thumbnails and subscription

**Custom thumbnails use subscription-gated Media Studio.** Official guidance limits Studio access to verified subscribers. Its library offers Change thumbnail with a video frame or uploaded image matching the video aspect ratio. Ordinary post composition is a different surface; payment alone does not prove that the library is ready.

[Official access](https://help.x.com/en/using-x/media-studio) · [Official thumbnail guidance](https://help.x.com/en/using-x/media-studio-faqs) (checked 2026-10-29).

**Tier:** Media Studio is included with Premium and Premium+, not Basic. [Official tier comparison](https://help.x.com/en/using-x/x-premium).

**Follow-up test, 2026-10-29:**

The library initially failed to initialize. A single direct Upload Media attempt succeeded and exposed media details. Change thumbnail → upload accepted the matching 16:9 production cover; artwork and metadata survived closing/reopening. Newly authorized Studio publication produced a new status with the original cover. The previous status was retained. This is host-UI validation, not a standalone Studio API or dedicated CLI cover-receipt command.

Workflow: verify account/media → upload or select existing media → Change thumbnail → upload same-ratio artwork → read back → Post when authorized → verify the status URL. Library media IDs differ from post IDs. Uploading media is not publication and does not replace an earlier post.

This Studio post retains the AI-generated text disclosure, but the native Made with AI badge was not observed. Text disclosure is not proof of the native label. Full playback and mobile presentation remain separate checks.

Related links: [Draft procedure](DRAFT_TESTING_EN.md)

