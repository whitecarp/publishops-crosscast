"""Private, persistent browser sessions owned by CrossCast, never host cookies."""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urlsplit

from .core import VpubError, account_name, atomic_json, config, home, how_to_clear_a_stop, now, read_json
from .progress import stage, watch

BROWSER_PLATFORMS = {"douyin", "instagram", "x", "youtube"}
URLS = {"douyin": "https://creator.douyin.com/creator-micro/home",
        "instagram": "https://www.instagram.com/accounts/edit/", "x": "https://x.com/home",
        "youtube": "https://studio.youtube.com/"}
INSTAGRAM_AVATAR = 'img[alt*="profile picture"], img[alt*="的头像"], img[alt*="的大頭貼"]'
HUMAN_LABELS = re.compile(r"安全验证|請完成驗證|请完成验证|滑块验证|滑塊驗證|验证是你本人|驗證是你本人|确认是你本人|確認是你本人|验证你的身份|驗證你的身分|确认你的身份|確認你的身分"
                          r"|Confirm it's you|Verify it's you|Verify your identity|Authenticate your account|Confirm your identity|Unusual activity"
                          # A platform saying too much, too fast.
                          r"|(?:操作|评论|評論|回复|回覆|私信|发送|發送)(?:过于|過於|太)?[频頻]繁|Action Blocked|We limit how often|We restrict certain activity|Rate limit exceeded|over the daily limit|temporarily (?:blocked|restricted|limited)"
                          # A locked account's recovery page. A person answers it; CrossCast never presses anything on it.
                          r"|Choose a way to recover|Get help from friends|选择恢复方式|選擇復原方式|向好友求助|向朋友求助", re.I)
# Addresses that only ever mean the platform is asking for a check, whatever the page says.
CHALLENGE_ADDRESSES = {"instagram.com": r"/(challenge|checkpoint|auth_platform|accounts/(suspended|disabled))(/|$)",
                       "x.com": r"/account/access(/|$)",
                       "google.com": r"(/v\d)?/signin(/v\d)?/(challenge|rejected)(/|$)"}
# Where other people's words show. A viewer writing "Confirm it's you" in a comment is not the platform asking,
# and it must not be able to stop the whole tool. These are the places CrossCast itself reads people's words from,
# not generic lists, which a real check can use too. A place missing here means a stop a person clears, never a missed check.
OTHER_PEOPLES_WORDS = ('article, [role="article"], [data-testid="cellInnerDiv"], [data-testid="tweetText"], [data-dm-message-row], [data-testid^="dm-conversation-item-"], [data-testid="dm-message-list"], '
                       '[data-pagelet^="IGD"], [aria-roledescription="message"], [data-crosscast-comment-row], [data-crosscast-mentions-panel], '
                       '[data-e2e="comment-list"], [data-e2e="listDlgTest-container"], [data-stack-layer="chat"], li[role="list-item"], [class*="comment" i], '
                       # A message opens from the other person's profile, which holds their name and their own words, and a banner of the site's own can sit beside them.
                       'main header section :is(h1, h2, [dir="auto"]):not([role="dialog"] *), [data-e2e="user-info"], '
                       'ytcp-comment-thread, ytcp-comment, ytd-comment-thread-renderer, ytd-comment-view-model, ytcp-video-row, ytd-watch-metadata')
