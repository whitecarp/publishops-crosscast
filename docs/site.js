(function () {
  "use strict";
  var EN = {
    skip: "Skip to content", navLabel: "Page navigation",
    navScenes: "Scenes", navPlatforms: "Platforms", navStart: "Get started",
    h1a: "One video,", h1b: "ready for five platforms",
    lead: "Let the AI assistant you already use, such as Codex or Claude, prepare titles, descriptions, covers and publishing plans for Bilibili, YouTube, Douyin, Instagram and X, then track every publishing receipt. No built-in model, no separate model API key.",
    installLabel: "Tell your AI assistant:",
    installPrompt: "Install PublishOps CrossCast from https://github.com/whitecarp/publishops-crosscast, read skills/publishops-crosscast/SKILL.md, and run vpub doctor first. Start by preparing publishing drafts for me; I will confirm the platforms, accounts and visibility.",
    copy: "Copy", ctaStart: "Get started in 3 steps", ctaDocs: "Read the docs",
    scenesEy: "How it works", scenesT: "You say it, your AI gets it ready",
    scenesSub: "Your existing host model writes the words; vpub organizes drafts, covers, plans and receipts.",
    you: "You say", ai: "AI does",
    s1q: "\u201cPost this to Bilibili and YouTube, keep the titles casual.\u201d",
    s1a: "Writes a title and description per platform, builds a publishing draft, then asks you to confirm platforms, accounts and visibility.",
    s2q: "\u201cThe cover text is too small, fix the Instagram one too.\u201d",
    s2a: "Saves an adjusted cover as a new file and keeps your original; Instagram uses the original video with no transcoding.",
    s3q: "\u201cSchedule it on Douyin for 8 tonight.\u201d",
    s3a: "Adds it to the publishing plan and hands off to a browser-capable assistant on the official site; you complete any QR scan or code. Queued does not mean published.",
    s4q: "\u201cDid my last video actually go out?\u201d",
    s4a: "Checks the receipt log and tells you which platforms have a receipt and which are still pending or need you.",
    platEy: "Platform support", platT: "Five platforms, each its own path",
    platSub: "Sign-in happens on official websites by default, and you complete QR scans or codes. This table shows the real current status.",
    thP: "Platform", thHow: "Publishing path", thNote: "Notes",
    tBili: "Official web handoff / optional bili-cli", nBili: "Host-operated official web publishing by default; the upstream bili-cli backend is optional. CrossCast does not reimplement all of its features.",
    tYt: "Host browser / official API", nYt: "Host browser handoff by default; the official YouTube API is optional.",
    pDy: "Douyin", tRelay: "Host browser handoff",
    nDy: "An optional standalone backend exists and is experimental.",
    nIg: "Keeps the original video, no transcoding.",
    nX: "Done on the official site by an assistant with browser tools.",
    tNo: "Not supported", nTt: "Not included in the current version.",
    note1: "Fully automatic publishing across all five platforms has not passed acceptance yet. Treat this as an alpha tool.",
    note2: "There are no general comment management or analytics features.",
    note3: "The CLI on its own only prepares the handoff; browser actions are performed by an agent with browser tools.",
    startEy: "Get started", startT: "From install to your first draft",
    st1: "Install", st1d: "Requires Python 3.10\u20133.12. Hand the prompt above to your AI assistant, or run:",
    st2: "Check your setup", st2d: "Run a quick health check to see which platform capabilities are ready.",
    st3: "Draft first, you decide",
    st3d: "Let the AI prepare drafts. Before publishing you choose platforms, accounts, visibility and review preferences; after that it only acts within what you authorized.",
    faqT: "Frequently asked questions",
    q1: "Do I need a separate model API key?", a1: "No. vpub has no built-in model; titles and descriptions are written by the host model you already use, such as Codex or Claude.",
    q2: "Will it sign in to my accounts?", a2: "Sign-in happens on official websites the host can operate, and you complete QR scans or codes yourself. This page never asks for, and does not need, any secrets.",
    q3: "Can running vpub in a terminal publish directly?", a3: "No. A standalone CLI cannot drive the built-in browser of apps like Codex. It prepares the handoff; browser steps need an agent with browser tools.",
    q4: "Does being queued mean it was published?", a4: "No. Queued only means the plan is recorded. It counts as published once the platform returns a receipt, and vpub helps you track those receipts.",
    q5: "Will it modify my original video or cover?", a5: "No. Cover adjustments keep the original image, and Instagram uploads use the original video without transcoding.",
    q6: "Could the AI post somewhere I did not intend?", a6: "Before publishing you choose platforms, accounts, visibility and review preferences; later actions stay within that authorized scope.",
    q7: "Does it support TikTok, comments or analytics?", a7: "TikTok is not supported yet, and there are no general comment or analytics features.",
    q8: "Is it ready for real publishing?", a8: "This is 0.3.0 alpha, and fully automatic publishing across all five platforms has not passed acceptance. Start with drafts and plans, and confirm each step yourself.",
    endT: "Open source, free, built in the open", endSub: "MIT licensed. Found a problem or want a new platform? Open an issue on GitHub.",
    ctaGh: "View on GitHub", footLic: "MIT License", footDocs: "Docs", footTop: "Back to top",
    _title: "PublishOps CrossCast \u00b7 One video, ready for five platforms",
    copyPromptA: "Copy install prompt", copyInstallA: "Copy install command", copyDoctorA: "Copy vpub doctor command",
    _desc: "PublishOps CrossCast (vpub) helps your existing AI assistant prepare titles, descriptions, covers and publishing plans for Bilibili, YouTube, Douyin, Instagram and X, then track receipts.",
    _copied: "Copied", _copyFail: "Copy failed, please select and copy manually", _copyErr: "Copy failed"
  };
  var ZH = { _copied: "\u5df2\u590d\u5236", _copyFail: "\u590d\u5236\u5931\u8d25\uff0c\u8bf7\u624b\u52a8\u9009\u4e2d\u590d\u5236", _copyErr: "\u590d\u5236\u5931\u8d25" };
  var TW = {
    skip: "跳到正文", navLabel: "頁面導覽", navScenes: "情境", navPlatforms: "平台", navStart: "上手",
    h1a: "一份影片，", h1b: "五個平台的發佈準備",
    lead: "讓你已經在用的 Codex、Claude 等 AI 助手，為 B站、YouTube、抖音、Instagram 和 X 準備標題、簡介、封面和發佈計畫，並追蹤每一次發佈回執。不內建模型，也不需要另配模型 API Key。",
    installLabel: "對你的 AI 助手說：",
    installPrompt: "幫我從 https://github.com/whitecarp/publishops-crosscast 安裝 PublishOps CrossCast，閱讀 skills/publishops-crosscast/SKILL.md，先執行 vpub doctor；先幫我準備發佈草稿，發佈平台、帳號和公開範圍由我確認。",
    copy: "複製", ctaStart: "三步上手", ctaDocs: "閱讀文件",
    scenesEy: "怎麼用", scenesT: "你說一句，AI 幫你準備好",
    scenesSub: "文字由你已有的宿主模型來寫；vpub 負責整理草稿、封面、計畫和回執。",
    you: "你說", ai: "AI 處理",
    s1q: "「把這支影片發 B站和 YouTube，標題口語一點。」",
    s1a: "依平台分別寫標題和簡介，產生發佈草稿，再請你確認平台、帳號和公開範圍。",
    s2q: "「封面字太小，Instagram 那版也調一下。」",
    s2a: "另存一份調整後的封面，原圖保留不動；Instagram 使用原影片，不轉碼。",
    s3q: "「今晚 8 點發抖音，先排進計畫。」",
    s3a: "寫入發佈計畫，並交給能操作瀏覽器的助手開啟官方網頁；掃碼或驗證碼由你完成。排隊不等於發佈成功。",
    s4q: "「上次那支到底發出去沒有？」",
    s4a: "查看回執紀錄，告訴你哪些平台已有回執、哪些還在等待或需要你處理。",
    platEy: "平台支援", platT: "五個平台，各走各的路",
    platSub: "登入預設走官方網頁，由你完成掃碼或驗證碼。下表寫的是目前真實狀態。",
    thP: "平台", thHow: "發佈方式", thNote: "說明",
    tBili: "官方網頁接力 / 可選 bili-cli", nBili: "預設由宿主操作官方網頁；亦可選用上游 bili-cli 後端。CrossCast 不重新實作其全部功能。",
    tYt: "宿主瀏覽器 / 官方 API", nYt: "預設宿主瀏覽器接力；也可選配 YouTube 官方 API。",
    pDy: "抖音", tRelay: "宿主瀏覽器接力", nDy: "另有可選的獨立後端，目前為實驗性。",
    nIg: "保留原影片，不做轉碼。", nX: "由支援瀏覽器工具的助手在官方網頁上操作。",
    tNo: "暫不支援", nTt: "目前版本未接入。",
    note1: "五個平台的全自動發佈尚未完成驗收，現階段請把它當作 alpha 工具。",
    note2: "沒有通用的留言管理或數據分析功能。",
    note3: "單獨執行的 CLI 只準備交接材料；真正的瀏覽器操作由支援瀏覽器工具的 Agent 執行。",
    startEy: "三步上手", startT: "從安裝到第一份草稿",
    st1: "安裝", st1d: "需要 Python 3.10–3.12。可以把上面的安裝指令交給 AI 助手，或者自己執行：",
    st2: "檢查環境", st2d: "先檢查一遍，看看哪些平台能力已經就緒。",
    st3: "準備草稿，由你拍板", st3d: "讓 AI 先出草稿。發佈前由你選定平台、帳號、公開範圍和審核偏好，之後它只在你授權的範圍內行動。"
    , faqT: "常見問題",
    q1: "需要單獨的模型 API Key 嗎？", a1: "不需要。vpub 不內建模型，標題和簡介由你已經在用的 Codex、Claude 等宿主模型來寫。",
    q2: "它會幫我登入帳號嗎？", a2: "登入預設在宿主能操作的官方網頁上進行，掃碼或驗證碼由你本人完成。這個頁面不會、也不需要收集任何密鑰。",
    q3: "只在終端機裡執行 vpub，能直接發出去嗎？", a3: "不能。獨立的 CLI 無法操作 Codex 等應用的內建瀏覽器，它負責準備交接材料；瀏覽器上的操作需要支援瀏覽器工具的 Agent 來完成。",
    q4: "進入發佈佇列就算發佈成功了嗎？", a4: "不算。排隊只代表計畫已記錄，只有拿到平台回執才算發佈完成，vpub 會幫你追蹤這些回執。",
    q5: "會改動我的原影片和封面嗎？", a5: "不會。調整封面時原圖會保留；發 Instagram 時直接使用原影片，不做轉碼。",
    q6: "AI 會不會自作主張發到別的地方？", a6: "發佈前由你選擇平台、帳號、公開範圍和審核偏好，後續操作只在你授權的範圍內進行。",
    q7: "支援 TikTok、留言管理或數據分析嗎？", a7: "暫不支援 TikTok，也沒有通用的留言或數據功能。",
    q8: "現在可以放心用於正式發佈嗎？", a8: "目前是 0.3.0 alpha，五個平台的全自動發佈尚未完成驗收。建議先從草稿和計畫開始，每一步都親自確認。",
    endT: "開源、免費，歡迎一起完善", endSub: "MIT 授權。遇到問題或想支援新平台，歡迎到 GitHub 提 Issue。",
    ctaGh: "在 GitHub 查看", footLic: "MIT 授權", footDocs: "文件", footTop: "回到頂部",
    copyPromptA: "複製安裝指令", copyInstallA: "複製安裝命令", copyDoctorA: "複製 vpub doctor 命令",
    _title: "PublishOps CrossCast · 一份影片，五個平台的發佈準備",
    _desc: "PublishOps CrossCast（vpub）幫你用已有的 Codex / Claude 等 AI 助手，為 B站、YouTube、抖音、Instagram、X 準備標題、簡介、封面和發佈計畫，並追蹤回執。",
    _copied: "已複製", _copyFail: "複製失敗，請手動選取複製", _copyErr: "複製失敗"
  };
  var DICTS = { "zh-CN": ZH, "zh-TW": TW, en: EN };
  var REPO = "https://github.com/whitecarp/publishops-crosscast";
  var DOCS = { "zh-CN": REPO + "#readme", "zh-TW": REPO + "/blob/main/README_ZH_TW.md", en: REPO + "/blob/main/README_EN.md" };
  var KEY = "crosscast-lang";
  var nodes = document.querySelectorAll("[data-i18n]");
  var ariaNodes = document.querySelectorAll("[data-i18n-aria]");
  nodes.forEach(function (n) { ZH[n.getAttribute("data-i18n")] = n.textContent; });
  ariaNodes.forEach(function (n) { ZH[n.getAttribute("data-i18n-aria")] = n.getAttribute("aria-label"); });
  ZH._title = document.title;
  var descMeta = document.querySelector('meta[name="description"]');
  ZH._desc = descMeta ? descMeta.content : "";

  var langBtns = document.querySelectorAll("button[data-lang]");
  var docLinks = document.querySelectorAll("a[data-doc]");
  var toast = document.getElementById("toast");
  var lang = "zh-CN";

  function norm(v) {
    v = String(v || "").toLowerCase();
    if (v === "en" || v.indexOf("en-") === 0) return "en";
    if (v === "zh-tw" || v === "zh-hk" || v === "zh-mo" || v === "zh-hant" || v === "tw" || v === "hant") return "zh-TW";
    if (v === "zh-cn" || v === "zh" || v === "zh-hans" || v === "cn") return "zh-CN";
    return null;
  }

  function t(key) { var d = DICTS[lang] || ZH; return d[key] != null ? d[key] : ZH[key]; }

  function apply(next, save) {
    lang = norm(next) || "zh-CN";
    nodes.forEach(function (n) {
      if (n.classList.contains("copy") && n.dataset.state) return;
      n.textContent = t(n.getAttribute("data-i18n"));
    });
    ariaNodes.forEach(function (n) { n.setAttribute("aria-label", t(n.getAttribute("data-i18n-aria"))); });
    document.documentElement.lang = lang;
    document.title = t("_title");
    if (descMeta) descMeta.content = t("_desc");
    docLinks.forEach(function (a) { a.href = DOCS[lang]; });
    langBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === lang)); });
    if (save) { try { localStorage.setItem(KEY, lang); } catch (e) {} }
  }

  langBtns.forEach(function (b) {
    b.addEventListener("click", function () { apply(b.getAttribute("data-lang"), true); });
  });

  var saved = null;
  try { saved = norm(localStorage.getItem(KEY)); } catch (e) {}
  var q = norm(new URLSearchParams(location.search).get("lang"));
  apply(q || saved || "zh-CN", !!q);

  var toastTimer;
  function say(msg) {
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 2200);
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
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
    var r = document.createRange(); r.selectNodeContents(el);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  }

  function flag(b, state, msg) {
    b.dataset.state = state;
    b.classList.remove("ok", "err"); b.classList.add(state);
    b.textContent = state === "ok" ? "\u2713 " + t("_copied") : "! " + t("_copyErr");
    say(msg);
    setTimeout(function () {
      delete b.dataset.state;
      b.classList.remove("ok", "err");
      b.textContent = t("copy");
    }, 2000);
  }

  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var src = document.getElementById(b.getAttribute("data-copy"));
      if (!src) return;
      copyText(src.textContent.trim()).then(function () {
        flag(b, "ok", t("_copied"));
      }, function () {
        selectEl(src);
        flag(b, "err", t("_copyFail"));
      });
    });
  });
})();
