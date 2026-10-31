# Feature scope

[繁體中文](FEATURES.md) · **English**

**X publishing supports subscribed users whose accounts can actually access Media Studio.**

This requirement exists because non-subscribers are limited to **140-second, 512 MB** video uploads and cannot use the Media Studio media-management and custom-thumbnail workflow on which this tool depends. CrossCast uploads videos and sets production thumbnails through Media Studio, so its X backend currently supports subscription accounts with that access. Login or payment alone does not establish access; the CLI checks the library entry and managed account before selecting media. [Official upload limits](https://help.x.com/en/using-x/x-videos) · [Media Studio access](https://help.x.com/en/using-x/media-studio).

**0.15.1 alpha.** New manifests use CLI execution after first login; `method: native` is an explicit host-browser fallback. Bilibili continues to call [bili-cli](https://github.com/kukemc/bili-cli).

| Capability | Bilibili | YouTube | Douyin | X | Instagram |
|---|---|---|---|---|---|
| Login | Upstream CLI | Desktop OAuth | Dedicated official Chrome | Dedicated official Chrome | Dedicated official Chrome |
| Default publish | bili-cli | Official API | Background browser | Background Media Studio | Background browser |
| Cover | Upstream | Thumbnail API | Landscape + portrait | Custom Studio thumbnail | Separate cover; Original crop |
| Target metadata language | Simplified Chinese | English | Simplified Chinese | English | English |
| Work read-back | Upstream | API | Creator cards/detail IDs | Own profile/status | Own grid/Reel |
| Comments/replies | Upstream CLI | Official API | Public reads/Creator Center top-level replies | Visible conversation/Reply | Visible comments/Reply |
| Work metrics | Creator data | Basic statistics | Creator Center | Post metrics | Visible values; not full Insights |
| Operations report | Local receipts | Local receipts | Local receipts | Local receipts | Local receipts |
| Like / unlike | Upstream CLI | Rating API | Video controls | Post controls | Post toolbar |
| Private text messages | Upstream (not live-tested this round) | No adapter | CLI official UI | CLI X Chat | CLI official UI |
| Save / remove | Selected favorite folder | Selected ordinary playlist | Default saved items | Private bookmarks | Default Saved |
| Mentions | Upstream | Not integrated | Visible previews | Mentions | Tags/mentions; nonempty fixture pending |
| Moderation / hiding | Upstream | Viewer hold/approve | Not integrated | Hide/unhide other-author replies | Not integrated |

Shared: exact-source plans, per-platform account checks, immutable upload snapshots, local receipts and no blind retry after final submission. Browser `works` is limited to the visible first page (up to ten Douyin cards). Review cards without detail IDs remain pending.

Real episode-two uploads and their development limitations are recorded in [validation](VALIDATION_EN.md). Background browser automation is not an official direct API or a speed guarantee. Platform changes, expired sessions and challenges can require intervention. Metadata writing/translation uses the host conversation model through request/apply; it does not translate the video itself.

When the source language differs, retain source plus translation; Chinese scripts become Simplified Chinese only on Bilibili/Douyin. Douyin custom chapters are integrated into CLI publication/verification. `vpub edit` handles YouTube metadata, Instagram captions, an available X Edit action and Douyin covers/chapters; native handoffs remain. The user confirmed the shared v3 portrait and visible footer in both supplied phone views; this is not all-device acceptance. Email supports Gmail only, remains off by default and offers plugin setup assistance only after an explicit enable request. Outlook, QQ, NetEase and SMTP are unsupported. Single-language replies and user-language reports are described in the [README](OPERATIONS_EN.md#comments-work-metrics-and-operations-reports). TikTok, automatic/bulk messages and likes, full-history exports and YouTube cards/clickable end screens are not implemented.

This release closes reliability issues without adding platform capabilities. The current table and [latest validation](VALIDATION_EN.md) define scope; version notes below are not a new backlog.

## 0.15.0 — recovery and complete reads

Collection transactions reuse one browser context. YouTube uploads persist private resumable sessions; `read-all` saves API cursors and follows supported official collection pagination. Browser uploads and full DOM histories retain their documented limits. [Commands](OPERATIONS_EN.md#faster-commands-upload-recovery-and-complete-reads).

## 0.14.0 — Collection membership and agent skill

Instagram collection rename and member add/remove; Douyin work-collection management; independent readback and a named PublishOps CrossCast skill. X named membership and large-list pagination remain pending. [Commands](OPERATIONS_EN.md#private-collections-and-platform-differences).

## 0.13.0 — Private collections

Private X folders: list/create/rename/delete. Instagram: list/create/delete, bounded items; rename disabled after failed readback. Douyin: explicit empty work-collection list only. Named membership writes and pagination remain pending. Durable request IDs, independent readback and explicit uncertainty. [Usage](OPERATIONS_EN.md#private-collections-and-platform-differences).

## 0.12.0 — Parallel reads, message resumption and playlists

Three platform workers by default, with per-platform connection/browser reuse, independent checkpoints, serialized merges and stderr progress. Comments, mentions and previews share freshness caches and `messages resume`. YouTube adds `playlist list/items/create/update/add/remove/move/delete/verify` for ordinary owned playlists, with one submission and independent readback. [Usage and limits](OPERATIONS_EN.md#faster-collection-and-unified-resumption).

## 0.11.0 — Existing works, incremental reads and resumption

`ops discover/adopt` uses bounded catalogues and fresh owner checks to adopt manual uploads, separate from publication receipts. `ops sync` adds 300-second successful-read reuse, change lists, per-step persistence and `ops runs/run/resume`. APIs retain page progress; unfinished browser works restart from their page while finished reads are skipped. Cached and partial results are explicit. Mention/DM process resumption was absent in 0.11 and is added in 0.12. [Usage and limits](OPERATIONS_EN.md#adopt-existing-works-and-resume-collection).

## 0.10.0 — Mention collection and live moderation

Douyin @ notifications and Instagram tags/mentions join `messages sync` with independent baselines and local marks. Douyin nonempty reads/deduplication passed; Instagram live coverage is navigation and empty state only. One YouTube viewer root-comment hold/approve and one X other-author hide/unhide round trip passed fresh verification, with visibility restored. Explicit pending-receipt recovery refuses unknown state. See [validation](VALIDATION_EN.md).

## 0.9.0 — Unified message collection

Shared local comment/mention/DM-preview lists and handling marks; independent source baselines, bounded browser comment expansion and explicit partial coverage. X mentions and three browser-platform previews were read live. YouTube review queues are available; viewer hold/approve and X hide/unhide remain experimental, without an eligible live fixture at that version; see 0.10.0 above for subsequent validation. See [commands and limits](OPERATIONS_EN.md#unified-messages-and-bounded-comment-collection).

## 0.8.0 — Douyin comment management and single-recipient messaging

Douyin adds comment like/unlike and deletion of your own comments without replies, with exact-ID binding and independent read-back. Douyin/Instagram/X add `dm list/read/send/verify`; X also provides user-operated `dm unlock x`. Bilibili messaging and top-level comment pins delegate to upstream, without Bilibili live tests this round. Unverified hide/pin operations are rejected instead of simulated. Messages use only the recipient's language; receipts distinguish sender-side visibility from recipient delivery and never resend pending attempts. See [commands and limits](OPERATIONS_EN.md#direct-messages-and-platform-differences).

## 0.7.0 — Batched reads and comment handling

Read-only browser batches reduce startup duplication. Account-scoped local comment history retains handled/deferred marks and reopens edited text. Bilibili/YouTube reply expansion is bounded by thread/page budgets. New controls are Bilibili/Instagram/X comment likes/unlikes and YouTube own-comment edits/deletion; each write has a durable receipt and fresh read-back. This was the 0.7.0 scope; 0.8.0 extends Douyin comment management and messaging as described above. A complete account inbox remains unsupported. [Commands](OPERATIONS_EN.md#manage-individual-comments).

## 0.6.0 — Explicit likes and saves

All five platforms use `engage status/like/save/verify`; `--remove` reverses the selected action. Bilibili targets a favorite folder, YouTube an ordinary owned playlist, Douyin and Instagram their default saved items, and X private bookmarks. `engage collections` lists supported collections; this historical scope has since expanded with `playlist` and `collection`; YouTube Watch Later remains unavailable.

Read current state, submit once and independently verify. Repeated identical commands cause no new write. Account changes, unknown states and ambiguous controls stop execution; pending attempts are not resent. Chinese/English reports count verified operations, reversals and unresolved attempts from receipts, excluding tests. Installation and collection do not enable automatic engagement. [Commands and limits](OPERATIONS_EN.md#likes-saves-and-reversals).

## 0.5.0 — Bounded operations collection

Five-platform work listing; Bilibili/YouTube pagination and reply-page reads; account-bound watchlists; `ops sync` with inbox candidates, comparable metric changes and per-run reports. Reads only, no scheduler or automatic engagement. [Commands and limits](OPERATIONS_EN.md#multi-platform-collection-and-metric-changes).

## Historical changes

## 0.2.0 — Cross-platform content settings

| Capability | This release |
| --- | --- |
| Copywriting | Host-model request/apply, source-bound validation; no independent model API |
| Language | Bilibili/Douyin → Simplified Chinese; YouTube/Instagram/X → English; bilingual opt-in |
| Covers | Production artwork → platform canvases; no cropping; immutable snapshots and read-back per variant |
| Existing-work edits | Metadata/cover changes retain the original work ID; privacy reduction preserved after actual read-back |
| Playlists | YouTube native fields and optional API membership operations; Bilibili collections remain upstream |
| Email | Off by default; host Gmail/Outlook send-tool handoff, claims and durable results; no SMS |
| Chapters | Native handoff; Douyin chapters submitted and mobile visibility user-confirmed |

See README and current validation for the latest acceptance status; these supersede historical live-upload entries above.

## 0.2.2 — Drafts and explicit publication

Draft-only mode distinguishes preview from saved drafts. Explicit operator authorization can enable submission of the same draft without reuploading. X draft and status were tested; Instagram succeeded after reauthentication and a rejection/absence-checked retry; profile-cover cropping remains unresolved. See [validation](VALIDATION_EN.md).

## 0.2.3 — Deletion receipts and cover limits

Instagram now has explicit deletion handoff, acknowledgement/absence evidence and history-preserving same-video cover replacement. Deletion was exercised; replacement has not been shared. X custom thumbnails use subscription-gated Media Studio; this session hit library initialization failure, so thumbnail changes remain unverified. [Guide](DELETION_AND_COVERS_EN.md).

## 0.2.4 — Historical experiment: lead-in withdrawn

The former preparation command added a one-second production-cover lead to landscape, passed portrait/square through, and retained original ratio, inputs, hashes and rerun reuse. This experiment was withdrawn in 0.2.5 after mobile testing. X's landscape cover was saved/published through Studio UI, not a new standalone API. Earlier 0.2.3 entries are historical; consult current validation.

## 0.2.5 — Original video and shared portrait cover

Mobile testing supersedes the one-second lead-in experiment; that command is removed. Preparation preserves original video bytes, ratio and duration, with a separate web cover and shared Douyin portrait for the phone camera-roll picker. The handoff returns the identical image path/hash; the user saves and verifies mobile changes.

Related links: [README](OPERATIONS_EN.md)

