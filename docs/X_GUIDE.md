# X：CLI Media Studio 發布

**繁體中文** · [English](X_GUIDE_EN.md)

**X 發布僅面向已訂閱、且帳號實際能進入 Media Studio 的使用者。**

原因是未訂閱帳號的影片上傳上限為 **140 秒、512 MB**，且無法使用本工具所依賴的 Media Studio 媒體管理與自訂封面流程。CrossCast 的 X 後端透過 Media Studio 上傳影片並設定製作封面，因此目前僅支援具備此權限的訂閱帳號。登入或付款本身不代表權限已開通，上傳前仍會核對媒體庫入口與所選帳號。[官方上傳限制](https://help.x.com/en/using-x/x-videos) · [Media Studio 權限](https://help.x.com/en/using-x/media-studio)。

**0.3.1 alpha：** 首次 `vpub auth login x` 開啟官方登入視窗。之後 `publish` 在背景復用 CrossCast 專用會話：核對帳號 → 上傳 Media Studio → 套用製作封面 → 填入按原語言規則產生的完整帖文 → 提交一次 → 讀回原帖。這是程式操作官方網站，不是 X API 用戶端。

```sh
vpub auth login x
vpub auth status x
vpub init --video final.mp4 --language zh-Hans --platforms x --out x.json
# 宿主準備英文內容的英文文案，或其他內容的原文＋英文，以及製作封面。
vpub plan x.json
vpub publish x.json --confirm CURRENT_PLAN_HASH
vpub verify RECEIPT_KEY
vpub works x
```

用 `expected_identity` 綁定目標帳號。上傳前檢查 Studio 權限及其管理中的帳號。處理或選擇器在 Post 前失敗時，`resume KEY` 可接續既有素材；Post 後只驗證。既有回執阻止重傳。`--draft-only` 停在預覽，不保證關閉瀏覽器後仍是持久草稿。此路徑沿用帳號受眾，不修改受保護帳號設定。

製作封面與第二期發布已實測，修復及限制見[驗證記錄](VALIDATION.md)。自訂縮圖需要真正可用的 Media Studio，訂閱本身不是成功保證。Studio 未觀察到原生 AI 標示控制，因此保留明確的 AI-generated 文字聲明。

## 支援條件與官方限制

本專案目前只適配具有實際 Media Studio 存取權的訂閱帳號，不以免費帳號的短片額度作為本後端的支援承諾。X 官方一般網頁上傳說明列出非訂閱帳號上限為 140 秒、512 MB；Media Studio 的自訂封面與媒體管理需要另外符合存取權限。[一般上傳限制](https://help.x.com/en/using-x/x-videos) · [Media Studio 權限](https://help.x.com/en/using-x/media-studio)。

現行官方級別表的傳統 Basic 不含 Media Studio，Premium／Premium+ 包含；平台亦有其他新方案，最終以帳號實際權限為準，不保證所有付費方案都適用。[官方級別說明](https://help.x.com/en/using-x/x-premium)。核對日期：2026-10-30。

## 歷史 native 瀏覽器指南

以下為先前由宿主操作的測試記錄，目前 CLI 支援以上方說明為準。

**繁體中文** · [English](X_GUIDE_EN.md)

本版提供 **內置瀏覽器交接**：登入網址、單一帖文文案、帳號核對、檔案快照、單次提交與回執校驗。Agent 仍須實際操作官方網站；沒有獨立 X API 發布器；一條原創影片已發布並在登入頁讀回，完整播放尚未驗收。

**0.2.2 實測** 影片達 Ready，保存到 Unsent posts 並重新開啟，英文文案、影片及 Made with AI 保留。其後依使用者明確授權從同一草稿發布，原帖已讀回，未刪除。僅自己可見的測試使用 `vpub init --platforms x --draft-only`，此時禁止下方一般流程的最終提交。詳見 [草稿指南](DRAFT_TESTING.md)。

```sh
vpub onboard --platforms x
vpub init --video final.mp4 --out x-publish.json --platforms x
# 修改帳號與文案
vpub plan x-publish.json
vpub publish x-publish.json --confirm CURRENT_PLAN_HASH
```

在平台選項加入 `expected_identity`，值為登入後個人檔案可見的 handle（不含 `@`）。同一瀏覽器確認帳號後才執行 `browser begin`；登入方式由使用者自行選擇。

- **文案：** `plan.platforms.x.post_text` 依序組合標題、說明與 hashtag；`ai_generated: true` 附上 AI 生成聲明。上傳時使用完整 `post_text`，不要重組或截斷。
- **受眾** `visibility: account` 遵循帳號和頁面當前受眾。不是每條影片的「僅自己可見」，不自動修改帳號公開／保護設定。
- **規格** 以網站的文案計數器、影片檢查和帳號權限為準；中文、emoji、網址不能用 Python `len()` 當成 X 的正式計數。Agent 先核對影片時長、大小與格式，有不符即停止，不自動裁片或假設有付費權限。
- **提交** 等待影片處理完成、核對全文與受眾，再點擊一次發布；不確定結果只檢查原帖。
- **回執** 讀回 `https://x.com/HANDLE/status/ID`，記錄數字 ID、原帳號、全文 `post_text` 與各來源欄位。校驗器拒絕另一帳號的網址、不同 ID 或遺漏 AI 說明的全文。

X 官方網頁上傳說明目前列出：未訂閱帳號最多 140 秒、512 MB；付費帳號有不同額度。這是查閱日期的參考，實際帳號介面仍須驗證。

搜尋、作品清單、數據分析、評論與收件箱尚未接成統一 X 命令；不要將此交接流程宣傳為完整 X CLI。

## 自訂封面與訂閱

**自訂封面走需要訂閱權限的 Media Studio。** X 官方資料說明 Media Studio 僅向已驗證訂閱者開放，媒體庫的 Change thumbnail 可選影片影格或上傳圖片；圖片比例應與影片相同。一般發帖頁沒有同等保證，訂閱付款也不代表媒體庫已完成初始化。

[官方權限說明](https://help.x.com/en/using-x/media-studio) · [官方縮圖說明](https://help.x.com/en/using-x/media-studio-faqs)（查閱：2026-10-29）。

**訂閱級別** Media Studio 列於 Premium 與 Premium+，Basic 不包含。[官方訂閱比較](https://help.x.com/en/using-x/x-premium)。

**2026-10-29 後續實測** 原本媒體庫初始化失敗；直接點 Upload Media 上傳原片一次後，素材入庫並可開啟詳情。Change thumbnail → upload 上傳同為 16:9 的製作封面；關閉及重開後封面、標題、簡述保留。依使用者新增的明確授權再從 Studio 發布，已讀到新帖與原封面；舊帖保留。這是宿主 UI 實測，尚未整合為獨立 Studio API 或 CLI 封面回執命令。

流程：確認帳號與素材 → 上傳／選擇既有媒體 → Change thumbnail → 上傳與影片同比例的原封面 → 讀回 → 依授權 Post → 核對新作品連結。資料庫素材 ID 與帖文 ID 不相同；素材入庫不等於發布，也不會自動替換舊帖影片。

本次 Studio 新帖保留文案中的 AI-generated 聲明，但未觀察到一般發帖頁的 Made with AI 標記。不要把文案聲明當成已驗證的原生標記。完整播放與手機顯示仍需另行驗收。

相關連結：[官方來源](https://help.x.com/en/using-x/x-videos)

