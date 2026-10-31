# 上傳方式研究

**繁體中文** · [English](RESEARCH_EN.md)

核對日期：**2026-10-29**。下列為來源與設計判斷，不是實發成功證明。

## 內置瀏覽器

實測 YouTube 在 Codex 內置瀏覽器沿用現有登入；Instagram 也進入登入後首頁，能打開照片／影片選擇入口。這只證明本次帳號與介面，不能外推所有地區、帳號或 AI 客戶端。畫面轉接的 Google 登入原型已從發行程式移除。

## YouTube

官方 API 使用 OAuth 與 可恢復上傳，需要開發者端設定；本工具保留為選配，不把設定交給預設流程的觀眾。預設用宿主的官方 Studio 網頁。

來源： [桌面 OAuth](https://developers.google.com/youtube/data/guides/auth/installed-apps), [可恢復上傳](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol), [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).

## TikTok（已暫停）

Direct Post 需要應用與使用者授權，未通過審查的 API client 有私密可見限制；使用者要能預覽、選擇當前允許的受眾並同意上傳。因此，不能把「個人有帳號」說成「公共 API 發布已可用」。本次香港手機登入未成功，現已暫停全部 TikTok 適配，詳見 [狀態說明](TIKTOK_STATUS.md)。

來源： [Direct Post 前置條件](https://developers.tiktok.com/docs/en/content-posting-api-get-started), [內容分享要求](https://developers.tiktok.com/docs/en/content-sharing-guidelines).

## Instagram

Meta 官方 API 文件描述 Professional 帳號的內容發布，Facebook Login 路線另有 Page 關聯及權限要求。不能把普通網站登入當作 API 已授權。本次先用官方網頁，API 整合另列待辦。

來源： [Meta's Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-1ff01566-3509-48bd-a0f4-8571a91ccfdf). Meta 開發者部分頁面本次回傳 429／無法讀取，未將第三方教學當成官方現況。

## Bilibili／抖音

B站復用 bili-cli；抖音官方開放平台有應用能力和授權要求，另保留個人帳號網頁路線。早期版本曾用固定版本的 SAU 輔助填寫表單，現已移除；目前由本工具自己的瀏覽器路線填表、提交一次並保留回執。

來源： [bili-cli](https://github.com/kukemc/bili-cli), [抖音影片發布文件](https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/create-video), [social-auto-upload 固定版本](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59).

## X（Twitter）

先提供官方網頁交接，不要求觀眾申請 API 金鑰。X 的單一帖文需要將標題、說明、hashtag 與 AI 聲明合併；實際文案計數和媒體規格由帳號頁面核對。回執使用 handle 與數字 status ID，驗證作者及網址一致。完整查詢／互動命令尚未實作。

來源： [X 網頁影片上傳](https://help.x.com/en/using-x/x-videos). 實作： [X 操作說明](X_GUIDE.md).

