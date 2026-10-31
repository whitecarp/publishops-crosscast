"""Private, persistent browser sessions owned by CrossCast, never host cookies."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .core import VpubError, account_name, atomic_json, home, now, read_json, stop_check

BROWSER_PLATFORMS = {"douyin", "instagram", "x"}
URLS = {"douyin": "https://creator.douyin.com/creator-micro/home",
        "instagram": "https://www.instagram.com/", "x": "https://x.com/home"}
HUMAN_LABELS = re.compile(r"安全验证|请完成验证|滑块验证|操作频繁|验证是你本人|Confirm it's you|Verify your identity|Authenticate your account|Confirm your identity|Unusual activity", re.I)


def profile_dir(platform, account="default"):
    if platform not in BROWSER_PLATFORMS:
        raise VpubError("invalid_platform", "Persistent browser supports douyin, instagram and x.")
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


async def risk_check(page):
    challenge=page.locator('iframe[src*="/verifycenter/captcha/"]')
    if await visible(challenge) or await visible(page.get_by_text(HUMAN_LABELS)):
        raise VpubError("human_verification_required", "The platform requires human verification.", "Run auth login for this platform and complete the official verification; inspect any existing draft before retrying.")


async def identity(page, platform):
    """Read the signed-in account from its own navigation, not arbitrary links."""
    if platform == "x":
        link = await visible(page.locator('a[data-testid="AppTabBar_Profile_Link"]'))
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
    try:
        context = await pw.chromium.launch_persistent_context(
            str(path), channel="chrome", headless=not interactive,
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


async def login(pw, payload):
    platform, account = payload["platform"], payload["account"]
    job = Path(payload["job_path"])
    context = await open_context(pw, platform, account, interactive=True)
    deadline = time.monotonic() + 900
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URLS[platform], wait_until="domcontentloaded", timeout=60000)
        atomic_json(job, {"state": "LOGIN_PENDING", "platform": platform, "login_url": URLS[platform],
                          "expires_epoch": time.time() + 900,
                          "hint": "Complete official login in the CrossCast Chrome window. No password, SMS or CAPTCHA is entered by the CLI."})
        while time.monotonic() < deadline:
            if page.is_closed():
                raise VpubError("login_cancelled", "Login window was closed before account verification.")
            actual = await identity(page, platform)
            if actual:
                # Wait for the login redirect to settle and prove a fresh load.
                await asyncio.sleep(3)
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(2)
                if await identity(page, platform) != actual:
                    continue
                await save_session(context, platform, account)
                atomic_json(profile_dir(platform, account).parent / (platform + "-identity.json"),
                            {"identity": actual, "verified_at": now(), "method": "browser"})
                return {"logged_in": True, "platform": platform, "account": account,
                        "identity": actual, "method": "browser"}
            await asyncio.sleep(2)
        raise VpubError("login_timeout", "Login was not completed within 15 minutes; saved profile is retained.")
    finally:
        await context.close()


async def save_session(context, platform, account):
    state = await context.storage_state()
    domains = {"x": ("x.com", "twitter.com"), "instagram": ("instagram.com",),
               "douyin": ("douyin.com", "bytedance.com")}[platform]
    cookies = [c for c in state["cookies"] if any(c["domain"].lstrip(".") == d or c["domain"].endswith("." + d) for d in domains)]
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
