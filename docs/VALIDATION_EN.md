# Validation status

[繁體中文](VALIDATION.md) · **English**

Date: **2026-11-01**, PublishOps CrossCast **0.16.0 alpha**.

## 0.16.0: paid API split, included bili-cli, status and cleanup

**Real-account checks for this version were read-only, plus one private YouTube test upload.** The live results further down belong to the versions they are listed under. Publishing and other writes through bili-cli 1.2.1 have not yet run on a real account.

| Live check | Result |
|---|---|
| Bilibili sign-in | QR sign-in through the included bili-cli 1.2.1 worked. |
| Bilibili message sources | `vpub messages sync --platforms bilibili --sources mentions,dm_preview` read 20 @ mentions and 20 conversation previews with no platform writes. A second full read found nothing new or changed. The report contained no message text. |
| Blank rows | The first run left 4 mentions and 1 conversation without text: a mention in a post (动态) has no text of its own, and Bilibili returned no name for 5 of the 20 conversations, one of which also had no message text. Rows now say who wrote and where, and a rerun filled all 40. |
| Unread badges | Unread counts were 0 before and after, so this check cannot show whether reading clears a badge. |
| YouTube sign-in | Google accepted sign-in in the dedicated Chrome, and the channel was read from Studio's own address. |
| YouTube background browser | Studio does not run under a headless browser, so it runs as a normal Chrome window on a hidden Windows desktop. Measured from the normal desktop: Chrome running, no window showing, nothing left after it closed. |
| YouTube free-route upload | `vpub publish` uploaded a 5-second private test clip in about 34 seconds, in Studio's Chinese interface, with nothing on screen. Studio then showed the planned title, multi-line description, tags, category, audience, altered-content answer, private visibility and custom thumbnail. A fresh `vpub verify` agreed, and a signed-out visit got YouTube's private-video refusal. Public and unlisted uploads, long videos, and Chrome being interrupted mid-upload have not been tried. |

**472 offline tests pass on Python 3.12**, and CI runs the same suite on Windows with Python 3.10, 3.11 and 3.12. The paid add-on's own 130 tests pass against this core.

| Offline check | Result |
|---|---|
| Included bili-cli | All 47 files match the 1.2.1 release. Its own suite passes on the included copy (172 passed, 3 skipped). Every `bili` command CrossCast builds is parsed against the real command tree. |
| Clean install | A built wheel installs into an empty environment. `vpub doctor` reports ready and `bili --version` reports 1.2.1. Upgrading over a separate bili-agent 0.7.0 works after following `vpub doctor`. |
| Background browser | Real Chrome on a blank page, with sign-in and commands stubbed. It started in 1.6 s and a reused session answered in 0.2 s. A busy command waited its turn, a lost startup race kept the winner, and no Chrome was left running after idle exit. |
| Malformed input | Wrong values in the JSON files assistants write now return a clear error. An audit that found 32 crash paths now finds none. |
| Docs and site | Every documented command parses and every link resolves. The site was checked in three languages at desktop and phone widths, with no overflow, console errors or broken images. |

## 0.15.1: existing-work closeout

Date: 2026-10-30.

**417 offline tests pass, including 15 new tests.** Added coverage includes cross-process exclusion, hard-exit recovery, active/damaged legacy markers, preserved receipts, partial replay retention, pending thumbnail/playlist steps, uncertain thumbnail outcomes without resending and a delayed Instagram edit menu.

| Live check | Result |
|---|---|
| YouTube hard interruption | A private excerpt called `os._exit(75)` after 8,388,608 acknowledged bytes. A second process continued that session and called `os._exit(76)` after saving its video ID. A third completed its thumbnail. The same ID was retained; private visibility and successful processing were read back. |
| Cleanup | Only this private test clip was deleted, with fresh absence confirmation. Original videos, captions, covers and schedules were unchanged. |
| Captions and chapters | Episode-two YouTube/Instagram captions matched the saved Chinese-plus-English copy. All six custom Douyin chapters were freshly read back. No writes. |
| Instagram editor | The first check reached the menu before Edit loaded. After a bounded wait was added, a fresh process verified the actual work editor's bilingual caption without saving it again. |
| Complete reads | Empty Douyin/Instagram/X collection lists had official end markers. A one-page YouTube budget paused and resumed to the end. Large nonempty browser lists remain untested. |
| Other existing actions | Publishing, comments/replies, likes/saves, moderation/hiding and collection rename/member changes retain the historical live evidence below. This round did not rerun every mutation or add Bilibili tests. |

**Scope is frozen for this closeout.** Multi-account operation and deep historical analytics are not added. Retained limits: no Douyin/Instagram website byte recovery; other browser work/comment/DM histories remain bounded; nonempty Instagram mentions, large lists and sustained unattended operation lack acceptance evidence. The user-accepted mobile cover is retained; the original X episode-two post stays unchanged; Gmail remains optional and off by default. Older pending notes below are historical, not new commitments.

## 0.15.0: session reuse, resumable upload and full-page reads

**402 offline tests pass, including 25 new tests.** Coverage adds lost chunk/final responses, expired sessions without a second upload, changed account/media/metadata, URL validation, durable page cursors, repeated cursors, overlapping rows, missing replies, server end markers and unrelated GraphQL responses.

