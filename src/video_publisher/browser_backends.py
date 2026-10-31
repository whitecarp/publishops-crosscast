"""CLI browser execution with durable phases and exactly one final submit."""
from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from .core import VpubError, atomic_json, file_sha, home, now, read_json, stop_check
from .browser_runtime import risk_check, unique_visible, visible

MANAGE = "https://creator.douyin.com/creator-micro/content/manage"


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def work_url(platform, identity, work_id):
    patterns = {"x": r"\d+", "douyin": r"\d+", "instagram": r"[A-Za-z0-9_-]+"}
    if not re.fullmatch(patterns[platform], str(work_id)):
        raise VpubError("invalid_id", "Use a platform work ID, not an arbitrary URL.")
    if platform == "x":
        return f"https://x.com/{identity}/status/{work_id}"
    if platform == "douyin":
        return f"https://www.douyin.com/video/{work_id}"
    return f"https://www.instagram.com/reel/{work_id}/"


async def click_named(scope, name):
    loc = scope.get_by_role("button", name=re.compile(name, re.I))
    target = await unique_visible(loc, name)
    await target.click(timeout=15000)
    return target


async def wait_until(page, predicate, *, seconds=120, label="page state"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await risk_check(page)
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(1)
    raise VpubError("processing_timeout", f"Timed out waiting for {label}; existing upload retained.")


async def upload_file(page, path, trigger=None, accept=None):
    inputs = page.locator('input[type="file"]')
    candidates = []
    for item in await inputs.all():
        value = (await item.get_attribute("accept") or "").lower()
        if accept is None or accept in value:
            candidates.append(item)
    if len(candidates) == 1:
        await candidates[0].set_input_files(str(path), timeout=60000)
    elif trigger:
        async with page.expect_file_chooser(timeout=15000) as chosen:
            await trigger.click(timeout=15000)
        await (await chosen.value).set_files(str(path), timeout=60000)
    else:
        raise VpubError("layout_changed", "Could not identify a unique file input for this upload.")


class Transaction:
    def __init__(self, platform, identity, plan, resume=False):
        self.platform, self.identity, self.plan = platform, identity, plan
        self.options = plan["options"]
        self.path = home() / "browser-receipts" / (plan["confirmation"] + "-" + platform + ".json")
        if self.path.exists() and not resume:
            raise VpubError("attempt_exists", "This independent browser attempt already has a durable receipt.", "Inspect/verify that attempt. Never reset it to upload or submit again.")
        self.receipt = {"state": "PREPARING", "method": "browser", "platform": platform,
                        "identity": identity, "title": self.options["title"], "phase": "initial",
                        "video_sha256": plan["video_sha256"], "final_submit_attempted": False,
                        "media_selected": False, "started_at": now()}
        if resume:
            saved = read_json(self.path)
            if saved.get('video_sha256') != plan['video_sha256'] or saved.get('identity') != identity:
                raise VpubError('receipt_conflict', 'Resume must use the same account and media.')
            self.receipt = saved
        elif platform=='douyin' and self.options.get('chapters'):
            self.receipt['pending_chapters']={'chapters':self.options['chapters'],
                                             'duration_seconds':self.options['duration_seconds']}
            self.receipt['chapters_state']='PENDING_WORK_ID'

    def save(self, **changes):
        self.receipt.update(changes, updated_at=now())
        atomic_json(self.path, self.receipt)
        return dict(self.receipt)

    def media(self):
        self.validate_assets()
        src = Path(self.plan["video"])
        dst = src.with_name("crosscast-" + self.plan["video_sha256"][:16] + src.suffix)
        if not dst.exists():
            shutil.copyfile(src, dst)
        if file_sha(dst) != self.plan["video_sha256"]:
            raise VpubError("source_changed", "Named upload snapshot differs from the planned video.")
        self.save(phase="selecting_media", media_selected=True, upload_filename=dst.name)
        return dst

    def validate_assets(self):
        if file_sha(self.plan["video"]) != self.plan["video_sha256"]:
            raise VpubError("source_changed", "Snapshot changed before browser upload.")
        for asset in self.plan.get("covers", {}).get(self.platform, {}).values():
            if file_sha(asset["path"]) != asset["sha256"]:
                raise VpubError("source_changed", "A cover snapshot changed before upload.")
        if self.plan.get("cover") and file_sha(self.plan["cover"]) != self.plan["cover_sha256"]:
            raise VpubError("source_changed", "Cover snapshot changed before upload.")

    def cover(self, role="landscape"):
        asset = self.plan.get("covers", {}).get(self.platform, {}).get(role)
        shared = self.plan.get("cover") if role == "landscape" else None
        if not asset and not shared and self.platform == 'instagram' and role == 'landscape':
            asset = self.plan.get('covers', {}).get('instagram', {}).get('portrait')
        return asset["path"] if asset else shared

    async def submit(self, page, button):
        if self.options.get("submission_mode") == "draft_only":
            raise VpubError("draft_only", "Final submission is forbidden in draft-only mode.")
        if self.receipt["final_submit_attempted"]:
            raise VpubError("already_submitted", "A final submission was already attempted.")
        await risk_check(page)
        stop_check(self.platform)
        self.save(state="UNKNOWN", phase="submitting", final_submit_attempted=True, review_url=page.url)
        # Persist BEFORE clicking. A timeout must never cause another click.
        await button.click(timeout=20000)


async def links(page, platform, identity):
    rows = await page.locator("a[href]").evaluate_all("es => es.map(e => ({url:e.href,text:(e.closest('article,tr') || e).innerText || e.querySelector('img')?.alt || ''}))")
    result = {}
    for row in rows:
        u = urlsplit(row["url"])
        if platform == "x":
            match = re.fullmatch(r"/" + re.escape(identity) + r"/status/(\d+)", u.path, re.I) if u.hostname in {"x.com", "www.x.com"} else None
        elif platform == "instagram":
            match = re.fullmatch(r"/(?:" + re.escape(identity) + r"/)?(?:reel|p)/([\w-]+)/?", u.path, re.I) if u.hostname == "www.instagram.com" else None
        else:
            match = re.fullmatch(r"/video/(\d+)", u.path) if u.hostname in {"www.douyin.com", "douyin.com"} else None
            if not match and u.hostname == "creator.douyin.com":
                from urllib.parse import parse_qs
                mid = parse_qs(u.query).get("mid", [""])[0]
                if mid.isdigit():
                    result[mid] = {"id": mid, "url": work_url(platform, identity, mid), "text": row["text"]}
        if match:
            k = match[1]
            result[k] = {"id": k, "url": work_url(platform, identity, k), "text": row["text"]}
    return result


async def works(page, platform, identity):
    url = MANAGE if platform == "douyin" else f"https://www.instagram.com/{identity}/" if platform == "instagram" else f"https://x.com/{identity}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    for _ in range(10):
        await page.wait_for_timeout(1000)
        await risk_check(page)
        found = await links(page, platform, identity)
        if found:
            return found
        if platform == 'douyin':
            # Creator Center cards use click handlers rather than hrefs.
            # Opening a work detail is read-only; never enter edit/delete here.
            titles = page.locator('[class^="info-title-text-"]')
            if await titles.count():
                for title in (await titles.all())[:10]:
                    text = await title.inner_text()
                    card = title.locator('xpath=ancestor::div[starts-with(@class,"video-card-content-")]')
                    if '审核中' in await card.inner_text():
                        continue  # Review cards do not open a detail popup yet.
                    async with page.expect_popup(timeout=15000) as opened:
                        await title.click()
                    detail = await opened.value
                    try:
                        await detail.wait_for_url(re.compile(r'/work-detail/\d+'), timeout=15000)
                        match = re.fullmatch(r'/creator-micro/work-management/work-detail/(\d+)', urlsplit(detail.url).path)
                        if match and urlsplit(detail.url).hostname == 'creator.douyin.com':
                            k = match[1]
                            found[k] = {'id': k, 'url': work_url(platform, identity, k), 'text': text}
                    finally:
                        await detail.close()
                return found
    return {}


async def verify(page, platform, identity, receipt):
    if receipt.get('identity') and receipt['identity'].casefold() != identity.casefold():
        raise VpubError('wrong_account', 'Original receipt belongs to another signed-in account.')
    if not receipt.get('id') and receipt.get('final_submit_attempted') and 'before_ids' in receipt:
        found = await works(page, platform, identity)
        matches = [v for k,v in found.items() if k not in receipt['before_ids'] and
                   receipt.get('title') and normalize(receipt['title']) in normalize(v['text'])]
        if len(matches) == 1:
            receipt = {**receipt, 'id':matches[0]['id'], 'url':matches[0]['url']}
    if not receipt.get("id"):
        if platform == 'douyin' and receipt.get('title'):
            cards = page.locator('[class^="video-card-content-"]').filter(has_text=receipt['title'])
            if await cards.count() == 1 and '审核中' in await cards.inner_text():
                return {**receipt, 'state':'SUBMITTED_UNVERIFIED', 'phase':'platform_review',
                        'platform_status':'UNDER_REVIEW', 'review_url':MANAGE, 'error':None,
                        'note':'Matching submitted card is under platform review; verify later, never reupload.'}
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "No durable work ID; inspect the original work, never reupload automatically."}
    url = work_url(platform, identity, receipt["id"])
    owned = None
    if platform == 'instagram':
        # Current Reel pages are not articles. Confirm ownership from this
        # account's grid first, then read the actual permalink and caption.
        owned = (await works(page, platform, identity)).get(str(receipt['id']))
    if platform == "douyin":
        found = await works(page, platform, identity)
        present = str(receipt["id"]) in found
    else:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if platform == 'x':
            try:
                await page.locator('[data-testid="tweetText"]').filter(has_text=receipt.get('title','')).first.wait_for(timeout=20000)
            except Exception:
                pass
        else:
            await page.wait_for_timeout(4000)
        await risk_check(page)
        present = str(receipt["id"]) in await links(page, platform, identity)
        if platform == "instagram":
            # An arbitrary video/player or the browser address is not evidence.
            author = await visible(page.locator('a[href="/'+identity+'/"]'))
            present = bool(owned and author and await visible(page.locator('video')) and
                           urlsplit(page.url).path.rstrip('/').endswith('/'+receipt['id']))
        expected = receipt.get("expected_text")
        if expected:
            actual = normalize(await page.locator("body").inner_text())
            present = present and normalize(expected) in actual
    if platform == 'douyin' and receipt.get('expected_text'):
        present = present and receipt['expected_text'] in found.get(str(receipt['id']), {}).get('text', '')
    return {**receipt, "url": url, "state": "SUBMISSION_VERIFIED" if present else "SUBMITTED_UNVERIFIED",
            'phase':'complete' if present else 'readback_pending', 'error':None if present else receipt.get('error'),
            "verification_source": "independent_browser_dom", "verified_at": now(),
            "note": "Work and expected text verified on the signed-in page; cover pixels and anonymous playback are separate." if present else "Expected work/text not yet verified; do not upload again."}


