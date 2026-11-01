<p align="center">
  <a href="README.md">简体中文</a> · <b>繁體中文</b> · <a href="README_EN.md">English</a>
</p>

<p align="center">
  <img src="assets/hero-zh-tw.svg" alt="一句話，發往你選的所有平台：你對 AI 助手說一句，它寫好各平台文案、調整封面，再逐一到各平台提交；示意發布單顯示 Bilibili 和 YouTube 已核驗，抖音已提交待核驗，Instagram 發布中，X 排隊中" width="860">
</p>

<h1 align="center">PublishOps CrossCast</h1>

<p align="center">
  <b>一句話，發往你選的所有平台。</b><br>
  你的 AI 助手為每個平台寫好標題和簡介、調整好封面；首次登入之後，CLI 在背景逐一到各平台提交，每一步都留下回執。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.16.0%20alpha-f59e0b" alt="版本 0.16.0 alpha">
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
> **0.16.0 alpha。** YouTube、抖音、Instagram、X 都已真實發布並讀回過，B站也用 bili-cli 上傳並核驗過（部分來自更早的版本），詳見[驗證記錄](docs/VALIDATION.md)。它仍是 alpha：登入會過期，平台也可能臨時要求驗證，長時間無人值守還沒驗收。

## 🚀 一句話開始

把下面這段話交給你的 AI 助手（Codex、Claude Code 這類能執行指令的助手都可以），並把 `final.mp4` 換成你的影片：

```text
請安裝 https://github.com/whitecarp/publishops-crosscast
讀取 skills/publishops-crosscast/SKILL.md，然後執行 vpub doctor。
把 final.mp4 公開發布到 B站、YouTube、抖音、Instagram 和 X，標題和簡介依平台來寫。
```

**開始前準備好這幾樣：**

- **B站**：用 B站 App 掃碼登入一次（透過 bili-cli）。
- **YouTube**：免費發布透過專用 Chrome 在背景操作 YouTube Studio，只有登入或 Google 要求驗證時才會看到瀏覽器。用 CLI 走官方 API 需要付費的 API 附加元件，以及你自己的 Google 桌面 OAuth 用戶端。
- **抖音、Instagram、X**：第一次會開啟 CrossCast 專用的官方 Chrome 視窗，由你自己掃碼或輸入密碼、驗證碼；之後 CLI 在背景沿用這個登入。
- **X**：只能用 Media Studio 發布，帳號必須已訂閱並能進入 Media Studio。
- **可能會有「新裝置」驗證**：CrossCast 透過自己的專用瀏覽器登入，YouTube、抖音、Instagram 或 X 可能把它當成新裝置，要求你驗證身分或找回帳號。請你自己在平台的官方頁面完成；AI 助手不會替你回答驗證。反覆重新登入沒有用，已儲存的登入之後也可能再次被要求驗證。
- **電腦上要有 Google Chrome**：沒有的話，執行 `vpub setup --install-browser` 安裝。

