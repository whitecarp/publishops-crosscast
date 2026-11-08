# Guide

[简体中文](GUIDE.md) · [繁體中文](GUIDE_ZH_TW.md) · **English**

[← Back to the README](../README_EN.md)

The full detail: how far each feature has been tried, what to know about each platform, installing, and the manual flow.

## A longer prompt for beginners

In mainland China, you’ll need to find your own way to access YouTube, Instagram or X.

Replace the brackets with your details and give the whole message to an assistant that can run local commands. Leave unknowns blank so it can ask only what is missing. Never fill in passwords.

> I'm new to computers. Please guide me through my first publication in plain language.
>
> Install CrossCast from https://github.com/whitecarp/publishops-crosscast in its own Python virtual environment, without changing other Python programs' environments.
>
> Run vpub doctor, read the skill it names, and explain any issues. Use saved preferences and this conversation; ask only the unknown setup choices together: which platforms I want and have accounts on; for YouTube/Instagram/X, whether I can access those websites; for X, whether I have both a subscription and actual Media Studio access; and whether I want to review every post before it goes out.
>
> Remember only my chosen platforms and show-first preference, not account names, location or access details. Run onboard only for those usable platforms. Leave X out if I lack either its subscription or actual Media Studio access.
>
> I will sign in myself on the official pages. Never ask me to send passwords, cookies, tokens or verification codes. Stop for platform checks and wait for me; do not keep retrying sign-in.
>
> Video: [full path]; cover: [path, or none]; platforms: [my choices]; video language: [language]; AI-generated: [yes/no]; content: [what actually happens]; audience: [public/private/account setting]. Ask for necessary missing details rather than defaulting to every platform or public visibility.
>
> Use prepare to write the copy and fit the covers, preserving my originals. Instagram and X follow the account's visibility; Instagram's cover is changed on my phone. Run go --dry-run locally and show the copy, accounts, visibility, AI declaration and cover plan. For the first publication on each platform, wait until I have reviewed the exact plan and say publish. A confirmation code is not consent; do not supply approval flags automatically.
>
> After that, follow my publishing requests on that platform. Review every plan only if I save show-first or ask to preview; material changes still need checking. Preferences are not standing publishing authorization. CLI commands must not ask interactive questions halfway through execution.
>
> Give me links to verified works and clearly separate uncertain results. After a timeout, inspect the original receipt and use its recovery path; never blindly repost.
>
> Finally create a feedback folder outside both CrossCast's and bili-cli's data directories (VPUB_HOME, BILI_CLI_HOME or their default locations), containing only redacted doctor/status/relevant receipts excerpts, reproduction steps and necessary redacted chat excerpts. Outputs may contain account IDs, private copy and file paths; do not copy them directly. Never export either data folder, sign-in files, browser profiles, full chat or raw receipts. Let me review the files and decide whether to share them.

The assistant can save your actual choices with `vpub preferences --platforms youtube,instagram --show-first yes`; those platforms are examples, not defaults. `vpub preferences --show-first no` turns off later review on that platform; the first publication on each platform in this local data folder still waits for my approval. This is not tracked per account or channel. Supply only the options you want to change. With show-first enabled, confirmation is for the current plan only; a changed plan needs a new confirmation. Commands do not pause for interactive questions.

### Redact feedback before sharing

