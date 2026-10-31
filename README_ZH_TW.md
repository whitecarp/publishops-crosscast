<p align="center">
  <a href="README.md">简体中文</a> · <b>繁體中文</b> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero-zh-tw.svg" alt="一句話，發往你選的所有平台：你對 AI 助手說一句，它寫好各平台文案、調整封面，再逐一到各平台提交；示意發布單顯示 Bilibili 和 YouTube 已核驗，抖音已提交待核驗，Instagram 發布中，X 排隊中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  <b>一句話，發往你選的所有平台。</b><br>
  你的 AI 助手為每個平台寫好標題和簡介、調整好封面，再逐一到各平台提交，每一步都留下回執。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="版本 0.3.0 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <img src="https://img.shields.io/badge/Windows-%E5%B7%B2%E5%AF%A6%E6%B8%AC-555" alt="Windows 已實測">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 授權"></a>
</p>

<p align="center">
  <a href="#-一句話開始">一句話開始</a> ·
  <a href="#-它能幫你做什麼">能做什麼</a> ·
  <a href="#-平台支援">平台支援</a> ·
  <a href="#-安全護欄">安全護欄</a> ·
  <a href="#-安裝">安裝</a> ·
  <a href="#-常見問題">常見問題</a> ·
  <a href="https://whitecarp.github.io/publishops-crosscast/?lang=zh-TW">線上介紹</a>
</p>