# An alert is the site's own, wherever it is put: only the site gives something that role. A window can hold people's words, so whichever is closer owns them:
# a viewer's comment inside a window is the viewer's, and a window inside a viewer's profile is the platform's.
# A small row with an author's link and a time is someone's post or comment, however the site builds it. A window is the edge of the search.
SOMEONES_WORDS = """(e, others) => {
  if (e.closest('[role="alert"],[role="alertdialog"]')) return false;
  if (e.closest('[contenteditable]:not([contenteditable="false"])')) return true;  // A box a person types into holds their words, and a reply can quote a warning.
  const place = e.closest(others), dialog = e.closest('[role="dialog"]');
  if (place && !(dialog && place.contains(dialog))) return true;
  for (let row = e, depth = 0; row && depth < 9 && row !== document.body && !row.matches('[role="dialog"],main'); row = row.parentElement, depth++) {
    if (row.innerText.length > 2000) return false;
    if (row.querySelector('time') && row.querySelector('a[href]')) return true;
  }
  return false;
}"""
# What a failure record says about a control: what it is, never what it holds.
CONTROL_SHAPES = """es => es.map(e => ({tag: e.tagName, type: e.getAttribute('type'), name: e.getAttribute('name'), id: e.id, role: e.getAttribute('role'), testid: e.getAttribute('data-testid'),
                                       test_id: e.getAttribute('test-id'), placeholder: e.getAttribute('placeholder'), label: e.getAttribute('aria-label'), disabled: e.hasAttribute('disabled'), visible: e.getClientRects().length > 0}))"""
FORM_FIELDS = "input,textarea,select,[contenteditable=true]"
# A command's quoted arguments can hold a viewer's or a person's own words. Only a selector made of nothing but page structure stays.
QUOTED = re.compile(r""""(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'""")
PLAIN_SELECTOR = re.compile(r"[\w\s#.>:,*()\[\]-]*")
# How far into a site's answer to look, and the part that describes the page rather than the change. A field name holds no words of anyone's.
ANSWER_DEPTH = 8
ABOUT_THE_PAGE = {"responseContext"}
FIELD_NAME = re.compile(r"[A-Za-z_]\w{0,40}")
# Why a press was refused, in Playwright's own fixed words.
REFUSED_PRESS = re.compile(r"element is not \w+|element is outside of the viewport|element was detached|intercepts pointer events")
# The page's words with its form fields taken out first, so even a record a person asked for holds nothing typed into one.
PAGE_WORDS = """() => { const copy = document.body.cloneNode(true); copy.querySelectorAll('input,textarea,select,script,style,[contenteditable=true]').forEach(e => e.remove()); return copy.textContent; }"""
SITES = {"x": ("x.com", "twitter.com"), "instagram": ("instagram.com",), "douyin": ("douyin.com", "bytedance.com"), "youtube": ("youtube.com", "google.com")}
# Chrome can stop answering, and this process holds the profile lock until it ends.
CLOSE_SECONDS = 30
EXIT_SECONDS = 90


def profile_dir(platform, account="default"):
    if platform not in BROWSER_PLATFORMS:
        raise VpubError("invalid_platform", "Persistent browser supports douyin, instagram, x and youtube.")
    return home() / "accounts" / account_name(account) / (platform + "-browser-profile")


@contextmanager
def profile_lock(platform, account):
    from .process_lock import hold
    path = profile_dir(platform, account).with_suffix(".lock")
    with hold(path, 'browser_busy', "This platform's login or browser command is already running."):
        yield


async def visible(locator):
    for item in await locator.all():
        if await item.is_visible():
            return item
    return None


async def unique_visible(locator, label):
    items = [x for x in await locator.all() if await x.is_visible()]
    if len(items) != 1:
        raise VpubError("layout_changed", f"Expected one visible {label}; found {len(items)}.", "Inspect the existing page before retrying. No final submission is repeated.")
    return items[0]