<details>
<summary>想自己在終端機裡安裝？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 檢查本機相依套件
vpub capabilities  # 查看本機實際能走的發布方式
```

需要 Python 3.10–3.12。這兩個指令都不會登入或發布。登入和手動流程見[安裝](#-安裝)。

</details>

## 🎬 發一支影片是什麼樣子

> **你**：把 final.mp4 公開發到這五個平台，標題口語一點。

| 步驟 | AI 助手和 vpub 在做什麼 |
| --- | --- |
| ✍️ 自動寫文案 | B站、抖音寫簡體中文，YouTube、Instagram、X 寫英文<br>YouTube 依影片內容選好類別；長度、標籤都檢查過，不夾帶外部連結 |
| 🖼️ 自動調封面 | 橫式：B站 16:10，YouTube、X 16:9<br>直式：AI 助手自動產生一張直式圖，抖音和 Instagram 共用；影片保持原樣 |
| 🧾 自動出方案 | 列清平台、帳號、公開範圍和 AI 聲明，產生確認碼 |
| 🔁 先預演 | `--dry-run` 跑一遍，不碰平台 |
| 📮 背景提交 | 登入後自動在背景提交影片 |

> **AI 助手**：五個平台都提交好了。已經讀回到作品的標成「已核驗」，還在審核的，通過後我再更新。Instagram 的封面請在手機上換成那張直式圖。

<sub>流程示意，不是實際發布記錄。</sub>

## ✨ 它能幫你做什麼

### ✍️ 自動寫好每個平台的文案

B站、抖音用簡體中文，YouTube、Instagram、X 用英文。影片本身是別的語言時，先保留原文，再附上平台語言的譯文；中文內容在 B站、抖音只寫簡體。文案由你正在用的 AI 助手來寫，vpub 提供寫作要求並檢查長度和格式，不內建模型，也不需要另外設定模型 API Key。YouTube 的類別依影片內容來選。

### 🖼️ 封面自動調整成各平台畫布

依各平台畫布另存封面，完整保留畫面，不裁切、不拉伸、不覆蓋原圖：B站 16:10，YouTube 16:9，X 透過 Media Studio 設定橫式封面。抖音和 Instagram 的直式封面，由 AI 助手（例如 Codex）自動產生一張專用直式圖，兩邊共用，不會拿橫式圖加黑邊湊數。沒有 AI 繪圖時，`vpub covers portrait` 不用 AI，也能把封面中選定的部分重新排成這張直式圖。影片本身不轉成直式，保留原比例。Instagram 的封面要在手機上換。

### 🧾 先出方案，再動手

每次發布先產生一份方案：平台、帳號、公開範圍和 AI 聲明都寫得清清楚楚，並產生一個確認碼。影片、封面或文案一改，確認碼就變，舊的確認自動作廢，保證發出去的就是你確認過的那一版。`--dry-run` 預演不會碰平台。

### 📮 登入一次，之後背景發布

首次登入之後，CLI 自己完成上傳：B站走 bili-cli，YouTube、抖音、Instagram、X 由 CLI 在背景操作專用的 Chrome。YouTube、X 和 Instagram 的官方 API 路徑需要付費附加元件。上傳時可以附上章節（B站、YouTube、抖音），並加入播放清單（YouTube）或合集（B站、抖音）。B站、YouTube、抖音和 X 都支援排程發布；Instagram 不支援，要求排程的方案會被拒絕。`vpub init --draft-only` 只上傳、不發布：B站和 YouTube 會留下未發布的草稿，抖音會按它自己的「暫存離開」，Instagram 和 X 則停在預覽，預覽不一定能保留。

### 🔁 中斷了也能接著做

上傳途中斷線，`vpub resume` 接著原本的任務繼續：X 接著已經存好的 Studio 素材，YouTube 用付費附加元件時從同一個上傳工作階段續傳。YouTube 免費路徑不會重新上傳，已上傳的內容留在 Studio 裡作為草稿。結果不明的提交不會自動再按一次。`vpub status` 只讀本機記錄，列出沒做完的事和下一步的確切指令；`vpub cleanup` 可釋放已發布影片副本佔用的磁碟空間。

### 🔍 每一步都有回執

每個平台的進度都叫它本來的名字：排隊中 → 發布中 → 已提交，待核驗 → 已核驗。只有從平台頁面或 API 讀回到作品，才算「已核驗」；排隊、登入連結和本機回執都不等於發布成功。

### ✏️ 發出去還能改

`vpub edit` 直接修改已發布的作品：B站的標題、簡介、標籤、封面和章節，YouTube 的標題、簡介、縮圖和章節，抖音的標題、簡介、直式封面和章節，Instagram 的貼文說明，X 在出現編輯入口時也能改。不換影片，不改公開範圍。不是 CrossCast 發布的舊作品，先用 `vpub ops adopt` 認領，再用 `vpub edit-work` 修改。

### 💬 留言、按讚和私訊

讀取留言、用留言者的語言回覆，按讚、收藏，在抖音、Instagram、X 上讀寫私訊，查看 @ 提及。還能在平台允許的地方對單則留言置頂、隱藏、刪除或移除，也能管理你的合集和播放清單（調整順序屬於付費附加元件）。每次只做你授權的那一則，不會自動群發。各平台能做的不一樣，詳見[功能範圍](docs/FEATURES.md)。

### 📊 數據和營運報告

查看單支作品的播放和互動，巡檢多個平台的新留言和數據變化。完整的營運報告屬於付費附加元件。

### 📧 需要你時寄信提醒（可選）

*屬於付費附加元件。*

平台要你掃碼或驗證時，可以寄一封提醒信給你。只支援 Gmail，預設關閉，你主動要求時才開啟。

## 🧭 平台支援

| 平台 | 登入 | 發布方式 | 實測記錄 |
| --- | --- | --- | --- |
| **Bilibili** | B站 App 掃碼（bili-cli） | bili-cli，可排程發布 | ✅ bili-cli |
| **YouTube** | 免費：在專用 Chrome 裡登入一次。付費附加元件：Google 桌面 OAuth 用戶端 | 免費：背景 Chrome 操作 YouTube Studio，可加入播放清單、排程發布。付費附加元件：官方 API | ✅ API 和免費路徑 |
| **抖音** | 專用官方 Chrome | CLI 背景瀏覽器，可排程發布、加入合集 | ✅ 已發布 |
| **Instagram** | 專用官方 Chrome | CLI 背景瀏覽器，封面在手機上改，不支援排程發布 | ✅ 已發布 |
| **X** | 專用官方 Chrome | 只能用 Media Studio（需訂閱），可排程發布 | ✅ 已發布 |
| **TikTok** | — | — | ⏸️ 暫停 |

實測記錄見[驗證記錄](docs/VALIDATION.md)。B站是作者直接用 bili-cli 上傳的，`vpub capabilities` 裡 B站仍標為未實測。這些是過去的真實發布，不代表在你的帳號、地區和平台狀態下一定成功。

**發布前需要知道**

- **YouTube**：免費路徑還能在背景讀取作品、數據、留言和播放清單，回覆留言，替影片和留言按讚，編輯或刪除自己的留言，修改標題、說明和縮圖，並建立、修改和刪除播放清單。目前在真實帳號上試過的有：各項讀取，建立、重新命名、刪除一個私人播放清單並在其中新增和移除影片，替影片按讚和收藏、替留言按讚（都已復原），回覆留言，以及修改自己的留言再改回去。置頂留言試過一次，沒能確認成功；修改播放清單的說明或公開範圍在儲存前就失敗了，之後還沒重試；刪除留言還沒用現在更嚴格的「確實刪掉了」檢查試過。其他會造成變更的操作都還沒試過。調整播放清單順序和待審核留言佇列需要付費的 API 附加元件，官方 API 路徑也是。免費的背景路徑可以依標題把影片加進播放清單，也能排程發布（依 Studio 顯示的時區，沒有顯示時依這台電腦的時區），兩者都在最後儲存前讀回確認；這兩項還沒在真實帳號上實測。專案還在測試模式時，要先把登入用的帳號加進「測試使用者」；依 Google 的規定，2020 年 7 月 28 日之後建立、又沒通過審查的 API 專案，上傳的影片會被鎖定為私人。
- **X**：只能用 Media Studio。帳號必須已訂閱並能進入 Media Studio；未訂閱帳號的影片上限是 140 秒、512 MB，本工具不支援。X 目前有兩個版本的網站：已登入的測試帳號仍然使用舊版，未登入的訪客看到的是重建後的新版。CrossCast 的 X 路徑兩種都支援；如果頁面兩種都對不上，指令會回報錯誤並停止、不送出任何內容。新版路徑來自 X 公開的網頁程式碼，還沒有在已登入帳號上試過。舊版上暫時還不能把貼文從書籤夾移除。在真實帳號（舊版）上，建立、重新命名、刪除書籤夾以及替貼文按讚、收藏都確認過；把貼文加入書籤夾試過一次，因為 X 的書籤夾頁面沒有載入出來，在變更任何內容之前就停了，修正後還沒再試。排程發布還沒試過。
- **Instagram**：影片保留原比例；封面必須在手機上改：開啟作品的「編輯封面 → 從相機膠卷新增」，選和抖音共用的那張直式圖，再看一下個人檔案格狀版面的裁切。Instagram 不能排程發布，要求排程的方案會被拒絕。刪除自己的留言還沒在真實帳號上試過。
- **抖音**：長時間維持登入的穩定性還在驗收；網頁上傳中斷後不能續傳，要先檢查既有的草稿。排程發布、加入合集和儲存草稿還沒在真實帳號上試過：只看過它們的控制項，而且抖音頁面沒有說明排程欄位用哪個時區，所以之後請核對頁面上顯示的時間。
- **B站**：合集（建立、重新命名、刪除）和一次透過 CrossCast 儲存並讀回的草稿，已在真實帳號上確認。透過 CrossCast 發布和排程發布、章節、修改、上傳時加入合集、刪除留言，還沒在那裡試過。B站的草稿要自己到上傳管理裡的「草稿」頁面刪除。
- **還沒有的**：TikTok（暫停）、YouTube 資訊卡和可點擊片尾、自動或大量私訊與按讚、完整歷史匯出。

## 🔒 安全護欄

用的是你自己的帳號，所以這些規則預設就開著：

- **回覆、私訊和按讚都有限制：** 每日上限、不對同一個人重複相同的話、標註 AI、不傳送金鑰和未經同意的連結。試圖誘導助手的訊息，或需要真人處理的訊息，會留給你。平台一旦發出警告（要求驗證、風控或限流），整個 CrossCast 都會停下，所有平台都不例外：只有該平台的 `vpub auth login` 還能開啟視窗，讓你自己通過檢查，等你看清它為什麼停下之後，再用 `vpub stop --clear` 解除。關鍵字檢查只能涵蓋常見情況，不是絕對保證。
- **你說了算**：你說要發布就是授權，平台、帳號和公開範圍都依你說的來，不會因為某個平台已登入就順手加上。內容、帳號、平台、受眾或任務範圍改變了，會再問你。
- **登入你自己來**：掃碼、密碼和驗證碼都在官方頁面由你本人完成。不要把 Cookie、密碼、驗證碼或權杖貼進對話、日誌或儲存庫。
- **不重複上傳**：同一支影片在同一個平台帳號只提交一次；結果不明時只讀回核對，不會再傳一次。
- **碰到風控就停**：遇到驗證碼、風控或限流，CrossCast 立刻停下，等你處理。
- **一次只做一件事**：同一時間只有一個發布任務在跑；留言和私訊每次只發你授權的那一則。
- **原素材不動**：調整後的封面另存為新檔案，原影片和原圖保持原樣。

## 🤝 誰負責什麼

- **你**：說清要發的平台、帳號和公開範圍；第一次登入時掃碼或輸入驗證碼，在手機上改 Instagram 封面。這句話就是授權；範圍改變時會再問你。
- **AI 助手**：讀取 `SKILL.md`，依平台寫文案和回覆，產生直式封面圖，替你執行 vpub。
- **vpub**：建立並檢查方案、調整封面，首次登入後在背景呼叫 bili-cli、官方 API 和專用 Chrome 完成提交，保存回執並讀回核對；不替你登入，也不替你確認內容。
- **平台**：負責帳號登入、審核、最終發布與公開；功能、格式限制和收費以當日官方頁面為準。

## 📦 安裝

需要 Python 3.10–3.12 和 Google Chrome。Windows 安裝腳本已經實測；macOS、Linux 尚未驗收。

**Windows 安裝腳本**

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

腳本會印出 `vpub.exe` 的完整路徑，但不會把它加入 PATH。可以直接使用該路徑，或在腳本建立的虛擬環境中執行指令。如果找不到 Python 3.10–3.12，加上 `-Python` 和合適的 `python.exe` 的完整路徑再執行一次。

**透過 pip 從 Git 安裝**（需要 Git 和相容的 Python）

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
```

