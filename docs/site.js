(function () {
  "use strict";

  var REPO = "https://github.com/whitecarp/publishops-crosscast";
  var KEY = "crosscast-lang";

  // Simplified Chinese is the HTML itself; it is read from the page below, so the page works without JS.
  var ZH = {
    _copied: "已复制",
    _copyFail: "复制失败。文字已选中，请按 Ctrl+C（Mac 上按 ⌘C）手动复制。",
    _copyErr: "复制失败"
  };

  var TW = {
    beforeTitle: "開始之前",
    beforeAgent: "用能執行本機指令的 AI 助手，如 Codex 或 Claude Code；只有聊天功能的視窗不夠。",
    beforeNetwork: "在中國大陸使用 YouTube、Instagram 或 X，需要自己想辦法哦。",
    beforeX: "X 需要訂閱，而且帳號確實能進入 Media Studio；僅付費不保證可用。",
    beginnerGuide: "<a href=\"https://github.com/whitecarp/publishops-crosscast/blob/main/docs/GUIDE_ZH_TW.md#新手完整提示詞\" rel=\"noopener\">不熟悉電腦？複製完整的新手提示詞</a>，含首次預覽與安全回報步驟。",
    skip: "跳到正文", navLabel: "頁面導覽",
    navHow: "怎麼用", navPlatforms: "平台", navStart: "上手", navFaq: "常見問題",
    h1a: "一句話，", h1b: "發往你選的所有平台。",
    lead: "把影片交給你正在用的 AI 助手（如 Codex、Claude）：它為 B站、YouTube、抖音、Instagram 和 X 各寫一版標題和簡介，vpub 調整封面，首次登入之後在背景逐一到各平台提交，讀回到作品才算發布。發完之後，改稿、回留言、看數據也能接著交給它。",
    promptLabel: "對你的 AI 助手說", copy: "複製", copyPromptA: "複製這段話",
    installPrompt: "幫我從 https://github.com/whitecarp/publishops-crosscast 安裝 PublishOps CrossCast，執行 vpub doctor 並讀取它給出的 skill 檔案。然後把 final.mp4 公開發布到[我選的平台]，封面用 cover.jpg，標題和簡介依平台來寫，每個平台首次發布都先給我看目前方案並等我明確同意；確認碼不代表同意。這支影片是中文的，（有/沒有）用 AI 製作，講的是：（用一句話說說）",
    promptHint: "把檔名和內容換成你的；平台和公開範圍可以依需要修改。",
    ctaStart: "三步上手", ctaDocs: "閱讀文件",
    alphaNote: "0.16.0 alpha：這一版已在 YouTube、抖音、Instagram 和 X 的真實帳號上公開發布過，B站目前只有直接用舊版 bili-cli 的上傳。登入會過期，平台也可能臨時要求驗證，長時間無人值守還沒驗收。",
    mfTitle: "發布單", mfTag: "示意", mfSrcVideo: "來源影片", mfSrcScript: "腳本", mfSrcScriptV: "簡體中文", mfSrcVis: "公開範圍", mfSrcVisV: "全部公開",
    mfColPlatform: "平台", mfColCover: "封面", mfColCopy: "文案", mfColState: "狀態",
    srCover: "封面：", srCopy: "文案語言：", srState: "狀態：",
    langZh: "簡中", langBoth: "中英",
    pDy: "抖音",
    mfFoot: "逐一到各平台直接提交；讀回到作品，才標為「已核驗」。",
    howTitle: "你說一句，AI 助手去辦",
    howSub: "文字由你正在用的 AI 助手來寫，不內建模型，也不用另外設定模型 API Key；vpub 負責方案、封面調整、預演和回執。",
    pbSay: "你可以這樣說", pbDo: "它會做什麼", pbNot: "它不會", pbSaySr: "你可以這樣說：",
    s1q: "把這支影片發 B站和 YouTube，標題口語一點。",
    s1a: "依平台各寫一版標題、簡介和標籤，直接提交到這兩個平台，並記錄每一步的回執。",
    s1n: "因為某個平台已登入就順手加上，或擅自改動公開範圍。",
    s2q: "封面依各平台尺寸調整一下，原圖別動。",
    s2a: "依平台畫布另存一份封面，保留完整畫面。發抖音或 Instagram 還需要一張直式封面：AI 助手能畫就畫一張，不能畫就用 vpub covers portrait 從原圖挑內容重新排版。平台預覽仍可能裁切，提交前請看一眼。",
    s2n: "裁切、拉伸或覆蓋你的原圖。",
    s3q: "這支中文影片也發 YouTube，讓外國觀眾看得懂。",
    s3a: "文案跟著影片語言走：B站、抖音用簡體中文，YouTube、Instagram、X 用英文；影片本身是別的語言時，先保留原文，再附上平台語言的譯文。所以這支在 YouTube 上是中文在前、英文在後。",
    s3n: "翻譯影片裡的配音或字幕。",
    s4q: "上次那支到底發出去沒有？",
    s4a: "查本機回執，依平台告訴你到了哪一步；能讀回的，再到平台上核對。",
    s4n: "把排隊、登入連結或本機回執說成「已發布」。",
    statesTitle: "每個狀態，都叫它本來的名字",
    statesSub: "直接發布，也要說清楚到了哪一步。AI 助手依實際證據回報，不會把前一步說成後一步。",
    stQueue: "排隊中", stQueueD: "任務已記錄，還沒開始上傳。",
    stRun: "發布中", stRunD: "CLI 正在背景上傳和提交。",
    stSent: "已提交，待核驗", stSentD: "平台已接收提交，可能還在審核或處理中，還沒有讀回到作品。",
    stOk: "已核驗", stOkD: "已從平台頁面或 API 讀回到這支作品。審核和公開狀態以平台顯示為準。",
    rule1a: "遇到驗證、風控或限流", rule1b: "：所有平台都停下。你在官方頁面處理好、查明原因後，用 <code>vpub stop --clear</code> 解除。",
    rule2a: "結果不明", rule2b: "：只核對原來的任務，不重複上傳。",
    platTitle: "五個平台，各走各的路",
    platSub: "實測記錄是過去的真實發布，不代表在你的帳號、地區和平台狀態下一定成功。本機實際能走哪條路徑，以 <code>vpub capabilities</code> 的結果為準。這裡寫的是免費路徑；YouTube、X 和 Instagram 的官方 API 路徑屬於付費附加元件。",
    thPlatform: "平台", thLogin: "登入", thRoute: "發布方式", thTested: "實測記錄", thNote: "說明",
    pBiliLogin: "B站 App 掃碼（bili-cli）", pBiliRoute: "內建的 bili-cli",
    pYtLogin: "在專用 Chrome 裡登入一次", pYtRoute: "背景 Chrome 操作 YouTube Studio",
    pChrome: "專用官方 Chrome", pBgBrowser: "CLI 背景瀏覽器",
    pIgRoute: "CLI 背景瀏覽器，封面在手機上改", pXRoute: "背景操作 Media Studio（需訂閱）",
    testedPub: "已發布", testedYt: "免費路徑", paused: "暫停",
    pTtNote: "CLI 會拒絕 TikTok 的登入和發布請求。",
    platNote1: "YouTube：2026 年 11 月 2 日已公開發布兩支影片並讀回。兩支第一次都只留下草稿，重新開啟再按一次發布才發出，現在自動處理這一步的程式還沒在真實頻道跑過。上傳時加入播放清單和排程只在草稿上傳裡確認過。",
    platNote2: "X：帳號必須已訂閱並能進入 Media Studio；未訂閱帳號的影片上限是 140 秒、512 MB，本工具不支援。X 目前有兩個版本的網站，CrossCast 兩種都支援；真實記錄都在舊版上，新版還沒有在已登入帳號上試過。 X 編輯的版本行為也未實測；未確認的修改可能已經儲存。CrossCast 拒絕重試，即使加 --person-approved 也不行；只能 --verify-only 讀回，或由使用者到 X 檢查並手動修改。",
    platNote3: "抖音：長時間維持登入還在驗收。上傳中斷後不能續傳，但 vpub resume 會接著留下的草稿完成。封面視窗曾卡住；只有使用者明確選擇略過封面時，才可不帶封面發布並在手機上補設。程式只會自動略過 4:3 橫式封面，前提是 3:4 直式封面照樣設上，例如直式影片。封面按鈕被擋住時會先等最多 90 秒，還是不行就停下，什麼都不發布。Windows 上現在改用隱藏桌面上的真實 Chrome 視窗，這還沒試過。標題和簡介儲存已嘗試，但仍未核驗成功。",
    platNote4: "暫不支援：TikTok、YouTube 資訊卡和可點擊片尾、自動或批次的私訊和按讚、完整歷史匯出。",
    startTitle: "三步，從安裝到第一次發布",
    st1: "安裝", st1d: "需要 Python 3.10–3.14 和 Google Chrome。把上面那段話交給 AI 助手，或自己執行：",
    st1n: "Windows 也可以用儲存庫裡的 <code>install.ps1</code>；macOS 和 Linux 尚未驗收。",
    st2: "檢查環境", st2d: "查看本機相依套件和實際可用的發布路徑。這兩個指令都不會登入或發布。",
    st3: "說一句，直接發布", st3d: "告訴 AI 助手影片檔案、要發的平台和公開範圍，它就逐一到各平台提交並記錄回執。第一次登入各平台時，掃碼和驗證碼由你完成；平台可能把 CrossCast 的專用瀏覽器當成新裝置，要求你驗證身分，也由你在官方頁面完成。之後 CLI 在背景沿用登入。",
    copyInstallA: "複製安裝指令", copyDoctorA: "複製 vpub doctor", copyCapsA: "複製 vpub capabilities",
    manualSum: "想自己動手？看手動流程",
    manualNote: "<code>--dry-run</code> 不會上傳。<code>response.json</code> 由 AI 助手依 <code>request.json</code> 裡的範本撰寫。<code>vpub init</code> 預設宣告為 AI 生成內容，YouTube 預設私人，Instagram 和 X 跟隨帳號設定；一般影片加 <code>--ai-generated no</code>，要改公開範圍就在 <code>vpub init</code> 之後馬上改 <code>publish.json</code> 裡的 <code>visibility</code>。 首次在每個平台發布或啟用「每次先給我看」時，上面的發布指令會停下要求審閱。展示目前方案並等使用者明確同意後，才用它的目前確認碼執行 <code>vpub publish ready.json --confirm CODE --person-approved</code>；<code>go</code> 也需要這兩個獨立參數。參數只是呼叫者聲明，軟體不能證明使用者同意。",
    faqTitle: "常見問題",
    faqSub: "沒找到答案？可以到 <a href=\"" + REPO + "/issues\" rel=\"noopener\">GitHub</a> 提問，提問前請先移除帳號和私密資訊。",
    q0: "發布前還要我再確認嗎？", a0: "目前本機資料目錄裡，每個平台首次正式發布前，助手都要展示目前方案並停下來，等你說「發」。這按平台判斷，不追蹤帳號或頻道是否首次使用。該平台後續依你的發布指令執行；儲存了「每次先給我看」或臨時要求預覽時，每次都要等你同意。需要審閱時，目前的 <code>--confirm CODE</code> 必須與獨立的 <code>--person-approved</code> 一起使用；確認碼本身不代表同意，參數也只是呼叫者的聲明，軟體無法證明你真的說過「可以」。內容、帳號、平台、受眾或範圍改變，仍需確認。偏好不等於長期發布授權。",
    q1: "需要單獨的模型 API Key 嗎？", a1: "不需要。CrossCast 不內建模型，標題和簡介由你正在用的 AI 助手來寫。只有付費的 YouTube API 路徑才需要自行設定 Google OAuth 桌面用戶端；免費路徑由背景 Chrome 在 YouTube Studio 上發布。",
    q2: "它會幫我登入帳號嗎？", a2: "登入在官方頁面完成，掃碼、密碼和驗證碼都由你本人操作。平台可能把 CrossCast 的專用瀏覽器當成新裝置，要求驗證或找回帳號，同樣由你本人在官方頁面處理。不要在對話、日誌或儲存庫裡提供 Cookie、密碼、驗證碼或權杖。這個頁面也不收集任何資訊。",
    q3: "只在終端機裡執行 vpub，能直接發出去嗎？", a3: "能。首次登入之後，CLI 自己在背景完成上傳，不需要 AI 助手一步步操作瀏覽器。登入過期或平台要求驗證時，需要你回到官方頁面處理。",
    q4: "進了佇列或有了回執，就算發出去了嗎？", a4: "不算。排隊只說明任務已記錄；本機回執記錄每一步的證據，但它本身不代替平台證據。是否發布成功，以平台頁面讀回或平台 API 為準。",
    q5: "會改動我的原影片和封面嗎？", a5: "不會。調整後的封面另存為新檔案，原圖保持不變；發 Instagram 時直接使用原影片，不轉檔。",
    q6: "會不會發到我沒選的平台？", a6: "不會因為某個平台已登入就把它加進來。平台、帳號和公開範圍由你選定；內容、帳號、平台、受眾或任務範圍有變化時，會重新請你確認。",
    q7: "要花錢嗎？", a7: "CrossCast 核心開源免費（MIT），發布和日常營運都在裡面。付費附加元件目前還沒有開賣，清單見上面的<a href=\"#plans\">免費與付費</a>，清單以外的功能都不需要它。你的 AI 助手訂閱和 X 的訂閱（發布要用 Media Studio）可能另有費用或限制，本專案不承諾免費或無限使用。",
    q8: "支援留言、私訊和數據嗎？TikTok 呢？", a8: "支援讀留言並回覆、按讚和收藏、查看 @ 提及，在抖音、Instagram、X 上讀發私訊（B站還沒實測），查看作品數據和巡檢摘要；完整營運報告屬於付費附加元件。各平台能做的不一樣，見上面的<a href=\"#can\">能力表</a>。每次只處理你授權的那一則，不會批次傳送。TikTok 目前暫停支援。",
    q9: "現在能放心用於正式發布嗎？", a9: "目前是 0.16.0 alpha。這一版已在 YouTube、抖音、Instagram 和 X 的真實帳號上公開發布過，B站目前只有直接用舊版 bili-cli 的上傳。登入會過期，平台也可能臨時要求驗證，長時間無人值守還沒驗收。第一次用時，可以先用預演（<code>--dry-run</code>）看一遍方案，再正式發布。",
    endTitle: "核心開源免費，歡迎一起完善",
    endSub: "MIT 授權。遇到問題或想支援新平台，歡迎到 GitHub 提 Issue；提交前請先移除帳號和私密資訊。",
    ctaGh: "在 GitHub 查看", ctaIssue: "回報問題",
    footIndie: "PublishOps CrossCast 是獨立整合工具，並非任何影片平台的官方產品。",
    footMeta: "MIT 授權，目前版本 0.16.0 alpha。",
    footNav: "頁尾連結", footDocs: "文件", footTop: "回到頂部",
    navCan: "能做什麼",
    navPlans: "免費與付費",
    canTitle: "能做什麼、試到哪一步，都寫在這裡",
    canSub: "這張表只算免費版。有些真實記錄來自更早的版本，也都是作者自己的帳號，不代表在你的帳號、地區和平台狀態下一定成功。",
    evOk: "在真實帳號上跑通過",
    evBuilt: "做好了，還沒在真實帳號上確認",
    evNone: "平台沒有，或免費版不提供",
    mxCap: "能力",
    mxGroupPub: "發布時",
    mxGroupAfter: "發布之後",
    mxR1: "公開發布並讀回",
    mxR1d: "標記以 CrossCast 原生讀回核驗為準",
    mxR2: "排程發布",
    mxR2d: "還沒有一則真正走到發布時間",
    mxR3: "存草稿或停在預覽",
    mxR3d: "只上傳，不發布",
    mxR4: "上傳時加入合集或播放清單",
    mxR5: "上傳時帶章節",
    mxR6: "修改已發布的作品",
    mxR6d: "標題、簡介、封面或章節，依平台不同",
    mxDelete: "刪除一條已發布作品",
    mxDeleteD: "已核驗的 CrossCast 回執；每條作品單獨批准",
    evUnsupported: "尚不支援",
    deleteFlow: "YouTube、抖音、Instagram、X 四條瀏覽器刪除路線已實作但尚未實測；B站不支援。逐條批准、命令和驗證限制見<a href=\"https://github.com/whitecarp/publishops-crosscast/blob/main/docs/GUIDE_ZH_TW.md#刪除一條已發布作品\" rel=\"noopener\">指南</a>。",
    deleteLimits: "無法核驗精確ID的抖音私密作品會拒絕；X、Instagram 的非空個人頁面不能證明清單完整，即使作品頁面顯示已消失，也會保持待確認。",
    mxR7: "認領以前發的作品",
    mxR7d: "不是 CrossCast 發的，也能接著管",
    mxR8: "作品列表和數據",
    mxR8d: "也能巡檢多個平台的新留言和數據變化",
    mxR9: "讀留言並回覆",
    mxR9d: "用留言者的語言，一次一則",
    mxR10: "管理單則留言",
    mxR10d: "置頂、隱藏、刪除或移除，依平台不同",
    mxR11: "@ 提及",
    mxR12: "單則私訊",
    mxR12d: "一次一則，不群發",
    mxR13: "按讚和收藏",
    mxR13d: "可以撤銷",
    mxR14: "管理合集和播放清單",
    mxR14d: "調整順序屬於付費附加元件",
    mxR15: "互動組件",
    mxR15d: "關聯影片、關注、投票和評分",
    canNote1: "實心表示這一列的主要動作至少跑通過一次，不是每個細項都試過。比如 YouTube 的單則留言管理，確認過的是置頂和取消置頂；私訊確認的是傳送方看得到，不代表對方已收到。",
    canSafeTitle: "預設開著的安全限制",
    safe1a: "回覆、私訊和按讚有上限",
    safe1b: "：每日上限，不對同一個人重複相同的話，標註 AI。",
    safe2a: "回覆和私訊不傳金鑰和未經同意的連結",
    safe2b: "：標題和簡介也會檢查，像密碼或金鑰的內容會被拒絕，連結只會提醒你看一眼。",
    safe3a: "可疑的訊息留給你",
    safe3b: "：試圖誘導助手的訊息，或需要真人處理的訊息，要有人批准才能回覆。被騙的助手仍可能自己批准，關鍵字檢查也不是絕對保證。",
    safe4a: "隨時能查還有什麼沒做完",
    safe4b: "：<code>vpub status</code> 只讀本機記錄，列出沒做完的事和下一步的確切指令。",
    platNoteBili: "B站：目前只有作者直接用舊版 bili-cli 的上傳，透過 CrossCast 發布和排程都還沒試過，<code>vpub capabilities</code> 裡仍標為未實測。合集和草稿已在真實帳號上確認過。",
    platNoteIg: "Instagram：影片公開發出過，但 CrossCast 自己的讀回記錄還沒核驗。封面只能在手機上改，也不能排程發布。",
    plansTitle: "發布和日常營運都免費，付費附加元件還沒開賣",
    plansSub: "免費版以 MIT 開源。附加元件只多出下面列出的幾項，現在還買不到，其他功能也都不需要它。",
    planFree: "免費版",
    planFreeTag: "現在就能用",
    planFreeSub: "不需要模型 API Key，也不需要平台的官方 API。",
    pf1: "<strong>發布</strong>：五個平台，讀回核驗，排程、草稿、章節，上傳時加入合集或播放清單，AI 聲明",
    pf2: "<strong>文案和封面</strong>：AI 助手為每個平台寫文案，vpub 檢查並調整封面",
    pf3: "<strong>發布之後</strong>：修改作品，認領舊作品，作品列表和數據，巡檢和簡短摘要",
    pf4: "<strong>互動</strong>：留言和回覆，單則留言管理，@ 提及，單則私訊，按讚和收藏",
    pf5: "<strong>合集和播放清單</strong>：建立、修改、增刪作品（調整順序除外）",
    pf6: "<strong>B站互動組件</strong>：關聯影片、關注、投票和評分",
    pf7: "<strong>安全和恢復</strong>：上面這些安全限制，<code>vpub status</code> 和 <code>vpub resume</code>",
    planPaid: "付費附加元件",
    planPaidTag: "還沒開賣",
    planPaidSub: "單獨安裝，給需要官方介面和完整讀取的人。",
    pp1: "YouTube、X 和 Instagram 的官方 API 路徑",
    pp2: "營運報告（vpub report）",
    pp3: "完整讀取（vpub read-all）",
    pp4: "郵件提醒（只支援 Gmail，預設關閉）",
    pp5: "播放清單調整順序",
    pp6: "YouTube 待審核留言佇列",
    plansNote: "另外，你的 AI 助手訂閱和 X 的訂閱（發布要用 Media Studio）由各自的公司收費，本專案不承諾免費或無限使用。",
    footReleases: "版本記錄",
    canNote2: "半實心的功能已經寫好，也用假平台測過，但真實頁面上可能還有沒發現的問題。每一格的依據見<a href=\"" + REPO + "/blob/main/docs/VALIDATION.md\" rel=\"noopener\">驗證記錄</a>，更細的範圍見<a href=\"" + REPO + "/blob/main/docs/FEATURES.md\" rel=\"noopener\">功能範圍</a>。",
    _title: "PublishOps CrossCast｜一句話，發往你選的所有平台",
    _desc: "PublishOps CrossCast（vpub）讓你正在用的 AI 助手，為 B站、YouTube、抖音、Instagram 和 X 寫好標題、簡介並調整封面，首次登入後在背景逐一到各平台提交、記錄回執；發完還能接著改稿、回留言、看數據。0.16.0 alpha。",
    _copied: "已複製", _copyFail: "複製失敗。文字已選取，請按 Ctrl+C（Mac 上按 ⌘C）手動複製。", _copyErr: "複製失敗"
  };

  var EN = {
    beforeTitle: "Before you start",
    beforeAgent: "Use an assistant that can run local commands, such as Codex or Claude Code. A chat-only window is not enough.",
    beforeNetwork: "In mainland China, you’ll need to find your own way to access YouTube, Instagram or X.",
    beforeX: "X requires both a subscription and actual Media Studio access. Paying alone does not establish access.",
    beginnerGuide: "<a href=\"https://github.com/whitecarp/publishops-crosscast/blob/main/docs/GUIDE_EN.md#a-longer-prompt-for-beginners\" rel=\"noopener\">New to computers? Use this longer message</a>, with a first-release preview and safe feedback steps.",
    skip: "Skip to content", navLabel: "Page sections",
    navHow: "How it works", navPlatforms: "Platforms", navStart: "Get started", navFaq: "FAQ",
    h1a: "One message. ", h1b: "Every platform you choose.",
    lead: "Hand your video to the AI assistant you already use, such as Codex or Claude. It writes a title and description for each of Bilibili, YouTube, Douyin, Instagram and X. vpub fits the covers and, after the first sign-in, submits to each platform in turn in the background. A post only counts once CrossCast has found it on the platform. After that, your assistant can handle edits, comment replies and stats too.",
    promptLabel: "Tell your AI assistant", copy: "Copy", copyPromptA: "Copy this prompt",
    installPrompt: "Install PublishOps CrossCast from https://github.com/whitecarp/publishops-crosscast, run vpub doctor and read the skill file it names. Then publish final.mp4 publicly to [my chosen platforms], with cover.jpg and copy written for each platform. Before the first publication on each platform, show me the exact current plan and wait for my yes; a confirmation code is not consent. The video is in English, (was / was not) made with AI, and is about: (one sentence)",
    promptHint: "Use your own file names and description, and change the platforms or visibility as you like.",
    ctaStart: "Get started in 3 steps", ctaDocs: "Read the docs",
    alphaNote: "0.16.0 alpha: this version has posted publicly on real YouTube, Douyin, Instagram and X accounts. Bilibili so far only has uploads made directly with an older bili-cli. Sign-ins expire, platforms may ask for verification at any time, and long unattended runs have not been validated.",
    mfTitle: "Release plan", mfTag: "Example", mfSrcVideo: "Source", mfSrcScript: "Script", mfSrcScriptV: "Simplified Chinese", mfSrcVis: "Visibility", mfSrcVisV: "All public",
    mfColPlatform: "Platform", mfColCover: "Cover", mfColCopy: "Copy", mfColState: "Status",
    srCover: "Cover: ", srCopy: "Copy language: ", srState: "Status: ",
    langZh: "ZH", langBoth: "ZH + EN",
    pDy: "Douyin",
    mfFoot: "Submitted platform by platform. Marked Verified only after the work is read back.",
    howTitle: "Say it once, your AI assistant handles it",
    howSub: "Your AI assistant writes the words, with no built-in model and no separate model API key. vpub handles the plan, cover fitting, dry runs and receipts.",
    pbSay: "You can say", pbDo: "What it does", pbNot: "What it won't do", pbSaySr: "You can say: ",
    s1q: "Post this to Bilibili and YouTube, keep the titles casual.",
    s1a: "Writes a title, description and tags for each platform, submits to both directly, and keeps a receipt for every step.",
    s1n: "Add a platform just because you are signed in to it, or quietly change the visibility.",
    s2q: "Fit the cover to each platform, and leave the original alone.",
    s2a: "Saves a new cover for each platform canvas, keeping the whole image. Douyin and Instagram also need a portrait cover: your assistant draws one if it can, or builds one from parts of your image with vpub covers portrait. Platform previews may still crop it, so take a look before submitting.",
    s2n: "Crop, stretch or overwrite your original image.",
    s3q: "Post this Chinese video to YouTube too, so English viewers can follow it.",
    s3a: "Copy follows the video's language: Simplified Chinese for Bilibili and Douyin, English for YouTube, Instagram and X. When the video is in another language, the original text comes first, followed by a translation, so on YouTube this one reads Chinese first, then English.",
    s3n: "Translate the video's voice-over or subtitles.",
    s4q: "Did my last video actually go out?",
    s4a: "Reads the local receipts and tells you how far each platform got, then checks the platform itself where a readback is available.",
    s4n: "Call a queued task, a sign-in link or a local receipt “published”.",
    statesTitle: "Every status is called what it is",
    statesSub: "Publishing directly still means saying exactly how far it got. Your AI assistant reports what the evidence shows and never describes an earlier step as a later one.",
    stQueue: "Queued", stQueueD: "The task is recorded and nothing has been uploaded yet.",
    stRun: "Publishing", stRunD: "The CLI is uploading and submitting in the background.",
    stSent: "Submitted, unverified", stSentD: "The platform accepted the submission. It may still be in review or processing, and the work has not been read back yet.",
    stOk: "Verified", stOkD: "The work was read back from the platform page or API. Review and public visibility still follow what the platform shows.",
    rule1a: "A verification request, risk check or rate limit", rule1b: ": every platform stops. Once you've dealt with it on the official page and know why it happened, <code>vpub stop --clear</code> lifts the stop.",
    rule2a: "Uncertain result", rule2b: ": it checks the original task only and never uploads again.",
    platTitle: "Five platforms, five different paths",
    platSub: "Test records are past real publications; they do not mean publishing will succeed for your account, region and platform status. Run <code>vpub capabilities</code> to see which paths actually work on your machine. This table shows the free routes; official API routes for YouTube, X and Instagram are in the paid add-on.",
    thPlatform: "Platform", thLogin: "Sign-in", thRoute: "Publishing route", thTested: "Tested", thNote: "Notes",
    pBiliLogin: "Bilibili app QR code (bili-cli)", pBiliRoute: "The included bili-cli",
    pYtLogin: "Sign in once in the dedicated Chrome", pYtRoute: "Background Chrome on YouTube Studio",
    pChrome: "Dedicated official Chrome", pBgBrowser: "CLI background browser",
    pIgRoute: "CLI background browser; cover changed on your phone", pXRoute: "Media Studio in the background (subscription required)",
    testedPub: "Published", testedYt: "Free route", paused: "Paused",
    pTtNote: "The CLI refuses TikTok sign-in and posting requests.",
    platNote1: "YouTube: two videos went public and were read back on November 2, 2026. Both first left only a draft and went out after it was reopened and Publish was pressed again, and the code that now handles this hasn't run on a real channel. Adding to a playlist at upload and scheduling have only been confirmed in draft uploads.",
    platNote2: "X: the account must be subscribed and able to open Media Studio. Accounts without it are limited to 140-second, 512 MB videos, which this tool does not support. X currently serves two versions of its website, and CrossCast handles both. All real-account records are on the earlier one; the rebuilt one hasn't been tried on a signed-in account. X edit-version behavior is also untested live; an unverified edit may have saved. CrossCast refuses retries even with --person-approved. Use --verify-only, or have the user inspect and fix it manually on X.",
    platNote3: "Douyin: long-term sign-in is still being validated. An upload can't pick up where it broke off, but vpub resume can finish from the draft it left. The cover window has stalled in testing. Posting without a cover and setting it on your phone requires your explicit choice. The only cover the tool leaves out by itself is the 4:3 one, and only while the 3:4 cover still goes on, for example on a portrait video. If something covers a cover button, it waits up to 90 seconds, and otherwise stops before anything is published. On Windows, uploads now use a real Chrome window on a hidden desktop, which hasn't been tried yet. Title and description saves have been tried, but success remains unverified.",
    platNote4: "Not available yet: TikTok, YouTube cards and clickable end screens, automatic or bulk messages and likes, and full history export.",
    startTitle: "Three steps from install to your first release",
    st1: "Install", st1d: "Requires Python 3.10–3.14 and Google Chrome. Hand the prompt above to your AI assistant, or run:",
    st1n: "On Windows you can also run <code>install.ps1</code> from the repository. macOS and Linux have not been verified yet.",
    st2: "Check your setup", st2d: "See your local dependencies and the posting paths that actually work. Neither command signs in or posts.",
    st3: "Say it, and it publishes", st3d: "Tell your AI assistant the video file, the platforms and the visibility, and it submits to each platform in turn and keeps receipts. The first time you sign in to each platform, you handle QR codes and verification codes. A platform may treat CrossCast's own browser as a new device and ask you to verify who you are, which you also do on the official page. After that, the CLI reuses the sign-in in the background.",
    copyInstallA: "Copy install command", copyDoctorA: "Copy vpub doctor", copyCapsA: "Copy vpub capabilities",
    manualSum: "Prefer to do it yourself? See the manual flow",
    manualNote: "<code>--dry-run</code> does not upload. Your AI assistant writes <code>response.json</code> from the template in <code>request.json</code>. <code>vpub init</code> declares the video as AI-generated, keeps YouTube private and follows the account's setting on Instagram and X. For an ordinary video add <code>--ai-generated no</code>, and to change visibility, edit <code>visibility</code> in <code>publish.json</code> right after <code>vpub init</code>. For the first publication on each platform or saved show-first, the publish command above stops for review. Show the exact current plan and wait for the user's yes before running <code>vpub publish ready.json --confirm CODE --person-approved</code> with its current code; <code>go</code> needs those two separate arguments too. This is caller attestation, not software proof of consent.",
    faqTitle: "Questions",
    faqSub: "Not covered here? <a href=\"" + REPO + "/issues\" rel=\"noopener\">Ask on GitHub</a>, and remove account details and private information first.",
    q0: "Do I have to approve each release?", a0: "Before the first publication on each platform in this local data folder, the assistant shows the exact current plan and stops until you say publish. This counts platforms, not first use of each account or channel. Later requests on that platform follow your instructions; saved show-first or a requested preview means waiting for your approval each time. When review is required, the current <code>--confirm CODE</code> must be paired with a separate <code>--person-approved</code>. The code alone is not consent, and the flag is only the caller's attestation: software cannot prove you said yes. Changes to content, accounts, platforms, audience or scope still need confirmation. Preferences are not standing authorization to publish.",
    q1: "Do I need a separate model API key?", a1: "No. CrossCast has no built-in model; titles and descriptions come from the AI assistant you already use. You only set up a Google desktop OAuth client on the paid YouTube API route; on the free route a background Chrome publishes through YouTube Studio.",
    q2: "Will it sign in to my accounts?", a2: "Sign-in happens on the official sites, and you handle QR codes, passwords and verification codes yourself. A platform may treat CrossCast's own browser as a new device and ask for a check or account recovery; you handle that yourself on the official page too. Never share cookies, passwords, codes or tokens in chats, logs or the repository. This page collects nothing.",
    q3: "Can vpub post directly from a terminal?", a3: "Yes. After the first sign-in, the CLI does the uploading in the background, with no AI assistant stepping through a browser. When a sign-in expires or a platform asks for verification, you need to handle it on the official page.",
    q4: "If it is queued or has a receipt, is it published?", a4: "No. Queued only means the task is recorded. Local receipts record the evidence for each step but are not proof from the platform. Whether it was published is decided by reading back the platform page or the platform API.",
    q5: "Will it change my original video or cover?", a5: "No. Fitted covers are saved as new files and your original image stays as it is. Instagram uploads use the original video without transcoding.",
    q6: "Could it post to a platform I didn't pick?", a6: "It will not add a platform just because you are signed in to it. You choose platforms, accounts and visibility; if the content, account, platform, audience or scope changes, it asks you again.",
    q7: "Does it cost anything?", a7: "The CrossCast core is free and open source (MIT), and publishing and daily operations are all in it. The paid add-on isn't on sale yet. Its list is under <a href=\"#plans\">Free &amp; paid</a>, and nothing outside that list needs it. Your AI assistant subscription and an X subscription (publishing needs Media Studio) may have their own costs or limits; this project does not promise free or unlimited use.",
    q8: "Does it handle comments, messages and stats? What about TikTok?", a8: "It can read and reply to comments, like and save works, show @ mentions, read and send direct messages on Douyin, Instagram and X (Bilibili hasn't been tested live), and show work stats with a short scan summary. Full operations reports are in the paid add-on. What's available differs by platform: see the <a href=\"#can\">capability table</a>. Each action covers only the single item you authorized, and nothing is sent in bulk. TikTok support is paused.",
    q9: "Is it ready for real releases?", a9: "This is 0.16.0 alpha. This version has posted publicly on real YouTube, Douyin, Instagram and X accounts, and Bilibili so far only has uploads made directly with an older bili-cli. Sign-ins expire, platforms may ask for verification at any time, and long unattended runs have not been validated. The first time, you can run a dry run (<code>--dry-run</code>) to review the plan before publishing for real.",
    endTitle: "Free, open-source core. Help make it better.",
    endSub: "MIT licensed. Found a problem or want another platform? Open an issue on GitHub, and remove account details and private information first.",
    ctaGh: "View on GitHub", ctaIssue: "Report a problem",
    footIndie: "PublishOps CrossCast is an independent integration tool, not an official product of any video platform.",
    footMeta: "MIT licensed. Current version 0.16.0 alpha.",
    footNav: "Footer links", footDocs: "Docs", footTop: "Back to top",
    navCan: "What it does",
    navPlans: "Free & paid",
    canTitle: "What it does, and how far each part has been tried",
    canSub: "This table covers the free version only. Some real-account records come from earlier versions, and all come from the author's own accounts, so they don't mean it will work the same for your account, region and platform status.",
    evOk: "Has run on a real account",
    evBuilt: "Built, not yet confirmed on a real account",
    evNone: "Not on this platform, or not in the free version",
    mxCap: "Capability",
    mxGroupPub: "When posting",
    mxGroupAfter: "After posting",
    mxR1: "Public post, read back",
    mxR1d: "Marks reflect native CrossCast read-back verification",
    mxR2: "Scheduled post",
    mxR2d: "None has reached its publish time yet",
    mxR3: "Draft or preview",
    mxR3d: "Uploads without publishing",
    mxR4: "Add to a collection or playlist at upload",
    mxR5: "Chapters at upload",
    mxR6: "Edit a published work",
    mxR6d: "Title, description, cover or chapters, depending on the platform",
    mxDelete: "Delete one published post",
    mxDeleteD: "Verified CrossCast receipt; separate approval for each post",
    evUnsupported: "Unsupported",
    deleteFlow: "Four browser deletion routes (YouTube, Douyin, Instagram and X) are built but untried live; Bilibili is unsupported. See the <a href=\"https://github.com/whitecarp/publishops-crosscast/blob/main/docs/GUIDE_EN.md#delete-one-published-post\" rel=\"noopener\">guide</a> for per-post approval, commands and verification limits.",
    deleteLimits: "Private Douyin works without a verifiable exact ID are refused; nonempty X and Instagram profiles cannot prove a complete list, so deletion stays pending even if the post page appears gone.",
    mxR7: "Adopt older works",
    mxR7d: "Manage works CrossCast didn't post",
    mxR8: "Works list and stats",
    mxR8d: "Also scans platforms for new comments and changes in stats",
    mxR9: "Read and reply to comments",
    mxR9d: "In the commenter's language, one at a time",
    mxR10: "Manage a single comment",
    mxR10d: "Pin, hide, delete or remove, depending on the platform",
    mxR11: "@ mentions",
    mxR12: "Single direct messages",
    mxR12d: "One at a time, never in bulk",
    mxR13: "Likes and saves",
    mxR13d: "Can be undone",
    mxR14: "Manage collections and playlists",
    mxR14d: "Reordering is in the paid add-on",
    mxR15: "Interactive widgets",
    mxR15d: "Related video, follow, poll and rating",
    canNote1: "A full mark means the row's main action has run at least once, not that every detail has been tried. On YouTube, for example, managing a comment has been confirmed for pinning and unpinning. A direct message is confirmed as visible to the sender, not as delivered.",
    canSafeTitle: "Safety limits, on by default",
    safe1a: "Caps on replies, messages and likes",
    safe1b: ": a daily limit, no repeating the same words to one person, and an AI label.",
    safe2a: "No secrets or unapproved links in replies and messages",
    safe2b: ": titles and descriptions are checked too. Text that looks like a password or key is refused, and links only get flagged for you to look at.",
    safe3a: "Suspicious messages are left to you",
    safe3b: ": messages trying to steer the assistant, or that need a person, wait for someone's approval. A tricked assistant could still approve one, and keyword checks aren't a guarantee.",
    safe4a: "See what's unfinished at any time",
    safe4b: ": <code>vpub status</code> reads local records only and lists what's unfinished, with the exact next command.",
    platNoteBili: "Bilibili: the only published uploads so far were made by the author directly with an older bili-cli. Publishing and scheduling through CrossCast haven't been tried, and <code>vpub capabilities</code> still marks it untested. Collections and drafts have been confirmed on a real account.",
    platNoteIg: "Instagram: a video has gone public, but CrossCast's own read-back record is still unverified. Covers can only be changed on a phone, and it can't schedule.",
    plansTitle: "Publishing and daily operations are free. The paid add-on isn't on sale yet.",
    plansSub: "The free version is MIT licensed. The add-on only adds the items listed here. You can't buy it yet, and nothing else needs it.",
    planFree: "Free version",
    planFreeTag: "Available now",
    planFreeSub: "No model API key, and no official platform API.",
    pf1: "<strong>Publishing</strong>: five platforms, read-back, scheduling, drafts, chapters, collections or playlists at upload, and an AI declaration",
    pf2: "<strong>Copy and covers</strong>: your AI assistant writes for each platform, and vpub checks the copy and fits the covers",
    pf3: "<strong>After posting</strong>: edits, adopting older works, works list and stats, scans with a short summary",
    pf4: "<strong>Engagement</strong>: comments and replies, managing a single comment, @ mentions, single direct messages, likes and saves",
    pf5: "<strong>Collections and playlists</strong>: create, change, add and remove works (reordering aside)",
    pf6: "<strong>Bilibili widgets</strong>: related video, follow, poll and rating",
    pf7: "<strong>Safety and recovery</strong>: the safety limits above, <code>vpub status</code> and <code>vpub resume</code>",
    planPaid: "Paid add-on",
    planPaidTag: "Not on sale yet",
    planPaidSub: "Installed separately, for people who need official APIs and complete reads.",
    pp1: "Official API routes for YouTube, X and Instagram",
    pp2: "Operations report (vpub report)",
    pp3: "Complete reads (vpub read-all)",
    pp4: "Email reminders (Gmail only, off by default)",
    pp5: "Playlist reordering",
    pp6: "YouTube's held-for-review comment queue",
    plansNote: "Separately, your AI assistant subscription and an X subscription (publishing needs Media Studio) are charged by those companies, and this project doesn't promise free or unlimited use.",
    footReleases: "Releases",
    canNote2: "Half-marked features are built and tested against fake platforms, but real pages may still hold problems nobody has found. The evidence for each mark is in the <a href=\"" + REPO + "/blob/main/docs/VALIDATION_EN.md\" rel=\"noopener\">validation record</a>, and the full scope is in <a href=\"" + REPO + "/blob/main/docs/FEATURES_EN.md\" rel=\"noopener\">Features</a>.",
    _title: "PublishOps CrossCast | One message. Every platform you choose.",
    _desc: "PublishOps CrossCast (vpub) lets the AI assistant you already use write titles and descriptions and fit covers for Bilibili, YouTube, Douyin, Instagram and X, then submit to each platform in turn in the background after the first sign-in and keep receipts. Edits, comment replies and stats can go through it afterwards. 0.16.0 alpha.",
    _copied: "Copied", _copyFail: "Copy failed. The text is selected; press Ctrl+C (⌘C on Mac) to copy it.", _copyErr: "Copy failed"
  };

  var DICTS = { "zh-CN": ZH, "zh-TW": TW, en: EN };
  var DOCS = { "zh-CN": REPO + "#readme", "zh-TW": REPO + "/blob/main/README_ZH_TW.md", en: REPO + "/blob/main/README_EN.md" };

  var root = document.documentElement;
  var textNodes = document.querySelectorAll("[data-i18n]");
  var htmlNodes = document.querySelectorAll("[data-i18n-html]");
  var ariaNodes = document.querySelectorAll("[data-i18n-aria]");
  var langBtns = document.querySelectorAll("button[data-lang]");
  var docLinks = document.querySelectorAll("a[data-doc]");
  var descMeta = document.querySelector('meta[name="description"]');
  var toast = document.getElementById("toast");
  var lang = "zh-CN";

  textNodes.forEach(function (n) { ZH[n.getAttribute("data-i18n")] = n.textContent; });
  htmlNodes.forEach(function (n) { ZH[n.getAttribute("data-i18n-html")] = n.innerHTML; });
  ariaNodes.forEach(function (n) { ZH[n.getAttribute("data-i18n-aria")] = n.getAttribute("aria-label"); });
  ZH._title = document.title;
  ZH._desc = descMeta ? descMeta.content : "";

  function norm(v) {
    v = String(v || "").toLowerCase();
    if (v === "en" || v.indexOf("en-") === 0) return "en";
    if (/^(zh-tw|zh-hk|zh-mo|zh-hant|tw|hant)$/.test(v)) return "zh-TW";
    if (/^(zh|zh-cn|zh-sg|zh-hans|cn)$/.test(v)) return "zh-CN";
    return null;
  }

  function t(key) {
    var d = DICTS[lang];
    return d && d[key] != null ? d[key] : ZH[key];
  }

  function syncUrl() {
    try {
      var url = new URL(location.href);
      if (lang === "zh-CN") url.searchParams.delete("lang");
      else url.searchParams.set("lang", lang);
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    } catch (e) {}
  }

  function apply(next, save) {
    lang = norm(next) || "zh-CN";
    textNodes.forEach(function (n) {
      if (n.dataset.state) return; // a copy button showing its result keeps it until the timer resets it
      n.textContent = t(n.getAttribute("data-i18n"));
    });
    // Only strings from the dictionaries above are written as HTML; nothing user-supplied reaches innerHTML.
    htmlNodes.forEach(function (n) { n.innerHTML = t(n.getAttribute("data-i18n-html")); });
    ariaNodes.forEach(function (n) { n.setAttribute("aria-label", t(n.getAttribute("data-i18n-aria"))); });
    root.lang = lang;
    document.title = t("_title");
    if (descMeta) descMeta.content = t("_desc");
    docLinks.forEach(function (a) { a.href = DOCS[lang]; });
    langBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === lang)); });
    if (save) {
      try { localStorage.setItem(KEY, lang); } catch (e) {}
      syncUrl();
    }
    root.classList.remove("i18n-wait");
  }

  langBtns.forEach(function (b) {
    b.addEventListener("click", function () { apply(b.getAttribute("data-lang"), true); });
  });

  var saved = null;
  try { saved = norm(localStorage.getItem(KEY)); } catch (e) {}
  var fromUrl = null;
  try { fromUrl = norm(new URLSearchParams(location.search).get("lang")); } catch (e) {}
  apply(fromUrl || saved || "zh-CN", false);
  if (fromUrl) { try { localStorage.setItem(KEY, fromUrl); } catch (e) {} }

  var toastTimer;
  function say(msg) {
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 2400);
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
    return ok ? Promise.resolve() : Promise.reject(new Error("copy"));
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () { return fallbackCopy(text); });
    }
    return fallbackCopy(text);
  }

  function selectEl(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function flag(btn, ok) {
    btn.dataset.state = ok ? "ok" : "err";
    btn.classList.remove("is-ok", "is-err");
    btn.classList.add(ok ? "is-ok" : "is-err");
    btn.textContent = ok ? t("_copied") : t("_copyErr");
    say(ok ? t("_copied") : t("_copyFail"));
    setTimeout(function () {
      delete btn.dataset.state;
      btn.classList.remove("is-ok", "is-err");
      btn.textContent = t("copy");
    }, 2000);
  }

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      if (!src) return;
      copyText(src.textContent.trim()).then(function () {
        flag(btn, true);
      }, function () {
        selectEl(src);
        flag(btn, false);
      });
    });
  });
})();