async def execute(page, platform, identity, payload):
    action = payload["action"]
    if action == 'edit':
        from .browser_edits import execute as edit
        return await edit(page, platform, identity, payload)
    if action == "works":
        return {"identity": identity, "items": list((await works(page, platform, identity)).values()),
                "scope": "Visible first page only; not a complete account export."}
    if action == "verify":
        result=await verify(page, platform, identity, payload["receipt"])
        return await complete_pending_chapters(page,platform,identity,result)
    if action not in {"publish", "resume"}:
        raise VpubError("unsupported_action", "Use status, works, publish or verify.")
    tx = Transaction(platform, identity, payload["plan"], resume=action == 'resume')
    if tx.receipt['final_submit_attempted']:
        result=await verify(page, platform, identity, tx.receipt)
        return tx.save(**await complete_pending_chapters(page,platform,identity,result))
    if action == 'resume' and tx.receipt['media_selected'] and platform != 'x':
        raise VpubError('inspect_existing_draft', 'An upload already started. Resume requires inspecting that existing draft; uploading again is prohibited.')
    tx.save(state='PREPARING', error=None, user_action_required=False)
    try:
        tx.validate_assets()
        from . import browser_forms
        result=await getattr(browser_forms, "publish_" + platform)(page, tx)
        return tx.save(**await complete_pending_chapters(page,platform,identity,result))
    except Exception as e:
        # Local diagnostics are deliberately outside the repository. No cookies,
        # form values, private URLs or raw page source are included in stdout.
        debug = home() / 'browser-debug' / (payload['plan']['confirmation']+'-'+platform)
        debug.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(debug/'last-page.png'))
            controls = await page.locator('button,input,textarea,[contenteditable=true]').evaluate_all("es=>es.map(e=>({tag:e.tagName,type:e.type,placeholder:e.getAttribute('placeholder'),label:e.getAttribute('aria-label'),text:e.tagName==='BUTTON'?e.innerText:null}))")
            atomic_json(debug/'controls.json', controls)
        except Exception:
            pass
        human = isinstance(e, VpubError) and e.code in {"human_verification_required", "auth_expired"}
        state = "WAITING_FOR_USER" if human else "UNKNOWN" if tx.receipt["final_submit_attempted"] else "PREPARATION_INCOMPLETE"
        return tx.save(state=state, blocked_phase=tx.receipt['phase'], phase="blocked", review_url=page.url,
                       error={"code": e.code if isinstance(e, VpubError) else "browser_step_failed",
                              "message": e.message if isinstance(e, VpubError) else type(e).__name__},
                       user_action_required=human,
                       hint="Inspect the existing draft/work. Progress is saved; no automatic upload or submit retry.")