async def look_until(page, look, milliseconds, step=500):
    """A fixed wait that ends as soon as what comes next is already there. It lasts at least as long as the wait it replaces when nothing shows, and looks once more at the end."""
    for _ in range(milliseconds // step):
        try:
            found = await look()
        except Exception:
            found = None  # The page is still changing, which is what the wait is for.
        if found:
            return found
        await page.wait_for_timeout(step)
    return await look()


def on_site(host, site):
    return host == site or host.endswith("." + site)


def platform_at(address):
    host = urlsplit(str(address)).hostname or ""
    return next((name for name, sites in SITES.items() if any(on_site(host, site) for site in sites)), "unknown")


def challenge_address(address):
    parts = urlsplit(str(address))
    return any(on_site(parts.hostname or "", site) and re.match(path, parts.path) for site, path in CHALLENGE_ADDRESSES.items())


async def platform_words(page):
    """The first thing on the page that asks for a check in the platform's own voice."""
    for item in await page.get_by_text(HUMAN_LABELS).all():
        if await item.is_visible() and not await item.evaluate(SOMEONES_WORDS, OTHER_PEOPLES_WORDS):
            return item
    return None


async def challenge_shown(page):
    """Whether the platform itself is asking for a check or an account recovery on this page."""
    return bool(challenge_address(page.url) or await visible(page.locator('iframe[src*="/verifycenter/captcha/"]')) or await platform_words(page))


async def what_asked(page):
    """For the stop's record, so a false alarm can be told from a real check. A field run stopped on X, and the next sign-in saw no check, with nothing to say why. It holds which sign it was, and for words only the phrase that matched and the part of the page, never anything else on the page."""
    try:
        if challenge_address(page.url):
            return {"sign": "address"}
        item = await platform_words(page)
        if not item:
            return {"sign": "check frame"}
        phrase = HUMAN_LABELS.search(await item.inner_text())
        place = await item.evaluate("""e => { const found = e.closest('[role="alert"],[role="alertdialog"],[role="dialog"],aside,nav,header,footer,main,[data-testid]');
          if (!found) return 'page'; const name = found.getAttribute('data-testid') || found.getAttribute('role') || found.tagName.toLowerCase(); return /^[A-Za-z_]{1,40}$/.test(name) ? name : 'other'; }""")
        return {"sign": "words", "phrase": phrase[0] if phrase else None, "place": place}
    except Exception:
        return None


async def risk_check(page):
    if await challenge_shown(page):
        from .safety import pushed_back
        # Recorded where it is seen: a batch or a multi-step read catches the error below so it can report its other steps.
        pushed_back(platform_at(page.url), "human_verification_required", await what_asked(page))
        raise VpubError("human_verification_required", "The platform requires human verification.", f"Run auth login for this platform and complete the official verification, then {how_to_clear_a_stop()}; inspect any existing draft before retrying.")


async def identity(page, platform):
    """Read the signed-in account from its own navigation, not arbitrary links."""
    if platform == "x":
        # X's rebuilt site dropped the test label; its main menu still links to the account's own page.
        link = await visible(page.locator('a[data-testid="AppTabBar_Profile_Link"]')) or await visible(page.locator("nav").get_by_role("link", name=re.compile(r"^(Profile|个人资料|個人資料)$", re.I)))
        if link:
            path = urlsplit(await link.get_attribute("href") or "").path.strip("/")
            if re.fullmatch(r"[A-Za-z0-9_]{1,15}", path):
                return path
    elif platform == "instagram":
        link = await visible(page.get_by_role("link", name=re.compile(r"^(Profile|个人主页|個人檔案|个人资料|個人資料)$", re.I)))
        if link:
            names = {urlsplit(await link.get_attribute("href") or "").path.strip("/")}
        elif urlsplit(page.url).path.rstrip("/") == "/accounts/edit":
            # A field check found no link named Profile on the real site, and on the home page seven avatars, five of them other people's. Its settings page shows one, and only this account's.
            names = {urlsplit(await item.get_attribute("href") or "").path.strip("/") for item in await page.locator("a").filter(has=page.locator(INSTAGRAM_AVATAR)).all() if await item.is_visible()}
        else:
            names = set()
        if len(names) == 1 and re.fullmatch(r"[A-Za-z0-9_.]{1,30}", path := names.pop()) and path not in {"accounts", "explore", "reels"}:
            return path
    elif platform == "youtube":
        # Studio's address names the signed-in channel; a signed-out visit lands on Google's sign-in page.
        address = urlsplit(page.url)
        found = re.fullmatch(r"/channel/(UC[\w-]{22})(?:/.*)?", address.path) if address.hostname == "studio.youtube.com" else None
        if found:
            return found[1]
    else:
        # Creator dashboard exposes the Douyin account identifier, distinct from nickname.
        text = await page.locator("body").inner_text(timeout=10000)
        found = re.search(r"抖音(?:号|號|ID)\s*[:：]\s*([A-Za-z0-9_.-]{1,64})", text)
        if found:
            return found[1]
    return None


def check_identity(actual, expected):
    if not actual:
        raise VpubError("identity_required", "Signed-in account could not be identified from the official page.", "Complete login in the dedicated browser; do not infer identity from a stored cookie file.")
    if expected and actual.casefold() != str(expected).casefold():
        raise VpubError("wrong_account", "The signed-in account differs from the publication manifest.")
    return actual


async def open_context(pw, platform, account, interactive=False):
    path = profile_dir(platform, account)
    path.mkdir(parents=True, exist_ok=True)
    # Studio does not run under a headless browser, so YouTube's background browser is a real Chrome window:
    # off-screen, and on Windows also on its own hidden desktop (see hidden_desktop.py), so there is no taskbar icon either.
    off_screen = platform == "youtube" and not interactive
    try:
        context = await pw.chromium.launch_persistent_context(
            str(path), channel="chrome", headless=not interactive and not off_screen,
            args=["--window-position=-32000,-32000"] if off_screen else [],
            viewport={"width": 1440, "height": 1000}, locale="en-US",
            accept_downloads=False)
        session = path.parent / (platform + "-browser-session.json")
        if session.exists():
            # Only our own dedicated session, never cookies from the host browser.
            cookies = read_json(session).get("cookies", [])
            await context.add_cookies(cookies)
        return context
    except Exception:
        raise VpubError("browser_unavailable", "Could not start the dedicated Chrome profile.", "Install Google Chrome, close any other CrossCast login for this platform, then retry. Your normal browser profile is never used.")


def identity_path(platform, account):
    return profile_dir(platform, account).parent / (platform + "-identity.json")


def remember_identity(platform, account, actual):
    atomic_json(identity_path(platform, account), {"identity": actual, "verified_at": now(), "method": "browser"})


async def login(pw, payload):
    platform, account = payload["platform"], payload["account"]
    job = Path(payload["job_path"])
    context = await open_context(pw, platform, account, interactive=True)
    deadline = time.monotonic() + 900
    waiting = {"platform": platform, "login_url": URLS[platform], "expires_epoch": time.time() + 900}
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URLS[platform], wait_until="domcontentloaded", timeout=60000)
        await page.bring_to_front()
        atomic_json(job, {"state": "LOGIN_PENDING", **waiting, "window_opened": True,
                          "hint": "Complete official login in the CrossCast Chrome window. No password, SMS or CAPTCHA is entered by the CLI."})
        asked = False
        while time.monotonic() < deadline:
            # A sign-in can replace its tab or open another, so only closing every tab cancels it.
            pages = [item for item in context.pages if not item.is_closed()]
            if not pages:
                raise VpubError("login_cancelled", "Login window was closed before account verification.")
            # A signed-in menu can still show behind a check, so a check on any tab comes before who the account is.
            challenged = await any_challenge(pages)
            if challenged is not False:
                if challenged and not asked:
                    asked = True
                    atomic_json(job, {"state": "VERIFICATION_REQUIRED", **waiting, "window_opened": True,
                                      "hint": "The platform is asking for a check or account recovery. Finish it yourself in the CrossCast Chrome window; the CLI never answers it."})
                await asyncio.sleep(2)
                continue
            for page in pages:
                try:
                    actual = await identity(page, platform)
                    if not actual:
                        # A sign-in can end on Instagram's home page, which cannot say for certain who this is.
                        if platform == "instagram" and urlsplit(page.url).path == "/" and await page.locator("a").filter(has=page.locator(INSTAGRAM_AVATAR)).count():
                            await page.goto(URLS[platform], wait_until="domcontentloaded", timeout=60000)
                        continue
                    # Wait for the login redirect to settle and prove a fresh load.
                    await asyncio.sleep(3)
                    await page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    if await any_challenge(pages) is not False or await identity(page, platform) != actual:
                        continue
                except Exception as exc:
                    if isinstance(exc, VpubError):
                        raise
                    continue  # This tab closed or moved on while it was being read; the next pass looks again.
                await keep_session(context, platform, account)
                remember_identity(platform, account, actual)
                result = {"logged_in": True, "platform": platform, "account": account, "identity": actual, "method": "browser"}
                if (home() / "STOP.json").exists():
                    result["next"] = f"CrossCast is still stopped because of a platform's warning. Once its check is done, run {how_to_clear_a_stop()}."
                return result
            await asyncio.sleep(2)
        raise VpubError("login_timeout", "Login was not completed within 15 minutes; saved profile is retained.")
    finally:
        await close_context(context)


