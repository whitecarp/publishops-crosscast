# Upload-route research

[繁體中文](RESEARCH.md) · **English**

Checked: **2026-10-29**. Sources and design decisions below do not prove live upload success.

## Native browser

YouTube reused an existing in-app session. Instagram reached an authenticated home page and a photo/video picker. This proves only the tested account/UI state, not all regions, accounts or agents. The Google screen-relay login prototype was removed from the distributed code.

## YouTube

The optional API uses OAuth and resumable upload with developer setup; default viewers use official Studio through the host browser.

Sources: [Desktop OAuth](https://developers.google.com/youtube/data/guides/auth/installed-apps), [resumable upload](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol), [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).

## TikTok (suspended)

Direct Post needs app/user authorization; unaudited API clients face private-viewing restrictions. Its UX requires preview, allowed audience choices and consent. Having an account does not establish public API publishing access. After an unsuccessful Hong Kong phone-number login test, all TikTok integration is suspended; see [status](TIKTOK_STATUS_EN.md).

Sources: [Direct Post prerequisites](https://developers.tiktok.com/docs/en/content-posting-api-get-started), [content sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines).

## Instagram

Meta's API documentation describes content publishing for professional accounts; its Facebook Login route adds Page linkage and permissions. A website login is not API authorization. This iteration uses official web UI, with API integration tracked separately.

Some Meta developer pages returned 429/unavailable; third-party tutorials were not treated as authoritative current behavior.

## Bilibili and Douyin

Bilibili reuses bili-cli. Douyin's open platform has app-capability/authorization requirements, while its official website offers an account workflow. Early versions used pinned SAU helpers to fill forms. They have been removed; this tool's own browser route now fills the form, submits once and keeps the receipt.

Sources: [bili-cli](https://github.com/kukemc/bili-cli), [Douyin video-create documentation](https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/create-video), [social-auto-upload pinned source](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59).

## X (Twitter)

The initial route hands off to the official website without requiring viewer API keys. It combines title, description, hashtags and AI disclosure into one post; verify the actual site's character counter and media constraints. Receipts bind the handle and numeric status ID to the matching author URL. Unified query/interaction commands are not implemented.

Source: [X web video upload](https://help.x.com/en/using-x/x-videos). Implementation: [X guide](X_GUIDE_EN.md).

Related links: [Meta's Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-1ff01566-3509-48bd-a0f4-8571a91ccfdf)

