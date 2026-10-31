"""Bounded Douyin browser adapter using pinned MIT-licensed SAU form helpers.

We deliberately do not call SAU's publish/retry/login-verification loops.
One publication click, then read-back; ambiguous outcomes never auto-retry.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from .core import SAU_COMMIT, VpubError, atomic_json, home, now, read_json, stop_check

UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE = "https://creator.douyin.com/creator-micro/content/manage"


def load_helpers():
    root = home() / "social-auto-upload"
    if not (root / ".git").exists():
        raise VpubError("setup_required", "Douyin backend is not installed.", "Run vpub setup.")
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    clean = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"], capture_output=True)
    if head != SAU_COMMIT or clean.returncode:
        raise VpubError("backend_changed", "Pinned Douyin source has changed; inspect it before publishing.")
    sys.path.insert(0, str(root))
    from uploader.douyin_uploader.main import DouYinVideo
    return DouYinVideo


async def risk_check(page):
    # Check visible UI only. Never request/read/submit SMS or CAPTCHA codes.
    for label in ("安全验证", "请完成验证", "操作频繁", "滑块验证"):
        loc = page.get_by_text(label, exact=True)
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                raise VpubError("captcha_required", "Douyin requests human verification or a cooldown.", "Complete verification yourself in the official creator center; do not automatically retry.")


async def logged_in(page):
    for label in ("手机号登录", "扫码登录"):
        loc = page.get_by_text(label, exact=True)
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                return False
    upload = "creator-micro/content/" in page.url and await page.locator('input[type="file"]').count() > 0
    dashboard = "/creator-micro/home" in page.url and await page.get_by_text("作品发布", exact=True).count() > 0
    return bool(upload or dashboard)


async def work_links(page, title=None):
    # Only DOM-visible anchors; no private API interception or invented IDs.
    rows = await page.locator("a[href]").evaluate_all("""els => els.map(a => ({
        href: a.href,
        text: (a.closest('tr,article,[class*="card"]') || a).innerText || ''
    }))""")
    found = {}
    for row in rows:
        match = re.fullmatch(r"https://(?:www\.)?douyin\.com/video/(\d+)(?:\?.*)?", row["href"])
        if match and (title is None or title in row["text"]):
            found[match[1]] = f"https://www.douyin.com/video/{match[1]}"
    return found


def ai_declaration(options):
    """Douyin self-declaration for AI content; ordinary videos are published without one."""
    return "内容由AI生成" if options["ai_generated"] else None


async def declare_ai(page):
    await page.get_by_text("自主声明", exact=True).click(timeout=10000)
    dialog = page.locator(".semi-modal-content").filter(has_text="请选择声明类型").first
    await dialog.wait_for(state="visible", timeout=10000)
    option = dialog.locator("label.semi-radio").filter(has_text="内容由AI生成")
    await option.click(timeout=10000)
    selected = option.locator('input[type="radio"]')
    if await selected.count() != 1 or not await selected.is_checked():
        raise VpubError("declaration_unverified", "Could not verify the AI-content declaration.")
    await dialog.get_by_role("button", name="确定", exact=True).click(timeout=10000)
    await dialog.wait_for(state="hidden", timeout=10000)


async def run(payload):
    from patchright.async_api import async_playwright
    action, account = payload["action"], payload["account"]
    state_path = home() / "accounts" / account / "douyin-session.json"
    if action != "login" and not state_path.exists():
        raise VpubError("not_logged_in", "Douyin login is missing.", "vpub auth login douyin")
    helper_class = load_helpers() if action == "publish" else None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=payload.get("headless", True))
        context = await browser.new_context(storage_state=str(state_path) if state_path.exists() else None)
        page = await context.new_page()
        page.set_default_timeout(20000)
        try:
            await page.goto(UPLOAD, wait_until="domcontentloaded", timeout=60000)
            if action == "login":
                deadline = time.monotonic() + 600
                job = Path(payload["job_path"]) if payload.get("job_path") else None
                saved_qr = False
                while time.monotonic() < deadline:
                    if await logged_in(page):
                        atomic_json(state_path, await context.storage_state())
                        return {"logged_in": True, "account": account, "method": "browser_qr", "identity": "default-session"}
                    if job and not saved_qr:
                        qr = page.locator('div#animate_qrcode_container img, div[class*="animate_qrcode_container"] img, div[class*="scan_qrcode_login_content"] img, img[aria-label="二维码"]').first
                        if await qr.count() and await qr.is_visible():
                            qr_path = job.with_suffix(".png")
                            await qr.screenshot(path=str(qr_path))
                            atomic_json(job, {"state": "LOGIN_PENDING", "platform": "douyin", "qr_image": str(qr_path),
                                              "hint": "Scan with Douyin. Complete any verification in the official app.",
                                              "created_at": now(), "expires_epoch": time.time() + 580})
                            saved_qr = True
                    if job:
                        try:
                            await risk_check(page)
                        except VpubError:
                            atomic_json(job, {"state": "VERIFICATION_REQUIRED", "platform": "douyin",
                                              "hint": "Complete the platform's verification yourself. If it cannot be completed on the phone, request a one-time visible verification session.",
                                              "expires_epoch": time.time() + max(0, deadline - time.monotonic())})
                            # Preserve partial cookies for a later human session.
                            atomic_json(state_path.with_name("douyin-pending-session.json"), await context.storage_state())
                            raise
                    await asyncio.sleep(2)
                raise VpubError("login_timeout", "Douyin login timed out; run login again when ready.")
            await page.wait_for_timeout(1500)
            await risk_check(page)
            if not await logged_in(page):
                raise VpubError("auth_expired", "Douyin session expired or the expected upload UI is unavailable.", "Run auth login douyin.")
            if action == "status":
                return {"logged_in": True, "account": account, "identity": "default-session",
                        "note": "Upload page accessible; first live run must confirm the displayed account."}
            plan = payload["plan"]
            await page.goto(MANAGE, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1500)
            await risk_check(page)
            if action == "verify":
                matches = await work_links(page, plan.get("title"))
                if plan.get("id") in matches:
                    return {**plan, "state": "SUBMISSION_VERIFIED", "note": "Matching work link found in creator center; review/public playback checked separately."}
                return {**plan, "state": "SUBMITTED_UNVERIFIED", "review_url": MANAGE,
                        "note": "No matching visible work link on this page. Inspect creator center; never re-upload automatically."}
            before = set(await work_links(page))
            await page.goto(UPLOAD, wait_until="domcontentloaded", timeout=60000)
            stop_check("douyin")
            await page.locator('input[type="file"]').first.set_input_files(plan["video"])
            # There is one bounded wait, not upstream's unbounded re-upload loop.
            await page.wait_for_url(re.compile(r"/content/(publish|post/video)"), timeout=120000)
            opts = plan["options"]
            helper = helper_class(opts["title"], plan["video"], opts["tags"], 0, str(state_path),
                                  thumbnail_landscape_path=plan.get("cover"), desc=opts["description"],
                                  debug=False, headless=False, declaration=ai_declaration(opts))
            await helper.fill_title_and_description(page, opts["title"], opts["description"], opts["tags"])
            ready = page.locator('[class^="long-card"] div:has-text("重新上传")').first
            await ready.wait_for(state="visible", timeout=900000)
            await risk_check(page)
            if plan.get("cover"):
                # Cover UI varies; v0.1 intentionally rejects custom covers until
                # that account's crop/confirm flow has been integration-tested.
                raise VpubError("unsupported_cover", "Douyin v0.1 does not accept a custom cover.")
            if opts["ai_generated"]:
                await declare_ai(page)
            title = await page.locator('input[placeholder*="填写作品标题"]').first.input_value()
            if title != opts["title"]:
                raise VpubError("metadata_mismatch", "Douyin title differs from the approved manifest.")
            public = page.get_by_role("radio", name="公开", exact=True)
            if await public.count() != 1 or not await public.is_checked():
                raise VpubError("visibility_unverified", "The public visibility setting could not be confirmed. Finish through the native browser after inspecting the existing draft; do not re-upload.")
            await risk_check(page)
            stop_check("douyin")
            button = page.get_by_role("button", name="发布", exact=True)
            receipt_path = home() / "browser-receipts" / (plan["confirmation"] + "-douyin.json")
            atomic_json(receipt_path, {"state": "UNKNOWN", "title": opts["title"], "review_url": MANAGE})
            await button.click(timeout=20000)  # exactly one final submission attempt
            try:
                await page.wait_for_url(re.compile(r"/content/manage"), timeout=60000)
            except Exception:
                return {"state": "UNKNOWN", "title": opts["title"], "review_url": MANAGE,
                        "note": "Submit was attempted; no confirmation. Do not click or upload again."}
            await page.wait_for_timeout(2000)
            matches = await work_links(page, opts["title"])
            new = {k: v for k, v in matches.items() if k not in before}
            atomic_json(state_path, await context.storage_state())
            if len(new) == 1:
                video_id, url = next(iter(new.items()))
                receipt = {"state": "SUBMISSION_VERIFIED", "id": video_id, "url": url,
                        "title": opts["title"], "note": "A new matching work link appeared; review/public playback checked separately."}
                atomic_json(receipt_path, receipt)
                return receipt
            return {"state": "SUBMITTED_UNVERIFIED", "title": opts["title"], "review_url": MANAGE,
                    "note": "Returned to management page, but no unique new video ID. Inspect the original work; do not upload again."}
        finally:
            await context.close()
            await browser.close()


def main():
    job = None
    try:
        payload = read_json(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--job" else json.loads(sys.stdin.read())
        job = Path(payload["job_path"]) if payload.get("job_path") else None
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(asyncio.wait_for(run(payload), timeout=1700))
        out = {"ok": True, "data": result}
        if job:
            atomic_json(job, {"state": "LOGGED_IN", "platform": "douyin", "completed_at": now(), **result})
    except VpubError as e:
        out = {"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}}
        if job and e.code != "captcha_required":
            atomic_json(job, {"state": "FAILED", "platform": "douyin", **out})
    except Exception as e:
        out = {"ok": False, "error": {"code": "browser_error", "message": f"Douyin browser step failed ({type(e).__name__}).",
                                       "hint": "Check login, Chrome and the current page layout; inspect any original submission before retrying."}}
        if job:
            atomic_json(job, {"state": "FAILED", "platform": "douyin", **out})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