async def any_challenge(pages):
    """True, False, or None when a tab that is still open could not be read, which proves nothing either way."""
    unread = False
    for page in pages:
        try:
            if await challenge_shown(page):
                return True
        except Exception:
            unread = unread or not page.is_closed()
    return None if unread else False


async def keep_session(context, platform, account):
    """Save with a limit. A session that cannot be saved must not cost a finished command its answer. False means this browser should not be used again."""
    try:
        await asyncio.wait_for(save_session(context, platform, account), CLOSE_SECONDS)
        return True
    except Exception:
        return False


async def close_context(context, platform=None, account=None):
    """Save and close, each with a limit. A session that cannot be saved or closed must not fail the finished command."""
    stage("closing the browser")
    if platform:
        await keep_session(context, platform, account)
    try:
        await asyncio.wait_for(context.close(), CLOSE_SECONDS)
    except Exception:
        pass


def exit_soon(seconds=EXIT_SECONDS):
    """The last resort: this process ends, which lets go of the profile lock, even if something it is waiting for never answers."""
    timer = threading.Timer(seconds, os._exit, args=(0,))
    timer.daemon = True
    timer.start()


def plain_address(address):
    """An address with no query or fragment, which can hold sign-in values. Receipts and failure records keep only this."""
    parts = urlsplit(str(address))
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}" if parts.scheme else ""


