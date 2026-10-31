> 0.15.1 完整指令參考。使用官方 API 的 YouTube 指令需要付費的 API 附加元件。入門請先看 [README](../README.md)。

# PublishOps CrossCast

**繁體中文** · [English](OPERATIONS_EN.md)

**X 發布僅面向已訂閱、且帳號實際能進入 Media Studio 的使用者。**

原因是未訂閱帳號的影片上傳上限為 **140 秒、512 MB**，且無法使用本工具所依賴的 Media Studio 媒體管理與自訂封面流程。CrossCast 的 X 後端透過 Media Studio 上傳影片並設定製作封面，因此目前僅支援具備此權限的訂閱帳號。登入或付款本身不代表權限已開通，上傳前仍會核對媒體庫入口與所選帳號。[官方上傳限制](https://help.x.com/en/using-x/x-videos) · [Media Studio 權限](https://help.x.com/en/using-x/media-studio)。

### 跨平台影片發布營運台

**v0.15.1 alpha · CLI `vpub` · AI 協助開發**

讓同一支原創影片擁有各平台適用的文案、封面與發布回執。選擇 Bilibili、抖音、YouTube、Instagram、X 的任意組合，首次登入後，CLI 復用本機專用會話完成後續上傳。

預設路徑：B站沿用 bili-cli，YouTube 由 AI 助手操作 YouTube Studio（安裝付費附加元件後使用官方 API），抖音／Instagram／X 由 CLI 在背景控制專用 Chrome。首次登入與官方驗證需要使用者協助；明確設定 `method: native` 仍可使用宿主瀏覽器交接。這是 alpha 版本，開發期實測與無人值守驗收分開記錄。已新增評論、作品數據及匯報命令；各站實測範圍見下方。TikTok、YouTube 卡片／可點擊片尾尚未實作。

[功能範圍](FEATURES.md) · [驗證記錄](VALIDATION.md) · [草稿測試](DRAFT_TESTING.md)

## 0.15.1 收尾：固定範圍與修復

本輪不增加新功能，集中修復既有流程並核驗結果。強制結束後，系統鎖會釋放；只有確認舊程序已不存在時才清除其標記，活躍或無法判定的程序仍會阻擋第二個執行者。原回執與瀏覽器資料保留。

YouTube `resume` 可接續同一影片尚未開始的封面與播放列表步驟，並重新核對帳號、原封面和作品歸屬；已嘗試但結果不明的封面不會重送。這些步驟未完成時，不再僅因影片 ID 存在就回報完成。瀏覽器分頁重走時保留先前的部分結果，只有新一輪讀到官方結束標記才視為完整。Instagram 等待編輯選單載入後再取控制項。

實測：私密測試片在伺服器確認 8 MiB 後硬退出，續傳後又在影片 ID 落盤、封面未送出前硬退出；第三個程序完成同一影片及封面，讀回私密狀態。測試片已刪除並確認不存在。YouTube／Instagram 第二期雙語文案、抖音六個自訂章節均再次唯讀核對。詳見[驗證記錄](VALIDATION.md)。

抖音／Instagram 網頁位元組續傳、大型瀏覽器清單和非空 Instagram 提及仍未完成實測；不把這些限制寫成已支援。既有功能依已記錄範圍使用，不再附加新的開發清單。

## 指令提速、上傳恢復與完整讀取

合集建立、更名、增刪成員與刪除，現在於同一個專用瀏覽器環境完成前置檢查、操作與重新讀回。本機三次相同的 Instagram 合集讀取由 **37.312 秒降至 17.297 秒**，耗時減少約 **54%**，瀏覽器啟動由三次減至一次。這是單次開發樣本，不是速度保證；仍會重新載入頁面核驗，不會自動重送不確定的寫入。

```sh
vpub resume UPLOAD_RECEIPT_KEY
vpub read-all start youtube works --page-size 50 --max-pages 100 --out works.json
vpub read-all start youtube comments --work VIDEO_ID --out comments.json
vpub read-all start youtube collections --out playlists.json
vpub read-all start youtube collection-items --collection PLAYLIST_ID --out items.json
vpub read-all start douyin collections --out collections.json
vpub read-all start instagram collection-items --collection COLLECTION_ID --out saved.json
vpub read-all resume RUN_ID --max-pages 100 --out continued.json
vpub read-all status RUN_ID
```

- **YouTube 位元組續傳：**新的 API 上傳會先在本機保存續傳會話，再傳送影片，逐塊保存伺服器確認進度。`resume` 核對原帳號、影片雜湊與文案，查詢伺服器已接收位置後接續原會話。影片 ID 先落盤，再處理封面與播放列表。過期或建立結果不明時不會偷偷另建上傳；沒有會話的舊回執需檢查。會話網址只存本機，不出現在 CLI 回傳資料。
- **完整 API 讀取：**YouTube 作品、播放列表、列表成員、頂層評論及各樓全部回覆；B站作品與評論委派上游。每頁將去重資料與下一頁游標一起保存。達到頁數預算會暫停，不會丟掉尾頁；恢復時跳過已提交的分頁。無法讀取的項目或回覆不足時不標記完整。平台資料持續變動，並非凍結的歷史快照。
- **瀏覽器合集：**抖音／Instagram 合集清單與成員透過官方下一頁或捲動控制讀取；X 書籤夾清單也可讀。只有伺服器明確表示結束才標記完整，每頁保存局部成果。瀏覽器恢復會重新開啟並重走官方頁面，尚無可持久化捲動游標。大型瀏覽器清單與 X 書籤夾成員分頁仍待實測；其他瀏覽器作品、評論與私信仍為有界讀取。
- **各站恢復邊界：**X 可接續已存於 Studio 的素材。抖音／Instagram 在最終提交前中斷上傳，仍需檢查原草稿，本版不宣稱這兩站支援位元組續傳。最終提交不明時只核驗原作品，保留既有抖音待辦章節處理。 程序結束會釋放系統鎖；確認舊程序不存在後才清理標記，未知或損壞的舊標記仍需檢查，不清除活躍鎖或瀏覽器資料。

繼續遵守影片來源語言、專用封面、Instagram 原片比例、X 權限條件與選用 Gmail 設定。[實測證據](VALIDATION.md)。YouTube 依照[官方續傳協定](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)。

## 在 Codex 中作為技能使用

倉庫附帶 `skills/publishops-crosscast/SKILL.md` 與 `agents/openai.yaml`，顯示名稱為 **PublishOps CrossCast**。將整個 `publishops-crosscast` 資料夾複製到個人的 `.agents/skills` 目錄；若尚未出現在列表，重新整理技能或開啟新對話，再以 `$publishops-crosscast` 呼叫已安裝的 `vpub`。

進度提示由 Codex 決定。技能有自己的名稱，但不保證逐字顯示「正在讀取 PublishOps CrossCast 技能」。安裝技能不會自動完成 CLI 安裝或平台登入。

## 私人收藏集與平台差異

`vpub collection` 使用確切 ID 與持久回執管理集合。Instagram Saved 與 X 書籤夾屬於私人收藏；抖音作品合集整理已發布作品，可能進入平台審核。YouTube 使用 `playlist`；B站沿用上游 bili-cli。

| 平台 | 已提供 | 限制 |
|---|---|---|
| Instagram | 列出、項目、建立、更名、加入、移除、刪除 | 加入前作品須已收藏，且出現在本次載入的 Saved 選擇器；成員讀取不完整時不修改。 |
| 抖音 | 列出、項目、帶封面建立、更名、加入、移除、刪除 | 仍受帳號資格及平台審核限制；不可加入的作品直接回報。作品合集不等於私人收藏夾。 |
| X | 列出、建立、更名、刪除及有界項目讀取 | 指定書籤夾成員增刪尚未開放；不把錯誤頁當空清單。 |

```sh
vpub collection list instagram
vpub collection rename instagram COLLECTION_ID --title "靈感" --request-id rename-01
vpub collection items instagram COLLECTION_ID
vpub collection add instagram COLLECTION_ID WORK_CODE --request-id add-01
vpub collection remove instagram COLLECTION_ID WORK_CODE --request-id remove-01
vpub collection create douyin --title "系列" --cover collection.jpg --description "系列簡介" --request-id create-01
vpub collection add douyin COLLECTION_ID WORK_ID --request-id add-02
vpub collection remove douyin COLLECTION_ID WORK_ID --request-id remove-02
vpub collection delete douyin COLLECTION_ID --request-id delete-01
vpub collection verify RECEIPT_KEY
```

使用 CLI 讀回的 ID，不填名稱或猜測網址。新意圖使用新的 request ID；重複原請求只回傳既有回執，不重送。核驗另行讀取；成員變更要求完整基線，且其他成員必須全部保留。支援的合集分頁使用 read-all；成員搬移仍待補齊。有界讀取不能證明某項不存在。

刪除集合不刪原影片，也不清空 Saved／All Bookmarks。Instagram 編輯視窗在實測中卡住，因此更名與成員操作使用官方頁面本身的集合用戶端；CLI 不匯出登入 Cookie、不硬編碼重放私人請求。抖音使用官方控制項並觀察回應。這些仍是背景瀏覽器操作，不是獨立公開 API。[實測結果](VALIDATION.md)。

## 更快的巡檢與統一續讀

不同平台預設以 **3 個工作執行緒**並行，`--workers 1–4` 可調整；同平台仍循序讀取，瀏覽器批次共用專用登入環境，YouTube 在同輪復用連線、頻道及作品歸屬查詢。各平台獨立保存檢查點，合併本機觀察時循序寫入。`--progress` 在 stderr 輸出不含留言正文的 JSON 進度；stdout 保持完整結果 JSON。

```sh
vpub ops sync --platforms youtube,douyin,instagram,x --workers 3 --progress
vpub messages sync --platforms douyin,instagram,x --sources comments,mentions,dm_preview --progress
vpub messages resume RUN_ID --progress
vpub ops runs
```

評論、@ 提及、私信會話預覽共用有界讀取、300 秒成功快取及可恢復執行。`messages resume` 不重讀已完成項目，未完成頁面仍從該來源重新讀起；`--full` 強制查詢但保留數量上限。`sources` 分清 OBSERVED、CACHED、FAILED、UNSUPPORTED；不支援或未設定的來源保留 PARTIAL，不冒充整個帳號均已讀完。YouTube 的提及／私信沒有接入。

訊息報告不包含私信正文；需查看已觀察內容時使用 `messages list`。預覽數不是新私信數，平台通知可能因開啟而標記已讀。這些命令不回覆、不寄信、不建立排程。`account_health` 僅描述本輪來源讀取；快取不能證明當下仍登入。

實測同樣的四平台 10 項強制查詢：0.11 為 **55.446 秒**，0.12 為 **27.398 秒**，首項結果約 **3 秒**。這是一組同機、同帳號的開發測試，不是穩定吞吐量或延遲保證；不以快取命中冒充查詢加速。

## YouTube 播放列表管理

使用官方 API 管理本帳號的一般播放列表：建立、改名、修改說明／可見性、添加／移除影片、調整順序和刪除列表。建立預設私密；改名保留原說明、語言及可見性。移動／移除使用 `playlist items` 返回的精確 **ITEM_ID**，不是 VIDEO_ID；排序位置從 0 開始。

```sh
vpub playlist list
vpub playlist create --title "My series" --privacy private --request-id series-create-01
vpub playlist update PLAYLIST_ID --title "New series title" --request-id series-rename-01
vpub playlist items PLAYLIST_ID
vpub playlist add PLAYLIST_ID VIDEO_ID --request-id series-add-01
vpub playlist move PLAYLIST_ID ITEM_ID --position 0 --request-id series-move-01
vpub playlist remove PLAYLIST_ID ITEM_ID --request-id series-remove-01
vpub playlist verify RECEIPT_KEY
```

刪除列表使用 `vpub playlist delete PLAYLIST_ID --request-id series-delete-01`；不刪除列表中的影片。每次有意的新變更使用新的 request ID，中斷或不確定時沿用原 ID，CLI 不會重送。`UNVERIFIED` 使用 `playlist verify` 讀回，不靠再次提交補救；若建立回應遺失且未取得列表 ID，保留待核驗，不依同名猜測歸屬。寫入後只有限時重讀，沒有寫入重試。

這組命令需要首次登入授權包含 `youtube.force-ssl`；目前預設已列入。若 API 回報 `manualSortRequired`，需先在 YouTube 將該列表排序設成手動；CLI 不改排序模式。官方文件：[列表修改](https://developers.google.com/youtube/v3/docs/playlists/update)、[項目排序](https://developers.google.com/youtube/v3/docs/playlistItems/update)。

私人收藏集使用上方獨立的 `collection` 指令；不宣稱與公開影片合集完全等價。


## 已有作品接管與可恢復巡檢

```sh
vpub ops discover instagram --pages 3 --limit 50
vpub ops adopt instagram WORK_ID
vpub ops sync --platforms youtube,douyin,instagram,x --out round.json
vpub ops runs
vpub ops run RUN_ID
vpub ops resume RUN_ID --out resumed.json
vpub ops sync --platforms youtube,douyin,instagram,x --full
```

`discover` 按頁數、數量預算探索已登入帳號的作品；B站／YouTube 使用原有分頁介面，另外三站使用官方頁面的翻頁或捲動。瀏覽器結果保留 `complete: false` 與停止原因，不把沒有可見續頁當成完整歷史。它只建立本機目錄，不自動接管全部作品。

`adopt` 重新核驗指定作品的歸屬，再加入追蹤清單。YouTube／B站使用原有帳號核驗；抖音核對創作者作品卡與確切詳情 ID；Instagram 核對指定作品的作者編輯入口；X 核對確切帖文連結與作者。可接管以前手動發布的作品，單獨保存接管證據，沒有偽造上傳回執或重新發布。這是評論／數據營運接管，不會自動授權刪除或修改影片。

`ops sync` 預設在 **300 秒**內復用相同帳號、作品、讀取範圍的成功結果。`CACHED` 保留實際觀察時間，不表示剛查過平台；`--min-interval 0` 或 `--full` 強制讀取。`changes` 列出本輪新觀察及正文變動的評論；首次觀察是基線，不代表剛收到。既有評論的處理標記與歷史仍保留。數據比較沿用來源、單位與精確數值檢查。這是有時效的增量巡檢，不是平台推送或完整歷史同步。

每一步完成便落盤。API 評論及樓中樓保存已驗證分頁與續頁 token；瀏覽器批次逐項保存已完成讀取。中斷後可用 `ops runs` 找回執行 ID，再用 `ops resume` 跳過已完成項目；未完成的瀏覽器作品從頁面重新讀起，不捏造可持久化捲動位置。已完成的整輪再次 resume 不會重新讀取。登入驗證、帳號變更或未知結果會停止該平台，保留進度，其他平台可繼續。

`--full` 仍受頁數與數量預算限制。作品與訊息來源現已共用上述可恢復執行。巡檢不建立排程、不自動回覆或寄信。原始評論、目錄及回執只存本機。


## 點讚、收藏與撤銷

首次登入後，以同一組 CLI 命令操作指定作品。B站沿用 bili-cli，YouTube 使用官方 API（需付費附加元件）；抖音、Instagram、X 復用 CLI 專用背景 Chrome，不需要宿主逐步操作內置瀏覽器。

| 平台 | 點讚／取消讚 | 保存／移除的實際含義 |
|---|---|---|
| B站 | bili-cli | 指定收藏夾；省略 ID 時使用唯一預設夾，撤銷不影響其他夾 |
| YouTube | 官方影片評分 API | 指定自己擁有的普通播放列表；不支援「稍後觀看」、喜歡清單或舊版最愛 |
| 抖音 | 指定影片的點讚按鈕 | 該影片的預設收藏 |
| Instagram | 作品工具列的 Like | 預設 Saved；具名容器使用 `collection`，評論點讚使用獨立指令 |
| X | 指定帖文的 Like | 預設私人 Bookmarks；具名容器使用 `collection`，X Lists 為不同功能 |

```sh
vpub engage status instagram WORK_ID
vpub engage like instagram WORK_ID
vpub engage like instagram WORK_ID --remove
vpub engage save x STATUS_ID
vpub engage save x STATUS_ID --remove
vpub engage collections bilibili
vpub engage save bilibili BV_ID --collection FOLDER_ID
vpub engage collections youtube
vpub engage save youtube VIDEO_ID --collection PLAYLIST_ID
vpub engage save youtube VIDEO_ID --collection PLAYLIST_ID --remove
vpub engage verify RECEIPT_KEY
```

`status`、`collections`、`verify` 都是唯讀。收藏夾／播放列表 ID 來自 `collections`；YouTube 清單可用 `--cursor` 繼續。支援 `--out result.json`。`like`／`save` 是直接執行命令，可加 `--dry-run` 預覽；不另加逐次確認。

B站的精確收藏夾成員查詢使用內含 bili-cli 的唯讀輔助函式；寫入仍走它的 CLI。更新內含版本後需要重新驗證這個內部介面，讀不到成員狀態時不猜測或刪除其他收藏。

每次先核對帳號和當前狀態，相同狀態返回 `NO_CHANGE`。寫入前保存回執，僅提交一次，再以獨立讀取確認；`ACTION_VERIFIED` 才表示讀回相符。若為 `ACTION_UNVERIFIED`，只用 `verify` 核驗，保留記錄，不重點、不反向切換。遇到登入驗證或不能唯一辨識的控制項會停止。操作記錄是帳號狀態證據，不能當作自然流量增長；[YouTube 評分 API](https://developers.google.com/youtube/v3/docs/videos/rate) 亦明確區分帳號評分和官方讚數。

匯報分別列出點讚、取消讚、保存、移除及待核驗操作，依回執去重。明確授權的測試可加 `--test-id LABEL` 排除於營運成果；該標籤本身不會自動撤銷操作。登入、安裝與 `ops sync` 都不會自動點讚或收藏。這一版不含批量互動、投幣、一鍵三連、轉發或新增收藏夾。

## 評論、作品數據與營運匯報

先讀取指定作品的評論，再由宿主聊天模型理解上下文並撰寫回覆。**回覆只使用評論者的語言，單語發送**；不套用影片簡介的雙語規則，也不跟隨帳號或匯報語言。宿主識別英語、法語等語言後傳入 `--language`；CLI 的字元提示不是完整語言識別器，不確定時不猜。

```sh
vpub comments list youtube VIDEO_ID --limit 20 --out comments.json
vpub comments reply youtube VIDEO_ID COMMENT_ID --file reply.txt --language zh
vpub comments verify-reply REPLY_KEY
vpub stats youtube VIDEO_ID --out stats.json
vpub preferences --language zh
vpub report --language auto --out report.json
```

把 `youtube` 換成 `bilibili`、`douyin`、`instagram` 或 `x` 即可使用相同入口。瀏覽器平台目前要求作品已有 CrossCast 回執，以核對登入帳號；不猜測其他人的作品歸屬。`comments reply` 直接發送，沒有逐條審批；`--dry-run` 僅為可選預覽。文案由宿主模型提供，CLI 不自行聊天、翻譯或追加署名。

發送前重新核對評論與帳號，先寫持久狀態，再提交一次；結果不明時只用 `verify-reply` 讀回，不盲目重送。一般回覆拒絕自己的評論及已知已有作者回覆的串。讀取只涵蓋回傳的頁面／可見列表；`complete: false` 不能當作已讀完所有評論。YouTube 與 B站可用 `next_cursor` 繼續；瀏覽器不捏造分頁游標。

| 平台 | 評論與回覆 | 作品數據 |
|---|---|---|
| B站 | 上游 bili-cli；保留串與回覆 ID | 播放、讚、評論、收藏等；創作者完播／觀看時長依上游可用欄位 |
| YouTube | 官方 Data API；驗證作品、串、作者、正文及回覆 ID | 播放、讚、評論；完播率／觀看時長尚未接入 Analytics |
| 抖音 | 作品頁讀取；創作者中心回覆頂層留言；新回覆及父留言已讀回確認 | 創作者後台顯示的播放、讚、評論、分享、收藏、完播率等 |
| Instagram | 可見評論及 Reply；以新評論 ID、作者、正文與操作記錄讀回 | 僅作品頁實際顯示的數值，未接入完整 Insights |
| X | 可見對話及 Reply；以新帖 ID、作者、正文與操作記錄讀回 | 帖文瀏覽、回覆、轉發、讚；帖文瀏覽不等於影片播放 |

抖音回覆使用創作者中心原生控制項，觀察該頁自己發出的請求以核對留言 ID；不是獨立官方 API。若後台尚未載入目標、存在同名同文的歧義或目標是子回覆，就停止並保留原記錄。發送回執與刷新後可見性分開判定。

各項數據保留來源、採樣時間和單位；缺少的欄位是 `null`，不寫成零。瀏覽器驗證記錄的是指定 Reply 動作及後續可見結果，不冒充官方 API 的父評論關係。詳見[實測邊界](VALIDATION.md)。

匯報跟隨**使用者**語言，預設中文或英文。宿主根據對話設定 `preferences --language zh|en`，也可單次 `report --language zh|en`；未設定時由系統語系決定。它把本機執行回執整理成一段話，只把讀回的回覆算作完成；自己的評論和自測不算真實觀眾互動。`--since` 可指定帶時區的起點，`--notes` 可附有來源 URL 的待跟進發現；不編造點讚、@消息、私信或研究成果。匯報不會自動寄信。

僅在使用者明確要求實發測試時執行：

```sh
vpub comments self-test youtube VIDEO_ID --test-id hello-test-01
```

這會在自己的作品下發送一次「你好」並回覆「你好」；同一 test-id 重跑復用記錄，未確認結果不重送。它不會在登入或安裝時執行，也不會自動刪除留言。

## 多平台巡檢與數據變化

先選擇要追蹤的作品，之後一個指令即可收集評論、作品數據與本輪匯報。這是手動執行的唯讀巡檢，不會建立排程、發送回覆、按讚或寄信。

```sh
vpub works youtube --limit 20
vpub works bilibili --limit 20
vpub works instagram --limit 10
vpub ops watch add youtube VIDEO_ID
vpub ops watch add douyin WORK_ID
vpub ops watch list
vpub ops sync --pages 2 --limit 20 --language zh --out round.json
vpub ops sync --platforms youtube,douyin --out selected-round.json
vpub ops watch remove youtube VIDEO_ID
```

`works` 支援五站：YouTube 從已授權頻道的上傳列表讀取，B站只列已發布稿件，保留定時與審核安排。這兩站用 `--cursor` 翻頁。抖音、Instagram、X 仍只列目前可見的首頁，不把它當成完整作品庫。瀏覽器平台的追蹤作品需要既有 CrossCast 發布回執，或使用上方 `ops adopt` 新建歸屬核驗記錄；API 平台直接核對作品擁有者。

`ops sync` 僅處理追蹤清單，每部作品最多讀取 `--pages` 頁，每個 API 頁最多 `--limit` 條頂層評論（瀏覽器為結果總上限）；API 回覆預覽可能另外增加項目數。首次讀取建立基線，後續按評論 ID 去重，自己的留言不列入觀眾互動。`inbox` 是已觀察評論的待處理候選清單，不是私訊或全帳號收件箱；`needs_thread_check` 表示尚不能確定是否已回覆，必須讀取上下文。CLI 不會因候選清單而自行發送。

作品數據保留歷次事件與最新快照；只有同帳號、同作品、同來源、同標籤及單位的精確數值才計算 `changes.delta`。缺失、約數、換來源都保留 `null`，百分比差值標為百分點，負數變化照實保留。不同平台的播放或瀏覽定義不相加。部分失敗會保留成功項目並回傳 `PARTIAL`、退出碼 4；遇到驗證或帳號錯誤，不會換路徑重試。每輪保存本機回執，匯報只涵蓋該輪事件。

### 讀取評論樓的下一頁

```sh
vpub comments replies youtube VIDEO_ID ROOT_COMMENT_ID --limit 20 --out replies.json
vpub comments replies youtube VIDEO_ID ROOT_COMMENT_ID --cursor NEXT_CURSOR
vpub comments replies bilibili BV_ID ROOT_COMMENT_ID --limit 20
```

這兩站可分頁讀取指定頂層留言下的回覆，核對作品與父留言。`next_cursor` 表示仍有下一頁；`complete` 僅描述目前請求範圍。這不會把 Instagram、X、抖音的可見留言讀取升級為完整歷史匯出，也不會繞過原有發送去重。

## 統一訊息巡檢與評論補讀

```sh
vpub messages sync --platforms douyin,instagram,x --sources comments,dm_preview --language zh --out messages.json
vpub messages sync --platforms douyin,instagram,x --sources mentions,dm_preview
vpub messages list --status pending --limit 50
vpub messages list --kind dm_preview --identity ACCOUNT_ID
vpub messages mark MESSAGE_KEY handled
vpub comments collect douyin WORK_ID --pages 3 --reply-pages 1 --limit 50
```

評論來源沿用 `ops watch` 追蹤清單；同一份評論歷史與處理標記同時供 `inbox` 和 `messages` 使用。抖音讀取「通知 → @我的」，Instagram 讀取「Notifications → Tags & mentions」，X 讀取 Mentions；三站亦可讀取私信會話預覽。抖音會同時核對創作者中心與公開個人頁的帳號。每個來源獨立建立首次基線，之後去重並保留處理標記；穩定會話 ID 的預覽正文改變會重新列為未讀。Instagram／抖音未提供穩定 ID 時，保存的是內容預覽快照，不能當作新收件訊息數，也不能用暱稱推斷收件人。相對時間標籤改變不算新訊息。

`messages sync` 只讀取並產生使用者語言的匯報，不建立排程或自動發送。私信正文只存本機，不放入匯報或 GitHub。開啟平台通知頁可能改變平台的已讀標示；`messages mark` 只改本機狀態。首次基線不代表新收到訊息，部分頁面沒有出現的舊項目不會刪除。來源回傳 `FAILED`／`UNSUPPORTED`／`NOT_CONFIGURED` 時，整輪明確標為 `PARTIAL`，不當成零訊息。抖音與 Instagram 的通知只覆蓋已載入可見區域；沒有穩定事件 ID 的項目標為 `stable_source_id: false`，完全相同的通知可能合併，不能作為精確新增數或回覆目標。抖音真實 @ 留言及再次讀取去重已測；Instagram 已測入口與明確空列表，非空提及仍待驗證。YouTube 私信及 B站訊息的統一來源尚未接入；B站既有上游指令仍可用。

`comments collect` 及瀏覽器 `ops sync` 加入有界捲動與可見回覆展開，保留跨頁 ID 聯集及停止原因。`--pages` 為讀取輪數，`--limit` 為瀏覽器結果總上限，`--reply-pages` 每單位最多展開五個可見回覆控制項；0 表示不主動展開。完整歷史、未知父子關係與頁碼不會被推測。B站／YouTube 繼續使用原有 API 分頁和樓層預算。

### YouTube 審核與 X 隱藏／恢復

```sh
vpub comments review youtube VIDEO_ID --status heldForReview
vpub comments moderate youtube VIDEO_ID COMMENT_ID --status heldForReview --dry-run
vpub comments moderate youtube VIDEO_ID COMMENT_ID --status published
vpub comments hide x WORK_ID REPLY_ID --dry-run
vpub comments hide x WORK_ID REPLY_ID --remove
vpub comments verify-action ACTION_KEY
```

移除 `--dry-run` 就會直接提交已明確指定的動作。YouTube 使用官方 `youtube.force-ssl` 權限，只處理自己影片上已觀察的觀眾評論；不拒絕評論、不封鎖作者。按 ID 讀取不會提供審核狀態，因此另以有界審核隊列確認；若執行緒有回覆而無法區分父評論與子評論的狀態，停止提交。2026-10-30 使用另一帳號的測試留言，完成「公開 → 待審 → 公開」並讀回確認。待審留言可能從 `comments.list(id)` 消失，因此改以擁有者審核隊列中的精確留言 ID、影片 ID 與狀態核驗；缺席本身不等於刪除。這是一組頂層留言實測，不代表所有回覆層級及帳號均已驗收。[官方審核介面](https://developers.google.com/youtube/data/reference/rest/v3/comments/setModerationStatus) · [狀態欄位限制](https://developers.google.com/youtube/data/reference/rest/v3/comments)。

X 僅對自己原帖下、其他作者的確切回覆提供隱藏／恢復；已實測一條使用者提供的留言，完成「顯示 → 隱藏 → 恢復」並讀回選單狀態。CLI 處理首次隱藏確認，並從原帖選單進入隱藏回覆頁；不以消失、刪除或封鎖代替驗證。隱藏的回覆仍可從 X 的隱藏回覆入口查看。[官方說明](https://help.x.com/en/using-x/mentions-and-replies)。

待核驗時先執行 `comments verify-action ACTION_KEY`，只讀取、不重送。若已查明仍為原狀態，可明確執行 `comments resume-action ACTION_KEY`；只支援待核驗的隱藏／審核回執，重新核對帳號、正文、原狀態及目前回執後才重試一次。狀態未知、舊回執、刪除或編輯不接受此恢復命令。它不是巡檢中的自動重試。

## 持久評論收件匣與減少瀏覽器啟動

`ops sync` 將同平台、同帳號最多十條追蹤作品合併到一次 CLI 專用瀏覽器會話。Instagram／X 在同一作品頁讀取評論與數據；抖音仍需切換公開評論頁與創作者數據頁。批次只接受讀取；驗證要求或讀取失敗會停止該平台後續讀取，保留已完成結果，再處理其他平台。這能減少重複啟動，但不是官方直連 API，也不承諾固定速度。

```sh
vpub ops sync --reply-pages 1 --reply-threads 5 --out round.json
vpub messages list --kind comments --status pending --limit 50
vpub messages list --kind comments --platform youtube --status all --include-own
vpub messages mark COMMENT_KEY handled
vpub messages mark COMMENT_KEY deferred
vpub messages mark COMMENT_KEY unread
```

本機收件匣按平台、帳號、作品與評論 ID 保存已讀取內容。新增項目為 unread；read／handled／deferred 標記在下次巡檢後保留，正文變更會重新列為未讀。局部分頁沒有出現的評論不會從歷史刪除，使用舊內容前應檢查 `last_seen_at`。`pending` 排除自己的評論、已知回覆過的評論，以及已處理／稍後處理項目；`--cursor` 繼續本機分頁，`--identity` 選擇記錄中的帳號。標記只改本機狀態，不發送回覆或修改平台。

B站／YouTube 可在 `--reply-threads`（每作品預設 5 樓）及 `--reply-pages`（每樓預設 1 頁）範圍內補讀不完整的回覆預覽；任一設為 0 可停用補讀。未完成樓層保留 `reply_continuations`，以 `comments replies` 繼續。瀏覽器平台現可有界補讀；提及與會話預覽的獨立範圍見上方統一訊息章節。

## 管理單條評論

```sh
vpub comments list instagram WORK_ID
vpub comments status instagram WORK_ID COMMENT_ID
vpub comments like instagram WORK_ID COMMENT_ID
vpub comments like instagram WORK_ID COMMENT_ID --remove
vpub comments edit youtube VIDEO_ID OWN_COMMENT_ID --text "修改後的評論"
vpub comments delete youtube VIDEO_ID OWN_COMMENT_ID
vpub comments like douyin WORK_ID COMMENT_ID
vpub comments like douyin WORK_ID COMMENT_ID --remove
vpub comments delete douyin WORK_ID OWN_COMMENT_ID
vpub comments verify-action RECEIPT_KEY
```

| 平台 | 新增評論控制 |
|---|---|
| B站 | 透過固定版本 bili-cli 點讚／取消讚，讀回確切評論狀態 |
| Instagram／X | 對確切可見評論點讚／取消讚；愛心控制項不唯一時停止 |
| YouTube | 透過官方 API 編輯／刪除自己影片下自己發出的評論 |
| 抖音 | 創作者中心評論點讚／取消讚；刪除自己作品下自己發出且沒有回覆的評論；既有頂層回覆仍可使用 |

這些指令明確指定單條評論，與影片點讚分開。先讀取評論再操作；不設逐條文案審核，`--dry-run` 為可選預覽。YouTube 編輯使用宿主提供的單語正文，不自行翻譯。提交前核對作者、帳號、作品與當前正文；逾時或未確認的操作會保留回執，阻止再次修改，應以唯讀核驗處理，不自動重送。自測使用 `--test-id`，普通匯報排除這些記錄。刪除只在明確要求時執行且不可復原，登入不會觸發。抖音刪除拒絕有回覆的父評論。置頂／隱藏邊界見下節；批量審核未實作。

## 私信與平台差異

首次登入後，`dm` 使用 CLI 自己保存的會話；抖音、Instagram、X 在背景操作官方網頁，並非獨立私有 API。訊息由宿主 Agent 根據收件者語言撰寫，**只用一種語言**。`--language` 記錄宿主的判斷，不會自動翻譯或判定所有語言。

```sh
vpub dm list instagram --limit 10
vpub dm read instagram EXACT_HANDLE --limit 20
vpub dm send instagram EXACT_HANDLE --file message.txt --language en
vpub dm send douyin MS4w_PUBLIC_PROFILE_ID --file message.txt --language zh
vpub dm send x EXACT_HANDLE --file message.txt --language en
vpub dm verify RECEIPT_KEY
vpub dm unlock x
```

| 平台 | 私信範圍與限制 |
|---|---|
| 抖音 | 創作者中心的可見會話摘要；透過確切公開主頁開啟、讀取及發送文字私信。收件者使用 `/user/` 後的 `MS4w…` ID，不用暱稱猜測。遵守陌生人訊息數量與收件限制。 |
| Instagram | 可見收件匣摘要；用確切帳號名稱開啟對話，讀取／發送文字。姓名或搜尋相似結果不能代替帳號。 |
| X | X Chat 可見收件匣、確切帳號對話及文字發送。官方 PIN 驗證時使用 `dm unlock x`，使用者在專用視窗輸入；CLI 不讀取 PIN。收件匣關閉時停止。 |
| B站 | 委派既有 bili-cli 的 `dm list/read/send`，保留其新聯絡人限制；本輪未做 B站實測。 |
| YouTube | 此後端沒有私信功能，使用既有評論回覆。 |

`list/read` 不發訊息，但打開官方會話可能將來訊標成已讀。只讀取當前可見範圍，並非完整歷史匯出。安裝、登入和 `ops sync` 不自動私信；使用者已授權的單條操作可直接執行，`--dry-run` 可選。

提交前保存帳號、收件者、原狀態及回執；只提交一次，再用新的瀏覽器會話讀回發出訊息。`DM_VISIBLE` 代表在發送方會話中確認可見，**不代表對方收到或已讀**。`DM_UNVERIFIED` 使用 `dm verify KEY`，不可重新發送；相同的最近成功訊息也不重送。官方驗證、無法確認收件者或介面變更會停止。私信正文與收件者只保存在本機回執，營運匯報只記錄去重後的狀態與數量；自測用 `--test-id LABEL` 排除。

### 置頂與隱藏的邊界

`vpub comments pin bilibili BV_ID ROOT_COMMENT_ID` 及 `--remove` 委派上游，僅支援頂層評論；已有其他置頂時停止，不暗中替換。本輪只做離線測試。抖音及 Instagram 的已觀察網頁沒有可驗證的評論置頂／隱藏入口，暫未接入；X 的「置頂到個人主頁」不是評論置頂；隱藏／恢復入口與實測限制見上節。其他未支援動作不用刪除、踩或帳號封鎖模擬。能力以 `vpub capabilities` 為準，不能把某個帳號的網頁限制推廣成平台永遠不支援。

## 安裝與開始

需要 Python 3.10–3.12，Git URL 安裝另需 Git。[Windows 安裝腳本](../install.ps1) 尋找相容 Python、建立獨立環境並安裝依賴；缺少 Python 時提示 Agent 準備。背景瀏覽器需要 Google Chrome，缺少時可用 `vpub setup --install-browser` 安裝。YouTube API 需要桌面 OAuth 用戶端 JSON，另外三站無需申請平台開發者金鑰；不需要獨立模型 API 金鑰。

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
vpub init --video final.mp4 --language zh-Hans --out publish.json --platforms bilibili,douyin,youtube
```

首次選擇平台及自動發布或先看文案；郵件僅支援 Gmail，預設關閉，使用者主動要求時才開啟。沿用已取得的授權，不逐片強迫文案審核。`--confirm` 是方案完整性雜湊；CLI 保存郵件設定，宿主保存文案審核偏好。

舊版升級先卸載套件 `whitecarp-video-publisher`，再安裝本版本。保留 `vpub`、Python 模組 `video_publisher` 與原有執行、登入及工作紀錄，不刪除使用者資料。

### YouTube 官方 API 登入

啟用 Google Cloud 的 YouTube Data API v3，建立「桌面應用程式」OAuth 用戶端並下載 JSON；API Key 本身不能授權上傳。若專案仍在測試模式，先於 Google Auth Platform 的「目標對象／受眾 → 測試使用者」加入登入帳號，避免 `403 access_denied`。

```sh
vpub auth login youtube --method api --client-json /path/to/client.json
vpub auth status youtube --method api
```

在執行 CLI 的同一台電腦開啟輸出的 Google 官方授權連結，選擇頻道並完成授權；回呼等待時間為五分鐘。JSON 與登入權杖留在本機，請勿提交 GitHub。方案的 YouTube `method` 設為 `api` 才會使用 API；登入不會自動改變既有方案。首次連接一次請求上傳、讀取及影片／播放列表管理權限，由使用者在 Google 頁面確認。舊版僅有上傳／讀取的權杖需重新授權一次。授權不等於允許任意刪片。

若授權已完成卻在 API 查詢逾時，先執行 `vpub doctor`。付費附加元件的依賴包含 [PySocks](https://pypi.org/project/PySocks/)；Google API 客戶端的 `httplib2` 缺少它時可能忽略已設定的代理。舊環境可在同一個 Python 環境執行 `python -m pip install PySocks==1.7.1`，再執行狀態查詢，毋須重傳影片。

## 登入一次，後續用 CLI 發布

```sh
vpub auth login x
vpub auth login instagram
vpub auth login douyin
vpub auth status all
```

首次登入開啟 CrossCast 專用的官方 Chrome 視窗，使用者自行選擇網站提供的掃碼、密碼等方式並完成簡訊或身份驗證。後續命令在背景復用同一本機會話。這與 Codex 內置瀏覽器分開，不搬移宿主 Cookie；登入可能過期或再次要求驗證。

`onboard` 只啟動選中的平台。既有方案不會自動遷移：抖音／Instagram／X 設為 `method: browser`，YouTube 設為 `method: api`；新版 `init` 已使用這些預設。`method: native` 是明確選用的宿主 Agent 備援。

X／Instagram 已通過重啟後 CLI 回讀；抖音重新登入後，多次獨立命令已可回讀和修改第二期，長期會話穩定性仍待驗收。詳見[驗證記錄](VALIDATION.md)。

## 自動簡介與語言

規則依影片內容的原語言自動選擇，不跟隨帳號地區或 GitHub 文件語言：

| 平台 | 內容語言 | 簡介 |
|---|---|---|
| Bilibili、抖音 | 中文，含簡體與繁體 | 僅簡體中文 |
| Bilibili、抖音 | 其他語言 | 原語言＋簡體中文 |
| YouTube、Instagram、X | 英文 | 僅英文 |
| YouTube、Instagram、X | 其他語言 | 原語言＋英文 |

先保留原語言段落，再放目標語言段落；符合平台語言時不重複。保留角色語氣、集數與情節，按字數限制共同精簡兩段。影片音軌、字幕和封面內的文字不會因此自動翻譯。

```sh
vpub metadata request publish.json --source script.txt --out request.json
# 宿主聊天模型讀取請求、核對腳本，撰寫 response.json。
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
```

雙語回應須包含兩段 `description_parts`，`description` 等於兩段以空行連接；CLI 校驗順序、模式與字數。原語言不明時先由宿主核對腳本／字幕並設定 `content_language`；不以文件語言猜測。

`bili-cli` 接收已寫好的簡介，寫作由宿主聊天模型完成。CrossCast 將此方式擴充成結構化請求、語言規則與校驗，不讀隱藏對話，也不內建獨立翻譯模型。預設不放外鏈，移除跨平台無用的 BV 參照，保留情節及第三方作者／授權的純文字資訊。結構校驗不等於翻譯品質驗證。

## 專用封面與原影片

```sh
vpub covers prepare localized.json --source production/cover.jpg --portrait production/portrait.jpg --out-dir covers-v1 --out ready.json
```

橫式角色優先使用方案的專用圖，再用製作封面，縮放補邊保留全圖。抖音與 Instagram 的直式角色必須使用同一張專用構圖：`--portrait` 優先，其次取方案的抖音／Instagram 直圖。兩站輸出保留完全相同的圖片位元組與 SHA-256，不再把橫圖補邊冒充直式封面。缺少直圖時提示準備素材；不覆蓋原圖。

| 平台 | 工作畫布與套用方式 |
|---|---|
| Bilibili | 1600×1000；上游 CLI 或內置瀏覽器 |
| YouTube | 1920×1080；API 或 Studio，可能要求官方驗證 |
| 抖音 | 1200×900 橫式＋共用直式原圖；CLI 分別上傳，平台直式編輯器會裁為 3:4 |
| Instagram | 1920×1080 網頁圖＋同一張共用直式原圖供手機選取；影片保留原比例 |
| X | CLI 經 Media Studio 上傳橫式封面；帳號需有 Studio 權限 |

這些是工作預設，不是永久平台規格。本地保留全圖不代表平台不裁切，必須檢查平台預覽與主頁縮圖。來源 SHA-256 也不是平台壓縮檔的遠端雜湊。

### Instagram 發布與封面

**Instagram 保留原片及「原版」比例，上傳時另外設定封面。** 手機「編輯封面 → 從相機膠卷中添加」可選與抖音共用的專用直式圖。0.2.4 的一秒片頭實驗未解決需求，0.2.5 已移除；目前準備命令不改影片、不轉碼，也不需要 FFmpeg。

```sh
vpub instagram prepare ready.json --out instagram-ready.json
# 若方案未指定共用直式圖，可加 --mobile-cover production/portrait.jpg。
```

手機操作：開啟作品的「編輯封面」，選「從相機膠卷中添加」及共用直式圖；檢查封面與「主頁網格」預覽，再保存。不同畫面可能裁切不同區域。

交接優先取明確的 `--mobile-cover`，其次是 `covers.douyin.portrait`，再取 `covers.instagram.portrait`。交接檔保存同一張圖片的路徑與雜湊，手機操作由使用者完成。未來共用封面優先考慮 9:16 構圖，把標題、人物臉部與必要文字放在兼顧 3:4 裁切的中央安全區，避開邊緣與介面遮擋，並檢查兩站實際預覽。這是設計原則，不保證所有畫面裁切一致。

**第二期共用封面已確認可用。** 2026-10-30，使用者在抖音與 Instagram 手機主頁套用 v3 後提供截圖並確認通過；底部小字已移入可見區域。這次驗收僅涵蓋提供的兩個手機主頁畫面，不能保證所有裝置與展示位置的裁切一致。後續封面沿用共同安全區，繼續檢查字級、對比、構圖及播放數等介面遮擋。

新方案明確選原製作成片，不要選 0.2.4 已插入片頭的實驗版本。更新 CLI 不會自動替換、刪除或重發既有作品。

[X Media Studio 流程](X_GUIDE.md) · [刪除與封面重發](DELETION_AND_COVERS.md)

修改支援平台的現有作品可用下列交接命令；需核對原帳號、作品及保存結果。Instagram 手機換圖與 X Studio 使用上面的專用流程。

Instagram 文案修正會打開原作品的編輯欄，完整比較已保存的原始文字；不把頁面翻譯、留言或舊成功回執當作目前簡介。相同文字不再保存；不一致時修正同一作品，再重新開啟編輯欄讀回。`vpub edit KEY PATCH --verify-only` 只核驗、不保存；`EDIT_MISMATCH` 代表實際文字不同，可檢查後明確重新執行修改。

```sh
vpub browser cover RECEIPT_KEY --file landscape.jpg --portrait portrait.jpg --identity OBSERVED_ACCOUNT
```

## 播放列表、類別與章節

YouTube 可設定 `playlist_titles`（內置瀏覽器）或 `playlist_ids`。API 先查成員避免重複加入，不盲目重試結果不明的寫入。所需管理權限已納入首次 OAuth 授權。Studio 新建私密清單並選入既有影片已驗證，API 路徑仍為模擬測試；B站合集沿用上游能力，尚無統一合集管理命令。

新 YouTube 方案必須明確填寫 `category_id`，由宿主依影片內容與官方選項選擇，不再固定為「人物和博客」。`metadata request/apply` 可提供類別，已指定值會保留。

抖音 `method: browser` 可在發布流程中設定 `duration_seconds` 與 `chapters`：作品取得持久 ID 後，CLI 填入自訂章節、保存並逐項讀回；不把平台自動建議當成已完成。若作品仍審核中，回執記錄待辦，後續 `vpub verify`／`resume` 完成這個章節步驟，不重傳影片。B站／抖音／YouTube 的 native 交接仍保留。時間點來自同一支成片，自零開始、遞增且小於片長；標題跟隨平台文案語言。

```json
{
  "duration_seconds": 78.506667,
  "chapters": [
    {"start_seconds": 0, "title": "迟到的回复"},
    {"start_seconds": 19, "title": "旧留言里的想念"},
    {"start_seconds": 46, "title": "用自己的身份回应"},
    {"start_seconds": 57, "title": "先读，再开口"}
  ]
}
```

以上為第一期的歷史範例，當時使用者確認手機可見；目前第二期的六個自訂章節也已由 CLI 保存並完整讀回。章節入口、12 字元限制與審核回執見[操作指南](CATEGORIES_AND_CHAPTERS.md)。在簡介寫時間碼不代表平台章節已生效；入口未開通或仍審核中時保留待辦。

## 修改已發布作品

```sh
vpub edit RECEIPT_KEY correction.json --dry-run
vpub edit RECEIPT_KEY correction.json
# 保存結果不明時只讀回，不再按保存：
vpub edit RECEIPT_KEY correction.json --verify-only
```

JSON 必須有 `expected_identity`，每次只改一組：完整 `title`／`description` 與語言欄位，或抖音 `portrait` 檔案，或 `chapters`／`duration_seconds`。YouTube 經 API、Instagram／X／抖音經專用 CLI 會話；不更換影片或修改可見性。回執保留原作品 ID，結果不明的保存禁止自動重試。封面回讀提供本機截圖，仍須視覺核對，不以檔案雜湊冒充遠端像素比對。

X 只有原帖出現 Edit 時才可嘗試修改；不可編輯時不自動刪除、重發或回覆。本次第二期依使用者選擇保留原帖，下一條才採新語言規則。

## 發布與回執

```sh
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm CURRENT_PLAN_HASH
vpub verify RECEIPT_KEY
vpub works instagram
# X：最終提交前，接續既有 Studio 素材。
vpub resume RECEIPT_KEY
vpub receipts
```

新回執保存帳號、完整文案、素材雜湊與最終提交標記。不確定的提交不會自動再按一次；`verify` 可尋找唯一吻合的新作品。`resume` 在提交後驗證原作品，並可完成原方案尚未執行的抖音章節；提交前可接續 X 已存的 Studio 素材。Instagram／抖音中斷上傳需檢查既有草稿。`works` 只讀可見首頁（抖音最多十張卡片），不是完整歷史匯出。進階 native 修改仍用 `browser request/begin/record`。

範本預設 B站／抖音公開、YouTube 私密、Instagram／X 依帳號受眾，宿主須按使用者意圖設定。登入不會自動增加發布目標。準備、待驗證、審核中與公開成功分開記錄；畫面證據及 API 查詢使用不同狀態。

## 可選郵件提醒：僅支援 Gmail

**預設關閉，只有使用者主動要求時才開啟。** 一般安裝／登入不詢問郵箱，也不會自動啟用。此功能僅支援宿主 Agent 的 Gmail 插件；不支援 Outlook、QQ、網易、SMTP 或簡訊。

使用者要求開啟後，才請其填寫自己的 Gmail 地址。宿主識別地址並檢查插件：缺少插件時協助安裝；已安裝但未連接時引導授權；隨時可放棄並維持關閉。寄出前核對當前連接帳號與所填地址完全一致，**只從自己發給自己**，不把其他服務的登入別名當作 Gmail。

```sh
# 需要時才執行；一般安裝不執行 configure
vpub notify configure --to you@gmail.com
# 隨時關閉
vpub notify configure --provider off
```

以下由宿主 Agent 操作；一般使用者不用編輯 JSON：

```sh
vpub notify inspect --email you@gmail.com --inventory host-mail-inventory.json
# 一次性測試不會開啟長期提醒，同一 test-id 不重寄
vpub notify test --email you@gmail.com --test-id setup-test-001 --inventory host-mail-inventory.json
# 填入 AI 助手自己的 Gmail 寄信工具的確切名稱；任何 AI 助手都可以
vpub notify claim RETURNED_ALERT_ID --available-tool YOUR_GMAIL_SEND_TOOL --inventory host-mail-inventory.json
# 宿主用返回的 message 呼叫該工具一次，再保存回傳並記錄
vpub notify record RETURNED_ALERT_ID --claim-token RETURNED_TOKEN --response actual-send-response.json
```

測試信明確標記 TEST，不偽造登入故障。官方驗證出現時保留原作品並立即在對話提示；只有已啟用郵件才入列。宿主必須當下能呼叫 Gmail 插件；宿主未執行時，CLI 無法代為寄信。已入列、已寄出與已送達收件匣是不同狀態。逾時或結果不明都不會自動重試。詳見[實測狀態](VALIDATION.md)。

## 後端與參考

B站使用內含的 [kukemc/bili-cli](https://github.com/kukemc/bili-cli)，並提供 `bili` 指令；YouTube 由 AI 助手操作 YouTube Studio，或使用官方 API（需付費附加元件）；抖音／X／Instagram 使用 CLI 專用背景瀏覽器，宿主瀏覽器僅為明確選用的備援。完整實發及限制見[驗證記錄](VALIDATION.md)。


## X、Instagram 與 YouTube 的官方 API 路徑

這些路徑是另外付費的附加元件，本文件中使用官方 API 的 YouTube 指令同樣需要它。預設的瀏覽器路徑維持免費，也不需要開發者應用。

## 加快連續指令

留言、數據、按讚、私訊等非發布的瀏覽器指令，現在每個帳號共用一個背景瀏覽器，後續指令不必重新啟動 Chrome。它只在你的電腦上運行，透過經過驗證的私有管道通訊；閒置 5 分鐘、滿 1 小時、發生任何錯誤或執行 `vpub stop` 後都會結束。發布、`resume`、`edit` 和登入會先關閉它再單獨執行。已送出的指令不會自動重送。第二個指令會等第一個完成，最多等三分鐘，不會直接失敗。若要關閉此功能，在設定檔把 `browser_idle_seconds` 設為 0，或設定環境變數 `VPUB_BROWSER_IDLE_SECONDS=0`。

## 查看需要處理的事項

```sh
vpub status
```

`vpub status` 只讀取本機記錄，不需要登入，也不會連線到任何平台。它會列出目前的暫停狀態與原因、每個未完成的發布及下一步的確切指令和上傳進度、執行中的背景瀏覽器、待處理訊息、最近一次巡檢，以及影片副本佔用的磁碟空間。列出的已存登入不代表即時狀態；要即時確認請用 `vpub auth status`。

`vpub stop` 會暫停發布和瀏覽器指令。工具不會自行解除暫停：處理完原因後，由操作者刪除 `vpub stop` 和 `vpub status` 指出的那個檔案。

## 釋放磁碟空間

```sh
vpub cleanup
vpub cleanup --apply
```

每次發布都會保留一份已核准影片的私有副本，避免之後的修改影響上傳內容。第一個指令只列出可刪除的項目。加上 `--apply` 時，只有當使用該副本的發布全部已核驗，且原始影片仍在原處、內容未變時才會刪除，因此不會刪掉唯一的完整副本。之後把同一方案發布到其他平台時，會從原始影片重新建立副本。