**登入各平台**

```sh
vpub auth login youtube --method browser
vpub auth login youtube --method api --client-json /path/to/client.json  # 付費 API 附加元件，用它代替上一行
vpub auth login douyin
vpub auth login instagram
vpub auth login x
vpub auth status all
```

登入 YouTube（免費路徑）、抖音、Instagram 或 X 時，會開啟 CrossCast 自己的 Chrome 視窗。平台可能把它當成新裝置，要求驗證或找回帳號：請你自己在平台的官方頁面完成，不要反覆重新登入。

YouTube 的 OAuth 用戶端 JSON 和登入權杖只留在本機，不要提交到 GitHub。

<details>
<summary><b>想自己動手？看一眼手動流程</b></summary>

```sh
vpub init --video final.mp4 --language zh-Hant --out publish.json --platforms bilibili,douyin,youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub covers prepare localized.json --source cover.jpg --portrait portrait.jpg --out-dir covers --out ready.json
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm <確認碼>
vpub verify RECEIPT_KEY
```

`--dry-run` 不會上傳。`response.json` 由 AI 助手依 `request.json` 中的範本撰寫。中斷後用 `vpub resume RECEIPT_KEY` 接著做，用 `vpub receipts` 查看所有回執。

</details>

## 💰 費用與隱私

