"""One independent browser command; JSON stdout and no credential logging."""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
import sys

from .core import VpubError, atomic_json, home, now, read_json, stop_check
from . import browser_runtime as runtime


def action_timeout(action):
    return 1800 if action in {"publish", "resume", "read_all"} else 570 if action in {'edit','verify','operations_batch','chat_unlock','message_sources','discover_works','adopt_work'} else 110


async def dispatch(page, platform, actual, payload):
    from .browser_backends import execute
    try:
        if payload['action'] == 'read_all':
            from .full_read_browser import execute as full_read
            return await full_read(page, platform, actual, payload)
        if payload['action'] in {'discover_works','adopt_work'}:
            from .work_discovery_browser import execute as discover
            return await discover(page,platform,actual,payload)
        if payload['action']=='message_sources':
            from .message_sources import execute as messages
            return await messages(page,platform,actual,payload)
        if payload['action']=='direct_messages':
            from .direct_messages_browser import execute as dm
            return await dm(page,platform,actual,payload)
        if payload['action']=='comment_management':
            from .comment_actions_browser import execute as manage_comment
            return await manage_comment(page,platform,actual,payload)
        if payload['action']=='engagement':
            from .engagement_browser import execute as engage
            return await engage(page,platform,actual,payload)
        if payload['action']=='collections':
            from .collections_browser import execute as collections
            return await collections(page,platform,actual,payload)
        if payload['action']=='operations':
            from .operations_browser import execute as operate
            return await operate(page,platform,actual,payload)
        if payload['action']=='operations_batch':
            from .operations_batch import execute as batch
            return await batch(page,platform,actual,payload)
        return await execute(page, platform, actual, payload)
    except Exception as exc:
        if payload['action'] in {'operations','collections'}:
            # Local diagnostics only; never included in public reports.
            try:
                atomic_json(home()/'operations/errors'/(platform+'-last-error.json'),
                    {'at':now(),'operation':payload.get('operation'),'error':str(exc)[:1800],
                     'url':page.url,'text':(await page.locator('body').inner_text())[:18000],
                     'editors':await page.locator('textarea,input,[contenteditable=true]').evaluate_all('es=>es.map(e=>e.outerHTML.slice(0,5000))')})
            except Exception: pass
        if payload['action']=='edit':
            output=Path(payload['edit_receipt_path'])
            # A closed page must not replace the real error with a diagnostics failure.
            try:
                atomic_json(output.with_suffix('.error.json'),{'type':type(exc).__name__, 'message':str(exc)[:1600], 'url':page.url,
                    'controls':await page.locator('input,textarea,button,[role=button],[contenteditable=true]').evaluate_all('es=>es.map(e=>({tag:e.tagName,type:e.type,placeholder:e.getAttribute("placeholder"),aria:e.getAttribute("aria-label"),text:e.tagName==="BUTTON"?e.innerText:null}))')})
                await page.screenshot(path=str(output.with_suffix('.error.png')),timeout=3000)
            except Exception: pass
        raise


async def run(payload):
    from patchright.async_api import async_playwright
    platform, account = payload["platform"], payload.get("account", "default")
    stop_check(platform)
    if payload['action'] == 'operations_batch':
        from .operations_batch import validate
        validate(payload)
    with runtime.profile_lock(platform, account):
        async with async_playwright() as pw:
            if payload["action"] == "login":
                return await runtime.login(pw, payload)
            if payload['action']=='chat_unlock' and platform!='x':
                raise VpubError('unsupported_operation','Chat unlock currently supports X only.')
            context = await runtime.open_context(pw, platform, account, interactive=payload['action']=='chat_unlock')
            try:
                page, actual = await runtime.signed_in_page(context, platform, payload.get("expected_identity"))
                if payload["action"] == "status":
                    return {"logged_in": True, "identity": actual, "account": account, "method": "browser"}
                if payload['action']=='chat_unlock':
                    from .direct_messages_x import unlock
                    return await unlock(page,actual)
                return await dispatch(page, platform, actual, payload)
            finally:
                try:
                    await runtime.save_session(context, platform, account)
                finally:
                    await context.close()


def main():
    payload = {}
    try:
        payload = read_json(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--job" else json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(asyncio.wait_for(run(payload), timeout=1800))
        out = {"ok": True, "data": result}
        if payload.get("job_path"):
            atomic_json(payload["job_path"], {"state": "LOGGED_IN", "completed_at": now(), **result})
    except Exception as e:
        out = {"ok": False, "error": {"code": e.code if isinstance(e, VpubError) else "browser_error",
               "message": e.message if isinstance(e, VpubError) else f"Browser command failed ({type(e).__name__}).",
               "hint": e.hint if isinstance(e, VpubError) else "Inspect the original draft/work and receipt before retrying; no automatic resubmission."}}
        if payload.get("job_path"):
            atomic_json(payload["job_path"], {"state": "FAILED", "platform": payload.get("platform"), **out})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
