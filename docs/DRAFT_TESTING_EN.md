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

The flag stores `submission_mode: draft_only` for selected Instagram/X platforms. Other platforms reject it instead of silently publishing. Use a separate Instagram plan or platform-specific `covers` for custom artwork; X rejects a shared custom thumbnail.

The commands create a durable handoff and record the attempt before file selection. A false final-submission flag requires the host to stop at preview or saved draft: **never Share/Post or schedule**. The CLI cannot disable browser controls; the executing agent must honor the flag. Visibility remains account-based rather than inventing a per-post private option.

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

