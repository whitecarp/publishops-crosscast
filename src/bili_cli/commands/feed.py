"""bili feed: what happened to my account — the input side of an autonomous agent loop.

Each reply / mention carries `reply_with`, a ready-to-run command, so an agent
never has to work out comment-area IDs, root and parent itself.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Optional

import typer
from bilibili_api import session

from .. import config, output
from ..errors import BiliError
from ..runtime import iso_time, run
from .comment import TYPE_NAMES

app = typer.Typer(help="首页推荐、关注动态、消息通知（未读数、回复、@、赞、系统通知；inbox 一次看完）。", no_args_is_help=True)

CURSOR_OPT = typer.Option(None, "--cursor", help="翻页游标，取上一页返回的 next_cursor。")
LIMIT_OPT = typer.Option(20, "--limit", min=1, max=100, help="最多返回的条数（会自动翻页凑够）。")

_BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10})")


def _parse_cursor(cursor: str | None) -> tuple[int | None, int | None]:
    if not cursor:
        return None, None
    try:
        last_id, last_time = cursor.split(",")
        return int(last_id), int(last_time)
    except ValueError as exc:
        raise BiliError("invalid_argument", f"无效的 --cursor：{cursor!r}，应为上一页返回的 next_cursor。") from exc


def _reply_command(item: dict[str, Any]) -> str | None:
    """Build the `bili comment reply ...` command that answers this comment."""
    type_name = TYPE_NAMES.get(item.get("business_id"))
    rpid = item.get("source_id")
    if not type_name or not rpid:
        return None
    m = _BV_RE.search(item.get("uri") or "")
    target = m.group(1) if (type_name == "video" and m) else str(item.get("subject_id"))
    parts = ["bili comment reply", target, str(rpid)]
    if item.get("root_id"):
        parts += ["--root", str(item["root_id"])]
    if type_name != "video":
        parts += ["--type", type_name]
    return " ".join(parts) + ' "<回复内容>"'


def _notice(raw: dict[str, Any], time_key: str, last_view_at: int | None, is_comment: bool) -> dict[str, Any]:
    item = raw.get("item") or {}
    user = raw.get("user") or {}
    ts = raw.get(time_key)
    m = _BV_RE.search(item.get("uri") or "")
    out: dict[str, Any] = {
        "id": raw.get("id"),
        "time": iso_time(ts),
        "is_new": bool(last_view_at and ts and ts > last_view_at),
        "user": {"mid": user.get("mid"), "name": user.get("nickname")},
        "content": item.get("source_content"),
        "replying_to": item.get("target_reply_content") or item.get("root_reply_content") or None,
        "on": {
            "type": TYPE_NAMES.get(item.get("business_id"), item.get("business")),
            "title": item.get("title") or None,
            "bvid": m.group(1) if m else None,
            "oid": item.get("subject_id"),
            "url": item.get("uri"),
        },
        "rpid": item.get("source_id") if is_comment else None,
        "root": item.get("root_id") or None,
    }
    out["reply_with"] = _reply_command(item) if is_comment else None
    return out


async def _collect(
    fetch: Callable[[int | None, int | None], Awaitable[dict]],
    cursor: str | None,
    limit: int,
    convert: Callable[[dict, int | None], dict],
) -> dict[str, Any]:
    last_id, last_time = _parse_cursor(cursor)
    items: list[dict] = []
    last_view_at = None
    is_end = False
    while len(items) < limit:
        data = await fetch(last_id, last_time)
        if output.settings.raw:
            return data
        last_view_at = data.get("last_view_at") or last_view_at
        page = data.get("items") or []
        items.extend(convert(r, last_view_at) for r in page)
        cur = data.get("cursor") or {}
        is_end = bool(cur.get("is_end")) or not page
        last_id, last_time = cur.get("id"), cur.get("time")
        if is_end:
            break
    items = items[:limit]
    # If we trimmed the page, resume after the last item we actually returned.
    next_cursor = None
    if items and not (is_end and len(items) < limit):
        next_cursor = f"{items[-1]['id']},{items[-1]['_ts']}"
    for i in items:
        i.pop("_ts", None)
    return {"last_view_at": iso_time(last_view_at), "items": items, "next_cursor": next_cursor}


RECOMMEND_URL = "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd"


@app.command()
def recommend(
    count: int = typer.Option(12, "--count", "-n", min=1, max=30, help="返回条数。"),
    page: int = typer.Option(1, "--page", min=1, help="第几次刷新：每次加 1 得到新的一批，就像在首页下拉刷新。"),
    unseen: bool = typer.Option(False, "--unseen", help="过滤掉已经用 `bili video watch` 看过的视频。"),
) -> None:
    """首页推荐：B 站为当前账号个性化推荐的视频（已去掉广告和直播）。登录后越看越懂你。"""

    async def body():
        from bilibili_api.utils.network import Api

        from ..cards import filter_unseen, video_card

        params = {"ps": count, "fresh_type": 4, "fresh_idx": page, "fresh_idx_1h": page, "brush": page, "feed_version": "V8"}
        data = await Api(url=RECOMMEND_URL, method="GET", wbi=True, credential=config.optional_credential()).update_params(**params).result
        if output.settings.raw:
            return data
        items = [i for i in data.get("item") or [] if i.get("goto") == "av" and i.get("bvid") and not i.get("business_info")]
        return {
            "page": page,
            "personalized": config.load_credential() is not None,
            "items": filter_unseen([video_card(i) for i in items], unseen),
            "next": f"`bili feed recommend --page {page + 1}` 换一批；`bili video watch <BV>` 看视频。",
        }

    run(body)


TIMELINE_TYPES = ("all", "video", "article", "pgc")


@app.command()
def timeline(
    kind: str = typer.Option("all", "--type", help="all（全部）/ video（只看投稿视频）/ article（专栏）/ pgc（番剧影视）。"),
    up: Optional[str] = typer.Option(None, "--up", help="只看某个关注的人：UID 或完整昵称。"),
    limit: int = typer.Option(20, "--limit", min=1, max=100, help="最多返回的条数（会自动翻页凑够）。"),
    offset: Optional[str] = typer.Option(None, "--offset", help="翻页游标，取上一页返回的 next_offset。"),
) -> None:
    """关注动态：首页「动态」里我关注的人发的视频、图文、转发等，从新到旧。"""

    async def body():
        from bilibili_api import dynamic, user

        from .dynamic import normalize
        from .user import resolve_mid

        if kind not in TIMELINE_TYPES:
            raise BiliError("invalid_argument", f"--type 只能是 {' / '.join(TIMELINE_TYPES)} 之一。")
        cred = config.require_credential()
        host_mid = await resolve_mid(up) if up else None
        items: list[dict[str, Any]] = []
        raw_pages: list[dict[str, Any]] = []
        cursor = offset
        while True:
            if host_mid:  # the timeline endpoint ignores host_mid, so read that user's own dynamics
                data = await user.User(host_mid, credential=cred).get_dynamics_new(offset=cursor or "")
            else:
                data = await dynamic.get_dynamic_page_info(cred, _type=dynamic.DynamicType(kind), offset=int(cursor) if cursor else None)
            raw_pages.append(data)
            page = [normalize(i) for i in data.get("items") or []]
            if host_mid and kind != "all":
                page = [i for i in page if i["type"] == kind]
            items += page
            cursor = data.get("offset") if data.get("has_more") else None
            if not cursor or len(items) >= limit:
                break
        if output.settings.raw:
            return raw_pages
        return {"items": items[:limit], "next_offset": cursor}

    run(body)


UNREAD_KEYS = {"reply": "replies", "at": "mentions", "like": "likes", "chat": "private_messages", "sys_msg": "system", "up": "up_assistant"}
_MARKUP_RE = re.compile(r"#\{([^}]*)\}\{([^}]*)\}")


async def _unread(cred) -> dict[str, int]:
    data = await session.get_unread_messages(cred)
    return {name: data.get(k, 0) for k, name in UNREAD_KEYS.items()}


async def _replies(cred, cursor: str | None, limit: int) -> dict[str, Any]:
    def convert(r, lva):
        n = _notice(r, "reply_time", lva, is_comment=True)
        n["_ts"] = r.get("reply_time")
        return n

    return await _collect(lambda i, t: session.get_replies(cred, last_reply_id=i, reply_time=t), cursor, limit, convert)


async def _mentions(cred, cursor: str | None, limit: int) -> dict[str, Any]:
    def convert(r, lva):
        n = _notice(r, "at_time", lva, is_comment=(r.get("item") or {}).get("type") == "reply")
        n["_ts"] = r.get("at_time")
        return n

    return await _collect(lambda i, t: session.get_at(cred, last_id=i, at_time=t), cursor, limit, convert)


def _like(raw: dict[str, Any], last_view_at: int | None) -> dict[str, Any]:
    item = raw.get("item") or {}
    m = _BV_RE.search(item.get("uri") or "")
    users = raw.get("users") or []
    ts = raw.get("like_time")
    kind = item.get("type")
    return {
        "id": raw.get("id"),
        "time": iso_time(ts),
        "is_new": bool(last_view_at and ts and ts > last_view_at),
        "total_likes": raw.get("counts"),
        "users": [{"mid": u.get("mid"), "name": u.get("nickname")} for u in users[:3]],
        "liked": {
            "type": {"reply": "comment", "video": "video", "dynamic": "dynamic", "album": "dynamic"}.get(kind, kind),
            # for a liked comment `title` is the comment's own text
            "text": item.get("title") or item.get("desc") or None,
            "rpid": item.get("item_id") if kind == "reply" else None,
            "bvid": m.group(1) if m else None,
            "url": item.get("uri"),
        },
        "_ts": ts,
    }


async def _likes(cred, cursor: str | None, limit: int) -> dict[str, Any]:
    async def fetch(last_id, like_time):
        data = await session.get_likes(cred, last_id=last_id, like_time=like_time)
        total = data.get("total") or {}
        return {**total, "last_view_at": (data.get("latest") or {}).get("last_view_at")}

    return await _collect(fetch, cursor, limit, _like)


async def _system(cred, limit: int) -> list[dict[str, Any]]:
    data = await session.get_system_messages(cred)
    out = []
    for n in (data.get("system_notify_list") or [])[:limit]:
        content = _MARKUP_RE.sub(lambda mt: f"{mt.group(1)}（{mt.group(2)}）", n.get("content") or "")
        out.append({"id": n.get("id"), "time": n.get("time_at"), "title": n.get("title"), "content": content})
    return out


@app.command()
def notifications() -> None:
    """各类未读消息数量：回复、@、收到的赞、私信、系统通知等。想直接看新消息内容用 `bili feed inbox`。"""

    async def body():
        cred = config.require_credential()
        if output.settings.raw:
            return await session.get_unread_messages(cred)
        counts = await _unread(cred)
        return {"unread": counts, "total": sum(counts.values())}

    run(body)


@app.command()
def replies(cursor: Optional[str] = CURSOR_OPT, limit: int = LIMIT_OPT) -> None:
    """收到的评论回复（新的在前）。每条都附带可直接执行的 reply_with 回复命令。"""

    async def body():
        return await _replies(config.require_credential(), cursor, limit)

    run(body)


@app.command()
def mentions(cursor: Optional[str] = CURSOR_OPT, limit: int = LIMIT_OPT) -> None:
    """@ 我的消息（新的在前）。出现在评论里的 @ 附带 reply_with 回复命令。"""

    async def body():
        return await _mentions(config.require_credential(), cursor, limit)

    run(body)


@app.command()
def likes(cursor: Optional[str] = CURSOR_OPT, limit: int = LIMIT_OPT) -> None:
    """收到的赞（新的在前）：谁赞了你的哪条评论 / 视频 / 动态，同一内容的多个赞会合并成一条。"""

    async def body():
        return await _likes(config.require_credential(), cursor, limit)

    run(body)


@app.command()
def system(limit: int = typer.Option(10, "--limit", min=1, max=50, help="最多返回多少条。")) -> None:
    """系统通知（官方公告、审核结果、等级提升等）。"""

    async def body():
        cred = config.require_credential()
        if output.settings.raw:
            return await session.get_system_messages(cred)
        return {"items": await _system(cred, limit)}

    run(body)


@app.command()
def inbox(limit: int = typer.Option(5, "--limit", min=1, max=20, help="每类最多返回几条新消息。")) -> None:
    """一次看完所有新消息：未读数，以及新的回复、@、赞、系统通知和未读私信的内容。适合 Agent 每轮开始时调用。

    注意：和在网页上点开通知一样，读取后 B 站会把这些消息标为已读，未读数随之清零。
    """

    async def body():
        from . import dm

        cred = config.require_credential()
        counts = await _unread(cred)
        result: dict[str, Any] = {"unread": counts, "total": sum(counts.values())}

        def fresh(section: dict[str, Any], n: int) -> list[dict[str, Any]]:
            # lists are newest first and the unread count says how many are new
            return (section.get("items") or [])[: min(n, limit)]

        if counts["replies"]:
            result["replies"] = fresh(await _replies(cred, None, min(counts["replies"], limit)), counts["replies"])
        if counts["mentions"]:
            result["mentions"] = fresh(await _mentions(cred, None, min(counts["mentions"], limit)), counts["mentions"])
        if counts["likes"]:
            result["likes"] = fresh(await _likes(cred, None, min(counts["likes"], limit)), counts["likes"])
        if counts["system"]:
            result["system"] = await _system(cred, min(counts["system"], limit))
        if counts["private_messages"]:
            result["private_messages"] = await dm.unread_conversations(cred, limit)
        if not result["total"]:
            result["note"] = "没有新消息。"
        return result

    run(body)
