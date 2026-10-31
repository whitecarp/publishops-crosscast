> Full command reference for 0.15.1. YouTube commands that use the official API need the paid API add-on. For an introduction, start with the [README](../README_EN.md).

# PublishOps CrossCast

[繁體中文](OPERATIONS.md) · **English**

**X publishing supports subscribed users whose accounts can actually access Media Studio.**

This requirement exists because non-subscribers are limited to **140-second, 512 MB** video uploads and cannot use the Media Studio media-management and custom-thumbnail workflow on which this tool depends. CrossCast uploads videos and sets production thumbnails through Media Studio, so its X backend currently supports subscription accounts with that access. Login or payment alone does not establish access; the CLI checks the library entry and managed account before selecting media. [Official upload limits](https://help.x.com/en/using-x/x-videos) · [Media Studio access](https://help.x.com/en/using-x/media-studio).

### Multi-platform video release operations console

**v0.15.1 alpha · CLI `vpub` · AI-assisted development**

Prepare platform-specific metadata, covers and publication receipts for one original video. Choose any subset of Bilibili, Douyin, YouTube, Instagram and X. Sign in once; the CLI reuses its own local sessions for later uploads.

Default routes: Bilibili uses bili-cli, YouTube uses an AI assistant on YouTube Studio (or its official API with the paid add-on), and Douyin/Instagram/X use a dedicated persistent Chrome profile controlled by the CLI in the background. First login and official verification require the user. Explicit `method: native` retains host-browser handoff. This remains alpha; live development tests are documented separately from unattended acceptance. Comments, work metrics and reports now share CLI commands, with platform-specific validation below. TikTok is suspended; YouTube cards/clickable end screens remain unimplemented.

[Features](FEATURES_EN.md) · [Validation](VALIDATION_EN.md) · [Draft testing](DRAFT_TESTING_EN.md)

## 0.15.1 closeout: fixed scope and repairs

This release adds no new feature scope. Kernel locks release when a process exits; only a positively absent prior owner permits marker cleanup. Active or unknown owners still block a second worker. Receipts and browser data are preserved.

YouTube `resume` completes never-started cover and playlist steps for the same video, after checking the account, original cover and video ownership. It does not resend a thumbnail with an uncertain outcome. A video ID alone no longer completes a receipt with pending steps. Partial browser replays retain previously collected rows; completeness requires a fresh pass with an official end marker. Instagram now waits for its edit menu before selecting the control.

Live check: a private excerpt hard-exited after 8 MiB were acknowledged, then hard-exited again after its video ID was saved but before the thumbnail write. A third process completed the same video and cover and read back private visibility. The test video was deleted and absence confirmed. Episode-two YouTube/Instagram bilingual captions and all six custom Douyin chapters were freshly checked without edits. See [validation](VALIDATION_EN.md).

Douyin/Instagram website byte recovery, large browser lists and nonempty Instagram mentions remain unaccepted. These limits are not advertised as supported; existing functions retain their documented scope without another expanding roadmap.

## Faster commands, upload recovery and complete reads

Collection create/rename/add/remove/delete now reuse one dedicated browser context for preflight, action and fresh readback. Three identical Instagram collection reads took **37.312 s → 17.297 s** on the test machine: about **54% less elapsed time**, with three browser launches reduced to one. This is one development sample, not a latency guarantee. Reads still navigate afresh; uncertain writes are never repeated automatically.

```sh
vpub resume UPLOAD_RECEIPT_KEY
vpub read-all start youtube works --page-size 50 --max-pages 100 --out works.json
vpub read-all start youtube comments --work VIDEO_ID --out comments.json
vpub read-all start youtube collections --out playlists.json
vpub read-all start youtube collection-items --collection PLAYLIST_ID --out items.json
vpub read-all start douyin collections --out collections.json
vpub read-all start instagram collection-items --collection COLLECTION_ID --out saved.json
vpub read-all resume RUN_ID --max-pages 100 --out continued.json
vpub read-all status RUN_ID
```

- **YouTube byte recovery:** new API uploads save the resumable session privately before sending video bytes and checkpoint each acknowledged chunk. `resume` checks the original account, source hash and metadata, queries the server offset, and continues that session. A returned video ID is saved before optional thumbnail/playlist work. Expired or uncertain initiation sessions are not silently replaced. Legacy attempts without a session need inspection. Session URLs stay local and are omitted from CLI receipts.
- **Complete API reads:** YouTube works, playlists, playlist items, top-level comments and all reply pages; Bilibili works/comments delegate to upstream. Each page atomically saves deduplicated rows and its next cursor. The page budget pauses a run instead of dropping its tail. Resume skips committed pages; unavailable rows or missing replies prevent a complete claim. Live data is not a frozen historical snapshot.
- **Browser collections:** Douyin/Instagram collection lists and members follow official next/scroll controls; X folder lists are also readable. Only the server's explicit end marker establishes completeness. Partial results are saved per page. Browser resume reopens and replays the official page, because a persistent scroll cursor is not established. Large browser lists and X folder-member pagination remain unverified; other browser work/comment/message feeds remain bounded.
- **Platform boundary:** X can continue an already-saved Studio asset. Douyin/Instagram uploads interrupted before final submission still require inspection of the original draft; this release does not claim byte recovery for those websites. Final-submit uncertainty remains verification-only, with existing pending Douyin chapter handling preserved. Kernel locks now release on process exit; only a confirmed dead owner permits marker cleanup. Unknown/legacy damaged markers still require inspection; never delete an active lock or browser profile.

The source-language rules, dedicated covers, original Instagram aspect ratio, X access requirement and optional Gmail setting remain in force. [Test evidence](VALIDATION_EN.md). YouTube follows the [official resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

## Use as a Codex skill

The repository includes `skills/publishops-crosscast/SKILL.md` and `agents/openai.yaml`, with display name **PublishOps CrossCast**. Copy the whole `publishops-crosscast` folder into your personal `.agents/skills` directory. Refresh skills or start a new chat if it is not listed yet; invoke `$publishops-crosscast` to use the installed `vpub` CLI.

Codex chooses its progress wording. The skill has its own name, but an exact sentence such as “Reading PublishOps CrossCast skill” is not guaranteed. Installing a skill does not install the CLI or sign in to platforms.

## Private collections and platform differences

`vpub collection` manages exact-ID collections with durable receipts. Instagram Saved and X bookmark folders are private; Douyin work collections group published works and may enter platform review. YouTube uses `playlist`; Bilibili uses upstream bili-cli.

| Platform | Available | Limits |
|---|---|---|
| Instagram | List, items, create, rename, add, remove, delete | Add requires the work to already appear in the loaded Saved picker. Incomplete member reads block changes. |
| Douyin | List, items, create with cover, rename, add, remove, delete | Account eligibility and platform review still apply. Ineligible works are rejected. A collection is distinct from a private favorite folder. |
| X | List, create, rename, delete; bounded items | Named-folder membership is still unavailable. An error page is never treated as an empty folder. |

```sh
vpub collection list instagram
vpub collection rename instagram COLLECTION_ID --title "Ideas" --request-id rename-01
vpub collection items instagram COLLECTION_ID
vpub collection add instagram COLLECTION_ID WORK_CODE --request-id add-01
vpub collection remove instagram COLLECTION_ID WORK_CODE --request-id remove-01
vpub collection create douyin --title "Series" --cover collection.jpg --description "Series description" --request-id create-01
vpub collection add douyin COLLECTION_ID WORK_ID --request-id add-02
vpub collection remove douyin COLLECTION_ID WORK_ID --request-id remove-02
vpub collection delete douyin COLLECTION_ID --request-id delete-01
vpub collection verify RECEIPT_KEY
```

Use IDs returned by CLI reads, not names or guessed URLs. Each new intent gets a new request ID; repeating it returns the existing receipt without resubmitting. Verification reads independently. Member changes require a complete baseline and must preserve every other member. Use read-all for supported collection pagination; member moves remain pending. Bounded reads do not prove absence.

Deleting a collection does not delete its videos or clear Saved/All Bookmarks. Instagram renaming and membership use the official page's own collection client because its edit dialog stalled in testing; the CLI does not export session cookies or hardcode/replay private request templates. Douyin uses official controls and observes their responses. These are background browser operations, not standalone public APIs. [Live results](VALIDATION_EN.md).

## Faster collection and unified resumption

Different platforms use **3 workers** by default, adjustable with `--workers 1–4`. Reads on one platform stay sequential. Browser batches reuse the dedicated signed-in context; YouTube reuses its connection, channel and work-ownership reads within one run. Each platform has an independent checkpoint journal; local observation merges are serialized. `--progress` emits body-free JSON progress to stderr, leaving stdout as the final JSON result.

```sh
vpub ops sync --platforms youtube,douyin,instagram,x --workers 3 --progress
vpub messages sync --platforms douyin,instagram,x --sources comments,mentions,dm_preview --progress
vpub messages resume RUN_ID --progress
vpub ops runs
```

Comments, mentions and conversation previews now share bounded collection, 300-second successful-read caches and resumable runs. `messages resume` skips completed items; unfinished browser sources restart from their page. `--full` bypasses freshness caches but retains item limits. `sources` distinguishes OBSERVED, CACHED, FAILED and UNSUPPORTED. Unsupported or unconfigured sources retain PARTIAL status; they are not a claim of complete account coverage. YouTube mentions/DMs are not integrated.

Sync reports exclude private bodies; use `messages list` to inspect locally observed content. Preview counts are not new-DM counts, and opening platform inboxes may mark platform notifications read. These commands do not reply, email or create schedules. `account_health` describes this run's reads only; cached data does not establish a current login.

One same-machine, same-account comparison of 10 forced reads across four platforms took **55.446 seconds** on 0.11 and **27.398 seconds** on 0.12, with the first saved result at about **3 seconds**. This is a development sample, not a throughput or latency guarantee. Cached results are not counted as faster live queries.

## YouTube playlist management

Use the official API for ordinary playlists owned by the signed-in channel: create, rename, change description/privacy, add/remove videos, reorder and delete. Creation defaults to private. Renaming preserves description, language and privacy. Move/remove use the exact **ITEM_ID** from `playlist items`, not VIDEO_ID; positions are zero-based.

```sh
vpub playlist list
vpub playlist create --title "My series" --privacy private --request-id series-create-01
vpub playlist update PLAYLIST_ID --title "New series title" --request-id series-rename-01
vpub playlist items PLAYLIST_ID
vpub playlist add PLAYLIST_ID VIDEO_ID --request-id series-add-01
vpub playlist move PLAYLIST_ID ITEM_ID --position 0 --request-id series-move-01
vpub playlist remove PLAYLIST_ID ITEM_ID --request-id series-remove-01
vpub playlist verify RECEIPT_KEY
```

Delete a playlist with `vpub playlist delete PLAYLIST_ID --request-id series-delete-01`; this does not delete its videos. Use a new request ID for each intended new change and reuse the original after interruptions. The CLI never resubmits an existing request. For `UNVERIFIED`, use `playlist verify`, not another write. A lost creation response without a playlist ID remains unresolved; a matching title is not enough to adopt a result. Post-write checks retry reads within a short bound, never writes.

These commands require `youtube.force-ssl`, included in the default first-login authorization. If the API returns `manualSortRequired`, set that playlist's ordering to Manual in YouTube; the CLI does not change the sorting mode. Official references: [playlist updates](https://developers.google.com/youtube/v3/docs/playlists/update), [item ordering](https://developers.google.com/youtube/v3/docs/playlistItems/update).

Private saved collections use the separate `collection` commands above. They are not public video-series equivalents.


## Adopt existing works and resume collection

```sh
vpub ops discover instagram --pages 3 --limit 50
vpub ops adopt instagram WORK_ID
vpub ops sync --platforms youtube,douyin,instagram,x --out round.json
vpub ops runs
vpub ops run RUN_ID
vpub ops resume RUN_ID --out resumed.json
vpub ops sync --platforms youtube,douyin,instagram,x --full
```

`discover` explores the signed-in account within page and item budgets. Bilibili/YouTube use their existing pagination; the other platforms use official-page pagination or scrolling. Browser results retain `complete: false` and a stop reason: lack of visible continuation does not prove a complete archive. Discovery saves a local catalogue and does not adopt every result.

`adopt` freshly verifies one work and adds it to the watchlist. YouTube/Bilibili use existing owner checks; Douyin checks an authenticated creator card and exact detail ID; Instagram checks the exact work's owner Edit control; X checks the exact post permalink and author. Previously manual uploads can be adopted with separate ownership evidence, without fabricating an upload receipt or republishing. This enables comment/statistics operations, not automatic deletion or video edits.

`ops sync` reuses successful reads for **300 seconds** when account, work and read scope match. `CACHED` retains the actual observation time, not a claim of a fresh platform read. Use `--min-interval 0` or `--full` to force reads. `changes` contains newly observed or edited comments; first observations establish a baseline, not a new-arrival timestamp. Existing handling marks and history remain. Metric comparisons retain source, unit and exact-value checks. This is freshness-bounded incremental collection, not push delivery or a full historical mirror.

Each completed step is saved. API comments and replies retain validated pages and continuation tokens; browser batches save completed reads individually. After interruption, find the run ID with `ops runs`, then `ops resume` skips finished steps. An unfinished browser work restarts from its page; no durable scroll cursor is invented. Resuming an already completed run performs no reads. Verification challenges, account changes or unknown results stop that platform while retaining progress; other platforms may continue.

`--full` still obeys page/item budgets. Work and message sources now share resumable runs as described above. Collection creates no schedule and sends no replies or email. Raw comments, catalogues and receipts remain local.


## Likes, saves and reversals

After first login, use the same CLI commands for an exact work. Bilibili reuses bili-cli and YouTube uses its official API (paid add-on required). Douyin, Instagram and X reuse the CLI's dedicated background Chrome session without step-by-step host-browser operation.

| Platform | Like / unlike | What save / remove actually means |
|---|---|---|
| Bilibili | bili-cli | Selected favorite folder; a unique default is used if omitted. Removal preserves membership in other folders. |
| YouTube | Official video rating API | Selected ordinary playlist owned by the account; no Watch Later, Likes-list or legacy Favorites management. |
| Douyin | Exact video's Like control | Default favorite/save for that video. |
| Instagram | Post toolbar Like | Default Saved. Named containers use `collection`; comment likes use separate commands. |
| X | Exact post's Like | Default private Bookmarks. Named containers use `collection`; X Lists are separate. |

```sh
vpub engage status instagram WORK_ID
vpub engage like instagram WORK_ID
vpub engage like instagram WORK_ID --remove
vpub engage save x STATUS_ID
vpub engage save x STATUS_ID --remove
vpub engage collections bilibili
vpub engage save bilibili BV_ID --collection FOLDER_ID
vpub engage collections youtube
vpub engage save youtube VIDEO_ID --collection PLAYLIST_ID
vpub engage save youtube VIDEO_ID --collection PLAYLIST_ID --remove
vpub engage verify RECEIPT_KEY
```

`status`, `collections` and `verify` are read-only. Choose folder/playlist IDs from `collections`; YouTube supports continuation through `--cursor`. Commands support `--out result.json`. `like` and `save` execute directly with optional `--dry-run`, without another per-action approval step.

Exact Bilibili folder membership uses a read-only helper in the included bili-cli; writes still use its CLI. That internal interface needs revalidation whenever the included copy is updated. Missing membership information never triggers guessing or removal from other folders.

The CLI checks the account and current state first; an already matching state returns `NO_CHANGE`. It writes a durable receipt before one submission and confirms the result through an independent read. Only matching read-back produces `ACTION_VERIFIED`. For `ACTION_UNVERIFIED`, use read-only `verify`, retain the receipt and do not repeat or reverse the pending attempt. Login challenges and ambiguous controls stop the command. Account-state evidence is not organic growth; the [YouTube rating API](https://developers.google.com/youtube/v3/docs/videos/rate) also distinguishes account ratings from official like counts.

Reports distinguish likes, unlikes, saves, removals and pending attempts, deduplicated by receipt. Explicitly authorized tests can use `--test-id LABEL` to exclude traffic from operations results; the label does not automatically undo an action. Login, installation and `ops sync` do not automatically engage. Bulk interaction, coins, triple actions, reposts and creating collections are outside this release.

## Comments, work metrics and operations reports

Read a selected work's comments, then let the host conversation model interpret the context and compose a reply. **Reply only in the comment author's language, using one language.** Publication metadata's bilingual policy and the report/account language do not apply. The host identifies languages such as English or French and supplies `--language`; the CLI's script hint is not a complete language detector and does not guess unknown cases.

```sh
vpub comments list youtube VIDEO_ID --limit 20 --out comments.json
vpub comments reply youtube VIDEO_ID COMMENT_ID --file reply.txt --language en
vpub comments verify-reply REPLY_KEY
vpub stats youtube VIDEO_ID --out stats.json
vpub preferences --language en
vpub report --language auto --out report.json
```

Replace `youtube` with `bilibili`, `douyin`, `instagram` or `x` for the same command interface. Browser operations currently require an existing CrossCast work receipt to verify the account; ownership is never guessed. `comments reply` sends directly without per-comment approval; `--dry-run` is optional. The host model supplies the copy; the CLI does not chat, translate or append a signature itself.

Before sending, the CLI checks the current comment and account, persists its intent and submits once. An uncertain result uses read-only `verify-reply`, never a blind resend. Ordinary replies reject own comments and threads known to contain an owner reply. Reads cover only the returned page or visible list; `complete: false` is not a full-history claim. YouTube and Bilibili return `next_cursor`; browser adapters do not invent pagination tokens.

| Platform | Comments and replies | Work metrics |
|---|---|---|
| Bilibili | Upstream bili-cli; thread and reply IDs retained | Views, likes, comments, favorites and other supplied fields; creator completion/watch time when available |
| YouTube | Official Data API; work, thread, author, text and reply ID checks | Views, likes and comments; completion/watch duration do not yet use Analytics |
| Douyin | Public work reads; Creator Center replies to top-level comments; fresh reply and parent read back | Creator Center's displayed views, likes, comments, shares, favorites and completion metrics |
| Instagram | Visible comments and Reply; fresh comment ID, author, text and action receipts | Values actually displayed on the work; full Insights is not integrated |
| X | Visible conversation and Reply; fresh status ID, author, text and action receipts | Post views, replies, reposts and likes; post views are not video plays |

Douyin replies use native Creator Center controls and observe requests made by that page to bind the comment ID; this is not a standalone official API. A missing target, ambiguous identical name/text, or nested-reply target stops before sending and retains the receipt. Submission acknowledgement is separate from fresh visibility.

Metrics retain source, observation time and units. Unavailable values stay `null`, not zero. Browser verification records the targeted Reply action and visible result; it does not claim an official API parent relationship. See [validation boundaries](VALIDATION_EN.md).

Reports follow the **user's** language, defaulting to Chinese or English. The host sets `preferences --language zh|en` from the conversation, or uses `report --language zh|en` for one run; an unset preference falls back to the system locale. The output is a natural paragraph based on local execution receipts, counting only read-back replies as completed. Own comments and self-tests are excluded from audience interactions. `--since` accepts a timezone-aware start, and `--notes` accepts sourced findings to follow up. Likes, mentions, private messages and research outcomes are not invented. Reports do not send email automatically.

Only when the user explicitly requests a live test:

```sh
vpub comments self-test youtube VIDEO_ID --test-id hello-test-01
```

This posts one “你好” comment and one “你好” reply on your own work. Reusing the test ID retains the attempt; an uncertain result is not resent. It never runs during login/setup and never deletes comments automatically.

## Multi-platform collection and metric changes

Choose works to watch, then use one command to collect comments, work metrics and a report for that run. This is an on-demand, read-only collection: it creates no schedule and sends no replies, likes or email.

```sh
vpub works youtube --limit 20
vpub works bilibili --limit 20
vpub works instagram --limit 10
vpub ops watch add youtube VIDEO_ID
vpub ops watch add douyin WORK_ID
vpub ops watch list
vpub ops sync --pages 2 --limit 20 --language en --out round.json
vpub ops sync --platforms youtube,douyin --out selected-round.json
vpub ops watch remove youtube VIDEO_ID
```

`works` supports all five platforms. YouTube reads the authorized channel uploads playlist; Bilibili lists published submissions and leaves scheduled/reviewing work alone. These two return a `--cursor` for pagination. Douyin, Instagram and X still expose only the visible first page, not a full account archive. Browser-platform watch targets require an existing CrossCast publication receipt or fresh ownership evidence from `ops adopt`; API platforms check work ownership directly.

`ops sync` processes only the watchlist, up to `--pages` per work and `--limit` top-level comments per API page (a total result cap for browser collection); API reply previews can add entries. The first read establishes a baseline. Later reads deduplicate by comment ID and exclude own comments from audience interactions. The local `inbox` contains observed candidates, not private messages or a full account inbox. `needs_thread_check` means reply status is uncertain and context must be read. Candidates never trigger automatic sending.

Metrics retain historical events and the latest snapshot. `changes.delta` requires exact numbers from the same account, work, source, label and unit. Missing, rounded or changed-source values keep a null delta; percentage changes use percentage points and negative changes are retained. View/play definitions are not added across platforms. A partial failure preserves successful work and returns `PARTIAL` with exit code 4. Challenges or account mismatches are not retried through another route. Each run saves a local receipt and reports only its own events.

### Read the next page of a comment thread

```sh
vpub comments replies youtube VIDEO_ID ROOT_COMMENT_ID --limit 20 --out replies.json
vpub comments replies youtube VIDEO_ID ROOT_COMMENT_ID --cursor NEXT_CURSOR
vpub comments replies bilibili BV_ID ROOT_COMMENT_ID --limit 20
```

These two platforms support paged replies to a top-level comment with work and parent checks. `next_cursor` indicates another page; `complete` describes only the requested scope. This does not turn Instagram, X or Douyin visible comments into a complete historical export or bypass reply deduplication.

## Unified messages and bounded comment collection

```sh
vpub messages sync --platforms douyin,instagram,x --sources comments,dm_preview --language en --out messages.json
vpub messages sync --platforms douyin,instagram,x --sources mentions,dm_preview
vpub messages list --status pending --limit 50
vpub messages list --kind dm_preview --identity ACCOUNT_ID
vpub messages mark MESSAGE_KEY handled
vpub comments collect douyin WORK_ID --pages 3 --reply-pages 1 --limit 50
```

Comments use the existing `ops watch` list and share history and handling marks with `inbox`. Douyin reads Notifications → @ mentions, Instagram reads Notifications → Tags & mentions, and X reads Mentions. All three also read conversation previews. Douyin checks the account on both Creator Center and its public profile. Each source establishes its own first-read baseline, then deduplicates observations and preserves local marks. Changed preview text reopens a stable conversation ID. Where Instagram/Douyin expose no stable ID, entries are content snapshots, not incoming-message counts or recipient identities. Relative-time labels alone do not create new messages.

`messages sync` only collects and reports in the user's language; it creates no schedule and sends nothing. Private bodies stay local and are excluded from reports and GitHub. Opening platform inboxes may change platform read badges; `messages mark` changes only local state. Baselines are not new arrivals, and missing rows on a partial page are never removed. Failed, unsupported and unconfigured sources make the run `PARTIAL`, not zero messages. Douyin/Instagram notification coverage is limited to loaded visible entries. Without an exposed event ID, `stable_source_id: false` marks a content snapshot; identical notifications may coalesce, and preview keys are not reply targets or exact incoming-event counts. A real Douyin mention and repeat-read deduplication passed. Instagram navigation and explicit empty state passed; nonempty mentions still need a live fixture. YouTube DMs and a unified Bilibili message source are not integrated; upstream Bilibili commands remain available.

`comments collect` and browser `ops sync` add bounded scrolling and visible reply expansion, retaining the union of observed IDs and an explicit stop reason. `--pages` bounds read rounds, `--limit` caps browser results in total, and each `--reply-pages` unit permits up to five visible expansion controls; 0 disables deliberate expansion. No complete history, unknown parent relationships or cursors are fabricated. Bilibili/YouTube retain their API page and thread budgets.

### YouTube moderation and X hide/unhide

```sh
vpub comments review youtube VIDEO_ID --status heldForReview
vpub comments moderate youtube VIDEO_ID COMMENT_ID --status heldForReview --dry-run
vpub comments moderate youtube VIDEO_ID COMMENT_ID --status published
vpub comments hide x WORK_ID REPLY_ID --dry-run
vpub comments hide x WORK_ID REPLY_ID --remove
vpub comments verify-action ACTION_KEY
```

Omit `--dry-run` to submit the explicitly selected action directly. YouTube uses `youtube.force-ssl` and only observed viewer comments on owned videos; rejection and author bans are not included. ID-filtered reads omit moderation state, so bounded review queues provide independent evidence. If replies make root/child state ambiguous, the command stops. On 2026-10-30, one viewer test comment completed published → heldForReview → published with fresh read-back. Held comments can disappear from `comments.list(id)`, so the owner queue verifies the exact comment, video and state; absence is not deletion. This covers one top-level comment, not every reply level or account. [Moderation API](https://developers.google.com/youtube/data/reference/rest/v3/comments/setModerationStatus) · [Status-field limits](https://developers.google.com/youtube/data/reference/rest/v3/comments).

X hides/unhides an exact other-author reply under an owned post. One user-supplied comment completed visible → hidden → restored, verified through the exact reply menu. The CLI handles first-use confirmation and the original post's hidden-replies menu entry; disappearance, deletion and blocking are not verification substitutes. Hidden replies remain accessible through X's hidden-replies entry. [Official explanation](https://help.x.com/en/using-x/mentions-and-replies).

For a pending action, first run `comments verify-action ACTION_KEY`; it only reads. If the exact original state is confirmed, explicitly use `comments resume-action ACTION_KEY` to retry once. Only pending hide/moderation receipts qualify, after checking the account, text, original state and current receipt. Unknown states, stale receipts, deletion and editing are rejected. Collection never retries writes automatically.

## Persistent comment inbox and fewer browser starts

`ops sync` groups up to ten watched works for the same platform/account into one CLI-owned browser session. Instagram/X reuse the selected work page for comments and statistics; Douyin still switches between public comments and Creator Center metrics. The batch accepts reads only. A challenge or failed read stops subsequent reads on that platform, preserves completed results and continues with other platforms. This reduces repeated startup; it is not an official API or a fixed speed guarantee.

```sh
vpub ops sync --reply-pages 1 --reply-threads 5 --out round.json
vpub messages list --kind comments --status pending --limit 50
vpub messages list --kind comments --platform youtube --status all --include-own
vpub messages mark COMMENT_KEY handled
vpub messages mark COMMENT_KEY deferred
vpub messages mark COMMENT_KEY unread
```

The local inbox retains observed comments by platform, account, work and comment ID. New entries are unread; read/handled/deferred marks survive later syncs. Changed text reopens an item. A missing comment on a partial page is not deleted from history. Read `last_seen_at` before relying on old entries. `pending` excludes own comments, known answered threads and handled/deferred items; `--cursor` continues local results. `--identity` selects one recorded account. Marking an item performs no platform write and does not send a reply.

For Bilibili/YouTube, sync expands incomplete reply previews within `--reply-threads` (default 5 per work) and `--reply-pages` (default 1 per expanded thread). Set either to 0 to disable expansion. Unfinished threads retain `reply_continuations`; use `comments replies` to continue. Browser reads now support bounded expansion; the unified-message section above defines the separate mention/preview coverage.

## Manage individual comments

```sh
vpub comments list instagram WORK_ID
vpub comments status instagram WORK_ID COMMENT_ID
vpub comments like instagram WORK_ID COMMENT_ID
vpub comments like instagram WORK_ID COMMENT_ID --remove
vpub comments edit youtube VIDEO_ID OWN_COMMENT_ID --text "Updated comment"
vpub comments delete youtube VIDEO_ID OWN_COMMENT_ID
vpub comments like douyin WORK_ID COMMENT_ID
vpub comments like douyin WORK_ID COMMENT_ID --remove
vpub comments delete douyin WORK_ID OWN_COMMENT_ID
vpub comments verify-action RECEIPT_KEY
```

| Platform | New comment controls |
|---|---|
| Bilibili | Like/unlike through pinned upstream bili-cli; exact comment state read back |
| Instagram / X | Like/unlike the exact visible comment; an ambiguous heart stops the action |
| YouTube | Edit/delete your own comment on your own video through the official API |
| Douyin | Creator Center comment like/unlike; delete your own comment without replies on your own work; existing top-level replies remain available |

These are explicit single-comment commands, separate from video likes. Read the comment first. There is no mandatory editorial approval; `--dry-run` is optional. YouTube editing takes the host's single-language text without translating it. Author, account, work and current text are checked before writing. A timed-out or unverified action is retained and blocks another mutation until read-only verification; there is no automatic resend. Test labels use `--test-id` and are excluded from ordinary reports. Deletion is only performed when explicitly requested and is irreversible; login never runs it. Douyin deletion refuses parent comments with replies. Pinning/hiding boundaries are described below; bulk moderation is not implemented.

## Direct messages and platform differences

After first login, `dm` reuses the CLI's own sessions. Douyin, Instagram and X operate their official websites in the background, not standalone private APIs. The host agent writes **one language matching the recipient**. `--language` records that decision; it does not translate messages or detect every language.

```sh
vpub dm list instagram --limit 10
vpub dm read instagram EXACT_HANDLE --limit 20
vpub dm send instagram EXACT_HANDLE --file message.txt --language en
vpub dm send douyin MS4w_PUBLIC_PROFILE_ID --file message.txt --language zh
vpub dm send x EXACT_HANDLE --file message.txt --language en
vpub dm verify RECEIPT_KEY
vpub dm unlock x
```

| Platform | Message scope and limits |
|---|---|
| Douyin | Visible Creator Center conversation summaries; open, read and send text through an exact public profile. Supply the `MS4w…` ID after `/user/`, never a guessed nickname. Recipient restrictions and stranger-message limits still apply. |
| Instagram | Visible inbox summaries; open an exact handle's conversation and read/send text. Display names and similar search results are not recipient identifiers. |
| X | Visible X Chat inbox, exact-handle conversations and text sends. Use `dm unlock x` for the user to enter an official PIN in the dedicated window; the CLI does not read it. Closed inboxes stop sending. |
| Bilibili | Delegates to upstream `dm list/read/send`, retaining its new-contact restrictions; no Bilibili live tests in this round. |
| YouTube | No DM adapter; use the existing comment-reply workflow. |

`list/read` send no messages, but opening an official conversation can mark incoming messages read. Results cover visible entries, not complete history. Installation, login and `ops sync` do not message anyone. Explicit single-message tasks run directly within the user's authorization, with optional `--dry-run`.

The CLI saves account, recipient, preflight state and receipt before one submission, then opens a new session to read back the outgoing message. `DM_VISIBLE` means visible in the sender's conversation, **not recipient delivery or read confirmation**. Resolve `DM_UNVERIFIED` with `dm verify KEY`, never a resend. Repeating the same most-recent successful message also causes no new send. Official challenges, uncertain recipients and changed layouts stop execution. Private message bodies and recipients stay in local receipts; operations reports contain deduplicated states and counts only. Authorized tests use `--test-id LABEL` to stay out of normal reports.

### Pinning and hiding boundaries

`vpub comments pin bilibili BV_ID ROOT_COMMENT_ID` and `--remove` delegate to upstream for top-level comments only. Another pinned comment stops the command instead of being silently replaced; this round covers offline tests only. The observed Douyin and Instagram web layouts exposed no verified comment pin/hide control, so those actions are not integrated. X's profile pin is not comment pinning. Its hide/unhide route and live-test limits are described above. Other unsupported hide actions never substitute deletion, dislike or account blocking. Check `vpub capabilities`; one account's observed web limits are not a claim that the entire platform can never support a feature.

## Install and start

Requires Python 3.10–3.12 and Git for Git-URL installation. The [Windows installer](../install.ps1) finds compatible Python, creates an isolated environment and installs dependencies; missing Python must be prepared by the agent. The background browser routes require Google Chrome. Run `vpub setup --install-browser` if it is missing. YouTube API mode requires a Desktop OAuth client JSON; the other three routes do not need platform developer keys. A standalone model API key is not required.

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
vpub init --video final.mp4 --language zh-Hans --out publish.json --platforms bilibili,douyin,youtube
```

Choose platforms and automatic publication versus editorial review once. Gmail-only email stays off unless explicitly requested. Reuse authorization without repeated editorial approvals. `--confirm` is a plan-integrity hash. The CLI persists email settings; the host remembers editorial preferences.

For upgrades, uninstall the old `whitecarp-video-publisher` package first. The `vpub` command, `video_publisher` Python module and runtime/sign-in/job storage are retained; do not delete user data.

### YouTube API sign-in

Enable YouTube Data API v3 in Google Cloud, create a Desktop app OAuth client and download its JSON. An API key alone cannot authorize uploads. If the project is in Testing mode, add your sign-in account under Google Auth Platform → Audience → Test users to avoid `403 access_denied`.

```sh
vpub auth login youtube --method api --client-json /path/to/client.json
vpub auth status youtube --method api
```

Open the printed official Google authorization link on the same computer running the CLI, choose your channel and grant access. The callback waits for five minutes. Keep client JSON and tokens local; never commit them to GitHub. Set the YouTube manifest's `method` to `api` to use this backend; signing in does not change existing manifests. First connection requests upload, read and video/playlist management permissions together for the user to approve in Google. Old upload/read-only tokens need one reauthorization. Permission does not authorize arbitrary deletion.

If authorization completes but API reads time out, run `vpub doctor`. The paid add-on's dependencies include [PySocks](https://pypi.org/project/PySocks/): without it, the Google client's `httplib2` transport can ignore a configured proxy. In an older environment, run `python -m pip install PySocks==1.7.1` with the same Python environment and retry the status query; do not reupload the video.

## Login once, publish through the CLI

```sh
vpub auth login x
vpub auth login instagram
vpub auth login douyin
vpub auth status all
```

Each first login opens CrossCast's own official Chrome window. Choose QR, password or another option offered by the site and complete any SMS/identity check yourself. Later commands run headlessly with the same local profile. This profile is separate from Codex's browser; no host cookies are exported. Sessions may expire or require verification again.

`onboard` can start only the selected platforms. Existing manifests are not migrated automatically: set `method: browser` for Douyin/Instagram/X and `method: api` for YouTube; `init` now supplies those defaults. `method: native` is the explicit host-agent fallback.

X/Instagram passed a fresh CLI read-back after restart. After reauthentication, several independent Douyin commands reused the session to read and edit episode two; long-term session stability remains unverified. See [validation](VALIDATION_EN.md).

## Copywriting and language

Routing follows the video's original content language, independently of account locale or GitHub documentation:

| Platform | Content language | Description |
|---|---|---|
| Bilibili, Douyin | Chinese, either script | Simplified Chinese only |
| Bilibili, Douyin | Any other language | Original language + Simplified Chinese |
| YouTube, Instagram, X | English | English only |
| YouTube, Instagram, X | Any other language | Original language + English |

Keep the source-language section first, then the target-language section. Matching-language content is not duplicated. Preserve voice, episode identity and events; shorten both sections together to fit limits. This does not translate audio, subtitles or text baked into artwork.

```sh
vpub metadata request publish.json --source script.txt --out request.json
# The host conversation model checks the script and writes response.json.
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
```

Bilingual responses include two `description_parts`; `description` must join them with a blank line. The CLI validates order, mode and length. If the language is unknown, the host inspects the script/subtitles and sets `content_language` first; documentation language is not evidence.

`bili-cli` submits supplied descriptions; the host conversation model writes them. CrossCast adds structured requests and validation without reading hidden history or embedding a translation model. External links are off by default; irrelevant BV references are omitted while context and plain-text third-party credits/licenses remain. Schema checks do not prove translation quality.

## Production covers and original video

```sh
vpub covers prepare localized.json --source production/cover.jpg --portrait production/portrait.jpg --out-dir covers-v1 --out ready.json
```

Landscape roles prefer platform-specific artwork, then the production cover, using contain fitting. Douyin and Instagram portrait roles require one dedicated composition: explicit `--portrait` first, otherwise the manifest's Douyin/Instagram portrait. Both outputs preserve identical image bytes and SHA-256. Landscape padding is no longer a portrait fallback. Missing artwork prompts preparation rather than replacing originals.

| Platform | Working canvas and application |
|---|---|
| Bilibili | 1600×1000; upstream CLI or native browser |
| YouTube | 1920×1080; API or Studio, possibly requiring official verification |
| Douyin | 1200×900 landscape + shared original portrait; CLI uploads both, and the portrait editor crops to 3:4 |
| Instagram | 1920×1080 web artwork + the same shared original portrait for the phone picker; original video ratio |
| X | Landscape artwork uploaded by the CLI through Media Studio; Studio access required |

These are working presets, not permanent platform specifications. Complete local artwork does not prevent platform cropping: inspect platform previews and profile thumbnails. Source SHA-256 is not a remote-byte hash of the platform's compressed file.

### Instagram publishing and covers

**Instagram retains original footage and Original crop, with a separate upload cover.** On a phone, Edit cover → Add from camera roll can select the same dedicated portrait used for Douyin. The 0.2.4 one-second lead-in experiment did not solve this requirement and was removed in 0.2.5. Preparation no longer modifies/transcodes video or requires FFmpeg.

```sh
vpub instagram prepare ready.json --out instagram-ready.json
# Add --mobile-cover production/portrait.jpg if the manifest lacks one.
```

On the phone, open Edit cover, choose Add from camera roll and select the shared portrait. Inspect both the cover and profile-grid previews before saving; different surfaces may crop different regions.

The handoff prefers an explicit `--mobile-cover`, then `covers.douyin.portrait`, then `covers.instagram.portrait`. It records the identical image path and hash; the user performs the mobile step. For future shared artwork, generally consider a 9:16 composition with titles, faces and essential text in a central safe area that also survives 3:4 crops. Avoid edges and UI overlays and inspect both platforms. This is a design guideline, not a guarantee of identical cropping.

**Episode-two shared cover accepted.** On 2026-10-30, the user applied v3 on Douyin and Instagram and confirmed both phone profile-grid screenshots. The footer now sits inside the visible area. Acceptance covers those two supplied views, not every device or display surface. Future artwork should retain shared safe areas and check text size, contrast, composition and playback-count overlays.

Use the production original in new plans, not retained 0.2.4 lead-in experiments. A CLI update does not replace, delete or repost existing publications.

[X Media Studio workflow](X_GUIDE_EN.md) · [Deletion and cover replacement](DELETION_AND_COVERS_EN.md)

For supported platforms, use the following handoff to edit an existing work, checking its original account, work ID and saved result. Instagram mobile covers and X Studio use the dedicated workflows above.

```sh
vpub browser cover RECEIPT_KEY --file landscape.jpg --portrait portrait.jpg --identity OBSERVED_ACCOUNT
```

## Playlists, categories and chapters

YouTube accepts native `playlist_titles` or `playlist_ids`. The API checks membership before inserting and never blindly retries uncertain writes. The required management permission is included in first-time OAuth. Studio creation of a private list and selection for the existing work were verified; the API route remains mock-tested. Bilibili collections remain upstream functionality rather than a unified collection-management command.

New YouTube plans require an explicit `category_id`, selected from content and official choices rather than defaulting to People & Blogs. The host model can supply it via `metadata request/apply`; explicit selections are preserved.

Douyin `method: browser` accepts measured `duration_seconds` and `chapters` in the publication plan. Once a durable published work ID is available, the CLI applies custom chapters, saves and reads each pair back; platform-generated suggestions are not treated as completion. Review delays leave a pending step, completed by later `vpub verify`/`resume` without reuploading. Native Bilibili/Douyin/YouTube handoffs remain available. Starts must come from the same final video, begin at zero and increase within its duration. Translate titles without altering timing.

```json
{
  "duration_seconds": 78.506667,
  "chapters": [
    {"start_seconds": 0, "title": "A late reply"},
    {"start_seconds": 19, "title": "Memories in old comments"},
    {"start_seconds": 46, "title": "Speaking in my own role"},
    {"start_seconds": 57, "title": "Read before replying"}
  ]
}
```

These four timestamps are a historical episode-one example with user-confirmed mobile visibility at the time. The six custom episode-two chapters have now also been saved and fully read back through the CLI. See the [guide](CATEGORIES_AND_CHAPTERS_EN.md) for the chapter entry, 12-character title limit and review receipts. Description timestamps do not prove native chapters are active; missing access or review stays pending.

## Edit an existing publication

```sh
vpub edit RECEIPT_KEY correction.json --dry-run
vpub edit RECEIPT_KEY correction.json
# Read back an uncertain save without saving again:
vpub edit RECEIPT_KEY correction.json --verify-only
```

Instagram caption edits read the original saved text from the existing work’s editor. Page translations, comments and old success receipts are not current-caption evidence. Matching text causes no save; a mismatch is corrected on the same work, then the editor is reopened for read-back. `vpub edit KEY PATCH --verify-only` checks without saving; `EDIT_MISMATCH` means the actual text differs and permits an explicit correction after inspection.

JSON requires `expected_identity` and exactly one group: full `title`/`description` with language fields, a Douyin `portrait` file, or `chapters`/`duration_seconds`. YouTube uses its API; Instagram/X/Douyin use the CLI-owned session. Video bytes and visibility are not changed. Receipts retain the work ID and block automatic retries after uncertain saves. Cover read-back provides a local screenshot for visual inspection; source hashes are not remote pixel comparisons.

X editing is attempted only when the original post exposes Edit. Missing access never triggers deletion, reposting or replies. The operator chose to keep episode two unchanged; the next post will follow the new language policy.

## Publication and receipts

```sh
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm CURRENT_PLAN_HASH
vpub verify RECEIPT_KEY
vpub works instagram
# X only: resume the existing Studio asset before final submission.
vpub resume RECEIPT_KEY
vpub receipts
```

Browser receipts persist the account, exact text, source hashes and final-submit flag. An uncertain submit is never automatically clicked again; `verify` can find a unique new matching work. After submission, `resume` verifies the original work and can complete its planned, unattempted Douyin chapter step; before submission it can continue the saved X Studio asset. Instagram/Douyin interrupted uploads require inspection of the existing draft. `works` lists the visible first page (up to ten Douyin cards), not a complete archive. Advanced native edits use `browser request/begin/record`.

Templates default to public Bilibili/Douyin, private YouTube and account audience for Instagram/X; the host must apply user intent. Signing in never adds publication targets. Preparation, verification waits, platform review and public success are distinct; UI evidence and API verification have separate states.

## Optional email notifications: Gmail only

**Off by default; enable only when explicitly requested by the user.** Ordinary installation/login does not ask for an address or turn on email. This feature supports the host agent's Gmail plugin only: no Outlook, QQ, NetEase, SMTP or SMS.

Only after an enable request does the host ask for the user's own Gmail address. It checks the address and plugin, helps install a missing plugin or authorize an unconnected one, and allows cancellation with email left off. Before sending, the connected account must exactly match the chosen address. **Send from that account to itself only.** External service sign-in aliases are not treated as Gmail mailboxes.

```sh
# Run only when email is wanted; ordinary installation does not configure it
vpub notify configure --to you@gmail.com
# Disable at any time
vpub notify configure --provider off
```

The following is performed by the host agent; ordinary users do not edit JSON:

```sh
vpub notify inspect --email you@gmail.com --inventory host-mail-inventory.json
# A one-off test does not enable ongoing alerts; the same test-id never resends
vpub notify test --email you@gmail.com --test-id setup-test-001 --inventory host-mail-inventory.json
# Use the exact name of your assistant's own Gmail send tool; any assistant works
vpub notify claim RETURNED_ALERT_ID --available-tool YOUR_GMAIL_SEND_TOOL --inventory host-mail-inventory.json
# The host calls that tool once with the returned message, saves its response, then records it
vpub notify record RETURNED_ALERT_ID --claim-token RETURNED_TOKEN --response actual-send-response.json
```

Test messages are labelled TEST, with no fabricated sign-in incident. When official verification interrupts publishing, preserve the existing work and notify inline. Email queues only if enabled. The host must have a usable Gmail plugin at execution time; the CLI cannot send through it when the host is not running. Queued, sent and inbox-delivered are separate states. Timeouts or uncertain results never cause automatic retries. See [live validation](VALIDATION_EN.md).

## Backends and references

Bilibili uses the included copy of [kukemc/bili-cli](https://github.com/kukemc/bili-cli), which also gives you the `bili` command. YouTube uses an AI assistant on YouTube Studio, or its official API with the paid add-on. Douyin/X/Instagram use CLI-owned background browsers, with host-browser handoff as an explicit fallback. See [validation](VALIDATION_EN.md) for actual publication evidence and limits.


## Official API routes for X, Instagram and YouTube

These routes are a separate paid add-on, and YouTube commands below that use the official API need it too. The default browser routes stay free and need no developer app.

## Faster repeat commands

Comment, stats, like, message and other non-publishing browser commands now share one background browser per account, so later commands skip starting Chrome. It runs only on your computer over a private authenticated pipe, and it exits after 5 idle minutes, after 1 hour, after any error, or when `vpub stop` is used. Publishing, `resume`, `edit` and login close it first and run alone. A command that was already sent is never repeated automatically. A second command waits for the first instead of failing, for up to three minutes. To turn this off, set `browser_idle_seconds` to 0 in the settings file or `VPUB_BROWSER_IDLE_SECONDS=0` in the environment.

## See what needs attention

```sh
vpub status
```

`vpub status` reads local records only, so it needs no sign-in and contacts no platform. It shows any active stop and its reason, each unfinished publication with the exact next command and how far its upload got, running background browsers, pending messages, the last sync and how much disk the video copies use. Saved sign-ins it lists are not a live check; use `vpub auth status` for that.

`vpub stop` pauses publishing and browser commands. The tool never clears a stop by itself: once the reason is dealt with, the operator deletes the file that `vpub stop` and `vpub status` name.

## Free up disk space

```sh
vpub cleanup
vpub cleanup --apply
```

Each publication keeps a private copy of the approved video, so a later edit cannot change what gets uploaded. The first command only lists what could go. `--apply` deletes a copy only when every publication using it is verified and the original video is still in place and unchanged, so it can never remove the last exact copy. Publishing the same plan to another platform later makes a new copy from the original.
