"""One independent browser command; JSON stdout and no credential logging."""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
import sys

from .core import VpubError, atomic_json, home, now, read_json, stop_check
from . import browser_runtime as runtime, progress


def action_timeout(action):
    return 1800 if action in {"publish", "resume", "read_all"} else 570 if action in {'edit','verify','operations_batch','chat_unlock','message_sources','discover_works','adopt_work'} else 110


async def dispatch(page, platform, actual, payload):
    from .browser_backends import execute
    try:
        if payload['action'] == 'read_all':
            from .adapters import paid_feature
            return await paid_feature('full_read_browser', 'Complete reads').execute(page, platform, actual, payload)
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
        # A closed page must not replace the real error with a diagnostics failure.
        try:
            if payload['action'] in {'operations','collections'}:
                await runtime.failure_record(page,exc,home()/'operations/errors'/(platform+'-last-error.json'),operation=payload.get('operation'))
            elif payload['action']=='edit':
                output=Path(payload['edit_receipt_path'])
                await runtime.failure_record(page,exc,output.with_suffix('.error.json'),picture=output.with_suffix('.error.png'))
        except Exception: pass
        raise


async def run(payload):
    from patchright.async_api import async_playwright
    platform, account = payload["platform"], payload.get("account", "default")
    progress.attach(payload)
    stop_check(platform, signing_in=payload["action"] == "login")
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
                await runtime.close_context(context, platform, account)


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
               "message": e.message if isinstance(e, VpubError) else runtime.failure_message(e),
               "hint": e.hint if isinstance(e, VpubError) else "Inspect the original draft/work and receipt before retrying; no automatic resubmission."}}
        if payload.get("job_path"):
            atomic_json(payload["job_path"], {"state": "FAILED", "platform": payload.get("platform"), **out})
    # On a hidden desktop there is no console to answer on.
    if payload.get("result_path"):
        atomic_json(payload["result_path"], out)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
