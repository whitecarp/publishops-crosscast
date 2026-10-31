<p align="center">
  <a href="README.md">简体中文</a> · <b>繁體中文</b> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero-zh-tw.svg" alt="一句話，發往你選的所有平台：你對 AI 助手說一句，它寫好各平台文案、調整封面，再逐一到各平台提交；示意發布單顯示 Bilibili 和 YouTube 已核驗，抖音已提交待核驗，Instagram 發布中，X 排隊中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  一句話，發往你選的所有平台。<br>
  AI 助手寫好各平台的標題、簡介與封面，再逐一到各平台直接提交，每一步都有回執。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0%20alpha-f59e0b" alt="版本 0.3.0 alpha">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-3776ab" alt="Python 3.10–3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-17213a" alt="MIT 授權"></a>
</p>

<p align="center">
  <a href="#交給-ai-助手">交給 AI 助手</a> ·
  <a href="#協作方式">協作方式</a> ·
  <a href="#平台範圍">平台範圍</a> ·
  <a href="#安裝">安裝</a> ·
  <a href="#費用與隱私">費用與隱私</a> ·
  <a href="#常見問題">常見問題</a> ·
  <a href="docs/USAGE_ZH_TW.md">使用指南</a>
</p>

> [!IMPORTANT]
> **0.3.0 alpha。** 你說要發布，就是授權：CrossCast 依你說的平台、帳號和公開範圍逐一提交，但不承諾五個平台全自動發布。預設的網頁流程需要能操作瀏覽器的 AI 宿主；沒有這類宿主時，助手只能準備素材與交接說明。

## 交給 AI 助手

最簡單的開始方式：把下面這段交給你的 AI 程式助手，並把 final.mp4 換成你的影片檔案。

```text
請安裝 https://github.com/whitecarp/publishops-crosscast
讀取 skills/publishops-crosscast/SKILL.md，然後執行 vpub doctor。
把 final.mp4 公開發布到 B站、YouTube、抖音、Instagram 和 X，標題和簡介依平台來寫。
```

<details>
<summary><b>接下來大概會怎樣？</b>（流程示意，不是實際發布記錄）</summary>

```text
你：把 final.mp4 公開發到 B站和 YouTube，標題口語一點。
AI：好的。我會依平台寫標題和說明、調整封面，然後逐一到各平台提交。B站需要你掃碼登入一次。
你：掃好了。
AI：B站已提交，讀回到作品，已核驗；YouTube 已提交，正在核驗。
```

CLI 不會把待辦、登入連結或本機回執說成「已發布」。最終結果以平台頁面讀回或平台 API 為準。

</details>

## 協作方式

一次發布涉及四個角色，各自的邊界是固定的：

| 角色 | 負責什麼 | 邊界 |
| --- | --- | --- |
| **你** | 說一句要發的平台、帳號與公開範圍；需要時掃碼或輸入驗證碼 | 這句話就是授權；範圍改變時會再問你 |
| **AI 宿主** | 讀取 `SKILL.md`，依平台撰寫標題、說明與標籤；具備瀏覽器能力時依你的授權操作官方網頁 | 沒有瀏覽器工具時，無法完成網頁發布 |
| **`vpub` CLI** | 建立並檢查發布方案、適配封面畫布、`--dry-run` 預演、保存本機狀態與回執 | 不執行瀏覽器登入，也不替你確認內容 |
| **官方平台** | 帳號登入、最終提交與公開 | 平台功能、格式限制與收費以當日官方頁面為準 |

既有授權只在原範圍內沿用；內容、帳號、平台、受眾或任務範圍改變時，會重新請你確認。

## 平台範圍

| 平台 | 預設路徑 | 選配路徑 | 狀態 |
| --- | --- | --- | --- |
| **Bilibili** | 宿主操作官方網頁 | 上游 `bili` CLI | 可用 |
| **YouTube** | 官方網頁交接 | YouTube Data API（需自行設定 OAuth） | 可用 |
| **Douyin** | 宿主協助官方網頁 | 獨立後端 | 網頁可用；獨立後端為實驗功能 |
| **Instagram** | 宿主操作官方網頁，可只存草稿 | 無獨立 API | 可用 |
| **X** | 宿主操作官方網頁，可只存草稿 | 無獨立 API | 可用 |
| **TikTok** | — | — | 暫停支援，CLI 會拒絕登入與發布請求 |