async def failure_record(page, exc, path, picture=None, **fields):
    """Which step failed and what kind of controls the page had. Page text, pictures and control labels can hold private messages or
    name people, so they are kept only with page_diagnostics turned on in config.json. Field contents are never kept."""
    wanted = config().get("page_diagnostics") is True
    controls = await page.locator("input,textarea,select,button,[role=button],[role=checkbox],[contenteditable=true],ytcp-button,ytcp-dropdown-trigger,tp-yt-paper-radio-button").evaluate_all(CONTROL_SHAPES)
    if not wanted:
        controls = [{key: value for key, value in control.items() if key not in {"label", "placeholder"}} for control in controls]
    record = {"at": now(), **fields, "error": exc.message if isinstance(exc, VpubError) else failure_message(exc), "url": plain_address(page.url), "controls": controls}
    if wanted:
        record["text"] = (await page.evaluate(PAGE_WORDS))[:18000]
    # First, because a picture waits for a page's fonts: a field run's ran out of time on them, and what the page said matters more than how it looked.
    atomic_json(path, record)
    if wanted and picture:
        await page.screenshot(path=str(picture), timeout=3000, mask=[frame.locator(FORM_FIELDS) for frame in page.frames])


def failure_message(exc):
    """Which step failed and on what kind of control, for whoever diagnoses it: the error's own first lines, without page text, query strings or the words it was looking for."""
    lines = str(exc).splitlines()
    step = [lines[0].strip()] if lines else []
    step += [line.strip(" -") for line in lines[1:] if "waiting for" in line][:1]
    # What got in the way is named by its tag alone, because the rest of that line can quote the page.
    in_the_way = {tag for line in lines[1:] if "intercepts pointer events" in line for tag in re.findall(r"^[\s-]*<([a-z][\w-]*)", line)}
    reasons = sorted({reason for line in lines[1:] for reason in REFUSED_PRESS.findall(line)} | {f"in the way: {tag}" for tag in in_the_way})
    step += [f"[{', '.join(reasons)}]"] if reasons else []
    note = re.sub(r"(https?://[^\s?#'\"]+)[^\s'\"]*", r"\1", " ".join(step))

    def kept(found):
        return found[0] if note[:found.start()].endswith("locator(") and PLAIN_SELECTOR.fullmatch(found[0][1:-1]) else "…"
    note = QUOTED.sub(kept, note)[:240]
    return f"Browser command failed ({type(exc).__name__})" + (f": {note}" if note else ".")


