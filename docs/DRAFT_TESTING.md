# 草稿測試與隱私範圍

**繁體中文** · [English](DRAFT_TESTING_EN.md)

版本：**0.2.2 alpha**，2026-10-29。以下為歷史測試階段，後續狀態見[驗證記錄](VALIDATION.md)。

Instagram 私密帳號及 X 保護帳號仍可能讓已獲准的追蹤者看到內容，不能當作「僅自己可見」。首次設定時區分「自動發布」和「僅預覽／草稿測試」；沿用已明確選定的範圍，不把登入當成發布授權，也不為測試改整個帳號的隱私設定。

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

`--draft-only` 在所選 Instagram／X 選項保存 `submission_mode: draft_only`；其他平台會拒絕這個旗標，不悄悄降級成公開發片。Instagram 專用封面請使用獨立方案或平台專用 `covers`；X 不能接受共用自訂封面。

`publish` 只建立持久交接，`begin` 在選檔前記錄本次嘗試。`final_submission_allowed: false` 要求宿主停在預覽或保存草稿，**不得點 Share／Post 或排期**。CLI 不控制瀏覽器按鈕，必須由執行 Agent 遵守。帳號受眾欄位仍是 `visibility: account`，不偽造平台不存在的 private 選項。

## 本次實測

| 平台 | 驗證內容 | 結果與限制 |
| --- | --- | --- |
| Instagram | 79 秒橫式原片、製作專用封面、1327/2200 英文配文、AI 標籤；Facebook 同步與定時關閉 | 最終預覽可見。退出僅有放棄／取消，提示不保存，已取消退出。未點分享，未確認伺服器完整上傳或持久草稿 |
| X | 160,441,478-byte 原片上傳至 Ready；英文文案及 Made with AI | Close → Save；Unsent posts 列表顯示影片 1:18。重新打開後 Ready、全文及 AI 標記仍在。未點 Post |

上表是首次草稿階段。其後使用者明確改為發布：X 從同一草稿提交一次，原帖作者、完整英文文案、Made with AI 與 1:19 嵌入影片已讀回；完整播放與未登入存取未驗證。Instagram 點分享後顯示「無法分享帖子」，未盲目重試、沒有成功作品 ID。使用者要求先處理 X，且暫不刪除。兩站 API 與跨裝置草稿同步未驗收。影片語音與內嵌字仍為原語言，英文適配指配文。

## 使用者改為發布

只有使用者明確改變要求後才執行：

```sh
vpub browser authorize-publish RECEIPT_KEY --identity OBSERVED_ACCOUNT
# Resume the same preview/draft. Never begin or select the video again.
# Submit once, capture its work ID, reopen it and record publication evidence.
```

命令保留原草稿回執和授權時間，更新方案雜湊及 final_submission_allowed；不呼叫平台、不自動發布、不改帳號受眾。拒絕錯誤帳號、尚未核驗的草稿及重複授權。作品發出後，草稿階段的 published:false 只是歷史紀錄；以最新回執為準。刪除是另外一項操作，本輪未執行。

## 宿主操作

- Instagram：Create → 選擇影片 → 按實際內容選原比例 → Edit 的封面檔案選擇器 → Next → 配文／AI 標籤。不要將 Reels 提示或「分享」當成保存草稿。
- X：Add photos or video 可能先開選單，再選 Upload 才出現檔案選擇器。等待 Ready，設定 Content disclosure → Made with AI，Close → Save，再由 Drafts → Unsent posts 開回同一份草稿。
- X 的本次 Edit media 只觀察到字幕及內容警告，未看到自訂縮圖入口；仍標記不支援，不宣稱專用封面已套用。
- 文案核驗需保留段落；X 的可見 Draft.js 段落讀取可避免 `innerText` 額外空行造成誤判。不讀取隱藏帳號狀態或 Cookie。
- 上傳逾時先檢查同一頁，不重新 begin 或重傳。未確認保存就記預覽，不把「按過保存」當成成功證據。

## 回執

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

`draft_saved: false` → `PREVIEW_READY`；`true` → `DRAFT_SAVED`。兩者都帶 `published: false`，不填作品 ID。驗證要求原帳號、官方頁、精確配文及明確保存狀態；draft-only 不能記成 verified publication。CLI 記錄的是宿主提供的 UI 證據，不是獨立平台查詢。相同影片重跑會去重，不因「尚未發布」而重傳。

## 登入恢復後的實發

Instagram 初次失敗後，使用者重新登入。原帳號的 4 條舊作品中沒有測試片；以新查驗及原錯誤作為證據，記錄 rejected 並執行一次恢復。重試成功，帖子數增為 5，原作品英文全文、AI 標記和影片畫面已讀回。專用橫式封面確已套用，但主頁直式網格裁切文字；完整封面版面驗收仍未通過。X 和 Instagram 作品均保留，未測刪除。

```sh
# Only after explicit rejection and a fresh same-account absence check:
vpub browser retry-rejected RECEIPT_KEY --identity OBSERVED_ACCOUNT --evidence retry-evidence.json
vpub browser begin RECEIPT_KEY --identity OBSERVED_ACCOUNT
```

retry evidence 僅含 identity、官方個人頁 page_url、work_absent:true、短 observation。未知提交或已知作品 ID 一律拒絕重試；保存原拒絕回執，重新 begin 後才選回傳快照。這不是自動重試迴圈。

X 片頭加入封面作為縮圖候選目前只屬待測方案，尚未實作，也不保證平台取第一幀。

## 後續狀態

使用者後續明確授權刪除 Instagram 測試作品，已確認刷新後不存在。替換上傳停在發布前封面檢查，尚無新作品 ID。X 原帖仍保留；使用者訂閱後改用 Media Studio 自訂封面方向，取消片頭封面方案，但媒體庫初始化尚未成功。此段取代以上歷史狀態中的下一步計畫。

相關連結：[X 官方受眾說明](https://help.x.com/en/safety-and-security/public-and-protected-posts) · [Meta 私密帳號說明](https://about.fb.com/news/2021/07/instagram-safe-and-private-for-young-people/)

