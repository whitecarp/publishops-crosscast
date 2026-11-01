# Draft tests and audience boundaries

[繁體中文](DRAFT_TESTING.md) · **English**

Version: **0.2.2 alpha**, 2026-10-29. These are historical test stages; see [validation](VALIDATION_EN.md) for subsequent results.

Instagram private accounts and X protected accounts can still expose posts to approved followers; they are not self-only publication. Distinguish automatic publication from preview/draft testing during onboarding, preserve that choice, and do not change account-wide privacy just to test. Signing in is not publication authorization. [Official X audience guidance](https://help.x.com/en/safety-and-security/public-and-protected-posts) · [Meta private-account guidance](https://about.fb.com/news/2021/07/instagram-safe-and-private-for-young-people/).

## CLI

```sh
vpub init --video final.mp4 --language zh-Hans --platforms instagram,x --draft-only --out drafts.json
# Host prepares English metadata, observes account identities, and sets expected_identity.
vpub plan drafts.json
vpub publish drafts.json --dry-run
vpub publish drafts.json --confirm CURRENT_PLAN_HASH
vpub browser request RECEIPT_KEY
vpub browser begin RECEIPT_KEY --identity OBSERVED_ACCOUNT
```

The flag stores `submission_mode: draft_only` for every selected platform. A route that cannot hold a draft (the paid API routes) rejects it instead of silently publishing. Use a separate Instagram plan or platform-specific `covers` for custom artwork; X rejects a shared custom thumbnail.

The commands create a durable handoff and record the attempt before file selection. A false final-submission flag requires the host to stop at preview or saved draft: **never Share/Post or schedule**. The CLI cannot disable browser controls; the executing agent must honor the flag. Visibility remains account-based rather than inventing a per-post private option.

## Draft-only on the current routes

With the default routes, `vpub publish` does the whole run itself and stops before the final button, so the `browser request` and `browser begin` handoff above is only for `method: native`. Where each platform stops and how the run ends:

| Platform | Stops at | State |
| --- | --- | --- |
| Bilibili | A draft saved through Bilibili's own upload requests and read back from its draft list | `DRAFT_SAVED` |
| YouTube | An unpublished draft in YouTube Studio, before the final Save | `PREVIEW_READY` |
| Douyin | Douyin's own 暂存离开 pressed once; the upload page may then offer a kept draft under 继续编辑, which CrossCast only looks at (it can't show whose draft that is, and opening it may use it up) | `PREVIEW_READY` |
| Instagram | The unpublished preview, which may not survive closing the browser | `PREVIEW_READY` |
| X | The post composer, with the media saved in Media Studio; not a confirmed saved draft | `PREVIEW_READY` |

The paid API routes for YouTube, X and Instagram have no drafts and refuse the flag. A Bilibili or Douyin draft keeps only the title, description, tags and cover, so a plan that also has a schedule, chapters or a collection is refused. CrossCast has no command that removes a draft: delete a Bilibili draft by hand on the 草稿 page of its upload manager. A person can open a Douyin draft from the upload page's 继续编辑 offer, but opening it may use the draft up. A plan with drafts reports `all_drafts_ready`, which is true once every draft platform stopped before publishing (`DRAFT_SAVED` or `PREVIEW_READY`) and sets the exit code (false exits 4), and `drafts_read_back`, which names the platforms whose draft was read back as this draft: today only Bilibili, from its draft list. Douyin's receipt records `draft_offer_seen`, whether the upload page then offered a draft it had not offered before, but that offer cannot show whose draft it is, so it is not counted in `drafts_read_back`. A platform not named in `drafts_read_back` has only a preview, and an Instagram or X preview may not outlive its browser, so never call it saved. Before it selects a video, a Douyin upload stops with `existing_draft_requires_attention` if the upload page is already offering a kept draft: continue or discard that draft there yourself, then run it again. When no platform in the plan was meant to publish, `all_submissions_verified` is `null`, so a draft never reads as a publication. `vpub status` lists the drafts and previews it has a local record of; that does not show the platform still holds them. On a real account Bilibili drafts were saved this way, read back and deleted by hand. On Douyin a draft was kept once, when `vpub resume` finished an earlier attempt, and a person who opened it from 继续编辑 found the exact test title (opening an offered draft used it up once and left it in place another time); CrossCast's own look for that offer has not been run on a real account. The other platforms were not tried through this command.

