# 使用說明

[简体中文](GUIDE.md) · **繁體中文** · [English](GUIDE_EN.md)

[← 回到首頁](../README_ZH_TW.md)

這裡是完整說明：每個功能試到哪一步、每個平台的注意事項、安裝和手動流程。

## 新手完整提示詞

在中國大陸使用 YouTube、Instagram 或 X，需要自己想辦法哦。

把方括號裡的內容換成你的，再把整段交給能執行本機指令的 AI 助手。暫時不知道的留空，助手只問缺少的資訊；不要填入密碼。

> 我不熟悉電腦，請用簡短的話帶我完成第一次發布。
>
> 請從 https://github.com/whitecarp/publishops-crosscast 安裝 CrossCast，使用獨立 Python 虛擬環境，不改動其他 Python 程式的環境。
>
> 先執行 vpub doctor，讀取它給出的 skill，並解釋需要處理的問題。依已有偏好與這段對話，一次只問還不知道的設定：我想用哪些平台、是否已有這些平台的帳號；若要用 YouTube/Instagram/X，這些網站是否能連線；若選 X，是否已訂閱且確實能進入 Media Studio；以後是否每次發布前都先給我看。
>
> 記住我選的平台與「每次先給我看」偏好，不儲存帳號名、所在地或連線方式。只為我選的可用平台執行 onboard。沒有 X 訂閱或實際 Media Studio 權限，就先不選 X。
>
> 登入由我在官方頁面親自完成；不要向我要密碼、Cookie、權杖或驗證碼。遇到平台驗證就停下等我處理，不要反覆嘗試。
>
> 影片：[完整檔案路徑]；封面：[路徑，沒有就寫沒有]；目標平台：[我選的平台]；影片語言：[語言]；是否使用 AI 製作：[是/否]；內容：[真實內容簡述]；誰能看：[公開/私密/帳號設定]。不清楚的必要資訊請先問我，不要預設全平台或公開。
>
> 用 prepare 準備文案與封面，保留原檔。Instagram 和 X 跟隨帳號的公開範圍；Instagram 封面要在手機上換。先做 go --dry-run 本機預演，展示各平台文案、帳號、公開範圍、AI 聲明與封面安排。每個平台首次發布都要等我看過目前方案並說「發」。確認碼不代表同意，不要自動代填核准參數。
>
> 該平台之後依我的發布指令執行；只有我儲存了「每次先給我看」或臨時要求預覽，才每次等我看過方案。方案有實質變化仍要重新核對。偏好不是長期發布授權，CLI 指令執行中不要彈出互動問題。
>
> 完成後給我已核驗的作品連結，結果不明的另行說明。逾時不要盲目重發，先看原任務回執並依狀態繼續。
>
> 最後在 CrossCast 和 bili-cli 的資料目錄（VPUB_HOME、BILI_CLI_HOME 或各自預設位置）之外建立回報資料夾，只放 doctor、status 和相關 receipts 的去識別化摘錄、重現步驟與必要的去識別化對話片段。輸出可能含帳號 ID、私密文案與路徑，不要直接複製。不要匯出任何一個資料目錄、登入資料、瀏覽器資料、完整聊天或原始回執。讓我檢查後，再由我決定是否分享。

助手可用 `vpub preferences --platforms youtube,instagram --show-first yes` 儲存你實際選的平台與每次預覽偏好；這只是例子，不要照搬平台。`vpub preferences --show-first no` 關閉該平台後續每次預覽；目前本機資料目錄裡，每個平台的首次發布仍要展示並等我同意。這不按帳號或頻道分別記錄。只填寫要更改的選項。啟用每次預覽後，助手應使用目前方案的確認碼，方案變更後舊確認不能重用；不會在指令執行到一半時詢問。

### 回報前先移除私密資訊