def names_target(request, target):
    """Whether a request mentions this target as a whole ID, plainly or as a whole string inside one of the encoded tokens a site sends.
    Only compared; nothing in the request is kept. Mentioning isn't proof the request acts on it, so an answer's target_at is what binds a change to its target."""
    try:
        text = f"{request.url} {request.post_data or ''}"
    except Exception:
        text = request.url
    if re.search(rf"(?<![\w.-]){re.escape(target)}(?![\w.-])", text):
        return True
    # An encoded string is stored after its length, so the length marks where it ends.
    whole = bytes([len(target)]) + target.encode() if len(target) < 128 else target.encode()
    for token in re.findall(r"[A-Za-z0-9_%=+/-]{16,}", text):
        try:
            if whole in base64.urlsafe_b64decode(unquote(token) + "==="):
                return True
        except ValueError:
            pass
    return False


def target_fields(value, target, path="", depth=0):
    """The names of the fields in a site's answer that hold exactly this target."""
    if depth >= ANSWER_DEPTH:
        return set()
    if isinstance(value, list):
        return set().union(*(target_fields(item, target, path, depth + 1) for item in value))
    if isinstance(value, dict):
        return set().union(*(target_fields(item, target, f"{path}.{key}" if path else key, depth + 1) for key, item in value.items() if key not in ABOUT_THE_PAGE and FIELD_NAME.fullmatch(key)))
    return {path} if value == target else set()