## Live observations

| Platform | Observed | Result and limit |
| --- | --- | --- |
| Instagram | 79-second landscape source, production cover, English caption, AI label; Facebook sharing and scheduling off | Final preview visible; exit offered Discard/Cancel and warned edits would be lost. Cancel retained preview. No Share, server-upload completion or persistent draft verified |
| X | Source uploaded to Ready with English text and Made with AI | Saved to Unsent posts; 1:18 video displayed. Reopened with Ready media, exact text and AI label retained. No Post |

The table records the initial draft stage. The user then explicitly authorized publication: X was submitted once from that draft, and the original status was read back with matching author, exact English text, Made with AI and a 1:19 embedded video. Full playback and anonymous access remain unverified. Instagram Share returned an error; no blind retry or successful work ID was recorded. The user prioritized X and requested no deletion. APIs and cross-device draft synchronization remain unverified. English adaptation refers to metadata, not translated audio or baked-in text.

## Explicit change to publication

Run only after explicit operator authorization:

```sh
vpub browser authorize-publish RECEIPT_KEY --identity OBSERVED_ACCOUNT
# Resume the same preview/draft. Never begin or select the video again.
# Submit once, capture its work ID, reopen it and record publication evidence.
```

This preserves the draft receipt and authorization timestamp, updates the plan hash and submission flag, and performs no platform action or audience change. Wrong accounts, unverified drafts and repeated authorization are rejected. After publication, the earlier unpublished receipt is historical; consult the current receipt. Deletion is a separate action and was not performed.

## Host procedure

- Select the original ratio, load production artwork in Edit, then fill caption and AI label. Share is not Save draft.
- The media button may open a menu first; choose Upload. Wait for Ready, set AI disclosure, save on close and reopen from Unsent posts.
- The tested X editor offered captions and content warnings, with no custom-thumbnail control observed. Custom thumbnails remain unsupported.
- Verify visible editor paragraphs: X innerText can insert extra blank lines. Read rendered text blocks, never hidden account state or cookies.
- Inspect the same page after timeouts; do not begin/upload again. Only claim a saved draft after read-back.

## Receipts

```json
{
  "outcome": "draft",
  "page_url": "https://x.com/compose/post",
  "identity": "OBSERVED_HANDLE",
  "post_text": "EXACT_APPROVED_AND_OBSERVED_TEXT",
  "media_preview": true,
  "draft_saved": true,
  "observation": "Describe the actual upload status, saved-draft list and reopened media. Never use this example as evidence."
}
```

False means PREVIEW_READY; true means DRAFT_SAVED. Both are unpublished and have no published work ID. Validation checks account, official URL, exact caption and explicit save status; draft-only cannot become a verified publication receipt. Evidence is supplied by the host, not independently queried by the CLI. Repeated runs deduplicate the same media even when unpublished.

## Publication after reauthentication

After reauthentication, the original account still had only four old posts and no test video. The fresh check plus the explicit error permitted one recorded recovery. Retry succeeded, the count became five, and the original reel's exact English caption, AI label and video scene were read back. Production artwork is applied, but the portrait profile grid crops text; full cover-layout acceptance remains incomplete. Both works remain published; deletion was not tested.

```sh
# Only after explicit rejection and a fresh same-account absence check:
vpub browser retry-rejected RECEIPT_KEY --identity OBSERVED_ACCOUNT --evidence retry-evidence.json
vpub browser begin RECEIPT_KEY --identity OBSERVED_ACCOUNT
```

Retry evidence contains only identity, official profile page_url, work_absent:true and a short observation. Unknown submissions or known work IDs cannot retry. Prior rejection is retained; begin before selecting the returned snapshot. This is not an automatic retry loop.

Inserting artwork at the start as an X thumbnail candidate remains a proposed test, not an implemented feature or a guarantee that X selects frame zero.

## Subsequent status — 0.2.3

The operator subsequently authorized deleting the Instagram test work; refreshed absence was verified. Replacement remains at an unpublished cover preview with no new work ID. The X status is retained. Following subscription, Media Studio replaces the cancelled intro-cover approach, but library setup has not succeeded. This supersedes next-step plans in the historical entries above.