- **模型**：CrossCast 本身不需要另外購買模型 API，文案由你正在用的 AI 助手完成。
- **可能的費用**：AI 助手的訂閱、X 的訂閱（發布要用 Media Studio），以及 YouTube API 的配額（由 Google 決定）。本專案不承諾免費或無限使用。
- **本機資料**：登入工作階段、任務、設定和回執都保存在本機應用程式資料目錄，請不要提交到 GitHub。分享清單、截圖或日誌前，先移除帳號識別資訊、作品 ID、私人路徑和憑證。
- **敏感資訊**：不要在對話、日誌或儲存庫中提供 Cookie、驗證碼、密碼、權杖或 OAuth 檔案。

## ❓ 常見問題

<details>
<summary><b>發布前還要我再確認嗎？</b></summary>

不用。你說要發布，就是授權：它依你說的平台、帳號和公開範圍提交。只有內容、帳號、平台、受眾或任務範圍變了，才會再問你。想先看一眼，就說「先別發」，或者用 `--dry-run` 預演。

</details>

<details>
<summary><b>YouTube 一定要設定 OAuth 用戶端嗎？</b></summary>

只有付費的 API 路徑需要：API 金鑰本身不能授權上傳，必須用「桌面應用程式」OAuth 用戶端在 Google 頁面授權一次。免費路徑由背景 Chrome 在 YouTube Studio 上發布，不需要 OAuth 用戶端。授權在你自己的電腦上完成，權杖只留在本機。