async def complete_pending_chapters(page,platform,identity,receipt):
    pending=receipt.get('pending_chapters')
    if platform!='douyin' or not pending or receipt.get('chapters_state')=='VERIFIED': return receipt
    if not receipt.get('id') or receipt.get('state')!='SUBMISSION_VERIFIED':
        return {**receipt,'chapters_state':'PENDING_WORK_ID','next':'Run vpub verify for this receipt after platform review; chapters are not yet applied.'}
    from .browser_edits import execute as edit
    path=home()/'edits'/('douyin-chapters-'+receipt['id']+'.json')
    if path.exists() and read_json(path).get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'}:
        return {**receipt,'chapters_state':'SAVE_UNVERIFIED','next':'Inspect the existing saved chapters; no automatic second Save.'}
    if path.exists() and read_json(path).get('state')=='EDIT_VERIFIED':
        return {**receipt,'chapters_state':'VERIFIED','chapters_receipt':read_json(path)}
    try:
        result=await edit(page,platform,identity,{'receipt':receipt,
            'patch':{**pending,'expected_identity':identity},'edit_receipt_path':str(path)})
        return {**receipt,'chapters_state':'VERIFIED' if result['state']=='EDIT_VERIFIED' else 'SAVE_UNVERIFIED','chapters_receipt':result}
    except Exception as exc:
        return {**receipt,'chapters_state':'INCOMPLETE','chapters_error':exc.code if isinstance(exc,VpubError) else type(exc).__name__,
                'next':'The video exists. Fix/retry only the chapter step; do not upload the video again.'}