> [!IMPORTANT]
> **0.3.0 alpha。** 實測記錄來自[實驗室營運版](https://github.com/whitecarp/agentic-work-lab/tree/main/tools/publishops-crosscast)（0.15.1）和作者的實際使用：YouTube、Instagram、X、抖音都在官方網頁上真實發布並讀回過；B站由作者直接用上游 bili-cli 上傳過，本版的 B站可選路線呼叫的就是它。X 發布需要帳號能進入 Media Studio。營運版的留言、私訊、數據巡檢等功能不在本版裡，本版也不承諾五個平台全自動發布。

## 🚀 一句話開始

把下面這段話交給你的 AI 助手（Codex、Claude Code 這類能執行指令的助手都可以），並把 `final.mp4` 換成你的影片：

```text
請安裝 https://github.com/whitecarp/publishops-crosscast
讀取 skills/publishops-crosscast/SKILL.md，然後執行 vpub doctor。
把 final.mp4 公開發布到 B站、YouTube、抖音、Instagram 和 X，標題和簡介依平台來寫。
```

平台和公開範圍可以依需要修改。第一次登入各平台時，掃碼、密碼和驗證碼由你本人在官方頁面完成。

<details>
<summary>想自己在終端機裡安裝？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 檢查本機相依套件
vpub capabilities  # 查看本機實際能走的發布方式
```

需要 Python 3.10–3.12。這兩個指令都不會登入或發布。Windows 安裝腳本和手動流程見[安裝](#-安裝)。

</details>

## 🎬 發一支影片是什麼樣子

> **你**：把 final.mp4 公開發到 B站和 YouTube，標題口語一點。

| 步驟 | AI 助手和 vpub 在做什麼 |
| --- | --- |
| ✍️ 自動寫文案 | AI 助手自動寫好：B站用簡體中文，YouTube 用英文，並依影片內容選好類別；長度、標籤自動檢查，不夾帶外部連結 |
| 🖼️ 自動調封面 | 自動依 B站 16:10、YouTube 16:9 調整，完整保留畫面，原圖不動 |
| 🧾 自動出方案 | 自動列清平台、帳號、公開範圍和 AI 聲明，產生方案雜湊 |
| 🔁 先預演 | `--dry-run` 跑一遍，不碰平台 |
| 📮 逐一發 | YouTube：官方頁面上傳 → 讀回到作品 → **已核驗**<br>B站：等你掃碼登入 → 上傳完成 → **已提交，待核驗** |

> **AI 助手**：YouTube 已發布，讀回到了作品。B站已提交，等平台審核通過、讀回到作品後，我再標成「已核驗」。

<sub>流程示意，不是實際發布記錄。</sub>

## ✨ 它能幫你做什麼

### ✍️ 自動寫好每個平台的文案

B站、抖音用簡體中文，YouTube、Instagram、X 用英文；要中英雙語時說一聲。文案由你正在用的 AI 助手來寫，vpub 提供寫作要求並檢查長度和格式，不內建模型，也不需要另外設定模型 API Key。YouTube 的類別依影片內容來選，不會一律放進同一個類別。

### 🖼️ 封面自動調整成各平台畫布

依各平台的畫布另存一份封面，完整保留畫面，不裁切、不拉伸、不覆蓋原圖。平台預覽仍可能裁切，提交前看一眼。發 Instagram 直接用原影片，不轉檔；直式封面可以和抖音共用一張。

### 🧾 先出方案，再動手

每次發布先產生一份方案：平台、帳號、公開範圍和 AI 聲明都寫得清清楚楚，並算出一個方案雜湊。影片、封面或文案一改，雜湊就變，舊的確認自動作廢。`--dry-run` 預演不會碰平台。

### 📮 逐一到各平台提交

預設由 AI 助手在官方網頁上傳。B站也可以走 bili-cli、YouTube 也可以走官方 API，由 CLI 直接提交，這兩條路也支援排程發布。Instagram 和 X 可以只上傳到預覽或存成草稿，不按「發布」。

### 🔍 每一步都有回執

每個平台的進度都叫它本來的名字：排隊中 → 發布中 → 已提交，待核驗 → 已核驗。只有從平台頁面或 API 讀回到作品，才算「已核驗」；排隊、登入連結和本機回執都不等於發布成功。

### ✏️ 發出去還能改

走網頁方式發出的作品，可以直接修改標題、簡介和標籤，或者換封面，不用重新上傳影片。

### 📧 需要你時寄信提醒（可選）

平台要你掃碼或驗證時，可以寄一封信提醒你。預設關閉；目前支援 Codex 的 Gmail / Outlook 寄信工具。

## 🧭 平台支援

| 平台 | 預設方式 | 可選方式 | 實測記錄 |
| --- | --- | --- | --- |
| **Bilibili** | AI 助手操作官方網頁 | bili-cli，可排程發布 | ✅ bili-cli |
| **YouTube** | AI 助手操作官方網頁 | 官方 API（自備 OAuth），可排程發布 | ✅ 網頁、API |
| **抖音** | AI 助手協助官方網頁 | 獨立後端（實驗功能） | ✅ 網頁 |
| **Instagram** | AI 助手操作官方網頁，可只存草稿 | — | ✅ 網頁 |
| **X** | AI 助手操作官方網頁，可只存草稿 | — | ✅ 網頁 |
| **TikTok** | — | — | ⏸️ 暫停 |

「實測記錄」來自實驗室營運版的[驗證文件](https://github.com/whitecarp/agentic-work-lab/blob/main/tools/publishops-crosscast/docs/VALIDATION.md)和作者的實際使用，是過去的真實發布，不代表本版重新驗收過，也不代表在你的帳號、地區和平台狀態下一定成功。B站是作者直接用 bili-cli 上傳的，本版經 vpub 呼叫這一步還沒單獨實測。`vpub capabilities` 目前只把 YouTube、Instagram、X 標為已實測，還沒同步這份記錄。

**發布前需要知道**

- **B站、抖音**：目前只能發布聲明為 AI 生成內容的影片；抖音的自訂封面只能走網頁。
- **YouTube API**：未通過 Google 審查的 API 專案，上傳的影片會被限制為私人。
- **Instagram**：網頁和個人檔案格狀版面的封面裁切可能不同，有時要在手機上選封面。
- **X**：發布需要帳號能進入 Media Studio（需要訂閱）；未訂閱帳號的影片上傳上限是 140 秒、512 MB。本工具不設定自訂封面。
- **TikTok**：暫停支援，CLI 會拒絕 TikTok 的登入與發布請求。

**通用邊界**

- 安裝成功不等於已登入或已有發布權限；本機實際能走哪條路，以 `vpub capabilities` 為準。
- 網頁方式需要能操作瀏覽器的 AI 助手；沒有這類助手時，只能準備素材和交接說明。
- 排程發布只在 B站 bili-cli（2 小時到 15 天內）和 YouTube API（需設為私人）兩條路上可用；網頁方式只支援立即發布。
- 目前沒有留言、回覆、私訊、收件匣或創作者數據指令。完整邊界見[平台說明](docs/PLATFORMS_ZH_TW.md)。

## 🔒 安全護欄

用的是你自己的帳號，所以這些規則預設就開著：

- **你說了算**：你說要發布就是授權，平台、帳號和公開範圍都依你說的來，不會因為某個平台已登入就順手加上。內容、帳號、平台、受眾或任務範圍改變了，會再問你。
- **登入你自己來**：掃碼、密碼和驗證碼都在官方頁面由你本人完成。不要把 Cookie、密碼、驗證碼或權杖貼進對話、日誌或儲存庫。
- **不重複上傳**：同一支影片在同一個平台帳號只提交一次；結果不明時只核對原本的任務，不會再傳一次。
- **碰到風控就停**：遇到驗證碼、風控或限流，發布立刻停下，等你處理；用 `vpub stop` 也能隨時手動暫停。
- **原素材不動**：調整後的封面另存為新檔案，原影片和原圖保持原樣。

## 🤝 誰負責什麼

- **你**：說清要發的平台、帳號和公開範圍，需要時掃碼或輸入驗證碼。這句話就是授權；範圍改變時會再問你。
- **AI 助手**：讀取 `SKILL.md`，依平台寫文案；具備瀏覽器能力時，依你的授權操作官方網頁。沒有瀏覽器工具，就無法完成網頁發布。
- **vpub**：建立並檢查方案、調整封面、預演、保存本機狀態與回執；不登入帳號，也不替你確認內容。
- **平台**：負責帳號登入、最終提交與公開；功能、格式限制和收費以當日官方頁面為準。

## 📦 安裝

需要 Python 3.10–3.12。Windows 安裝腳本已經實測；macOS、Linux 尚未驗收。

**Windows 安裝腳本**

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

腳本會印出 `vpub.exe` 的完整路徑，但不會把它加入 PATH。可以直接使用該路徑，或在腳本建立的虛擬環境中執行指令。

**透過 pip 從 Git 安裝**（需要 Git 和相容的 Python）

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub capabilities
```

`doctor` 檢查本機相依套件，`capabilities` 顯示實際後端與驗證邊界；兩者都不會登入或發布。

<details>
<summary><b>想自己動手？看一眼手動流程</b></summary>

```sh
vpub init --video final.mp4 --language zh-Hant --out publish.json --platforms youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub plan localized.json
vpub publish localized.json --dry-run
```

`--dry-run` 不會上傳。`response.json` 由 AI 助手依 `request.json` 中的範本撰寫。每一步的意義、封面處理和草稿模式見[使用指南](docs/USAGE_ZH_TW.md)。

</details>

## 💰 費用與隱私

- **模型**：CrossCast 本身不需要另外購買模型 API，文案由你正在用的 AI 助手完成。
- **可能的費用**：AI 助手的訂閱、平台功能，以及可選的 YouTube API（需自備 Google OAuth，配額與審查由 Google 決定）可能另有費用或限制。本專案不承諾免費或無限使用。
- **本機資料**：帳號工作階段、任務、設定和回執保存在本機應用程式資料目錄，請不要提交到 GitHub。分享清單、截圖或日誌前，先移除帳號識別資訊、作品 ID、私人路徑和憑證。
- **敏感資訊**：不要在對話、日誌或儲存庫中提供 Cookie、驗證碼、密碼、權杖或 OAuth 檔案。

## ❓ 常見問題

<details>
<summary><b>發布前還要我再確認嗎？</b></summary>

不用。你說要發布，就是授權：它依你說的平台、帳號和公開範圍提交。只有內容、帳號、平台、受眾或任務範圍變了，才會再問你。想先看一眼，就說「先別發」，或者用 `--dry-run` 預演。

</details>

<details>
<summary><b>能發布不是 AI 生成的影片嗎？</b></summary>

YouTube、Instagram 和 X 可以，以一般影片發布，不加 AI 標示。B站和抖音這一版只支援聲明為 AI 生成內容的影片，一般影片在產生方案時會被拒絕。

</details>

<details>
<summary><b>只在終端機裡執行 vpub，能直接發出去嗎？</b></summary>

預設的網頁方式不能：CLI 沒辦法操作瀏覽器，只能準備方案和交接說明，網頁上的步驟要由能操作瀏覽器的 AI 助手完成。B站 bili-cli 和 YouTube API 兩條路設定好之後，可以由 CLI 直接提交。

</details>

<details>
<summary><b>進了佇列或有了回執，就算發出去了嗎？</b></summary>

不算。排隊只說明任務記下了；本機回執記錄每一步的證據，但它本身不代替平台證據。有沒有發布成功，以平台頁面或 API 讀回為準。

</details>

<details>
<summary><b>安裝後就能操作五個平台嗎？</b></summary>

不一定。每個平台都需要相應的帳號狀態、功能權限和 AI 助手的瀏覽器能力。請先看[平台說明](docs/PLATFORMS_ZH_TW.md)。

</details>

<details>
<summary><b>需要另外購買模型 API 嗎？</b></summary>

不需要為 CrossCast 本身另外購買模型 API。只有選擇 YouTube API 路徑時，才需要自行設定 Google OAuth；其他費用見[費用與隱私](#-費用與隱私)。

</details>

<details>
<summary><b>提交結果不確定怎麼辦？</b></summary>

先核對同一個任務和平台頁面，不要盲目重新上傳。本機回執可以用 `vpub receipts` 查看，但它本身不代替平台證據。

</details>

## 📚 文件與鳴謝

| | |
| --- | --- |
| 📘 [使用指南](docs/USAGE_ZH_TW.md) | 安裝檢查、發布方案、文案與封面、草稿與回執 |
| 🧭 [平台說明](docs/PLATFORMS_ZH_TW.md) | 各平台路徑、通用不支援項目與重要邊界 |
| 🌐 [線上介紹](https://whitecarp.github.io/publishops-crosscast/?lang=zh-TW) | 專案概覽頁 |
| 🐞 [回報問題](https://github.com/whitecarp/publishops-crosscast/issues) | 問題回饋（請先移除帳號與私密資訊） |

來源鳴謝：[ai-video-lab](https://github.com/whitecarp/ai-video-lab)（開發來源與說明）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。第三方授權詳見 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 授權](LICENSE) 發布，是獨立整合工具，並非任何影音平台的官方產品。
