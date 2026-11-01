"""Bilibili video drafts through the requests its own upload page sends. These use the included bili-cli's request helpers
in a separate process, so the GPL library never loads in CrossCast's process. A draft is never submitted for review."""
import json

from . import adapters
from .core import now

HELPER = '''import json
from pathlib import Path
import sys
import time
from bili_cli import config, guard
from bili_cli.commands import upload
from bili_cli.errors import BiliError
from bili_cli.runtime import run
from bilibili_api import Picture
from bilibili_api import video_uploader as vu
from bilibili_api.utils.network import Api

DRAFTS = "https://member.bilibili.com/x/vupre/web/draft"


def request(cred, path, method="GET", **options):
    return (Api(url=f"{DRAFTS}/{path}", method=method, verify=True, credential=cred, no_csrf=True, **options)
            .update_params(csrf=cred.bili_jct, t=int(time.time()))
            .update_headers(referer="https://member.bilibili.com"))


async def add(cred, draft):
    video = Path(draft["video"])
    predicted = await upload._predict_zone(cred, draft["title"])
    if predicted is None and not draft.get("tid"):
        raise BiliError("invalid_argument", "无法自动预测分区。", hint="在计划里写明 tid。")
    rejected = await upload._check_tags(cred, draft["tags"])
    if rejected:
        raise BiliError("invalid_argument", f"B 站不接受这些标签：{', '.join(rejected)}", detail={"rejected_tags": rejected})
    guard.check("upload")
    await upload._preflight(cred, video)
    part = await upload._upload_file(cred, video, None)
    cover = Path(draft["cover"]) if draft.get("cover") else upload._extract_cover(video)
    meta = {"title": draft["title"], "copyright": 1, "tid": draft.get("tid") or predicted["tid"], "tag": ",".join(draft["tags"]), "desc_format_id": 9999, "desc": draft["description"],
            "recreate": -1, "dynamic": "", "interactive": 0, "act_reserve_create": 0, "no_disturbance": 0, "no_reprint": 0, "subtitle": {"open": 0, "lan": ""}, "dolby": 0, "lossless_music": 0,
            "up_selection_reply": False, "up_close_reply": False, "up_close_danmu": False, "web_os": 1, "is_only_self": 1 if draft["private"] else 0,
            "creation_statement": {"id": upload.STATEMENT_AI if draft["ai_generated"] else upload.STATEMENT_NONE},
            "cover": await vu.upload_cover(Picture.from_file(str(cover)), cred),
            "videos": [{"filename": part["filename"], "cid": part["cid"], "title": draft["title"], "desc": ""}]}
    if predicted:
        meta["human_type2"] = predicted["zone"]
    made = await request(cred, "add", "POST", json_body=True).update_data(**meta, csrf=cred.bili_jct).result
    guard.record("upload", {"draft": draft["title"]}, "ok")
    return {"draft": made}


async def body():
    cred = config.require_credential()
    if sys.argv[1] == "list":
        return {"drafts": await request(cred, "list").result}
    return await add(cred, json.loads(sys.argv[2]))


run(body, deadline=None)  # uploading a large video takes as long as it takes
'''


def same_titled(title):
    """The account's drafts with exactly this title, or None if the list came back in a shape this does not know."""
    rows = adapters.bili(["list"], program=HELPER).get("drafts")
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict) and row.get("title") == title]


def save(plan, options, checkpoint):
    """Saved once and read back. A refusal or an unclear ending goes up as it is, and the receipt blocks a repeat."""
    title = options["title"]
    before = same_titled(title)
    receipt = {"state": "UNKNOWN", "phase": "saving_draft", "draft_title": title, "same_title_before": None if before is None else len(before), "platform": "bilibili", "at": now()}
    checkpoint(receipt)
    draft = {"video": plan["video"], "cover": plan.get("cover"), "title": title, "description": options["description"], "tags": options["tags"], "tid": options.get("tid"),
             "private": options["visibility"] == "private", "ai_generated": options["ai_generated"]}
    adapters.bili(["add", json.dumps(draft, ensure_ascii=False)], timeout=3600, program=HELPER)
    receipt.update(state="SUBMITTED_UNVERIFIED", phase="readback_pending")
    checkpoint(receipt)
    return verify(receipt)


def verify(receipt):
    """Reads only. One more draft with this title than before means this one was saved."""
    rows = same_titled(receipt["draft_title"])
    known = receipt.get("same_title_before")
    if rows is not None and known is not None and len(rows) == known + 1:
        return {**receipt, "state": "DRAFT_SAVED", "phase": "draft", "verified_at": now(), "verification_source": "bili_cli_draft_list",
                "note": "Saved as a draft and read back; nothing was submitted. It is on the 草稿 page of Bilibili's upload manager, which is also where to remove it."}
    return {**receipt, "state": "SUBMITTED_UNVERIFIED", "phase": "readback_pending", "note": "Not read back from Bilibili's draft list. Look on the 草稿 page of its upload manager; do not save it again."}