| Live check | Observed result |
|---|---|
| Browser latency | Three fresh Instagram collection reads: 37.312 s with three contexts, 17.297 s with one. Identical results; no platform writes. One machine/account sample only. |
| Process interruption | An EP02 excerpt uploaded privately to YouTube. The test process exited after 8,388,608 bytes were acknowledged. A separate `vpub resume` process completed the same upload and read back its ID and private state. The temporary video was deleted and a later fresh read confirmed absence; originals stayed unchanged. |
| YouTube pages | Page size 1 read two existing videos in two pages. EP02 comments/replies required three pages and returned three unique comment records. Page budgets paused and resumed from committed cursors. |
| YouTube playlists | Two unique playlists across four size-1 pages (including overlapping results); one playlist had one member, the other five members across five pages. Reads only; no playlist changes. |
| Browser end markers | Douyin, Instagram and X empty collection lists returned explicit server end markers. A temporary Instagram collection's items returned a confirmed complete empty list. Large/nonempty multi-page browser lists are not live-accepted. |
| Reused-session writes | Temporary Instagram create/rename/delete persisted. The first delete readback was uncertain; a later read-only verify confirmed deletion without a second delete. All temporary collections were removed. |

The first YouTube initiation exposed the current `youtube.googleapis.com` upload host, in addition to the documented `www.googleapis.com` host. The allowlist was corrected before successful testing; that initial attempt sent no video bytes and its uncertain receipt is retained. Session URLs are private. Browser parsing was also corrected to ignore unrelated null GraphQL results; malformed relevant pages still fail.

No new Bilibili live test, public repost, interaction message or existing cover/metadata change was made. Full browser work/comment/DM history, browser byte recovery, forced-kill lock recovery and sustained unattended/large-account validation remain open. Earlier version sections below are historical.

## 0.14.0: collection membership, rename and named skill

**377 offline tests pass, including 16 new tests.** New checks cover complete member baselines, preservation of other members, no-op/repeated requests, receipt tampering, account and collection binding, pagination, default Saved exclusion and schema failures.

| Live check | Result |
|---|---|
| Instagram rename | The website modal stalled without saving. The official page's own collection client successfully renamed the temporary collection; a fresh browser read confirmed the new name. |
| Instagram membership | Removed an existing saved work, independently confirmed an empty collection, added that same work through the CLI and read back the exact shortcode and numeric media ID. |
| Douyin collections | CLI create, repeated-create deduplication, rename, add/remove an existing work and delete were independently read back. A prior add completed despite a response timeout; `verify` confirmed it without another add. |
| Cleanup | Temporary collections removed; the two deliberately saved test posts restored to their original unsaved state. Original videos, comments, captions, covers and schedules were not deleted or edited. Bilibili was not tested. |
| Agent skill | Bundled `SKILL.md` and `agents/openai.yaml` validated and installed locally as PublishOps CrossCast. Exact Codex progress wording remains client-controlled. |

These tests used small collections on the operator's accounts. They do not establish large-list pagination, all account variants, or sustained unattended operation. X named-folder membership remains unverified and disabled; its normal bookmark control does not establish folder membership. Instagram adds require an already-saved work visible in the loaded picker. Incomplete lists block membership writes. Browser startup and independent readback still take time; no new speed benchmark is claimed.

Earlier sections below describe historical versions; their unsupported statements do not override the current scope.

## 0.13.0: private collections and bookmark folders

**361 offline tests pass, including 30 new tests.** Coverage includes account/exact-ID binding, request deduplication, lost creations not adopted by title, partial lists not proving deletion, pre-submit receipts, response-schema changes and retrying only read-only navigation while X's menu initializes.

| Live check | Result |
|---|---|
| X private folders | Temporary-folder create, rename and delete independently read back; cleanup restored the empty list. |
| Instagram private collections | Temporary create/delete and empty-collection item reads tested. Rename did not persist, so this operation is disabled. |
| Douyin work collections | Explicit empty collection list read; creation requires a cover and works. No public test collection was submitted. Nonempty lists and writes remain pending. |
| X item reads | The platform displayed an error; the CLI returned `read_failed` instead of treating the accompanying empty-state text as success. Nonempty items remain unverified. |
| Uncertain submissions and network loss | Submitted requests without an exact returned ID remain unverified, without title-based success claims or automatic resubmission. Test folders were cleaned up using exact IDs from listing. |

No videos, comments or DMs were sent, no default saved items changed, and Bilibili was not tested. Private accounts, collection names/IDs and diagnostics remain local. Create/rename/delete still launch several independent browser contexts for readback; they are not instant direct-API operations, and this release makes no new speedup claim. Pending: named membership changes, collection pagination, Instagram rename, full Douyin work collections and clean-machine/long-running acceptance.

## 0.12.0: speed, unified message resumption and YouTube playlists

**331 offline tests pass, including 25 new tests.** Coverage includes overlapping reads, platform isolation, child-journal recovery, message caches/private reports, YouTube connection/ownership reuse, playlist ownership, field preservation, ordering, request deduplication and uncertain writes.