1. 在本機查看 `vpub doctor`、`vpub status` 和 `vpub receipts`，不要為了回報重新發布或登入。**這些輸出不會自動去識別化，可能含帳號或作品 ID、私密文案與檔案路徑。**
2. 在 CrossCast 資料目錄（`VPUB_HOME`）和 bili-cli 資料目錄（`BILI_CLI_HOME` 或預設位置）之外建立回報資料夾。只放版本、系統、指令、重現步驟、預期與實際結果、錯誤碼，以及本次問題需要的去識別化摘錄。帳號名、ID、作品連結、信箱、私密文字與本機路徑改為一致的占位符；移除密碼、Cookie、權杖及驗證碼。
3. 對話只摘錄重現問題必需的片段，逐條移除私密資訊。**不要提交 CrossCast 或 bili-cli 的整個資料目錄、登入資料（包括 `credential.json` 和 `pending_login.json`）、瀏覽器資料、完整聊天匯出、原始日誌或未去識別化的回執。**
4. 自己開啟回報檔逐項檢查，再決定是否提交到 [GitHub Issues](https://github.com/whitecarp/publishops-crosscast/issues)。助手不應自動上傳回報。

bili-cli 的登入資料獨立存放：若設定了 `BILI_CLI_HOME`，使用該目錄；否則 Windows 使用 `%APPDATA%\bili-cli`（未設定 `APPDATA` 時使用使用者家目錄下的 `bili-cli`），macOS/Linux 使用 `$XDG_CONFIG_HOME/bili-cli`（未設定 `XDG_CONFIG_HOME` 時使用 `~/.config/bili-cli`）。排除 CrossCast 資料目錄並不會自動排除這些位置；它們都不能放入回報包。

## 🎬 發一支影片是什麼樣子

> **你**：把 final.mp4 公開發到這五個平台，標題口語一點。

- ✍️ **自動寫文案**：B站、抖音寫簡體中文，YouTube、Instagram、X 寫英文；影片是別的語言時，先保留原文，再附譯文<br>YouTube 依影片內容選好類別；長度、標籤都檢查過，不夾帶外部連結
- 🖼️ **自動調封面**：橫式：B站 16:10，YouTube、X 16:9<br>直式：AI 助手自動產生一張直式圖，抖音和 Instagram 共用；影片保持原樣
- 🧾 **自動出方案**：列清平台、帳號、公開範圍和 AI 聲明，產生確認碼
- 🔁 **先預演**：`--dry-run` 跑一遍，不碰平台
- 📮 **背景提交**：登入後自動在背景提交影片

> **AI 助手**：五個平台都提交好了。已經讀回到作品的標成「已核驗」，還在審核的，通過後我再更新。Instagram 的封面請在手機上換成那張直式圖。

<sub>流程示意，不是實際發布記錄。</sub>

## ✅ 能做什麼、試到哪一步

這張表只算免費版。有些真實記錄來自更早的版本，也都是作者自己的帳號，不代表在你的帳號、地區和平台狀態下一定成功。

✅ 在真實帳號上跑通過 · 🟡 做好了，還沒在真實帳號上確認

**發布時**

- **公開發布並讀回**：✅ YouTube、抖音、Instagram、X · 🟡 B站
- **排程發布**：🟡 B站、YouTube、抖音、X
- **存草稿或停在預覽**：✅ B站、YouTube、抖音、X · 🟡 Instagram
- **上傳時加入合集或播放清單**：🟡 B站、YouTube、抖音
- **上傳時帶章節**：🟡 B站、YouTube、抖音

**發布之後**

- **修改已發布的作品**：✅ YouTube、Instagram · 🟡 B站、抖音、X
- **認領以前發的作品**：✅ B站、抖音、Instagram、X · 🟡 YouTube
- **作品列表和數據**：✅ 全部五個平台
- **讀留言並回覆**：✅ 全部五個平台
- **管理單則留言**：✅ YouTube、抖音、X · 🟡 B站、Instagram
- **@ 提及**：✅ B站、抖音、X · 🟡 Instagram
- **單則私訊**：✅ 抖音、Instagram、X · 🟡 B站
- **按讚和收藏**：✅ 全部五個平台
- **管理合集和播放清單**：✅ 全部五個平台
- **互動組件**：🟡 B站

<sub>✅ 表示這一列的主要動作至少跑通過一次，不是每個細項都試過。比如 YouTube 的單則留言管理，確認過的是置頂和取消置頂；私訊確認的是傳送方看得到，不代表對方已收到。</sub>

<sub>🟡 的功能已經寫好，也用假平台測過，但真實頁面上可能還有沒發現的問題。每一格的依據見[驗證記錄](VALIDATION.md)，更細的範圍見[功能範圍](FEATURES.md)。</sub>

<sub>表裡沒列出的平台，就是這個平台沒有這項，或免費版不提供。排程發布還沒有一則真正走到發布時間。</sub>

## ✨ 每一項怎麼運作

<details>
<summary><b>✍️ 自動寫好每個平台的文案</b></summary>


B站、抖音用簡體中文，YouTube、Instagram、X 用英文。影片本身是別的語言時，先保留原文，再附上平台語言的譯文；中文內容在 B站、抖音只寫簡體。文案由你正在用的 AI 助手來寫，vpub 提供寫作要求並檢查長度和格式，不內建模型，也不需要另外設定模型 API Key。YouTube 的類別依影片內容來選。

</details>

<details>
<summary><b>🖼️ 封面自動調整成各平台畫布</b></summary>


依各平台畫布另存封面，完整保留畫面，不裁切、不拉伸、不覆蓋原圖：B站 16:10，YouTube 16:9，X 透過 Media Studio 設定橫式封面。抖音和 Instagram 的直式封面，由 AI 助手（例如 Codex）自動產生一張專用直式圖，兩邊共用，不會拿橫式圖加黑邊湊數。沒有 AI 繪圖時，`vpub covers portrait` 不用 AI，也能把封面中選定的部分重新排成這張直式圖。影片本身不轉成直式，保留原比例。Instagram 的封面要在手機上換。

B站（含存草稿）需要提供封面，或在 PATH 中有 ffmpeg 自動擷取畫面。`prepare` 和演練會提前檢查；缺少時用 `--cover 圖片` 或安裝 ffmpeg。找到工具不代表已驗證影片能解碼。

</details>

<details>
<summary><b>🧾 先出方案，再動手</b></summary>


每次發布先產生一份方案：平台、帳號、公開範圍和 AI 聲明都寫得清清楚楚，並產生一個確認碼。影片、封面或文案一改，確認碼就變，舊的確認自動作廢，保證發出去的就是你確認過的那一版。`--dry-run` 預演不會碰平台。

</details>

<details>
<summary><b>📮 登入一次，之後背景發布</b></summary>


首次登入之後，CLI 自己完成上傳：B站走 bili-cli，YouTube、抖音、Instagram、X 由 CLI 在背景操作專用的 Chrome。YouTube、X 和 Instagram 的官方 API 路徑需要付費附加元件，附加元件目前還沒有開賣；這裡的其他功能都不需要它。上傳時可以附上章節（B站、YouTube、抖音），並加入播放清單（YouTube）或合集（B站、抖音）。B站、YouTube、抖音和 X 都支援排程發布；Instagram 不支援，要求排程的方案會被拒絕。`vpub init --draft-only` 只上傳、不發布：B站和 YouTube 會留下未發布的草稿，抖音會按它自己的「暫存離開」，之後上傳頁可能會在「繼續編輯」下提供這份草稿（CrossCast 只看不按，因為這個入口說不出是誰的草稿，而且打開它可能會把草稿用掉），Instagram 和 X 則停在預覽，預覽不一定能保留，所以不會被說成已儲存。

</details>

<details>
<summary><b>🔁 中斷了也能接著做</b></summary>


上傳途中斷線，`vpub resume` 接著原本的任務繼續：X 接著已經存好的 Studio 素材，YouTube 用付費附加元件時從同一個上傳工作階段續傳。YouTube 免費路徑和抖音會回到上傳留下的草稿，從那裡接著完成。只有沒有它的草稿、且 YouTube 沒給過 ID 時，才會重新上傳。Instagram 在按下分享前不會留下任何內容，所以停在那之前的嘗試會從頭開始。如果一直卡在封面，`vpub resume KEY --no-cover` 可以不帶封面完成；`--cover 檔案` 可以為方案裡沒有封面的嘗試加上封面。結果不明的提交不會自動再按一次。如果 YouTube 讀回顯示作品仍是草稿，代表什麼都沒有公開，`vpub resume` 會在同一份草稿上按發布。`vpub status` 只讀本機記錄，列出沒做完的事和下一步的確切指令；`vpub cleanup` 可釋放已發布影片副本佔用的磁碟空間。

</details>

<details>
<summary><b>🔍 每一步都有回執</b></summary>


每個平台的進度都叫它本來的名字：排隊中 → 發布中 → 已提交，待核驗 → 已核驗。只有從平台頁面或 API 讀回到作品，才算「已核驗」；排隊、登入連結和本機回執都不等於發布成功。

</details>

<details>
<summary><b>✏️ 發出去還能改</b></summary>


`vpub edit` 直接修改已發布的作品：B站的標題、簡介、標籤、封面和章節，YouTube 的標題、簡介、縮圖和章節，抖音的標題、簡介、直式封面和章節，Instagram 的貼文說明，X 在出現編輯入口時也能改。不換影片，不改公開範圍。不是 CrossCast 發布的舊作品，先用 `vpub ops adopt` 認領，再用 `vpub edit-work` 修改。

X 編輯尚未通過真實帳號驗收。若結果為 `EDIT_SUBMITTED_UNVERIFIED`，編輯可能已經成功；原連結仍顯示舊文字不能證明失敗。請在 X 開啟貼文並檢查目前內容和編輯歷史，不要根據這次讀回再次提交。

`vpub widgets` 可以在你自己的 B站影片上加入互動組件：關聯影片、關注或三連按鈕、投票和評分。可以寫在方案裡，也可以加到已經發布的影片上。影片公開之前 B站不會列出這些組件，所以方案裡的組件要等到公開後，由 `vpub verify` 送出。`vpub verify` 和 B站修改也會回報影片上現有的組件。預約組件不包含在內，因為刪除它會通知所有已預約的人。這條路徑還沒有透過 `vpub` 在真實帳號上跑過。

</details>

<details>
<summary><b>💬 留言、按讚和私訊</b></summary>


讀取留言、用留言者的語言回覆，按讚、收藏，在抖音、Instagram、X 上讀寫私訊，查看 @ 提及。還能在平台允許的地方對單則留言置頂、隱藏、刪除或移除，也能管理你的合集和播放清單（調整順序屬於付費附加元件）。每次只做你授權的那一則，不會自動群發。各平台能做的不一樣，詳見[功能範圍](FEATURES.md)。

</details>

<details>
<summary><b>📊 數據和營運報告</b></summary>


查看單支作品的播放和互動，巡檢多個平台的新留言和數據變化。完整的營運報告屬於付費附加元件。

</details>

<details>
<summary><b>📧 需要你時寄信提醒（可選）</b></summary>


*屬於付費附加元件，目前還沒有開賣。*

平台要你掃碼或驗證時，可以寄一封提醒信給你。只支援 Gmail，預設關閉，你主動要求時才開啟。

</details>

## 🆓 免費與付費

發布和日常營運都免費，以 MIT 開源。付費附加元件只多出下面列出的幾項，現在還買不到，其他功能也都不需要它。

**免費版**（現在就能用，不需要模型 API Key，也不需要平台的官方 API）

- **發布**：五個平台，讀回核驗，排程、草稿、章節，上傳時加入合集或播放清單，AI 聲明
- **文案和封面**：AI 助手為每個平台寫文案，vpub 檢查並調整封面
- **發布之後**：修改作品，認領舊作品，作品列表和數據，巡檢和簡短摘要
- **互動**：留言和回覆，單則留言管理，@ 提及，單則私訊，按讚和收藏
- **合集和播放清單**：建立、修改、增刪作品（調整順序除外）
- **B站互動組件**：關聯影片、關注、投票和評分
- **安全和恢復**：下面這些安全護欄，`vpub status` 和 `vpub resume`

**付費附加元件**（還沒開賣，單獨安裝）

- YouTube、X 和 Instagram 的官方 API 路徑
- 營運報告（`vpub report`）
- 完整讀取（`vpub read-all`）
- 郵件提醒（只支援 Gmail，預設關閉）
- 播放清單調整順序
- YouTube 待審核留言佇列

## 🧭 平台支援

實測記錄是過去的真實發布，不代表在你的帳號、地區和平台狀態下一定成功，詳見[驗證記錄](VALIDATION.md)。這裡寫的是免費路徑；YouTube、X 和 Instagram 的官方 API 路徑屬於付費附加元件。

- **B站**（B站 App 掃碼登入，用內建的 bili-cli 發布）：目前只有作者直接用舊版 bili-cli 的上傳，透過 CrossCast 發布和排程都還沒試過，`vpub capabilities` 裡仍標為未實測。合集和草稿已在真實帳號上確認過。
- **YouTube**（在專用 Chrome 裡登入一次，背景操作 YouTube Studio）：2026 年 11 月 2 日已公開發布兩支影片並讀回。兩支第一次都只留下草稿，重新開啟再按一次發布才發出，現在自動處理這一步的程式還沒在真實頻道跑過。上傳時加入播放清單和排程只在草稿上傳裡確認過。
- **抖音**（專用官方 Chrome，CLI 背景瀏覽器）：長時間維持登入還在驗收。上傳中斷後不能續傳，但 `vpub resume` 會接著留下的草稿完成。封面視窗曾卡住；只有使用者明確選擇略過封面時，才可不帶封面發布並在手機上補設，程式不會自動略過。Windows 上現在改用隱藏桌面上的真實 Chrome 視窗，這還沒試過。標題和簡介儲存已嘗試，但仍未核驗成功。
- **Instagram**（專用官方 Chrome，CLI 背景瀏覽器）：影片公開發出並由 CrossCast 自己讀回核驗過。封面只能在手機上改，也不能排程發布。
- **X**（專用官方 Chrome，背景操作 Media Studio）：帳號必須已訂閱並能進入 Media Studio；未訂閱帳號的影片上限是 140 秒、512 MB，本工具不支援。X 目前有兩個版本的網站，CrossCast 兩種都支援；真實記錄都在舊版上，新版還沒有在已登入帳號上試過。
- **TikTok**：暫停，CLI 會拒絕 TikTok 的登入和發布請求。
- **還沒有的**：TikTok（暫停）、YouTube 資訊卡和可點擊片尾、自動或大量私訊與按讚、完整歷史匯出。

<details>
<summary><b>每個平台的完整記錄</b></summary>

- **YouTube**：免費路徑還能在背景讀取作品、數據、留言和播放清單，回覆留言，替影片和留言按讚，編輯或刪除自己的留言，修改標題、說明和縮圖，並建立、修改和刪除播放清單。目前在真實帳號上試過的有：各項讀取；建立、修改（名稱、說明、公開範圍）、刪除一個私人播放清單並在其中新增和移除影片；替影片按讚和收藏、替留言按讚（都已復原）；回覆留言；以及修改自己的留言再改回去。修改一部私人影片的標題和說明再改回去，以及置頂留言和取消置頂，也都確認過。CrossCast 在影片自己的頁面上讀取置頂狀態，因為 Studio 的列和選單在置頂之後仍然只提供「置頂」。刪除自己的一則回覆試過一次：回覆從頁面上消失了，但 CrossCast 無法證明 YouTube 已經接受，所以回報為未確認；要等再有一次真實刪除顯示成功的回覆長什麼樣，才會改變。上述實測之外的寫入不能據此視為已驗收；上傳另有私享測試和 2026-11-02 公開發布記錄，見驗證記錄。新縮圖會回報為未確認（`EDIT_SUBMITTED_UNVERIFIED`），因為 Studio 沒有可以比對的東西，請自己到 Studio 裡看一下。調整播放清單順序和待審核留言佇列需要付費的 API 附加元件，官方 API 路徑也是。免費的背景路徑可以依標題把影片加進播放清單，也能排程發布，但只在 Studio 的排程區域明確標識一個 IANA 時區或 UTC 時自動操作。時間依目標日期的時區規則換算，不從電腦時區或數字 GMT 偏移猜測時區。只顯示偏移、時區未知或不明確，以及夏令時間回撥時重複出現的時間，都會在最後儲存前停下，保留草稿供手動設定排程；不要重新上傳。播放清單和排程都會在儲存前讀回確認，時區和時間還會在按下儲存前再次核對。這項保守的時區保護只經過離線模擬測試，沒有新增真實帳號驗證證據。真實帳號上的存草稿選中了新播放清單，也保留了排程的日期和時間（整刻鐘和非整刻鐘都試過），並讀回確認。這些執行都停在最後儲存之前，所以最後儲存時把影片加進播放清單和真正儲存排程，都還沒在那裡確認過。只有付費的 API 路徑才涉及這兩點：Google 專案還在測試模式時，要先把登入用的帳號加進「測試使用者」；依 Google 的規定，2020 年 7 月 28 日之後建立、又沒通過審查的 API 專案，上傳的影片會被鎖定為私人。免費路徑都不需要。
- **X**：免費路徑只能用 Media Studio。帳號必須已訂閱並能進入 Media Studio；未訂閱帳號的影片上限是 140 秒、512 MB，此免費瀏覽器路徑不支援。X 目前有兩個版本的網站：已登入的測試帳號仍然使用舊版，未登入的訪客看到的是重建後的新版。CrossCast 的 X 路徑兩種都支援；如果頁面兩種都對不上，指令會回報錯誤並停止，這次嘗試的回執會說明有沒有東西已經送出。新版路徑來自 X 公開的網頁程式碼，還沒有在已登入帳號上試過。在舊版上，把貼文移出書籤夾要透過書籤夾頁面自己的分享選單。在真實帳號（舊版）上，建立、重新命名、刪除書籤夾，把貼文加入書籤夾再移出，以及替貼文按讚、收藏都確認過。回覆（由後續的 `vpub comments verify-reply` 確認）和刪除這則回覆也確認過。透過 Media Studio 上傳有三次在發文前停下，什麼都沒有送出，因為上傳好的檔案打不開它的頁面；第四次停下是因為 X 要求驗證。這一步改過之後，一次草稿走到了 X 的發文框，正確檔案的資訊也填好了，之後未發布就刪除了。接下來一次排程用的上傳，在同一個頁面等了十分鐘後停下，什麼都沒有排上，原因還沒有確認。這一步又改過之後，一次排程測試走到了 X 的日曆，並顯示日曆的 Save 只是確認日期，真正的最後一步是寫著 Done 的按鈕。這一點改過之後，下一次測試按了一次 Done，貼文就出現在 X 的排程頁面上，文字、日期、時間都對，也帶著影片；到點之前已從那個頁面取消，影片也刪除了。另一次全新上傳的草稿也走到了發文框，影片和檔案對得上，之後未發布就刪除了。還沒有排程貼文真正走到發布時間，普通貼文已經在真實帳號上發出過，詳見驗證記錄。 X 的編輯版本行為尚未在真實帳號上驗證；原網址顯示舊文案或跳到不同貼文 ID，都不能證明編輯失敗。`EDIT_SUBMITTED_UNVERIFIED` 表示可能已儲存。CrossCast 會拒絕再次編輯這則結果不明的作品，即使加 `--person-approved` 也不能重試；只能用原修改請求做 `--verify-only` 讀回，或由使用者到 X 查看目前文字和編輯歷史並手動處理。
- **Instagram**：影片保留原比例；封面必須在手機上改：開啟作品的「編輯封面 → 從相機膠卷新增」，選和抖音共用的那張直式圖，再看一下個人檔案格狀版面的裁切。Instagram 不能排程發布，要求排程的方案會被拒絕。刪除自己的留言在真實帳號上試過一次：事後重新讀取已看不到那則留言，但 CrossCast 無法證明 Instagram 已經接受刪除，所以回報為未確認（`COMMENT_UNVERIFIED`）；要等再有一次真實刪除顯示成功的回覆長什麼樣，才會改變。
- **抖音**：長時間維持登入的穩定性還在驗收；網頁上傳中斷後不能續傳，要先檢查既有的草稿：如果抖音的上傳頁已經在提供一份留下的草稿，新的上傳會在選任何影片之前以 `existing_draft_requires_attention` 停下，請自己在頁面上繼續或放棄那份草稿，然後再執行一次。真實帳號上留下過一次草稿：`vpub resume` 接著完成了先前中斷的一次嘗試，抖音隨後在「繼續編輯」下提供了這份草稿，標題和測試時用的完全一致。後來的一次存草稿以 `PREVIEW_READY` 結束，CrossCast 自己也看到了這樣的入口，它只作記錄，不算讀回了草稿。上傳前頁面已經提供草稿時的停止，還沒在那裡跑過。建立和刪除合集在真實帳號上通過了（第一次嘗試曾停在上傳封面後的一次等待，這個等待現在加長了），上傳表單裡的合集選擇器也列出了真實的合集。私人排程發布已經從卡片上確認過，文字、時間和私人標記都對，並在到點前手動刪除，所以還沒有一次真正走到發布時間。 **早期版本可能在含換行的說明裡留下多餘的 `*`：有一部真實影片的公開說明曾顯示這些符號。** 這部影片後來已刪除，所以已經沒有線上的例子。現在說明改為一行一行輸入，測試卡片上已經沒有這些符號，但還沒有檢查過新影片的公開頁面。如果你以前往抖音發過含換行的說明，請看一下，並在抖音上或用 `vpub edit` 改正；`vpub edit` 在抖音上的說明儲存已在私人測試影片上嘗試，但尚未驗證成功；審核中的作品會阻止修改。一部真實影片的直式封面已經在 CrossCast 的背景瀏覽器裡改好了，用的是和 `vpub edit` 相同步驟的腳本；重新查看時顯示新封面，文字和可見範圍都沒有變。`vpub edit` 本身在那之前就停下了，停在一個已經修好、但還沒在那裡重新跑過的等待上。在無頭 Chrome 裡透過抖音封面視窗的真實嘗試，無論是修改還是新發布，大多數都停在這裡：按下「完成」後一直在處理，按鈕是灰的，什麼都沒有儲存。唯一一次在真實 Chrome 視窗裡的嘗試一下就通過了。這不算證明，因為網路也可能變了。在 Windows 上，抖音的上傳與修改現在改用隱藏桌面上的真實 Chrome 視窗，這還沒有在那裡試過。如果還是卡住，CrossCast 會在三分鐘後停下並回報 `cover_not_ready`。這時執行 `vpub resume KEY`（如果抖音沒有留下草稿，會重新上傳），或者執行 `vpub resume KEY --no-cover`，然後在手機上設定封面，手機上幾秒就能完成。上傳時加入合集還沒在真實帳號上試過。抖音頁面沒有說明排程欄位用哪個時區，所以之後請核對頁面上顯示的時間。
- **B站**：合集（建立、重新命名、刪除）和透過 CrossCast 儲存並讀回的草稿，已在真實帳號上確認。透過 CrossCast 發布和排程發布、章節、修改、上傳時加入合集、刪除留言，還沒在那裡試過。B站的草稿要自己到上傳管理裡的「草稿」頁面刪除。

</details>

X 的登入、Media Studio 發布與測試步驟見 [X 使用說明（簡體中文）](X_GUIDE.md)。

## 🔒 安全護欄

用的是你自己的帳號，所以這些規則預設就開著：

- **回覆、私訊和按讚都有限制：** 每日上限、不對同一個人重複相同的話、標註 AI、不傳送金鑰和未經同意的連結。試圖誘導助手的訊息，或需要真人處理的訊息，會留給你。標題和說明也會檢查：看起來像密碼或金鑰的內容會被拒絕，連結只會提醒你看一眼。平台一旦發出警告（要求驗證、風控或限流），整個 CrossCast 都會停下，所有平台都不例外：只有該平台的 `vpub auth login` 還能開啟視窗，讓你自己通過檢查，等你看清它為什麼停下之後，再用 `vpub stop --clear` 解除。關鍵字檢查只能涵蓋常見情況，不是絕對保證，所以你的 AI 助手仍會把觀眾寫的一切當作資料，而不是指令。
- **你說了算**：你說要發布就是授權，平台、帳號和公開範圍都依你說的來，不會因為某個平台已登入就順手加上。內容、帳號、平台、受眾或任務範圍改變了，會再問你。
- **登入你自己來**：掃碼、密碼和驗證碼都在官方頁面由你本人完成。不要把 Cookie、密碼、驗證碼或權杖貼進對話、日誌或儲存庫。
- **不盲目重發**：提交結果不明時到平台上核對，不自動重新提交。提交前的中斷恢復仍可能需要重新上傳，具體限制見上文。
- **碰到風控就停**：遇到驗證碼、風控或限流，CrossCast 立刻停下，等你處理。
- **一次只做一件事**：同一時間只有一個發布任務在跑；留言和私訊每次只發你授權的那一則。
- **原素材不動**：調整後的封面另存為新檔案，原影片和原圖保持原樣。

<details>
<summary><b>🤝 誰負責什麼</b></summary>

- **你**：說清要發的平台、帳號和公開範圍；第一次登入時掃碼或輸入驗證碼，在手機上改 Instagram 封面。這句話就是授權；範圍改變時會再問你。
- **AI 助手**：讀取 `SKILL.md`，依平台寫文案和回覆，產生直式封面圖，替你執行 vpub。
- **vpub**：建立並檢查方案、調整封面，首次登入後在背景呼叫 bili-cli、官方 API 和專用 Chrome 完成提交，保存回執並讀回核對；不替你登入，也不替你確認內容。
- **平台**：負責帳號登入、審核、最終發布與公開；功能、格式限制和收費以當日官方頁面為準。

</details>

## 📦 安裝

<details>
<summary><b>開始前準備好這幾樣</b></summary>

- **B站**：用 B站 App 掃碼登入一次（透過 bili-cli）。
- **YouTube**：免費發布透過專用 Chrome 在背景操作 YouTube Studio，只有登入或 Google 要求驗證時才會看到瀏覽器。用 CLI 走官方 API 需要付費的 API 附加元件，以及你自己的 Google 桌面 OAuth 用戶端。
- **抖音、Instagram、X**：第一次會開啟 CrossCast 專用的官方 Chrome 視窗，由你自己掃碼或輸入密碼、驗證碼；之後 CLI 在背景沿用這個登入。
- **X**：免費路徑只能用 Media Studio 發布，帳號必須已訂閱並能進入 Media Studio。
- **可能會有「新裝置」驗證**：CrossCast 透過自己的專用瀏覽器登入，YouTube、抖音、Instagram 或 X 可能把它當成新裝置，要求你驗證身分或找回帳號。請你自己在平台的官方頁面完成；AI 助手不會替你回答驗證。反覆重新登入沒有用，已儲存的登入之後也可能再次被要求驗證。
- **電腦上要有 Google Chrome**：沒有的話，執行 `vpub setup --install-browser` 安裝。

</details>

<details>
<summary>想自己在終端機裡安裝？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 檢查本機相依套件
vpub capabilities  # 查看這次安裝提供的發布方式；vpub doctor 會檢查缺少什麼
```

需要 Python 3.10–3.14。這兩個指令都不會登入或發布。登入和手動流程見[安裝](#-安裝)。

</details>

需要 Python 3.10–3.14 和 Google Chrome。Windows 安裝腳本已經實測；macOS、Linux 尚未驗收。最好裝進獨立的 Python 虛擬環境（Windows 安裝腳本會自動建好），以免改動電腦上其他 Python 程式在用的元件。

上面的安裝指令使用目前的 main 分支。現有 v0.16.0 發行套件仍要求 Python 3.10–3.12。

<details>
<summary><b>Windows 安裝腳本</b></summary>

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

腳本會印出 `vpub.exe` 的完整路徑，但不會把它加入 PATH。可以直接使用該路徑，或在腳本建立的虛擬環境中執行指令。如果找不到 Python 3.10–3.14，加上 `-Python` 和合適的 `python.exe` 的完整路徑再執行一次。

</details>

<details>
<summary><b>透過 pip 從 Git 安裝（需要 Git 和相容的 Python）</b></summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
```

pip 安裝不會留下程式碼目錄，所以 `SKILL.md` 在該 Python 環境的資料夾裡：`share/publishops-crosscast/skills/publishops-crosscast/SKILL.md`。`vpub doctor` 發現需要處理的問題時，會以結束代碼 4 和 `ok: false` 結束，`issues` 裡的每一項都說明怎麼處理。

</details>

<details>
<summary><b>登入各平台</b></summary>

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

</details>

<details>
<summary><b>想自己動手？看一眼手動流程</b></summary>

```sh
vpub prepare final.mp4 --platforms bilibili,douyin,youtube --language zh-Hant --visibility public --ai-generated yes --about "這支影片在講什麼" --cover cover.jpg --portrait portrait.jpg
vpub go final-crosscast --dry-run
vpub go final-crosscast
```

`vpub prepare` 不會上傳，只告訴你要寫什麼。AI 助手把文案寫進新資料夾裡的 `copy.json`。`--dry-run` 不會上傳。`vpub go` 可以放心重跑，已經發出去的會跳過。

想在步驟之間停下來，可以手動一步步來：

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

目前本機資料目錄裡，每個平台首次正式發布，或啟用了「每次先給我看」時，`go` 和 `publish` 都會在存取帳號前停下。該檢查不追蹤每個帳號或頻道。必須先展示傳回方案的完整文案、帳號、平台、公開範圍、AI 聲明、封面及文案警告，停下來等使用者明確同意。之後才執行 `vpub go final-crosscast --confirm CODE --person-approved` 或 `vpub publish ready.json --confirm CODE --person-approved`，CODE 必須是目前方案的確認碼。單獨的 `go --confirm CODE` 不代表已經審閱；參數只是呼叫者聲明，軟體不能證明使用者真的同意。方案變更要重新展示並取得同意。僅存草稿不算首次正式發布；啟用每次預覽時草稿也要審閱。該平台已有一次核驗成功的正式發布後，未要求審閱的後續發布依現有指令執行。

明確使用原生瀏覽器交接路徑時，先執行 `vpub browser authorize-publish KEY --identity ID`，取得從草稿改為正式發布的新方案。展示它並等使用者明確同意後，才執行 `vpub browser authorize-publish KEY --identity ID --confirm CODE --person-approved`。這只授權繼續同一份草稿，不會重新上傳；舊草稿確認碼不能代替新方案的確認碼。

`--dry-run` 只做本機檢查，不聯絡平台。缺少登入紀錄會提供登入命令並以退出碼 4 結束；有紀錄也不等於登入仍有效，真正發布時會核對帳號，無須先逐一執行平台狀態查詢。檔案檢查只辨識容器標頭，不證明能播放或平台會接受。

`--dry-run` 不會上傳。`response.json` 由 AI 助手依 `request.json` 中的範本撰寫。中斷後用 `vpub resume RECEIPT_KEY` 接著做，用 `vpub receipts` 查看所有回執。

可選：`vpub go final-crosscast --workers 2`（或 `vpub publish ready.json --confirm <confirmation-code> --workers 2`） 會把 YouTube、抖音、Instagram 和 X 兩個兩個一起發，不再一個接一個。B站，以及走付費外掛或你的助手自帶瀏覽器的平台，仍然一個接一個發。任何平台發出警告，仍會停下所有平台。按 Ctrl+C 會等正在上傳的平台安全停下，已經發出的紀錄照樣保留，之後 CrossCast 維持停止，直到你用 `vpub stop --clear` 解除。目前只做過離線測試：能快多少、兩個隱藏瀏覽器佔多少記憶體，還沒在真實帳號上測過。

</details>

## 💰 費用與隱私

- **模型**：CrossCast 本身不需要另外購買模型 API，文案由你正在用的 AI 助手完成。
- **可能的費用**：AI 助手的訂閱、X 的訂閱（免費瀏覽器發布路徑要用 Media Studio），以及 YouTube API 的配額（由 Google 決定）。本專案不承諾免費或無限使用。
- **本機資料**：登入工作階段、任務、設定和回執都保存在本機應用程式資料目錄，請不要提交到 GitHub。分享清單、截圖或日誌前，先移除帳號識別資訊、作品 ID、私人路徑和憑證。
- **敏感資訊**：不要在對話、日誌或儲存庫中提供 Cookie、驗證碼、密碼、權杖或 OAuth 檔案。

## ❓ 常見問題

<details>
<summary><b>發布前還要我再確認嗎？</b></summary>

目前本機資料目錄裡，每個平台首次正式發布前，助手都要展示目前方案並停下來，等你說「發」。這按平台判斷，不追蹤帳號或頻道是否首次使用。該平台後續依你的發布指令執行；儲存了「每次先給我看」或臨時要求預覽時，每次都要等你同意。需要審閱時，目前的 `--confirm CODE` 必須與獨立的 `--person-approved` 一起使用；確認碼本身不代表同意，參數也只是呼叫者的聲明，軟體無法證明你真的說過「可以」。內容、帳號、平台、受眾或範圍改變，仍需確認。偏好不等於長期發布授權。

</details>

<details>
<summary><b>YouTube 一定要設定 OAuth 用戶端嗎？</b></summary>

只有付費的 API 路徑需要：API 金鑰本身不能授權上傳，必須用「桌面應用程式」OAuth 用戶端在 Google 頁面授權一次。免費路徑由背景 Chrome 在 YouTube Studio 上發布，不需要 OAuth 用戶端。授權在你自己的電腦上完成，權杖只留在本機。

</details>

<details>
<summary><b>X 沒訂閱能發嗎？</b></summary>

免費瀏覽器路徑不能：它只透過 Media Studio 發布 X，帳號必須已訂閱並能進入 Media Studio。付了費或登入了，不代表 Media Studio 已經開通，上傳前會先核對。官方 API 路徑屬於獨立的付費附加元件，目前尚未開賣；這不代表一般免費帳號或 Basic 帳號已受支援。

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