1. Inspect `vpub doctor`, `vpub status` and `vpub receipts` locally. Do not publish or sign in again to collect feedback. **These outputs are not automatically redacted and may contain account/work IDs, private text and file paths.**
2. Create a feedback folder outside both CrossCast's data directory (`VPUB_HOME`) and bili-cli's data directory (`BILI_CLI_HOME` or its default location). Include versions, OS, commands, reproduction steps, expected/actual results, error codes and only the relevant redacted excerpts. Replace account names, IDs, work links, email addresses, private text and local paths with consistent placeholders; remove passwords, cookies, tokens and verification codes.
3. Include only necessary, individually redacted chat excerpts. **Never submit either application's entire data directory, sign-in files (including `credential.json` and `pending_login.json`), browser profiles, a full chat export, raw logs or unredacted receipts.**
4. Open and inspect every feedback file yourself before deciding to share it on [GitHub Issues](https://github.com/whitecarp/publishops-crosscast/issues). The assistant should not upload feedback automatically.

bili-cli stores sign-in data separately: it uses `BILI_CLI_HOME` when set. Otherwise, Windows uses `%APPDATA%\bili-cli` (`bili-cli` under the user's home directory if `APPDATA` is unset); macOS/Linux use `$XDG_CONFIG_HOME/bili-cli` (`~/.config/bili-cli` if `XDG_CONFIG_HOME` is unset). Excluding CrossCast's data directory does not automatically exclude these locations; keep all of them out of the feedback package.

## 🎬 What publishing one video looks like

> **You**: Publish final.mp4 publicly to all five platforms, and keep the titles casual.

- ✍️ **Auto copy**: Simplified Chinese for Bilibili and Douyin, English for YouTube, Instagram and X; a video in another language keeps its own words first, then the translation<br>A YouTube category chosen from the video; lengths and tags checked, no external links
- 🖼️ **Auto covers**: Landscape: 16:10 for Bilibili, 16:9 for YouTube and X<br>Portrait: one portrait image generated by the AI assistant, shared by Douyin and Instagram; the video stays as it is
- 🧾 **Auto plan**: Platforms, accounts, visibility and AI disclosure listed, plus a confirmation code
- 🔁 **Rehearse**: One `--dry-run` pass that never touches a platform
- 📮 **Submit**: After sign-in, videos are submitted automatically in the background

> **AI assistant**: All five platforms are submitted. Works already read back are marked "Verified"; the ones still in review will be updated once they pass. Please swap the Instagram cover to the portrait image on your phone.

<sub>An illustration, not a real publication record.</sub>

## ✅ What it does, and how far each part has been tried

This table covers the free version only. Some real-account records come from earlier versions, and all come from the author's own accounts, so they don't mean it will work the same for your account, region and platform status.

✅ Has run on a real account · 🟡 Built, not yet confirmed on a real account

**When posting**

- **Public post, read back**: ✅ YouTube, Douyin, Instagram, X · 🟡 Bilibili
- **Scheduled post**: 🟡 Bilibili, YouTube, Douyin, X
- **Draft or preview**: ✅ Bilibili, YouTube, Douyin, X · 🟡 Instagram
- **Add to a collection or playlist at upload**: 🟡 Bilibili, YouTube, Douyin
- **Chapters at upload**: 🟡 Bilibili, YouTube, Douyin

**After posting**

- **Edit a published work**: ✅ YouTube, Instagram · 🟡 Bilibili, Douyin, X
- **Delete one published post**: 🟡 YouTube, Douyin, Instagram, X; Bilibili unsupported
- **Adopt older works**: ✅ Bilibili, Douyin, Instagram, X · 🟡 YouTube
- **Works list and stats**: ✅ All five
- **Read and reply to comments**: ✅ All five
- **Manage a single comment**: ✅ YouTube, Douyin, X · 🟡 Bilibili, Instagram
- **@ mentions**: ✅ Bilibili, Douyin, X · 🟡 Instagram
- **Single direct messages**: ✅ Douyin, Instagram, X · 🟡 Bilibili
- **Likes and saves**: ✅ All five
- **Manage collections and playlists**: ✅ All five
- **Interactive widgets**: 🟡 Bilibili

<sub>A ✅ means the row's main action has run at least once, not that every detail has been tried. On YouTube, for example, managing a comment has been confirmed for pinning and unpinning. A direct message is confirmed as visible to the sender, not as delivered.</sub>

<sub>🟡 features are built and tested against fake platforms, but real pages may still hold problems nobody has found. The evidence for each mark is in the [validation record](VALIDATION_EN.md), and the full scope is in [Features](FEATURES_EN.md).</sub>

<sub>A platform missing from a row doesn't have that feature, or the free version doesn't offer it. No scheduled post has reached its publish time yet.</sub>

## ✨ How each part works

<details>
<summary><b>✍️ Copy written for every platform, automatically</b></summary>


Simplified Chinese for Bilibili and Douyin, English for YouTube, Instagram and X. When the video is in another language, the original text comes first, followed by a translation into the platform's language; Chinese content is written only in Simplified Chinese on Bilibili and Douyin. Your current AI assistant writes the words, while vpub supplies the writing brief and checks lengths and formats. There is no built-in model and no extra model API key. YouTube categories are chosen from the video's content.

</details>

<details>
<summary><b>🖼️ Covers fitted to each platform's canvas, automatically</b></summary>


A separate cover is saved for each platform's canvas with the whole image kept: no cropping, stretching or overwriting the original. Bilibili gets 16:10, YouTube 16:9, and X a landscape cover set through Media Studio. For Douyin and Instagram, the AI assistant (such as Codex) generates one dedicated portrait image that both share, instead of padding a landscape image with black bars. With no image generator, `vpub covers portrait` lays chosen parts of the cover into that portrait with no AI. The video itself is never converted to portrait and keeps its original ratio. The Instagram cover is changed on your phone.

Bilibili (including drafts) needs a supplied cover or ffmpeg on PATH to extract one. Prepare and rehearsal check this locally: pass --cover IMAGE or install ffmpeg if missing. Finding the tool does not prove the video can be decoded.

</details>

<details>
<summary><b>🧾 A plan before anything moves</b></summary>


Every release starts with a plan that spells out platforms, accounts, visibility and AI disclosure, along with a confirmation code. Change the video, cover or copy and the code changes, so an old confirmation no longer applies and what gets published is exactly the version you approved. A `--dry-run` rehearsal never touches a platform.

</details>

<details>
<summary><b>📮 Sign in once, then publish in the background</b></summary>


After the first sign-in, the CLI does the uploading itself: Bilibili through bili-cli, and YouTube, Douyin, Instagram and X through a dedicated Chrome the CLI runs in the background. The official API routes for YouTube, X and Instagram need the paid add-on, which isn't on sale yet; everything else here works without it. Uploads can carry chapters (Bilibili, YouTube, Douyin) and join a playlist (YouTube) or a collection (Bilibili, Douyin). Bilibili, YouTube, Douyin and X can schedule a future time; Instagram cannot, and a plan that asks for it is refused. `vpub init --draft-only` uploads without publishing: Bilibili and YouTube keep an unpublished draft, Douyin presses its own save-and-leave button, and its upload page may then offer the draft under 继续编辑 (CrossCast only looks, because it can't show whose draft that is and opening it may use it up), and Instagram and X stop at a preview that may not last, so it is never called saved.

</details>

<details>
<summary><b>🔁 Pick up where it stopped</b></summary>


If an upload is interrupted, `vpub resume` continues the original task: X continues from the Studio media it already saved, and YouTube resumes the same upload session with the paid add-on. If X's browser closed before Media Studio had the whole file, Library shows no file of its name, and resume uploads it once more. YouTube's free route and Douyin go back to the draft an upload left, and finish it from there. A file goes up again only if there's no draft of it and YouTube never gave it an ID. Instagram keeps nothing until Share, so an attempt that stopped before it starts over. If a cover is what keeps stopping an attempt, `vpub resume KEY --no-cover` finishes it without one, and `--cover FILE` gives an attempt a cover its plan didn't have. A submission with an unclear result is never clicked again automatically. If YouTube's read-back shows the work is still a draft, nothing went public, so `vpub resume` presses Publish on that same draft. `vpub status` lists what is unfinished with the exact next command, using local records only, and `vpub cleanup` previews disposable copies and runtime files. Publishing leaves these files for explicit `vpub cleanup --apply`; it does not bypass the host's deletion confirmation. Changed files and files with live or uncertain owners are retained. Safe runtime removal currently works on Windows; other hosts retain those runtime files. Byte counts are conservative logical sizes, not measured free disk space, and retained hardlinks can keep bytes allocated.

</details>

<details>
<summary><b>🔍 A receipt for every step</b></summary>


Each platform's progress keeps its real name: Queued → Publishing → Submitted, unverified → Verified. Only a work read back from the platform's page or API counts as "Verified"; a queued task, a sign-in link or a local receipt is not a successful publication.

</details>

<details>
<summary><b>✏️ Edit after posting</b></summary>


`vpub edit` changes a published work in place: Bilibili titles, descriptions, tags, covers and chapters; YouTube titles, descriptions, thumbnails and chapters; Douyin titles, descriptions, portrait covers and chapters; Instagram captions; and X posts when an Edit option appears. The video and visibility stay the same. For older works CrossCast didn't publish, adopt them with `vpub ops adopt`, then edit them with `vpub edit-work`.

X edits have not passed real-account validation. `EDIT_SUBMITTED_UNVERIFIED` can mean the edit succeeded; old text at the original address does not prove failure. Open the post on X and inspect its current text and edit history. Do not submit another edit based on that read-back.

`vpub widgets` puts Bilibili's interactive widgets on your own video: a link to another video, a follow or triple button, a vote, a rating and a reservation. Choose them in the plan, or add them to a video that's already up. Bilibili doesn't list them before a video is public, so a plan's wait until then, and `vpub verify` sends them. `vpub verify` and a Bilibili edit also report what's on the video. A reservation books a live stream on your account, so it needs a `live` start time and goes only through `vpub widgets add`, after you've seen its dry run and said yes. It never goes in a plan, and CrossCast never removes one, because removing it notifies everyone who booked it. Follow, vote, rating and reservation widgets have gone onto a real video through `vpub`; a link hasn't yet.

</details>

### Delete one published post

<details>
<summary><b>Commands, per-post approval and verification limits</b></summary>

Use a verified CrossCast publication receipt key, not a platform ID, URL, adopted work, draft or uncertain publication. Preview the target first; get one separate explicit yes for each post. Permission to publish is not permission to delete.

```sh
vpub delete RECEIPT_KEY --dry-run
# Only after the person explicitly approves deleting this exact post; use the preview CODE
vpub delete RECEIPT_KEY --confirm CODE --person-approved
```

The original publication history stays intact; `vpub status` lists deletion records under `deletions`. If the outcome is uncertain, use only `vpub delete RECEIPT_KEY --verify-only` to read back; never repeat the deletion.

The four browser deletion routes for YouTube, Douyin, Instagram and X are built but untried on real accounts. Bilibili is unsupported: there is no verified own-video deletion API or explicit gone-response contract. Private Douyin works without a verifiable exact ID are refused. Nonempty X and Instagram profiles cannot yet prove a complete list, so deletion stays pending even if the post page appears gone. Future live tests need separate user approval.

Verified deletion requires explicit evidence that this exact post is gone and a fresh, complete own-works list without it. Generic unavailability, permission errors and network failures do not count.

</details>

<details>
<summary><b>💬 Comments, likes and messages</b></summary>


Read comments and reply in the commenter's language, like and save works, read and send direct messages on Douyin, Instagram and X, and check @-mentions. You can also pin, hide, delete or remove individual comments where the platform allows it, and manage your collections and playlists (reordering them is part of the paid add-on). Each action covers only the single item you authorized; nothing is sent in bulk. What's available differs by platform; see the [feature scope](FEATURES_EN.md).

</details>

<details>
<summary><b>📊 Stats and operations reports</b></summary>


See plays and engagement for a single work, and scan several platforms for new comments and changes in numbers. The full operations report is part of the paid add-on.

</details>

<details>
<summary><b>📧 Email when it needs you (optional)</b></summary>


*Part of the paid add-on, which isn't on sale yet.*

When a platform needs you to scan a QR code or verify, CrossCast can email you a reminder. Gmail only; off by default and turned on only when you ask.

</details>

## 🆓 Free and paid

Publishing and daily operations are free and MIT licensed. The paid add-on only adds the items listed here. You can't buy it yet, and nothing else needs it.

**Free version** (available now, with no model API key and no official platform API)

- **Publishing**: five platforms, read-back, scheduling, drafts, chapters, collections or playlists at upload, and an AI declaration
- **Copy and covers**: your AI assistant writes for each platform, and vpub checks the copy and fits the covers
- **After posting**: edits, adopting older works, works list and stats, scans with a short summary
- **Engagement**: comments and replies, managing a single comment, @ mentions, single direct messages, likes and saves
- **Collections and playlists**: create, change, add and remove works (reordering aside)
- **Bilibili widgets**: related video, follow, poll and rating
- **Safety and recovery**: the safety rails below, `vpub status` and `vpub resume`

**Paid add-on** (not on sale yet, installed separately)

- Official API routes for YouTube, X and Instagram
- Operations report (`vpub report`)
- Complete reads (`vpub read-all`)
- Email reminders (Gmail only, off by default)
- Playlist reordering
- YouTube's held-for-review comment queue

## 🧭 Platform support

Test records are past real publications; they do not mean publishing will succeed for your account, region and platform status. See the [validation record](VALIDATION_EN.md). This table shows the free routes; official API routes for YouTube, X and Instagram are in the paid add-on.

- **Bilibili** (Bilibili app QR code, the included bili-cli): the only published uploads so far were made by the author directly with an older bili-cli. Publishing and scheduling through CrossCast haven't been tried, and `vpub capabilities` still marks it untested. Collections and drafts have been confirmed on a real account.
- **YouTube** (sign in once in the dedicated Chrome, YouTube Studio in the background): two videos went public and were read back on November 2, 2026. Both first left only a draft and went out after it was reopened and Publish was pressed again, and the code that now handles this hasn't run on a real channel. Adding to a playlist at upload and scheduling have only been confirmed in draft uploads.
- **Douyin** (dedicated official Chrome, CLI background browser): long-term sign-in is still being validated. An upload can't pick up where it broke off, but `vpub resume` can finish from the draft it left. The cover window has stalled in testing. Posting without a cover and setting it on your phone requires your explicit choice; the tool does not omit the cover automatically. On Windows, uploads now use a real Chrome window on a hidden desktop, which hasn't been tried yet. Title and description saves have been tried, but success remains unverified.
- **Instagram** (dedicated official Chrome, CLI background browser): videos have gone public and CrossCast read them back itself. Covers can only be changed on a phone, and it can't schedule.
- **X** (dedicated official Chrome, Media Studio in the background): the account must be subscribed and able to open Media Studio. Accounts without it are limited to 140-second, 512 MB videos, which this tool does not support. X currently serves two versions of its website, and CrossCast handles both. All real-account records are on the earlier one; the rebuilt one hasn't been tried on a signed-in account.
- **TikTok**: paused. The CLI refuses TikTok sign-in and posting requests.
- **Not available yet**: TikTok (suspended), YouTube cards and clickable end screens, automatic or bulk messages and likes, and full history export.

<details>
<summary><b>The full record for each platform</b></summary>

- **YouTube**: the free route can also read your works, stats, comments and playlists, reply to comments, like videos and comments, edit or delete your own comments, change titles, descriptions and thumbnails, and create, change and delete playlists in the background. On a real account so far, the reads have been tried, and so have creating, changing (name, description, visibility) and deleting a private playlist and adding a video to it and removing it, liking and saving a video and liking a comment (each undone), replying to a comment, and editing your own comment and putting it back. Changing a private video's title and description and putting them back was confirmed, and so were pinning a comment and taking the pin off. CrossCast reads a pin on the video's own page, because Studio's row and menu keep offering to pin a pinned comment. A delete of your own reply was tried once: the reply left the page, but CrossCast could not prove that YouTube had accepted it, so it was reported as unconfirmed, and it stays that way until one more real delete shows what a success answer looks like. These checks do not establish acceptance of other writes; private test uploads and the public posts of 2026-11-02 are recorded separately in the validation record. A new thumbnail is reported as unconfirmed (`EDIT_SUBMITTED_UNVERIFIED`), because Studio shows nothing to compare it with, so look at it in Studio. Reordering a playlist and the held-for-review queue need the paid API add-on, as do the official API routes. The free background route adds the video to playlists by their titles and can schedule it only when Studio's scheduler explicitly identifies one IANA time zone or UTC. The target date's zone rules determine the time; the computer's zone and numeric GMT offsets are never used to guess a zone. Offset-only, unknown or ambiguous zones and repeated daylight-saving times stop before the final Save and retain the draft for manual scheduling; do not upload again. Both playlist selection and the schedule are read back before Save, and the zone and time are checked again immediately before that press. This conservative zone guard has synthetic offline tests only; it adds no new live validation evidence. On a real account draft runs chose a new playlist and kept a schedule's date and time, on and off the quarter hour, and read them back. Those runs stop before the final Save, so a video joining a playlist at that Save, and a saved schedule, have not been confirmed there yet. On the paid API route only, while your Google project is in testing mode, add the signing-in account under "Test users" first; under Google's rules, videos uploaded by an API project created after July 28, 2020 that has not passed Google's audit are locked to private. The free route needs neither.
- **X**: the free route is Media Studio only. The account must be subscribed and able to open Media Studio; accounts without it are limited to 140-second, 512 MB videos, which this free browser route does not support. X currently serves two versions of its website: a signed-in test account still gets the earlier one, and signed-out visitors get a rebuilt one. CrossCast's X route handles both, and stops with an error if a page matches neither; the receipt for that attempt says whether anything had already gone out. The rebuilt-site paths come from X's public page code and have not been tried on a signed-in account. On the earlier site a post comes out of a folder through the share menu on the folder's own page. On a real account (the earlier site), creating, renaming and deleting a folder, adding a post to a folder and taking it out again, and liking and saving a post were confirmed. So were a reply, which a follow-up `vpub comments verify-reply` confirmed, and deleting it. Uploads through Media Studio stopped three times before posting anything, because the uploaded file's page did not open, and a fourth time when X asked for a verification. After changes to that step, a draft got as far as X's composer with the right file's details filled in, and was removed unposted. The next upload, for scheduling, waited ten minutes for the same page and stopped there, with nothing scheduled; the cause isn't confirmed. After another change to that step, a scheduling test reached X's calendar and showed that the calendar's Save only confirms the date, and that the real final button reads Done. With that changed, the next test pressed Done once and the post appeared on X's schedule page with its exact words, date and time and a video, and it was cancelled from that page before its time, with its clip deleted. A draft on a fresh upload also reached the composer with its video matching the file's, and was removed unposted. No scheduled post has reached its time. Ordinary posts have gone out on a real account, as the validation notes record. X edit-version behavior has not been verified on a live account. Old text at the original address or a redirect to another post ID does not prove the edit failed; `EDIT_SUBMITTED_UNVERIFIED` may mean it saved. CrossCast refuses another edit of that uncertain work even with `--person-approved`. Only read it with the original edit request and `--verify-only`, or have the user inspect its current text and edit history and fix it manually on X.
- **Instagram**: the video keeps its original ratio, and the cover must be changed on your phone: open the work's "Edit cover → Add from camera roll", pick the portrait image shared with Douyin, then check how the profile grid crops it. Instagram can't be scheduled, so a plan that asks for it is refused. Deleting your own comment was tried once on a real account: a fresh read afterwards no longer listed it, but CrossCast could not prove that Instagram had accepted the delete, so it was reported as unconfirmed (`COMMENT_UNVERIFIED`), and it stays that way until one more real delete shows what a success answer looks like.
- **Douyin**: long-term sign-in stability is still being validated, and an interrupted web upload cannot resume, so check existing drafts first: if Douyin's upload page is already offering a kept draft, a new upload stops with `existing_draft_requires_attention` before any video is chosen, and you continue or discard that draft there yourself, then run it again. A draft was kept on a real account once, when `vpub resume` finished an earlier attempt: Douyin then offered it under 继续编辑 with the exact test title. A later draft ended `PREVIEW_READY` with CrossCast seeing such an offer itself, which it records and does not count as reading the draft back. The stop for an offer already there before an upload has not run there. Creating and deleting a collection passed on a real account (a first try had stopped at a wait after the cover upload, which is longer now), and the upload form's collection picker listed real collections. A private schedule was confirmed from its card, with the right words, time and private mark, and removed by hand before its time, so none has reached its time yet. **Earlier versions could leave stray `*` in a caption with line breaks: one real video showed them in its public description.** That video has since been deleted, so no live example remains. Captions are now typed a line at a time, and the test card came out clean, but a new video's public page has not been checked. If you published a caption with line breaks to Douyin before, look at it, and correct it on Douyin or with `vpub edit`, whose caption saves have been tried on private Douyin test videos but have not been verified successful; works under review block edits. A real video's portrait cover was changed in CrossCast's background browser, by a script taking the same steps as `vpub edit`, and a fresh look showed the new cover with its words and visibility unchanged. `vpub edit` itself stopped before that, at a wait that has been fixed and not re-run there. Most real tries through Douyin's cover window in a headless Chrome, on edits and new posts, have stopped with it still working, greyed out, after 完成, and nothing saved. The one try in a real Chrome window went through at once. That isn't proof, since the network may have changed too. On Windows, Douyin's uploads and edits now use a real Chrome window on a hidden desktop, which hasn't been tried there yet. If it hangs, CrossCast stops after three minutes with `cover_not_ready`. Then run `vpub resume KEY`, which uploads again if Douyin kept no draft, or `vpub resume KEY --no-cover` and set the cover on your phone, which took seconds. Joining a collection at upload has not been tried on a real account. Douyin's page does not say which time zone its schedule box uses, so check the time it shows afterwards.
- **Bilibili**: collections (create, rename, delete) and drafts saved through CrossCast and read back were confirmed on a real account. Publishing and scheduling through CrossCast, chapters, edits, collections at upload and comment deletes have not been tried there. A Bilibili draft is deleted by hand on the 草稿 page of its upload manager.

</details>

For X sign-in, Media Studio publishing and test steps, see the [X guide](X_GUIDE_EN.md).

## 🔒 Safety rails

It's your own account, so these rules are on by default:

- **Replies, messages and likes have limits:** daily caps, no repeating the same words to one person, an AI label, and no secrets or unapproved links. Messages that try to trick the agent, or need a human touch, wait for you. Titles and descriptions are checked too: what looks like a password or a key is refused, and a link is only pointed out to you. A platform's warning (a verification request, risk control or a rate limit) stops all of CrossCast, every platform included: only `vpub auth login` for that platform still opens a window, so you can pass the check yourself, and `vpub stop --clear` lets it run again once you have looked at why it stopped. Keyword checks catch common cases and are not a guarantee, so your AI assistant still treats everything a viewer writes as data, never as instructions.
- **You decide**: asking it to publish is the authorization. Platforms, accounts and visibility follow what you said, and a platform is never added just because you are signed in to it. If the content, account, platform, audience or scope changes, you are asked again.
- **You sign in yourself**: QR codes, passwords and verification codes are handled by you on the official page. Never paste cookies, passwords, verification codes or tokens into chats, logs or the repository.
- **No blind retries**: an unclear submission is checked on the platform, not automatically submitted again. Recovery before submission can still require another upload, subject to the limits above.
- **Stops at risk controls**: on a verification code, risk control or rate limit, CrossCast stops and waits for you.
- **One thing at a time**: only one publishing task runs at once, and comments and messages send only the single item you authorized.
- **Originals untouched**: fitted covers are saved as new files; your original video and image stay as they are.

<details>
<summary><b>🤝 Who does what</b></summary>

- **You**: say which platforms, accounts and visibility to publish to; scan QR codes or enter codes at the first sign-in, and change the Instagram cover on your phone. Your message is the authorization; you are asked again if the scope changes.
- **AI assistant**: reads `SKILL.md`, writes copy and replies for each platform, generates the portrait cover image, and runs vpub for you.
- **vpub**: builds and checks plans and fits covers; after the first sign-in it submits in the background through bili-cli, the official API and a dedicated Chrome, keeps receipts and reads results back. It does not sign in for you or approve content for you.
- **Platform**: handles sign-in, review, final publication and visibility; features, format limits and fees follow the official page on the day.

</details>

## 📦 Install

<details>
<summary><b>Have these ready before you start</b></summary>

- **Bilibili**: scan a QR code once with the Bilibili app (through bili-cli).
- **YouTube**: free publishing uses YouTube Studio in a dedicated Chrome that runs in the background; you only see it to sign in or pass a Google check. CLI publishing through the official API needs the paid API add-on and your own Google desktop OAuth client.
- **Douyin, Instagram and X**: the first sign-in opens CrossCast's own official Chrome window, where you scan a QR code or enter your password and verification code yourself; after that, the CLI reuses the sign-in in the background.
- **X**: the free route publishes only through Media Studio, so the account must be subscribed and able to open Media Studio.
- **A new-device check**: CrossCast signs in through its own dedicated browser, and YouTube, Douyin, Instagram or X may treat it as a new device and ask you to verify who you are or to recover the account. Complete that yourself on the platform's own page; your AI assistant never answers it for you. Restarting the sign-in again and again does not help, and a saved sign-in can be asked to verify again later.
- **Google Chrome on your computer**: if it's missing, run `vpub setup --install-browser`.

</details>

<details>
<summary>Prefer to install it yourself in a terminal?</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # check local dependencies
vpub capabilities  # see which publishing routes this install offers; vpub doctor checks what's missing
```

Requires Python 3.10–3.14. Neither command signs in or publishes. See [Install](#-install) for sign-in and the manual flow.

</details>

Requires Python 3.10–3.14 and Google Chrome. The Windows installer has been tested; macOS and Linux have not been validated. It's best installed in its own Python virtual environment, which the Windows installer creates for you, so it doesn't change packages other Python programs use.

The install command above uses the current main branch. The existing v0.16.0 release downloads still require Python 3.10–3.12.

<details>
<summary><b>Windows installer</b></summary>

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script prints the full path to `vpub.exe` but does not add it to PATH. Use that path directly, or run commands from the virtual environment the script creates. If it can't find Python 3.10–3.14, run it again with `-Python` and the full path to a suitable `python.exe`.

</details>

<details>
<summary><b>pip from Git (requires Git and a compatible Python)</b></summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
```

A pip install leaves no checkout, so `SKILL.md` is in that Python environment's folder, at `share/publishops-crosscast/skills/publishops-crosscast/SKILL.md`. When `vpub doctor` finds something to fix, it ends with exit code 4 and `ok: false`, and each item under `issues` says how.

</details>

<details>
<summary><b>Sign in to each platform</b></summary>

```sh
vpub auth login youtube --method browser
vpub auth login youtube --method api --client-json /path/to/client.json  # paid API add-on, instead of the line above
vpub auth login douyin
vpub auth login instagram
vpub auth login x
vpub auth status all
```

Signing in to YouTube (free route), Douyin, Instagram or X opens CrossCast's own Chrome window. A platform may treat it as a new device and ask for a check or account recovery: finish it yourself on the platform's own page, and don't restart the sign-in again and again.

The login command returns promptly while its window starts. Leave it open and check with `vpub auth status PLATFORM --method browser --wait 5` (use the same `--account` if selected). If your AI app stops a waiting command, the sign-in worker may still finish; check its status instead of starting another. `login_interrupted` means the worker stopped before completion was recorded: close any leftover CrossCast login window, then run login once with the saved profile. If the app repeatedly stops it, run these commands in your own terminal. No host controls need bypassing, and this does not clear a platform stop.

Keep the YouTube OAuth client JSON and sign-in tokens on your machine; never commit them to GitHub.

</details>

<details>
<summary><b>Prefer to drive it yourself? A look at the manual flow</b></summary>

```sh
vpub prepare final.mp4 --platforms bilibili,douyin,youtube --language en --visibility public --ai-generated yes --about "What the video is about" --cover cover.jpg --portrait portrait.jpg
vpub go final-crosscast --dry-run
vpub go final-crosscast
```

`vpub prepare` uploads nothing and says what to write. Your AI assistant fills in `copy.json` in the new folder. `--dry-run` does not upload. `vpub go` is safe to run again, because what already went out is skipped.

To stop between steps, here's the same thing by hand:

```sh
vpub init --video final.mp4 --language en --out publish.json --platforms bilibili,douyin,youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub covers prepare localized.json --source cover.jpg --portrait portrait.jpg --out-dir covers --out ready.json
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm <confirmation-code>
vpub verify RECEIPT_KEY
```

For the first publication on each platform in this local data folder, or saved show-first, both `go` and `publish` stop before account access. This check does not track individual accounts or channels. Show the returned plan's full copy, accounts, platforms, audience, AI labels, covers and text warnings, then stop and wait for the user's explicit yes. Only then run `vpub go final-crosscast --confirm CODE --person-approved` or `vpub publish ready.json --confirm CODE --person-approved`, with the exact current plan code. `go --confirm CODE` alone does not attest review; the separate flag is caller attestation, and software cannot prove the user consented. Changed plans require a fresh review and yes. Draft-only work does not count as a first publication; saved show-first still requires draft review. After a verified publication on that platform, later runs without a review requirement follow the existing publishing request.

For the explicit native-browser handoff, first run `vpub browser authorize-publish KEY --identity ID` to get the changed draft-to-publish plan. Show it and wait for the user's explicit yes, then run `vpub browser authorize-publish KEY --identity ID --confirm CODE --person-approved`. This authorizes continuation of the same draft without reuploading; its old draft code cannot approve the changed publication plan.

`--dry-run` checks local files without contacting a platform. Missing sign-in records produce a login command and exit code 4. Saved records do not prove a live sign-in; publishing checks the account, so separate status calls are optional. The media check recognises a container header, not playback or platform acceptance.

Every vpub command prints one line of JSON. Exit code 0 means the command finished and its work is done. 4 means it ran and answered, but something isn't finished yet, such as a check that hasn't confirmed the work, or a missing sign-in: read `data` for the state, the note and the next command. 2 means CrossCast refused or stopped and says why under `error`. 3 means the command was interrupted or hit an unexpected error, so read the receipt before running anything again.

`--dry-run` does not upload. Your AI assistant writes `response.json` following the template inside `request.json`. After an interruption, continue with `vpub resume RECEIPT_KEY`; `vpub receipts` lists every receipt.

Optional: `vpub go final-crosscast --workers 2` (or `vpub publish ready.json --confirm <confirmation-code> --workers 2`) publishes to YouTube, Douyin, Instagram and X two at a time instead of one after another. Bilibili, and anything going through the paid add-on or your assistant's own browser, still goes one at a time. A platform's warning still stops every platform. Ctrl+C waits for each upload in progress to stop safely, keeps the record of anything already posted, and leaves CrossCast stopped until you clear it with `vpub stop --clear`. So far this has only been tested offline: how much faster it is, and how much memory two hidden browsers use, haven't been measured on a real account.

</details>

## 💰 Costs and privacy

- **Model:** CrossCast itself needs no extra model API; your current AI assistant writes the copy.
- **Possible costs:** your AI assistant's subscription, an X subscription (the free browser publishing route needs Media Studio), and YouTube API quota (set by Google). Free or unlimited use is not promised.
- **Local data:** sign-in sessions, tasks, settings and receipts live in the local application data directory. Do not commit them to GitHub. Before sharing manifests, screenshots or logs, remove account identifiers, work IDs, private paths and credentials.
- **Secrets:** never paste cookies, verification codes, passwords, tokens or OAuth files into chats, logs or the repository.

## ❓ FAQ

<details>
<summary><b>Do I have to approve each release?</b></summary>

Before the first publication on each platform in this local data folder, the assistant shows the exact current plan and stops until you say publish. This counts platforms, not first use of each account or channel. Later requests on that platform follow your instructions; saved show-first or a requested preview means waiting for your approval each time. When review is required, the current `--confirm CODE` must be paired with a separate `--person-approved`. The code alone is not consent, and the flag is only the caller's attestation: software cannot prove you said yes. Changes to content, accounts, platforms, audience or scope still need confirmation. Preferences are not standing authorization to publish.

</details>

<details>
<summary><b>Does YouTube need an OAuth client?</b></summary>

Only on the paid API route, where an API key on its own cannot authorize uploads. The free route publishes through YouTube Studio in a background Chrome, with no OAuth client. You authorize once on Google's page with a "Desktop app" OAuth client; it happens on your own computer, and the token stays there.

</details>

<details>
<summary><b>Can I post to X without a subscription?</b></summary>

Not through the free browser route: it publishes only through Media Studio, so the account must be subscribed and able to open Media Studio. Paying or signing in doesn't guarantee Media Studio access, so it is checked before uploading. The official API route belongs to the separate paid add-on, which is not on sale yet; this does not establish support for free or Basic accounts.

</details>

<details>
<summary><b>Why does the Instagram cover have to be changed on a phone?</b></summary>

In testing, the portrait image shared with Douyin could only be applied through the phone app's "Edit cover → Add from camera roll". Your AI assistant prepares the image; you do the step on your phone.

</details>

<details>
<summary><b>Can I publish videos that aren't AI-generated?</b></summary>

Yes. Tell your AI assistant whether the video is AI-generated: if it is, each platform's AI-content declaration is ticked (on X it is a line in the post text); if not, it is published as an ordinary video without an AI label.

</details>

<details>
<summary><b>If I only run vpub in a terminal, can it publish?</b></summary>

Yes. After the first sign-in, the CLI does the uploading in the background, with no AI assistant stepping through a browser. When a sign-in expires or a platform asks for verification, you need to handle it on the official page.

</details>

<details>
<summary><b>What if a submission result is unclear?</b></summary>

Use `vpub verify` to read it back, or `vpub resume` to continue the original task; do not blindly upload again. `vpub receipts` shows the local receipts, but a receipt on its own is not platform evidence.

</details>