| Live check | Result |
|---|---|
| 10 live reads across four platforms | Same watchlist and page budgets: 55.446 seconds on 0.11 versus 27.398 seconds with three platform workers on 0.12; first saved result at about 3 seconds. Both completed 10 reads using 3 browser contexts. One comparison, not a sustained latency guarantee. |
| YouTube playlists | Created a private test list, renamed it, added two existing videos, moved the second item to position 0, removed one item and deleted the test list. All 7 changes were independently read back. Existing public playlists and videos were not deleted or changed. |
| Post-write visibility | An immediate read missed the newly created list. Its returned ID was saved and a later verify confirmed it. Bounded readback retries were added; creation was not repeated. |
| Unified message sources | Read watched comments on four platforms plus mentions and conversation previews on Douyin/Instagram/X. Instagram mentions remained explicitly empty; a nonempty fixture is still pending. X returned 1 visible mention this round. YouTube mentions/DMs remain UNSUPPORTED. |
| Windows checkpoint concurrency | Found a file-sharing race between status reads and atomic replacement; added a process-local read/write lock. Resumption completed the remaining 3 jobs after 8 had finished, without repeating completed work. |
| Actual forced interruption | The Douyin worker had saved both source results; the parent was forcibly exited after merging the first mention source. A new process recovered the second source, preserving the first observation time, with 0 new browsers and 0 platform writes. This validates source-level recovery, not a persistent browser scroll cursor. |
| Immediate repeat | All 11 successful reads reused caches: 0 new queries and 0 browser contexts. Cached observations are not live reads. |

No comments, DMs, likes or videos were sent this round; covers, captions, schedules and Gmail settings were unchanged. Bilibili was not live-tested. Accounts, message bodies, receipts and diagnostic details remain local.