def objected(value, depth=0):
    """Whether a site's answer says it refused, in the ways sites say so: an error field, or a status that says it failed."""
    if depth >= ANSWER_DEPTH:
        return False
    if isinstance(value, list):
        return any(objected(item, depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    if value.get("error") or value.get("errors") or (isinstance(status, str) and re.search("fail|error|denied|reject", status, re.I)):
        return True
    return any(objected(item, depth + 1) for key, item in value.items() if key not in ABOUT_THE_PAGE)


def status_words(value, path="", depth=0):
    """Which status word a site's answer gives at which of its fields. These are the site's own fixed words."""
    if depth >= ANSWER_DEPTH:
        return {}
    if isinstance(value, list):
        return {field: word for item in value for field, word in status_words(item, path, depth + 1).items()}
    if not isinstance(value, dict):
        return {}
    found = {f"{path}.status" if path else "status": value["status"]} if isinstance(value.get("status"), str) and re.fullmatch(r"[\w-]{1,40}", value["status"]) else {}
    for key, item in value.items():
        if key not in ABOUT_THE_PAGE and FIELD_NAME.fullmatch(key):
            found.update(status_words(item, f"{path}.{key}" if path else key, depth + 1))
    return found


def type_names(value, path="", depth=0):
    """Which type name a site's answer gives at which of its fields. Like status words, these are the site's own fixed words."""
    if depth >= ANSWER_DEPTH:
        return {}
    if isinstance(value, list):
        return {field: name for item in value for field, name in type_names(item, path, depth + 1).items()}
    if not isinstance(value, dict):
        return {}
    found = {path: value["__typename"]} if isinstance(value.get("__typename"), str) and FIELD_NAME.fullmatch(value["__typename"]) else {}
    for key, item in value.items():
        if key not in ABOUT_THE_PAGE and FIELD_NAME.fullmatch(key):
            found.update(type_names(item, f"{path}.{key}" if path else key, depth + 1))
    return found


def answer_shape(value, depth=0):
    """The names of an answer's fields, nested, and never what they hold, so a receipt can show what a site really sends."""
    if depth >= ANSWER_DEPTH:
        return []
    if isinstance(value, list):
        return sorted({name for item in value for name in answer_shape(item, depth + 1)})
    if not isinstance(value, dict):
        return []
    names = set()
    for key, item in value.items():
        if FIELD_NAME.fullmatch(key):
            names.add(key)
            if key not in ABOUT_THE_PAGE:
                names |= {f"{key}.{inner}" for inner in answer_shape(item, depth + 1)}
    return sorted(names)


async def write_answers(requests, target):
    """How a page's own POST requests were answered, by shape only: where each went, whether it named this target, and what its answer said of itself."""
    answers = []
    for request in requests:
        if request.method != "POST":
            continue
        answer = {"path": urlsplit(request.url).path, "names_target": names_target(request, target), "target_at": [], "http": None, "fields": [], "shape": [], "statuses": {}, "types": {}, "objected": False}
        try:
            response = await asyncio.wait_for(request.response(), 5)
            answer["http"] = response.status
            body = await asyncio.wait_for(response.json(), 5)
            answer["fields"] = sorted(key for key in body if FIELD_NAME.fullmatch(key))[:12] if isinstance(body, dict) else []
            answer["shape"] = answer_shape(body)[:60]
            answer["target_at"] = sorted(target_fields(body, target))[:8]
            answer["statuses"] = dict(sorted(status_words(body).items())[:8])
            answer["types"] = dict(sorted(type_names(body).items())[:8])
            answer["objected"] = objected(body)
        except Exception:
            pass  # No answer, or one that isn't JSON. The status, if any, is all it says.
        answers.append(answer)
    return answers


def acknowledged(answers, endpoint, success):
    """Positive evidence only: the request that makes this change went to `endpoint` and named this target, and `success` recognises its answer.
    An answer that can't be read, or says anything else, proves nothing. Any other request naming the target must not have objected."""
    named = [answer for answer in answers if answer["names_target"]]
    made = [answer for answer in named if re.fullmatch(endpoint, answer["path"])]
    return (bool(made) and all(answer["http"] and 200 <= answer["http"] < 300 and not answer["objected"] for answer in named)
            and all(success(answer) for answer in made))


async def save_session(context, platform, account):
    # Google replaces its sign-in cookies as they are used, and restoring an older copy can sign the
    # account out. The dedicated Chrome profile already keeps them, so YouTube saves no separate copy.
    if platform == "youtube":
        return
    state = await context.storage_state()
    cookies = [c for c in state["cookies"] if any(on_site(c["domain"].lstrip("."), site) for site in SITES[platform])]
    atomic_json(profile_dir(platform, account).parent / (platform + "-browser-session.json"), {"cookies": cookies})


async def signed_in_page(context, platform, expected=None):
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(10000)
    stage("checking the account")
    watch(page)
    await page.goto(URLS[platform], wait_until="domcontentloaded", timeout=60000)
    for _ in range(45):
        await risk_check(page)
        actual = await identity(page, platform)
        if actual:
            return page, check_identity(actual, expected)
        await asyncio.sleep(1)
    if not (await page.locator('body').inner_text()).strip():
        raise VpubError('page_not_ready', 'The official site returned an empty/loading page; login expiry is not established.', 'Check network/site availability; no upload or edit was attempted.')
    raise VpubError("auth_expired", "Official page did not confirm a signed-in account.", f"Run vpub auth login {platform} --method browser.")
