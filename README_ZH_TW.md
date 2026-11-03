<div align="center">

<img src="assets/hero-zh-tw.svg" alt="發布流程概念示意：一句請求，經 AI 助手寫文案、調整封面後發往五個平台，附範例進度；不是實際發布記錄" width="100%">

<br>

<a href="https://github.com/whitecarp/publishops-crosscast/releases"><img src="https://img.shields.io/badge/version-0.16.0%20alpha-f59e0b" alt="版本 0.16.0 alpha"></a> <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12"> <img src="https://img.shields.io/badge/Windows-%E5%B7%B2%E5%AF%A6%E6%B8%AC-555" alt="Windows 已實測"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 授權"></a>

[简体中文](README.md) | **繁體中文** | [English](README_EN.md)

</div>

## 💡 這是什麼

做完一支影片，還要給每個平台寫標題、改封面、一個個上傳。**CrossCast 讓你的 AI 助手替你做完這些**：你說一句，它寫好文案、調整好封面，發到 B站、YouTube、抖音、Instagram 和 X。發完之後，改稿、回留言、看數據也能交給它。

<p align="center"><img src="assets/one-message.png" alt="概念插圖：一句請求經 AI 助手發往五個平台，不是實際發布記錄" width="88%"></p>

> 🎬 五個平台 · 🤖 Codex、Claude Code 都能用 · 🔑 不用另外設定 API Key · 🛡️ 預設帶護欄 · 🆓 免費開源