安裝成功不等於已登入或已取得發布權限，實際後端請用 `vpub capabilities` 查看。目前**沒有**通用的評論、回覆、私訊、收件匣或創作者數據命令。完整邊界見[平台說明](docs/PLATFORMS_ZH_TW.md)。

## 安裝

需要 Python 3.10–3.12。Windows 安裝腳本已實測；macOS、Linux 尚未驗收。

**Windows 安裝腳本**

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

腳本會列印 `vpub.exe` 的完整路徑，但不會加入 PATH。可直接使用該路徑，或在腳本建立的虛擬環境中執行命令。

**透過 pip 從 Git 安裝**（需要 Git 與相容的 Python）

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub capabilities
```

`doctor` 檢查本機依賴，`capabilities` 顯示實際後端與驗證邊界；兩者都不會登入或發布。

<details>
<summary><b>想自己動手？看一眼手動流程</b></summary>

```sh
vpub init --video final.mp4 --language zh-Hant --out publish.json --platforms youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub plan localized.json
vpub publish localized.json --dry-run
```

`--dry-run` 不會上傳。`response.json` 由宿主模型依 `request.json` 中的範本撰寫。各步驟的意義、封面處理與草稿模式見[使用指南](docs/USAGE_ZH_TW.md)。

</details>

## 費用與隱私

- **模型**：CrossCast 本身不需另購模型 API，文案由宿主對話模型處理。
- **可能的費用**：宿主訂閱、平台功能，以及選配的 YouTube API（需自備 Google OAuth，配額與審核由 Google 決定）可能另有費用或限制。本專案不保證免費或無限使用。
- **本機資料**：帳戶工作階段、任務、設定與回執保存在本機應用程式資料目錄，請勿提交到 GitHub。分享清單、截圖或日誌前，先移除帳號識別、作品 ID、私人路徑與憑證。
- **敏感資訊**：不要在對話、日誌或儲存庫中提供 Cookie、驗證碼、密碼、權杖或 OAuth 檔案。

## 常見問題

<details>
<summary><b>它會自動發布嗎？</b></summary>

預設 `native` 網頁流程需要能操作瀏覽器的宿主；你說要發布，它就依授權逐一到各平台提交。完成設定與授權後，Bilibili CLI 或 YouTube API 路徑可由 CLI 直接提交；實際後端以方案與 `vpub capabilities` 為準。

</details>

<details>
<summary><b>安裝後就能操作五個平台嗎？</b></summary>

不一定。每個平台都需要相應的帳號狀態、功能權限與宿主瀏覽器能力。請先看[平台邊界](docs/PLATFORMS_ZH_TW.md)。

</details>

<details>
<summary><b>需要另外購買模型 API 嗎？</b></summary>

不需要為 CrossCast 本身另購模型 API。只有選擇 YouTube API 路徑時，才需自行設定 Google OAuth；其他費用見[費用與隱私](#費用與隱私)。

</details>

<details>
<summary><b>提交結果不確定怎麼辦？</b></summary>

先核對同一任務與平台頁面，不要盲目重試上傳。本機回執可用 `vpub receipts` 查看，但它本身不代替平台證據。

</details>

## 文件與鳴謝

| | |
| --- | --- |
| 📘 [使用指南](docs/USAGE_ZH_TW.md) | 安裝檢查、發布方案、文案與封面、草稿與回執 |
| 🧭 [平台範圍](docs/PLATFORMS_ZH_TW.md) | 各平台路徑、通用不支援項與重要邊界 |
| 🌐 [線上介紹](https://whitecarp.github.io/publishops-crosscast/) | 專案概覽頁 |
| 🐞 [回報問題](https://github.com/whitecarp/publishops-crosscast/issues) | 問題回饋（請先移除帳號與私密資訊） |

來源鳴謝：[ai-video-lab](https://github.com/whitecarp/ai-video-lab)（開發來源與說明）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 上游工具）。第三方授權詳見 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 授權](LICENSE) 發布，是獨立整合工具，並非任何影音平台的官方產品。
