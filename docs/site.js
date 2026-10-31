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
    skip: "跳到正文", navLabel: "頁面導覽",
    navHow: "怎麼用", navStates: "狀態", navPlatforms: "平台", navStart: "上手", navFaq: "常見問題",
    h1a: "一句話，", h1b: "發往你選的所有平台。",
    lead: "你正在用的 AI 助手（如 Codex、Claude）為 B站、YouTube、抖音、Instagram 和 X 各寫一版標題、簡介和標籤，vpub 產生並檢查發布方案、依平台調整封面，然後逐一到各平台直接提交，每一步都記錄回執。不內建模型，也不需要另外設定模型 API Key。",
    promptLabel: "對你的 AI 助手說", copy: "複製", copyPromptA: "複製這段話",
    installPrompt: "幫我從 https://github.com/whitecarp/publishops-crosscast 安裝 PublishOps CrossCast，閱讀 skills/publishops-crosscast/SKILL.md 並執行 vpub doctor。然後把 final.mp4 公開發布到 B站、YouTube、抖音、Instagram 和 X，標題和簡介依平台來寫。",
    promptHint: "把 final.mp4 換成你的影片檔案；平台和公開範圍可以依需要修改。",
    ctaStart: "三步上手", ctaDocs: "閱讀文件",
    alphaNote: "0.3.0 alpha：網頁提交需要能操作瀏覽器的 AI 助手，掃碼和驗證碼由你完成；五個平台的全自動發布尚未驗收。",
    mfTitle: "發布單", mfTag: "示意", mfSrcVideo: "來源影片", mfSrcScript: "腳本", mfSrcScriptV: "簡體中文", mfSrcVis: "公開範圍", mfSrcVisV: "全部公開",
    mfColPlatform: "平台", mfColCover: "封面", mfColCopy: "文案", mfColState: "狀態",
    srCover: "封面：", srCopy: "文案語言：", srState: "狀態：",
    langZh: "簡中", langEn: "英文",
    coverVideo: "沿用影片", pDy: "抖音",
    mfFoot: "逐一到各平台直接提交；讀回到作品，才標為「已核驗」。",
    howTitle: "你說一句，AI 助手去辦",
    howSub: "文字由你正在用的 AI 助手來寫；vpub 負責方案、封面調整、預演和回執。",
    pbSay: "你可以這樣說", pbDo: "它會做什麼", pbNot: "它不會", pbSaySr: "你可以這樣說：",
    s1q: "把這支影片發 B站和 YouTube，標題口語一點。",
    s1a: "依平台各寫一版標題、簡介和標籤，直接提交到這兩個平台，並記錄每一步的回執。",
    s1n: "因為某個平台已登入就順手加上，或擅自改動公開範圍。",
    s2q: "封面依各平台尺寸調整一下，原圖別動。",
    s2a: "依平台畫布另存一份封面，保留完整畫面。平台預覽仍可能裁切，提交前請看一眼。",
    s2n: "裁切、拉伸或覆蓋你的原圖。",
    s3q: "B站和抖音用中文，YouTube 用英文。",
    s3a: "依平台語言各寫一版：B站、抖音用簡體中文，YouTube、Instagram、X 用英文；要中英雙語時說一聲。",
    s3n: "翻譯影片裡的配音或字幕。",
    s4q: "上次那支到底發出去沒有？",
    s4a: "查本機回執，依平台告訴你到了哪一步；能讀回的，再到平台上核對。",
    s4n: "把排隊、登入連結或本機回執說成「已發布」。",
    statesTitle: "每個狀態，都叫它本來的名字",
    statesSub: "直接發布，也要說清楚到了哪一步。AI 助手依實際證據回報，不會把前一步說成後一步。",
    stQueue: "排隊中", stQueueD: "任務已記錄，還沒開始上傳。",
    stRun: "發布中", stRunD: "AI 助手正在官方頁面上傳和填寫，或 CLI 正在透過已設定的後端提交。",
    stSent: "已提交，待核驗", stSentD: "平台已接收提交，可能還在審核或處理中，還沒有讀回到作品。",
    stOk: "已核驗", stOkD: "已從平台頁面或 API 讀回到這支作品。審核和公開狀態以平台顯示為準。",
    rule1a: "遇到掃碼、驗證碼或風控", rule1b: "：停在原處，等你在官方頁面處理。",
    rule2a: "結果不明", rule2b: "：只核對原來的任務，不重複上傳。",
    platTitle: "五個平台，各走各的路",
    platSub: "安裝成功不等於已登入或已有發布權限。本機實際能走哪條路徑，以 <code>vpub capabilities</code> 的結果為準。",
    thPlatform: "平台", thDefault: "預設方式", thOptional: "可選方式", thStatus: "現況", thNote: "說明",
    pBiliDef: "AI 助手操作官方網頁", pBiliOpt: "上游 bili CLI",
    pYtDef: "官方網頁交接", pYtOpt: "YouTube Data API（需自行設定 OAuth）",
    pDyDef: "AI 助手協助官方網頁", pDyOpt: "獨立後端（實驗功能）",
    pWebDraft: "AI 助手操作官方網頁，可只存草稿", pNoApi: "無獨立 API",
    avail: "可用", availWeb: "網頁可用", paused: "暫停支援",
    pTtNote: "CLI 會拒絕 TikTok 的登入和發布請求。",
    platNote1: "網頁方式需要能操作瀏覽器的 AI 助手；沒有這類助手時，只能準備素材和交接說明。",
    platNote2: "排程發布只在 Bilibili CLI（2 小時到 15 天內）和 YouTube API（需設為私密）兩條路徑上可用；網頁方式只支援立即發布。",
    platNote3: "目前沒有通用的留言、回覆、私訊、收件匣或創作者數據指令。",
    startTitle: "三步，從安裝到第一次發布",
    st1: "安裝", st1d: "需要 Python 3.10–3.12。把上面那段話交給 AI 助手，或自己執行：",
    st1n: "Windows 也可以用儲存庫裡的 <code>install.ps1</code>；macOS 和 Linux 尚未驗收。",
    st2: "檢查環境", st2d: "查看本機相依套件和實際可用的發布路徑。這兩個指令都不會登入或發布。",
    st3: "說一句，直接發布", st3d: "告訴 AI 助手影片檔案、要發的平台和公開範圍，它就逐一到各平台提交並記錄回執。第一次登入各平台時，掃碼和驗證碼由你完成。",
    copyInstallA: "複製安裝指令", copyDoctorA: "複製 vpub doctor", copyCapsA: "複製 vpub capabilities",
    manualSum: "想自己動手？看手動流程",
    manualNote: "<code>--dry-run</code> 不會上傳。<code>response.json</code> 由 AI 助手依 <code>request.json</code> 裡的範本撰寫。",
    faqTitle: "常見問題",
    faqSub: "沒找到答案？可以到 <a href=\"" + REPO + "/issues\" rel=\"noopener\">GitHub</a> 提問，提問前請先移除帳號和私密資訊。",
    q0: "發布前還要我再確認嗎？", a0: "不用。你說要發布，就是授權：它依你說的平台、帳號和公開範圍直接提交。只有內容、帳號、平台、受眾或任務範圍變了，它才會再問你。想先看一眼，就說「先別發」，或用 <code>--dry-run</code> 預演。",
    q1: "需要單獨的模型 API Key 嗎？", a1: "不需要。CrossCast 不內建模型，標題和簡介由你正在用的 AI 助手來寫。只有選用 YouTube API 路徑時，才需要自行設定 Google OAuth。",
    q2: "它會幫我登入帳號嗎？", a2: "登入在官方頁面完成，掃碼、密碼和驗證碼都由你本人操作。不要在對話、日誌或儲存庫裡提供 Cookie、密碼、驗證碼或權杖。這個頁面也不收集任何資訊。",
    q3: "只在終端機裡執行 vpub，能直接發出去嗎？", a3: "預設的網頁方式不能：獨立執行的 CLI 無法操作瀏覽器，只能準備方案和交接說明，網頁上的步驟要由能操作瀏覽器的 AI 助手完成。如果你自行設定了 Bilibili CLI 或 YouTube API 並明確授權，這兩條路徑可以由 CLI 直接提交。",
    q4: "進了佇列或有了回執，就算發出去了嗎？", a4: "不算。排隊只說明任務已記錄；本機回執記錄每一步的證據，但它本身不代替平台證據。是否發布成功，以平台頁面讀回或平台 API 為準。",
    q5: "會改動我的原影片和封面嗎？", a5: "不會。調整後的封面另存為新檔案，原圖保持不變；發 Instagram 時直接使用原影片，不轉檔。",
    q6: "會不會發到我沒選的平台？", a6: "不會因為某個平台已登入就把它加進來。平台、帳號和公開範圍由你選定；內容、帳號、平台、受眾或任務範圍有變化時，會重新請你確認。",
    q7: "要花錢嗎？", a7: "CrossCast 本身開源免費（MIT）。你的 AI 助手訂閱、平台功能，以及可選的 YouTube API 可能另有費用或限制，本專案不承諾免費或無限使用。",
    q8: "支援 TikTok、留言管理或數據分析嗎？", a8: "TikTok 目前暫停支援。這個版本也沒有通用的留言、回覆、私訊、收件匣或創作者數據功能。",
    q9: "現在能放心用於正式發布嗎？", a9: "目前是 0.3.0 alpha，五個平台的全自動發布尚未完成驗收。第一次用時，可以先用預演（<code>--dry-run</code>）看一遍方案，再正式發布。",
    endTitle: "開源免費，歡迎一起完善",
    endSub: "MIT 授權。遇到問題或想支援新平台，歡迎到 GitHub 提 Issue；提交前請先移除帳號和私密資訊。",
    ctaGh: "在 GitHub 查看", ctaIssue: "回報問題",
    footIndie: "PublishOps CrossCast 是獨立整合工具，並非任何影片平台的官方產品。",
    footMeta: "MIT 授權，目前版本 0.3.0 alpha。",
    footNav: "頁尾連結", footDocs: "文件", footTop: "回到頂部",
    _title: "PublishOps CrossCast｜一句話，發往你選的所有平台",
    _desc: "PublishOps CrossCast（vpub）讓你正在用的 AI 助手，為 B站、YouTube、抖音、Instagram 和 X 寫好標題、簡介並調整封面，再逐一到各平台直接提交、記錄回執。0.3.0 alpha。",
    _copied: "已複製", _copyFail: "複製失敗。文字已選取，請按 Ctrl+C（Mac 上按 ⌘C）手動複製。", _copyErr: "複製失敗"
  };

  var EN = {
    skip: "Skip to content", navLabel: "Page sections",
    navHow: "How it works", navStates: "Status", navPlatforms: "Platforms", navStart: "Get started", navFaq: "FAQ",
    h1a: "One message. ", h1b: "Every platform you choose.",
    lead: "The AI assistant you already use, such as Codex or Claude, writes a title, description and tags for each of Bilibili, YouTube, Douyin, Instagram and X. vpub builds and checks the release plan, fits your cover to each platform, then submits to each platform in turn and keeps a receipt for every step. No built-in model, and no separate model API key.",
    promptLabel: "Tell your AI assistant", copy: "Copy", copyPromptA: "Copy this prompt",
    installPrompt: "Install PublishOps CrossCast from https://github.com/whitecarp/publishops-crosscast, read skills/publishops-crosscast/SKILL.md and run vpub doctor. Then publish final.mp4 publicly to Bilibili, YouTube, Douyin, Instagram and X, with titles and descriptions written for each platform.",
    promptHint: "Replace final.mp4 with your video file, and change the platforms or visibility as you like.",
    ctaStart: "Get started in 3 steps", ctaDocs: "Read the docs",
    alphaNote: "0.3.0 alpha: web submission needs an AI assistant that can operate a browser, and you handle QR codes and verification. Fully automatic posting to all five platforms has not passed acceptance.",
    mfTitle: "Release plan", mfTag: "Example", mfSrcVideo: "Source", mfSrcScript: "Script", mfSrcScriptV: "Simplified Chinese", mfSrcVis: "Visibility", mfSrcVisV: "All public",
    mfColPlatform: "Platform", mfColCover: "Cover", mfColCopy: "Copy", mfColState: "Status",
    srCover: "Cover: ", srCopy: "Copy language: ", srState: "Status: ",
    langZh: "Chinese", langEn: "English",
    coverVideo: "Video", pDy: "Douyin",
    mfFoot: "Submitted platform by platform. Marked Verified only after the work is read back.",
    howTitle: "Say it once, your AI assistant handles it",
    howSub: "Your AI assistant writes the words; vpub handles the plan, cover fitting, dry runs and receipts.",
    pbSay: "You can say", pbDo: "What it does", pbNot: "What it won't do", pbSaySr: "You can say: ",
    s1q: "Post this to Bilibili and YouTube, keep the titles casual.",
    s1a: "Writes a title, description and tags for each platform, submits to both directly, and keeps a receipt for every step.",
    s1n: "Add a platform just because you are signed in to it, or quietly change the visibility.",
    s2q: "Fit the cover to each platform, and leave the original alone.",
    s2a: "Saves a new cover for each platform canvas, keeping the whole image. Platform previews may still crop it, so take a look before submitting.",
    s2n: "Crop, stretch or overwrite your original image.",
    s3q: "Chinese for Bilibili and Douyin, English for YouTube.",
    s3a: "Writes each platform's copy in its language: Simplified Chinese for Bilibili and Douyin, English for YouTube, Instagram and X. Ask if you want both languages.",
    s3n: "Translate the video's voice-over or subtitles.",
    s4q: "Did my last video actually go out?",
    s4a: "Reads the local receipts and tells you how far each platform got, then checks the platform itself where a readback is available.",
    s4n: "Call a queued task, a sign-in link or a local receipt “published”.",
    statesTitle: "Every status is called what it is",
    statesSub: "Publishing directly still means saying exactly how far it got. Your AI assistant reports what the evidence shows and never describes an earlier step as a later one.",
    stQueue: "Queued", stQueueD: "The task is recorded and nothing has been uploaded yet.",
    stRun: "Publishing", stRunD: "Your AI assistant is uploading and filling in the official page, or the CLI is submitting through a configured backend.",
    stSent: "Submitted, unverified", stSentD: "The platform accepted the submission. It may still be in review or processing, and the work has not been read back yet.",
    stOk: "Verified", stOkD: "The work was read back from the platform page or API. Review and public visibility still follow what the platform shows.",
    rule1a: "QR code, verification or risk check", rule1b: ": it stops where it is and waits for you on the official page.",
    rule2a: "Uncertain result", rule2b: ": it checks the original task only and never uploads again.",
    platTitle: "Five platforms, five different paths",
    platSub: "A successful install does not mean you are signed in or allowed to post. Run <code>vpub capabilities</code> to see which paths actually work on your machine.",
    thPlatform: "Platform", thDefault: "Default path", thOptional: "Optional path", thStatus: "Status", thNote: "Notes",
    pBiliDef: "AI assistant operates the official site", pBiliOpt: "Upstream bili CLI",
    pYtDef: "Handoff on the official site", pYtOpt: "YouTube Data API (bring your own OAuth)",
    pDyDef: "AI assistant helps on the official site", pDyOpt: "Standalone backend (experimental)",
    pWebDraft: "AI assistant operates the official site; drafts supported", pNoApi: "No standalone API",
    avail: "Available", availWeb: "Web available", paused: "Paused",
    pTtNote: "The CLI refuses TikTok sign-in and posting requests.",
    platNote1: "Web paths need an AI assistant that can operate a browser. Without one, it can only prepare materials and handoff notes.",
    platNote2: "Scheduling works only on the Bilibili CLI path (2 hours to 15 days ahead) and the YouTube API path (set to private). Web paths post immediately.",
    platNote3: "There are no general comment, reply, direct message, inbox or creator analytics commands.",
    startTitle: "Three steps from install to your first release",
    st1: "Install", st1d: "Requires Python 3.10–3.12. Hand the prompt above to your AI assistant, or run:",
    st1n: "On Windows you can also run <code>install.ps1</code> from the repository. macOS and Linux have not been verified yet.",
    st2: "Check your setup", st2d: "See your local dependencies and the posting paths that actually work. Neither command signs in or posts.",
    st3: "Say it, and it publishes", st3d: "Tell your AI assistant the video file, the platforms and the visibility, and it submits to each platform in turn and keeps receipts. The first time you sign in to each platform, you handle QR codes and verification codes.",
    copyInstallA: "Copy install command", copyDoctorA: "Copy vpub doctor", copyCapsA: "Copy vpub capabilities",
    manualSum: "Prefer to do it yourself? See the manual flow",
    manualNote: "<code>--dry-run</code> does not upload. Your AI assistant writes <code>response.json</code> from the template in <code>request.json</code>.",
    faqTitle: "Questions",
    faqSub: "Not covered here? <a href=\"" + REPO + "/issues\" rel=\"noopener\">Ask on GitHub</a>, and remove account details and private information first.",
    q0: "Do I have to approve each release?", a0: "No. Asking it to publish is the authorization: it submits directly with the platforms, accounts and visibility you named, and only asks again if the content, account, platform, audience or scope changes. To look first, say \u201cdon\u2019t post yet\u201d, or run a <code>--dry-run</code>.",
    q1: "Do I need a separate model API key?", a1: "No. CrossCast has no built-in model; titles and descriptions come from the AI assistant you already use. You only set up Google OAuth yourself if you choose the YouTube API path.",
    q2: "Will it sign in to my accounts?", a2: "Sign-in happens on the official sites, and you handle QR codes, passwords and verification codes yourself. Never share cookies, passwords, codes or tokens in chats, logs or the repository. This page collects nothing.",
    q3: "Can vpub post directly from a terminal?", a3: "Not on the default web path: a standalone CLI cannot operate a browser, so it prepares the plan and handoff, and a browser-capable AI assistant does the web steps. If you set up the Bilibili CLI or the YouTube API yourself and authorize it, the CLI can submit on those two paths.",
    q4: "If it is queued or has a receipt, is it published?", a4: "No. Queued only means the task is recorded. Local receipts record the evidence for each step but are not proof from the platform. Whether it was published is decided by reading back the platform page or the platform API.",
    q5: "Will it change my original video or cover?", a5: "No. Fitted covers are saved as new files and your original image stays as it is. Instagram uploads use the original video without transcoding.",
    q6: "Could it post to a platform I didn't pick?", a6: "It will not add a platform just because you are signed in to it. You choose platforms, accounts and visibility; if the content, account, platform, audience or scope changes, it asks you again.",
    q7: "Does it cost anything?", a7: "CrossCast is free and open source (MIT). Your AI assistant subscription, platform features and the optional YouTube API may have their own costs or limits; this project does not promise free or unlimited use.",
    q8: "Does it support TikTok, comments or analytics?", a8: "TikTok support is paused. This version has no general comment, reply, direct message, inbox or creator analytics features.",
    q9: "Is it ready for real releases?", a9: "This is 0.3.0 alpha, and fully automatic posting to all five platforms has not passed acceptance. The first time, you can run a dry run (<code>--dry-run</code>) to review the plan before publishing for real.",
    endTitle: "Free and open source. Help make it better.",
    endSub: "MIT licensed. Found a problem or want another platform? Open an issue on GitHub, and remove account details and private information first.",
    ctaGh: "View on GitHub", ctaIssue: "Report a problem",
    footIndie: "PublishOps CrossCast is an independent integration tool, not an official product of any video platform.",
    footMeta: "MIT licensed. Current version 0.3.0 alpha.",
    footNav: "Footer links", footDocs: "Docs", footTop: "Back to top",
    _title: "PublishOps CrossCast | One message. Every platform you choose.",
    _desc: "PublishOps CrossCast (vpub) lets the AI assistant you already use write titles and descriptions and fit covers for Bilibili, YouTube, Douyin, Instagram and X, then submit to each platform in turn and keep receipts. 0.3.0 alpha.",
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
