"""Private, persistent browser sessions owned by CrossCast, never host cookies."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

from .core import VpubError, account_name, atomic_json, home, now, read_json

BROWSER_PLATFORMS = {"douyin", "instagram", "x", "youtube"}
URLS = {"douyin": "https://creator.douyin.com/creator-micro/home",
        "instagram": "https://www.instagram.com/", "x": "https://x.com/home",
        "youtube": "https://studio.youtube.com/"}
HUMAN_LABELS = re.compile(r"安全验证|請完成驗證|请完成验证|滑块验证|滑塊驗證|操作频繁|操作頻繁|验证是你本人|驗證是你本人|确认是你本人|確認是你本人|验证你的身份|驗證你的身分|确认你的身份|確認你的身分"
                          r"|Confirm it's you|Verify it's you|Verify your identity|Authenticate your account|Confirm your identity|Unusual activity"
                          # A locked account's recovery page. A person answers it; CrossCast never presses anything on it.
                          r"|Choose a way to recover|Get help from friends|选择恢复方式|選擇復原方式|向好友求助|向朋友求助", re.I)
# Addresses that only ever mean the platform is asking for a check, whatever the page says.
CHALLENGE_ADDRESSES = {"instagram.com": r"/(challenge|checkpoint|auth_platform|accounts/(suspended|disabled))(/|$)",
                       "x.com": r"/account/access(/|$)",
                       "google.com": r"(/v\d)?/signin(/v\d)?/(challenge|rejected)(/|$)"}
# Where other people's words show. A viewer writing "Confirm it's you" in a comment is not the platform asking,
# and it must not be able to stop the whole tool. A place missing from this list means a stop a person clears, never a missed check.
OTHER_PEOPLES_WORDS = ('article, li, [role="article"], [role="listitem"], [role="row"], [role="gridcell"], [data-testid="cellInnerDiv"], [class*="comment" i], '
                       'ytcp-comment-thread, ytcp-comment, ytd-comment-thread-renderer, ytd-comment-view-model, ytcp-video-row, ytd-watch-metadata')
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


def on_site(host, site):
    return host == site or host.endswith("." + site)


def platform_at(address):
    host = urlsplit(str(address)).hostname or ""
    return next((name for name, sites in SITES.items() if any(on_site(host, site) for site in sites)), "unknown")


def challenge_address(address):
    parts = urlsplit(str(address))
    return any(on_site(parts.hostname or "", site) and re.match(path, parts.path) for site, path in CHALLENGE_ADDRESSES.items())


async def challenge_shown(page):
    """Whether the platform itself is asking for a check or an account recovery on this page."""
    if challenge_address(page.url) or await visible(page.locator('iframe[src*="/verifycenter/captcha/"]')):
        return True
    for item in await page.get_by_text(HUMAN_LABELS).all():
        if await item.is_visible() and not await item.evaluate("(e, others) => !!e.closest(others)", OTHER_PEOPLES_WORDS):
            return True
    return False


async def risk_check(page):
    if await challenge_shown(page):
        from .safety import pushed_back
        # Recorded where it is seen: a batch or a multi-step read catches the error below so it can report its other steps.
        pushed_back(platform_at(page.url), "human_verification_required")
        raise VpubError("human_verification_required", "The platform requires human verification.", "Run auth login for this platform and complete the official verification, then vpub stop --clear; inspect any existing draft before retrying.")


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
        if not link:
            link = await visible(page.locator('a').filter(has=page.locator('img[alt*="profile picture"], img[alt*="的头像"], img[alt*="的大頭貼"]')))
        if link:
            path = urlsplit(await link.get_attribute("href") or "").path.strip("/")
            if re.fullmatch(r"[A-Za-z0-9_.]{1,30}", path) and path not in {"accounts", "explore", "reels"}:
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
            if await any_challenge(pages):
                if not asked:
                    asked = True
                    atomic_json(job, {"state": "VERIFICATION_REQUIRED", **waiting, "window_opened": True,
                                      "hint": "The platform is asking for a check or account recovery. Finish it yourself in the CrossCast Chrome window; the CLI never answers it."})
                await asyncio.sleep(2)
                continue
            for page in pages:
                try:
                    actual = await identity(page, platform)
                    if not actual:
                        continue
                    # Wait for the login redirect to settle and prove a fresh load.
                    await asyncio.sleep(3)
                    await page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    if await any_challenge(pages) or await identity(page, platform) != actual:
                        continue
                except Exception as exc:
                    if isinstance(exc, VpubError):
                        raise
                    continue  # This tab closed or moved on while it was being read; the next pass looks again.
                await keep_session(context, platform, account)
                remember_identity(platform, account, actual)
                result = {"logged_in": True, "platform": platform, "account": account, "identity": actual, "method": "browser"}
                if (home() / "STOP.json").exists():
                    result["next"] = "CrossCast is still stopped because of a platform's warning. Once its check is done, run vpub stop --clear."
                return result
            await asyncio.sleep(2)
        raise VpubError("login_timeout", "Login was not completed within 15 minutes; saved profile is retained.")
    finally:
        await close_context(context)


async def any_challenge(pages):
    for page in pages:
        try:
            if await challenge_shown(page):
                return True
        except Exception:
            pass  # This tab closed or moved on while it was being read.
    return False


async def keep_session(context, platform, account):
    """Save with a limit. A session that cannot be saved must not cost a finished command its answer. False means this browser should not be used again."""
    try:
        await asyncio.wait_for(save_session(context, platform, account), CLOSE_SECONDS)
        return True
    except Exception:
        return False


async def close_context(context, platform=None, account=None):
    """Save and close, each with a limit. A session that cannot be saved or closed must not fail the finished command."""
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


def failure_message(exc):
    """Which step failed and on which control, for whoever diagnoses it: the error's own first lines, without page text or query strings."""
    lines = str(exc).splitlines()
    step = [lines[0].strip()] if lines else []
    step += [line.strip(" -") for line in lines[1:] if "waiting for" in line][:1]
    note = re.sub(r"(https?://[^\s?#'\"]+)[^\s'\"]*", r"\1", " ".join(step))[:240]
    return f"Browser command failed ({type(exc).__name__})" + (f": {note}" if note else ".")


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