> **五個平台都有實發記錄。** 目前仍是 0.16.0 alpha，有些功能還沒在真實帳號上試過。各版本與功能的驗證範圍見[使用說明](docs/GUIDE_ZH_TW.md#-能做什麼試到哪一步)。

## 🚀 一句話開始

**把下面這段話交給你的 AI 助手**，把 `final.mp4` 換成你的影片，它會自己裝好，再帶你登入：

```text
請安裝 https://github.com/whitecarp/publishops-crosscast
讀取 skills/publishops-crosscast/SKILL.md，然後執行 vpub doctor。
把 final.mp4 公開發布到 B站、YouTube、抖音、Instagram 和 X，標題和簡介依平台來寫。
```

每個平台第一次都要你自己掃碼或輸入驗證碼登入一次，之後就在背景發布。

<details>
<summary>或者自己在終端機裡裝</summary>

```bash
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor              # 檢查本機缺什麼
vpub auth login douyin   # 每個平台登入一次
```

需要 Python 3.10–3.12 和 Google Chrome。Windows 已實測，macOS 和 Linux 尚未驗收。完整步驟見[使用說明](docs/GUIDE_ZH_TW.md#-安裝)。

</details>

## 🎬 發一支影片是什麼樣子

> **你**：把 final.mp4 公開發到這五個平台，標題口語一點。

```text
✍️  寫文案    B站、抖音用簡體中文，YouTube、Instagram、X 用英文；需翻譯時原文在前
🖼️  調封面    依每個平台的尺寸調好，原圖不動
🧾  出方案    發到哪、用哪個帳號、誰能看，都列清楚
🔁  先預演    跑一遍流程，不會真的發布
📮  發出去    背景一個個平台發布，在平台上看到作品才算成功
```

> **AI 助手**：五個平台都提交好了。已經在平台上看到作品的標成「已核驗」，還在審核的，通過後我再更新。Instagram 的封面請在手機上換成那張直式圖。

<sub>流程示意，不是實際發布記錄。</sub>

## ✨ 它能幫你做什麼

### ✍️ 寫好每個平台的文案
標題、簡介和標籤由你的 AI 助手寫，CrossCast 檢查。B站、抖音用簡體中文，YouTube、Instagram、X 用英文；影片語言不同時，先保留原文，再附譯文。中文內容在 B站、抖音只用簡體。

### 🖼️ 調好封面
依每個平台的尺寸另存一份，不裁切、不拉伸，也不碰原圖。抖音和 Instagram 要直式封面，可以讓 AI 助手畫一張，也可以從原圖重新排版。

### 📮 登入一次，之後背景發布
依平台支援排程發布（Instagram 除外）、存草稿或停在預覽、帶章節，上傳時加入合集或播放清單。中斷後可以恢復，但尚未提交的嘗試可能需要重新上傳。

### ✏️ 發完還能改
改標題、簡介、封面和章節，各平台不一樣。不是 CrossCast 發的舊作品，認領後也能改。

### 💬 回留言和私訊
讀留言、只用留言者的語言回覆，看 @ 提及，發單則私訊，按讚和收藏，還能置頂、隱藏或刪除留言。每次只做你說的那一則，不群發。

### 📊 看數據
看作品列表、播放和互動，也能一次看多個平台的新留言和數據變化。還能告訴你哪些事沒做完、下一步做什麼。

### 🎛️ B站互動組件
給自己的 B站影片加關聯影片、關注按鈕、投票和評分。

## 📺 支援的平台

- **B站**：用 B站 App 掃碼登入
- **YouTube、抖音**：在 CrossCast 開啟的 Chrome 視窗裡登入一次
- **Instagram**：同樣登入一次；封面要在手機上改，不能排程發布
- **X**：同樣登入一次；帳號需要已訂閱，並能進入 Media Studio

TikTok 暫停支援。注意事項見[使用說明](docs/GUIDE_ZH_TW.md#-平台支援)。

## 🆓 免費與付費

上面這些都免費，MIT 開源。付費附加元件還沒開賣，你現在用不到它；它只多出官方 API 發布、營運報告和郵件提醒這類進階功能，[完整清單](docs/GUIDE_ZH_TW.md#-免費與付費)在使用說明裡。

## 🛡️ 安全護欄

用的是你自己的帳號，所以預設都開著：

- ⏱️ **有上限**：回覆、私訊和按讚都有每日上限，不對同一個人重複相同的話，並標註 AI
- 🛑 **碰到風控就停**：平台要求驗證、風控或限流時，所有平台都停下，等你處理
- 🔁 **不盲目重發**：提交結果不明時去平台上核對，不自動重新提交
- 🔑 **登入你自己來**：不要把 Cookie、密碼或驗證碼貼進對話
- 🖼️ **原素材不動**：封面另存為新檔案，原影片和原圖保持原樣

## ❓ 常見問題

<details>
<summary>發布前還要我再確認嗎？</summary>

不用。你說要發布，就是授權：它依你說的平台、帳號和公開範圍提交。只有內容、帳號、平台、受眾或任務範圍變了，才會再問你。想先看一眼，就說「先別發」。

</details>

<details>
<summary>會不會被封號？</summary>

自動化總有風險。CrossCast 預設限制用量，碰到驗證、風控或限流就停下，等你處理。平台也可能把它的專用瀏覽器當成新裝置，要求你驗證身分，這要你自己在官方頁面完成。

</details>

<details>
<summary>現在能放心用於正式發布嗎？</summary>

還在測試階段。登入會過期，平台也可能臨時要求驗證，長時間無人值守還沒驗收。第一次用時，可以讓 AI 助手先預演一遍，看過方案再正式發布。

</details>

更多問題見[使用說明](docs/GUIDE_ZH_TW.md#-常見問題)。

## 📚 文件

這個儲存庫專注於打磨軟體本身。專案的來歷、研究和製作記錄見 [agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)。

- [使用說明](docs/GUIDE_ZH_TW.md)：每個功能試到哪一步、平台注意事項、安裝步驟
- [功能範圍](docs/FEATURES.md)：各平台能做什麼
- [驗證記錄](docs/VALIDATION.md)：每個版本的真實測試結果
- [線上介紹](https://whitecarp.github.io/publishops-crosscast/?lang=zh-TW)
- [回報問題](https://github.com/whitecarp/publishops-crosscast/issues)：請先移除帳號與私密資訊

來源鳴謝：[agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)（開發來源）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 功能，其程式碼已包含在內）。第三方授權詳見 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 授權](LICENSE) 發布，是獨立整合工具，並非任何影音平台的官方產品。