Pending: live long-history/large-comment acceptance, nonempty Instagram mentions, clean-machine and sustained unattended acceptance. See [usage](OPERATIONS_EN.md#youtube-playlist-management) for playlist authorization, special-list and manual-ordering limits. Douyin work collections, named Instagram collections and X bookmark folders are not integrated.

Historical validation follows; earlier unsupported statements do not override this release.

## 0.11.0: existing-work adoption, incremental collection and resumption

**306 offline tests pass.** The 23 new tests cover freshness caches, scope/account isolation, text changes, API/reply continuations, cursor loops, recovery of completed browser reads, platform failure isolation, process locks and ownership conflicts during adoption.

| Live check | Result |
|---|---|
| Work discovery | Instagram returned 6 works; Douyin returned 2; X returned 3 IDs across 3 bounded rounds. All retain an incomplete-history flag. |
| Existing-work adoption | An old manual Instagram post without a CrossCast upload receipt was verified through its owner Edit control, tracked and successfully read for comments/metrics. Douyin/X episode two also passed fresh adoption checks. |
| Actual process interruption | The process exited with code 91 after saving the first real YouTube read. A new process resumed and completed 10 reads across four platforms; the first step retained its observation time and was not repeated. |
| Immediate repeat | All 10 reads reused freshness caches: 0 new platform reads, 0 browser starts and 0 changes. This is a development check on these accounts, not a general speed guarantee. |

Fixed a false missing-Edit result while Instagram's management menu was still loading. No comments, DMs, likes or videos were sent; captions, covers, visibility and schedules were unchanged. Bilibili was not live-tested this round. Raw catalogues, comments, accounts and receipts remain local.

**Limits:** these accounts have short work lists; long-history browsing has not passed live acceptance. API second-page/reply interruption recovery was verified with offline fault injection; the live process interruption occurred after a completed read. Unfinished browser works restart from their page, and `--full` remains bounded. Mention/DM-preview sources do not use this process-resumption system. More devices, accounts, long lists and clean-machine acceptance remain pending.

## 0.10.0: mentions, viewer moderation and hide/unhide

**283 offline tests pass.** New coverage includes notification/feed isolation, relative-time deduplication, CAPTCHA iframe detection, review-queue reads when held comments disappear, unknown/conflicting states, explicit receipt recovery and X first-use confirmation/hidden-reply navigation.

| Item | Observed result |
|---|---|
| Douyin @ mentions | The CLI read one real test mention from the user's second account. A fresh repeated read retained the same entry with zero new observations. Returning to Creator Center for DM previews also passed. |
| Instagram tags/mentions | Two CLI runs opened Tags & mentions and read an explicit empty state. No second account was available; nonempty parsing has offline tests only, not full live acceptance. |
| YouTube viewer moderation | One top-level test comment completed published → held → published; owner-review and public-state reads confirmed both transitions. Restored. |
| X hide/unhide | One other-author test reply completed visible → hidden → restored. First-use confirmation, the owned post's hidden-replies entry and exact menu-state reads passed. |

Notification previews lack event IDs and identical content may coalesce; bounded visible coverage is not full history. These checks cover specific accounts/pages/fixtures. No new DMs, Bilibili tests, video/cover/schedule edits or Gmail changes occurred. Diagnostic pages, comment bodies and test-account data remain local. Earlier-version records follow.

## 0.9.0: unified messages, bounded collection and experimental moderation

**263 offline tests pass.** Added coverage includes per-source baselines/deduplication, relative-time changes, account isolation, handling marks, private bodies excluded from reports, partial-page absence preserving history, bounded cross-page unions, root/child moderation ambiguity, unknown states and own-reply safeguards.

| Live check this round | Result |
|---|---|
| Douyin/Instagram/X comment collection | Each existing work returned two test comments/replies; IDs were retained across bounded rounds. No full-history claim. Large-list coverage still lacks a live fixture. |
| Unified message collection | Watched comments read successfully on four platforms, with conversation previews on Douyin/Instagram/X. Repeated previews produced no new or changed entries. Unsupported YouTube DMs correctly made the combined result PARTIAL. |
| X mentions | Explicit empty Mentions state matched on two reads; no nonempty mention fixture yet. |
| Douyin inbox loading | Replaced the early shell check with a wait for actual rows or an explicit empty state. |
| YouTube review queue | Published queue read successfully. A separate own childless test comment received an accepted hold request, but fresh state remained published: not a verified transition. The temporary comment was deleted and absence confirmed; the original test thread remains. Viewer-comment transitions await validation. |
| X hiding | Own-reply dry-run stopped without mutation. No eligible viewer-reply fixture, so live hide/unhide is not accepted. |

No new DMs, Bilibili live tests, video/cover/schedule edits or email-setting changes occurred in this round. Raw messages, pages and test-account data remain local. New moderation/hiding routes are experimental; offline tests and accepted requests do not establish live platform state.

Historical validation scopes follow; current behavior is defined above and in the README.

## 0.8.0: comment management and direct-message live checks

**240 offline tests pass.** New coverage includes pending-message resend suppression, wrong-account/recipient rejection, outgoing-bubble sender and sent-state checks, truncated results not reducing prior identical-message counts, explicit language, test-traffic exclusion, partial-page absence not proving Douyin deletion, and Bilibili pins never replacing another pinned comment.

| Live check this round | Result |
|---|---|
| Douyin comment like/unlike | An existing test comment changed from unliked to liked and was restored; fresh sessions confirmed both states. |
| Douyin comment deletion | Created one separate, own test comment without replies, deleted it and confirmed absence on the complete comment page. The original hello thread remains. |
| Douyin text message | One Chinese test message to one public account; a fresh session confirmed the exact recipient and outgoing bubble. |
| Instagram text message | One English test message to one public account; a fresh session confirmed sender, text and recipient. |
| X text message | The user completed the official PIN step; a new CLI session reused it. No message went to a closed inbox. One English message went to another public account, followed by fresh You-label and sent-state read-back. |
| Inbox summaries | All three browser platforms returned visible summaries through the CLI; this is not complete history, total unread count or a private API. |

Exactly one message was sent per platform, with no bulk sending or recipient delivery/read claim. Initially uncertain messages stayed pending; parser repairs used read-only verification without resending. The Douyin delete-dialog button repair also retried only before final confirmation. Messages, raw pages and recipient data remain local and are not published to GitHub.

**No Bilibili live tests this round.** Messaging and comment pins delegate to upstream and were checked offline only. The observed Douyin/Instagram web layouts expose no verified comment pin/hide entry. X hide-reply is not integrated; profile pinning is not comment pinning. Existing YouTube comments remain; no DM or moderation API was added. Videos, covers, captions, schedules, email and other operations agents are outside this change.

The following sections preserve historical validation scopes. Earlier unsupported-feature statements do not override the 0.8.0 results above.

## 0.7.0: batched reads, persistent inbox and comment management

CLI reads succeeded for comments and metrics on the five existing episode-two works. The run took about **76 seconds**, with **3 browser sessions** across the three browser backends and no platform writes. Reads share a context per platform/account; Instagram/X reuse the same work page. This is one development run, not a cross-machine speed guarantee or a controlled before/after timing comparison.

| Live check | Result |
|---|---|
| Bilibili, Instagram and X comment likes | Changed state, repeated the command without writing, then restored the original state; independent read-back confirmed each |
| YouTube own comments | Edited an existing test reply and restored its text; created a separate disposable test comment, deleted it and verified absence by exact ID |
| Local inbox | A handled mark survived resync, then was restored; the repeated YouTube read found zero new audience comments |

**9 comment-management writes** were verified, plus one disposable test-comment creation. Three repeated like commands returned `NO_CHANGE`. Existing hello threads remain and audience comments were not changed. Instagram initially stopped before writing because text and heart controls are sibling containers; the corrected exact-comment scope passed live testing and cannot climb into the post toolbar. One local lock conflict stopped before submission; testing resumed only after the earlier operation finished.

**218 offline tests pass.** Coverage includes rejecting writes in read batches, stopping on failure while retaining prior results, account isolation, history retention and reopened text, bounded reply expansion and continuations, exact work/author checks, no resend after uncertainty, isolated comment controls and excluding test traffic from reports. Reply-preview expansion was regression-tested; the live sample had no incomplete previews, so large-thread live coverage is not claimed.

Douyin comment management, pinning/hiding, private messages and a complete account inbox remain unimplemented. Browser collection covers visible comments only. This round did not change videos, covers, captions, visibility or schedules. Language rules and Gmail-off defaults remain. Private receipts and page diagnostics are not published.

Official references: [YouTube comment editing](https://developers.google.com/youtube/v3/docs/comments/update), [deletion](https://developers.google.com/youtube/v3/docs/comments/delete), [reading](https://developers.google.com/youtube/v3/docs/comments/list).

## 0.6.0: likes, saves, reversals and deduplication

The user authorized bounded live CLI tests on the existing episode-two work on each platform. Each action records its original state, changes it once and verifies independently; repeating the same command must return `NO_CHANGE`, then the original state is restored and queried again. Private receipts retain the test marker and are excluded from ordinary operations reports. Test traffic is not organic engagement or growth.

| Platform | Like and reverse | Save and remove | Evidence |
|---|---|---|---|
| Bilibili | Passed, restored | Selected favorite folder passed, restored | Upstream CLI relation plus video-specific folder membership |
| YouTube | Passed, restored | Selected owned playlist passed, restored | `getRating` and exact video/playlist item IDs |
| Instagram | Passed, restored | Default Saved passed, restored | Post toolbar read after closing and restarting the CLI browser |
| X | Passed, restored | Private bookmarks passed, restored | Exact post controls in an independent CLI browser |
| Douyin | Passed, restored | Default saved items passed, restored | `aweme_detail.user_digged` / `collect_stat` loaded by the normal work page, matched to the exact video ID |

Across five platforms, **20 writes including restorations were independently verified and 10 repeated commands returned `NO_CHANGE`**. All like/save states were restored to their recorded baselines. Failed reads and repairs remain recorded; no repeated clicks were used to manufacture success. Existing playlist items and membership in other favorite folders were preserved.

Instagram live testing exposed nested sections matching the same toolbar. Selection now uses the innermost post toolbar, excluding comment likes. One X read stopped when the post appeared before its toolbar. After adding a bounded wait, bookmark creation, duplicate-command checking and removal completed. YouTube removes the exact playlist item; Bilibili removal targets only one folder.

Douyin retained its Like tooltip after a successful like, causing the original check to mark the action unverified. The fix observes account state in the normal page's own loaded detail response and independently verifies the existing receipt without liking again. Tooltips locate controls only; neither heart color nor aggregate like count determines account state. This observes official-page responses, not an independent official API. Missing fields, wrong video IDs and states other than 0/1 stop execution.

**193 offline tests pass.** Added state deduplication, durable submission, no resend or opposite toggle after timeout, account/collection checks, duplicate membership refusal, test-traffic report isolation and seven network-free DOM tests. This round did not reupload videos or change copy, covers, visibility or schedules. Existing language rules and Gmail-off defaults remain. Live development checks cover these accounts and layouts, not all accounts, layouts or clean-machine unattended operation.

Official references: [YouTube rating](https://developers.google.com/youtube/v3/docs/videos/rate), [rating reads](https://developers.google.com/youtube/v3/docs/videos/getRating), [playlist membership](https://developers.google.com/youtube/v3/docs/playlistItems/insert), [X bookmarks](https://help.x.com/en/using-x/bookmarks). YouTube API rating success is distinct from official like counts; this test verifies account ratings. Raw pages, private receipts and login data are not published.

## 0.5.0: Read-only multi-platform collection

Live checks used the current signed-in accounts and existing episode-two works through the CLI, without the host's in-app browser. All five `works` calls returned works. New YouTube/Bilibili work pagination and `comments replies` read back the existing hello comment and reply. No test comments were resent and no video, copy, cover, visibility or Bilibili schedule was changed.

After adding the five works to the local watchlist, `ops sync` completed all comment reads and statistics queries. Bilibili, YouTube, Douyin and X returned available metrics; Instagram's current work surface returned none, retained null values and reported the limitation. Own comments were excluded from audience interactions. Browser comments remain limited to visible entries and Bilibili thread lists retain reply previews; this collection is not a complete historical inbox.

YouTube and Bilibili work lists were followed to the next page. A second API collection reported zero newly observed comments on both platforms. X can display a zero comment count while loading conversation entries, so all three browser adapters now retain `complete: false`.

**164 offline tests pass.** New cases cover page budgets and cursor loops, deduplication, account-change stops, partial failures, source/unit/rounded-value incompatibility, percentage-point deltas, watchlists and read-only dispatch. Live acceptance covers these accounts, works and layouts, not every account or clean-machine unattended operation. Watch duration, full Insights, private messages and automatic likes remain unintegrated; Gmail stays off by default.

API references: [YouTube upload-list pagination](https://developers.google.com/youtube/v3/docs/playlistItems/list), [comment-reply pagination](https://developers.google.com/youtube/v3/docs/comments/list).

## 0.4.1: Douyin reply and read-back repair

The user confirmed on the phone that only the original comment existed. Public-work submission requests produced no readable reply. Replies now use the selected comment's Creator Center composer, scoped separately from the top-level comment box. After fresh Creator Center data showed zero replies, a recorded repair test ran without creating another top-level seed.

**The reply succeeded and was read back by an independent CLI session.** A fresh comment ID, author, exact “你好” text and nested parent all matched; data loaded by the official page independently corroborated the parent. Earlier failed attempts remain recorded. Video, caption, cover, visibility and schedules were unchanged. HTTP 200 or a successful click alone is not success evidence.

The repair also handles missing absolute timestamps, extracts nested comment parents, and distinguishes platform rejection, uncertain submission and verified visibility. An ambiguous mapping between a comment ID and a visible Creator Center row stops before sending. Only top-level targets are supported. No automatic resend mechanism was added; this was a controlled repair supported by the user's report and fresh creator data.

**147 tests pass.** New regressions cover work/parent/author/text matching, empty acknowledgements and rejections, retained repair history, nested DOM and missing timestamps. All five live hello tests now have read-back replies. These development tests do not establish full unattended acceptance for every account, layout or clean installation.

## 0.4.0: comments, replies and operations reports

The user explicitly authorized one “你好” comment and one “你好” reply on the existing episode-two works. Video, cover, caption, visibility and Bilibili schedules were unchanged. Test comments are retained, not automatically deleted.

| Platform | Live result and evidence boundary |
|---|---|
| Bilibili | Upstream CLI posted the test comment/reply; author, exact text, parent and reply IDs read back. The existing audience comment had already received an owner reply and was left alone. |
| YouTube | Official Data API posted and verified the comment/reply. A live bug was fixed: Comment resources may omit videoId, so CommentThread now establishes work membership; matching the channel alone is insufficient. |
| Instagram | The CLI session clicked Reply on the selected comment; a fresh comment ID, author and “@author 你好” text were read back. Relative-time changes and delayed Post-button appearance were fixed. No official API parent relationship was verified. |
| X | The CLI session posted and read back fresh IDs, author and exact text. Delayed nested dialogs and Draft.js CJK input were fixed; the original video post was preserved. No official API parent relationship was verified. |
| Douyin | The seed comment was read back on the public work page while Creator Center temporarily omitted it. The submitted reply is still REPLY_UNVERIFIED, was not resent, and was subsequently repaired as recorded in 0.4.1 above. |

One work per platform was queried: Bilibili creator data, YouTube basic statistics, Douyin creator metrics, X post counters and Instagram visible fields. Missing Instagram values stay null; full Insights is not integrated. YouTube completion/watch duration still needs separate Analytics support. X post views are not video plays. Debug visits and test comments can affect raw counters; these are not organic growth or efficiency evidence.

Chinese and English paragraph reports were generated from deduplicated execution receipts. Own comments and self-test replies do not count as audience interactions; uncertain sends are not completed replies. Reply language follows the commenter, while report language follows the user separately.

**140 offline tests pass.** Coverage includes pagination, ownership, single-language routing, direct send, duplicate suppression, parent verification, missing metrics/units, test isolation, reports and two Chrome DOM tests that never visit platforms. Live development tests required repairs and are not a clean-machine unattended acceptance run. Douyin reply visibility remains open. The asynchronous-send wait fix has not had an additional live write test.

Official references: [YouTube reply insertion](https://developers.google.com/youtube/data/reference/rest/v3/comments/insert), [thread pagination](https://developers.google.com/youtube/data/reference/rest/v3/commentThreads/list), [video statistics](https://developers.google.com/youtube/data/reference/rest/v3/videos). Raw pages, audience data, sign-in information and private receipts stay local and are not published.


## 0.3.3: persisted Instagram caption checks and cover acceptance

- **Observed problem:** The user reported English-only copy on episode two. Reopening the original work’s editor confirmed it. Earlier success records did not establish current platform state; the cause of this discrepancy is unconfirmed.
- **Correction:** The CLI-owned session restored original Chinese plus English on the [same Reel](https://www.instagram.com/reel/DfExzPeiC_i/), totaling 1,771 characters. The saved editor was reopened, followed by an independent read after closing and restarting the CLI browser; the full text matched exactly. No reupload, deletion, cover or visibility action occurred.
- **Code fix:** An old `EDIT_VERIFIED` no longer short-circuits a current platform check. Instagram compares full original text in the requested work’s editor instead of searching the whole page and rejects wrong-work redirects. Verify-only returns `EDIT_MISMATCH` without saving; a mismatching read after a save remains unverified.
- **Cover:** The user applied the shared episode-two v3 artwork on Douyin and Instagram, supplied two phone profile-grid screenshots and confirmed acceptance, including visible footer text. This covers only those views; phone screenshots and private data remain unpublished.

**113 offline tests pass.** Six added regressions cover old success receipts, misleading body text, matching-text no-op, ineffective saves, reopened read-back and wrong-work redirects. Gmail alerts remain explicit opt-in and off by default.

## 0.3.2: Gmail-only notifications and cover candidate

- **Gmail:** A real plugin sent one clearly labelled TEST message from the connected account to itself. Matching From/To and SENT/INBOX were read back; the user confirmed receipt. Ongoing alerts remained off.
- **Final scope:** Gmail only, off by default, enabled only on an explicit user request. Ordinary setup does not prompt for email. Plugin discovery, installation/connection assistance and exact-account checks precede sending. Outlook, QQ, NetEase and SMTP are excluded; unsupported legacy preferences cannot dispatch.
- **Outlook test history:** A test went to a QQ address returned by the connected profile. It appeared in Sent Items, but the user reported no receipt in QQ or Outlook. This was not delivery success. No retry occurred; Outlook adaptation was removed at the user's request.
- **Cover:** Episode-two v3 shared portrait was delivered for user replacement, with the footer moved upward. Text remains complete in local 3:4/4:5 center-crop previews. This is not phone-platform acceptance; no online cover was changed.

**107 offline tests pass.** Added explicit opt-in, Gmail address/account checks, missing/disconnected plugins, unsupported-provider rejection, one-off test deduplication and uncertain-send suppression. Email addresses, private responses and authorization data are not published.

## 0.3.1: episode-two corrections and acceptance limits

- **YouTube:** The CLI official API updated the same work to original Chinese plus English. Full title/description were read back; category, tags and public visibility were retained. Management authorization completed; first connection now requests the tool's required scopes together.
- **Instagram historical correction:** The earlier record reported successful Chinese-plus-English editing. On 2026-10-30, the editor still contained English only, so that record cannot establish current state. See 0.3.3 for the correction and new independent read-back.
- **X:** The original post no longer exposed Edit. The operator chose to retain its English copy and use the new policy on the next post; no deletion, repost or reply occurred. A successful available-Edit save remains untested.
- **Douyin:** [Episode two](https://www.douyin.com/video/7702037050320506122) is listed as published. The CLI saved the shared dedicated portrait, visible in the management page and the user's phone screenshot. Six custom chapters at 00:00 / 00:14 / 00:36 / 00:49 / 01:00 / 01:09 were saved and reopened, replacing the default chapter list. No video was reuploaded.
- **Cover limitation:** Both phone grids still crop the footer line. Cover application is confirmed; visual adaptation and aesthetics are not accepted. Next video prioritizes common safe areas, UI overlays, thumbnail text size, contrast and composition; retain the current art this round. Local SHA-256 does not establish equality with platform image pixels.

**98 offline tests pass.** Added language matrix, bilingual-section, shared-portrait, existing-work edit and uncertain-save safeguards. YouTube edit tests cover account ownership and retention of category, tags, audio language and visibility. Chapter tests cover time/title pairing and pending-step recovery. These tests are not fully unattended or phone-layout acceptance.

Corrections used the official API or CLI-owned sessions; the user's phone cover selection is a separate manual step. Raw pages, private profiles, sign-in data and phone screenshots are excluded from the public repository.


## 0.3.0 history: episode 02 independent backends

The operator authorized this exact test asset: **GPT Duty Log 02 | Asked for a Song, I Asked a Question First**. Source: 84.4 seconds, 1920×1080, SHA-256 `decb95dbb5ebaac47aa41a9d578c55e17e027d565b2610652ca435277dd70991`. All uploads used the same original bytes. Bilibili was not reuploaded and existing schedules were retained.

| Platform | Observed result |
|---|---|
| YouTube | [API-uploaded episode](https://www.youtube.com/watch?v=zuLz9rEBcj0). Exact English title, public visibility, category 1 (Film & Animation) and processing `succeeded` read back. Production-thumbnail API write succeeded. |
| X | [Studio post](https://x.com/White_Carp2000/status/2115749753178992679). Dedicated session, one media upload, saved production thumbnail and exact English post text read back. Resumed the same Studio asset after selector fixes; no duplicate post. Text AI disclosure is present; native AI badge was not observed. |
| Instagram | [Reel](https://www.instagram.com/reel/DfExzPeiC_i/). Original crop and unchanged video, separate production cover, full English caption and native AI Content label read back. Grid/mobile cropping is not guaranteed. |
| Douyin | One episode-two work submitted with the exact Simplified Chinese title/caption, native AI declaration, public setting and both cover variants. Creator Center displayed the matching 1:24 work under platform review. This is submission evidence, not anonymous playback acceptance. |

X and Instagram passed a fresh CLI read-back after closing their test browsers. Douyin initially reused the dedicated session, but the post-upload restart returned to the official login page; stable session reuse remains an open acceptance item. Its submitted work was retained, and no duplicate was uploaded. YouTube used the public CLI API route. Browser backend development runs included selector repairs while preserving the existing upload; Instagram and Douyin were exercised through a persistent local development harness. These are real publications, but **not a clean-machine, uninterrupted end-to-end acceptance run**. No full continuous playback or cross-OS installation acceptance was performed.

The production cover is the correct episode-two artwork. Douyin accepted the paired contain-and-pad images, but flagged the portrait's large border area as a quality issue. Dedicated portrait composition is preferable; automatic fitting does not guarantee a good thumbnail. Source hashes and UI acceptance do not prove pixel-identical remote assets.

**86 offline tests pass.** New coverage includes single-submit persistence on timeout, profile locks, wrong-account rejection, changed assets, recovery without reupload, Instagram's account-prefixed work links, portrait-only artwork handling and verified-receipt persistence. Tests do not post to platforms or send email.

Unfinished scope: direct X/Instagram official APIs, unified analytics/comments/inbox, YouTube cards/clickable end screens, live API playlist writes and fully unattended multi-platform acceptance. Advanced chapters and edits remain explicit native handoffs. TikTok stays suspended. Private profiles, tokens, raw page captures and receipts are excluded from GitHub.

## 0.2.5 — Historical status

**2026-10-29 live YouTube API connection:** Desktop OAuth completed and `vpub auth status youtube --method api` returned the intended channel. An API read confirmed the existing test video's public visibility and processed upload status. Missing PySocks caused `httplib2` to ignore the configured proxy and time out; installing `PySocks==1.7.1` restored connectivity. It is now an installation dependency and a `doctor` check. This session made read-only calls, with no new upload or platform edit. Live API upload, thumbnail writes and playlist writes remain unverified.

The operator supplied two phone recordings and reported that the one-second lead did not solve the need, while camera-roll portrait covers worked. Sampled frames show cover editing and return to the work; this is not agent-operated phone control or full-playback acceptance. Lead-in functionality is removed. Original-video/shared-artwork handoff is implemented; this rule update did not republish the test work.

72 tests pass. Four current Instagram tests check unchanged video SHA-256, exact Douyin portrait reuse, scoped/idempotent manifests, overwrite guards and truthful missing-artwork state. The 0.2.4 FFmpeg test is historical, not an active publishing feature.

## Earlier live-test records

- **72 offline tests passed:** source/plan integrity, media snapshots, duplicate suppression, durable IDs, native-account checks, language routing, immutable privacy, optional email with single claim and uncertain-send suppression, cover fitting with no cropping, per-platform cover evidence, and playlist membership/deduplication. Added draft receipts, no-publication guards, explicit same-draft authorization, scoped CLI initialization and rejection-only recovery with fresh absence evidence. Tests use synthetic media and mocked services, including original-video/shared-cover handoff tests; they do not create live posts or mail.
- **YouTube:** One original native private upload was verified. The latest Simplified Chinese/English description without external links was saved and read back. The dedicated cover and private GPT Duty Log playlist were saved and read back in Studio. Category changed from People & Blogs to Film & Animation; the saved selection and disabled Save button were observed.
- **Category fix:** Explicit plans, host selection, in-place revision and receipt matching are offline-tested; API assignability and mismatch handling use mocks.
- **Douyin:** The same work stays private. Management now shows the revised Simplified Chinese caption without URLs and the dedicated portrait cover. Landscape cover was not freshly rechecked in this round. Four chapters were submitted. Initial read-back reported review; the user subsequently confirmed mobile chapter visibility.
- **Bilibili:** Existing upstream CLI/work retained; no reupload or operations takeover. Historical stop-file state is not presented as current.
- **Instagram initial publication (historical):** Original-ratio video, production cover, English caption and AI label were published and read back. Initial Share failed; after user reauthentication and a fresh same-account absence check, one retry succeeded, retaining failure/recovery history. The AI Content badge, exact caption and video scene were visible. The selected landscape cover is present, but profile-grid cropping cuts some text; grid fitting remains unresolved.
- **X:** Real upload reached Ready and survived draft save/reopen. After explicit authorization it was submitted once; the original status showed the matching author, exact English text, Made with AI and a 1:19 embedded video. Full playback and anonymous access were not tested. No deletion; custom thumbnails remain unimplemented. [Procedure](DRAFT_TESTING_EN.md).
- **Email:** Handoff, deduplication and result classification have offline tests; no test email was sent and delivery is unverified. A send tool must actually be available at execution.
- **API playlists:** Scope checks, list-before-insert and uncertain-write suppression have mock tests, not live API acceptance.
- **Chapters:** The hover menu entry was exercised. Four starts were checked before submission; the user confirmed mobile visibility; full post-review list inspection by the agent remains pending. [Procedure](CATEGORIES_AND_CHAPTERS_EN.md).
- **Not completed:** YouTube cards/clickable end screens; X/Instagram APIs; unified analytics/comments; clean-machine/cross-OS installation acceptance. TikTok remains suspended.

Private receipts, sign-in data, raw pages and test-account details stay local and are excluded from this code update.

## 0.2.3 — Historical status

- **Instagram deletion:** The operator explicitly authorized deletion of that test work. Confirmation returned to the profile; refresh showed 4 rather than 5 posts and no original work link. Both receipts are retained. Recovery and permanent purge were not tested.
- **Instagram replacement:** The original video was uploaded to an unpublished preview. A dedicated Douyin portrait cover was still cropped in the landscape preview. The operator is inspecting Instagram; replacement publication is incomplete.
- **X Media Studio:** After subscription Studio opened, but its library reported a setup failure after onboarding dismissal and reload. No custom thumbnail was saved; the existing X post was neither deleted nor reposted.
- **Added tests:** Tests reject wrong identities/IDs, missing deletion or absence evidence, premature/duplicate reposts and changed video/settings; the allowed path retains history and cover snapshots.

## 0.2.4 — Historical experiment

- **Instagram original-ratio replacement:** The original-ratio replacement was shared, then deleted by the operator; a refreshed profile confirmed absence. The new 1920×1080 cover-lead version is about 79.5 seconds; sampled frames show cover at 0.5 s and original footage at 1.2 s. Original crop, English caption and AI disclosure were selected before sharing; success and a new work link were observed. The exact 1327-character caption, native AI badge and cover thumbnail were read back; the profile grid still crops the landscape artwork. Mobile cover selection remains operator acceptance.
- **X Studio:** Direct library upload and persistent original 16:9 thumbnail succeeded. Newly authorized publication was read back with matching author, text, 1:19 video and cover; the earlier post remains. Text disclosure is present but the native AI badge was not observed. Full playback remains unverified.
- **New tests:** Tests cover portrait/square passthrough, Instagram-only output, missing artwork, overwrite/duration guards, changed covers, duplicate lead prevention, and real FFmpeg duration/audio/frame-colour/reuse checks.

