# Categories and chapters

[繁體中文](CATEGORIES_AND_CHAPTERS.md) · **English**

## YouTube category

Choose a category from each video's content. AI use alone does not make a video a technology demonstration. The tested GPT character narrative was changed from People & Blogs to Film & Animation and saved in Studio. This is a choice for that video, not a universal default.

Since 0.2.1, new YouTube plans require a string `category_id`. `vpub init` leaves it unset. The host supplies it through `metadata request/apply`, using the script and current Studio/API choices. Copywriting preserves an explicit selection. This validation does not add mandatory editorial approval.

- Native: Details → Show more → Category → Save, then inspect the saved choice. Use `browser revise` for an existing work; its receipt must match `category_id`.
- API: check assignability before upload and reject mismatched read-back as unverified. This path is mock-tested.
- Free route (Studio's own menu): only the categories Studio offers can be chosen, category IDs 1, 2, 10, 15, 17, 19, 20 and 22 to 29. Any other ID is refused with `invalid_category` before anything is uploaded.
- Existing receipts remain readable; supply an explicit category when rebuilding or revising a plan, without clearing history or reuploading.

Official references: [videoCategories.list](https://developers.google.com/youtube/v3/docs/videoCategories/list), [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert).

## Douyin chapters

Observed on 2026-10-29: Content management → the intended work → hover Edit work → Add chapters. Existing chapters use Edit chapters. Clicking the main label can open ordinary metadata editing instead.

1. Use timings from the same final cut and transcript.
2. Seek, choose Manual add, enter the title and confirm. Reopen the form for each chapter.
3. Titles allow at most 12 UTF-16 units; CLI does not truncate them. Fractional seeking can round differently in the chapter list; verify its final whole-second starts.
4. Check the full list, then Save. A chapter-review notice means submitted pending review; keep the work ID and do not resubmit or reupload.
5. After review, reopen Edit chapters and read every start/title and the visible progress-bar setting before recording verified evidence.

Four chapters at 00:00 / 00:19 / 00:46 / 00:57 were submitted. The first read-back showed platform review, then the user confirmed that chapters are visible in the Douyin mobile app. A full desktop read-back has not yet been repeated. The work stays private. Video copy and chapter titles use Simplified Chinese; this documentation uses Traditional Chinese and English.



## 0.3.1: Douyin CLI chapters

Plans with `method: browser` can include custom chapters. The CLI applies them after a durable work ID becomes readable. Review delays leave a step for `verify`/`resume`; no video is reuploaded. Platform-generated suggestions do not count as custom-chapter acceptance.

Episode two's six chapter pairs were saved and independently reopened: 00:00, 00:14, 00:36, 00:49, 01:00 and 01:09. This is saved desktop-list evidence; phone presentation is separate. After an uncertain save, retain the receipt and use `vpub edit ... --verify-only` without another Save.

## Bilibili and YouTube chapters

Both take `chapters` and `duration_seconds` in the manifest, with the same rules as Douyin: whole-second starts that begin at 0, increase and stay inside the measured length of the final video.

- **Bilibili** (CLI route): the chapters are sent through bili-cli right after the upload, and Bilibili reviews them before they show. If Bilibili refuses, the upload stays and the receipt says `chapters_state: NOT_APPLIED`; `vpub verify` reads first and sends them again only after a clear refusal. If it is unclear whether they arrived (`SAVE_UNVERIFIED`) it only reads, and it never overwrites a list that someone else set (`CONFLICT`); to send them anyway, use `vpub edit`.
- **YouTube** (browser route): chapters are timestamped lines in the description, added after your own text: at least three chapters, each at least 10 seconds, and no `<` or `>` in a title. `vpub edit` replaces only the list of timestamped lines that starts at zero. With the paid API, put the lines in the description yourself.

Neither has been tried on a real account.