</details>

<details>
<summary><b>X 沒訂閱能發嗎？</b></summary>

不能。CrossCast 只透過 Media Studio 發布 X，帳號必須已訂閱並能進入 Media Studio。付了費或登入了，不代表 Media Studio 已經開通，上傳前會先核對。

</details>

<details>
<summary><b>Instagram 的封面為什麼要在手機上改？</b></summary>

實測下來，要在手機 App 裡「編輯封面 → 從相機膠卷新增」才能換上和抖音共用的直式圖。AI 助手會準備好這張圖，手機上的操作由你完成。

</details>

<details>
<summary><b>能發布不是 AI 生成的影片嗎？</b></summary>

能。告訴 AI 助手影片是不是 AI 生成的：是的話，各平台都會勾選 AI 生成內容聲明（X 上是貼文文字裡的一行）；不是的話，就以一般影片發布，不加 AI 標示。

</details>

<details>
<summary><b>只在終端機裡執行 vpub，能直接發出去嗎？</b></summary>

能。首次登入之後，CLI 自己在背景完成上傳，不需要 AI 助手一步步操作瀏覽器。登入過期或平台要求驗證時，需要你回到官方頁面處理。

</details>

<details>
<summary><b>提交結果不確定怎麼辦？</b></summary>

用 `vpub verify` 讀回核對，或用 `vpub resume` 接著原本的任務，不要盲目重新上傳。本機回執可以用 `vpub receipts` 查看，但它本身不代替平台證據。

</details>

## 📚 文件與鳴謝

| | |
| --- | --- |
| 🧭 [功能範圍](docs/FEATURES.md) | 各平台能做什麼、用什麼方式做 |
| ✅ [驗證記錄](docs/VALIDATION.md) | 每個版本的真實發布與測試結果 |
| 🐦 [X Media Studio 流程](docs/X_GUIDE.md) | X 的上傳、封面與發布步驟 |
| 🌐 [線上介紹](https://whitecarp.github.io/publishops-crosscast/?lang=zh-TW) | 專案概覽頁 |
| 🐞 [回報問題](https://github.com/whitecarp/publishops-crosscast/issues) | 問題回饋（請先移除帳號與私密資訊） |

來源鳴謝：[agentic-work-lab](https://github.com/whitecarp/agentic-work-lab)（開發來源）· [bili-cli](https://github.com/kukemc/bili-cli)（Bilibili 功能，其程式碼已包含在內）。第三方授權詳見 [NOTICE](NOTICE.md)。CrossCast 以 [MIT 授權](LICENSE) 發布，是獨立整合工具，並非任何影音平台的官方產品。
