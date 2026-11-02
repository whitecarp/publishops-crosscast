"""bili comment: read and interact with comments on videos, dynamics and articles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from bilibili_api import comment, video

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import iso_time, parse_video_id, read_text, run

app = typer.Typer(help="评论：读取、发表、回复、点赞、删除（视频 / 动态 / 专栏）。", no_args_is_help=True)

TARGET_ARG = typer.Argument(
    ..., help="评论区所在对象：视频填 BV 号 / av 号 / 链接；其他类型填评论区 ID（oid）。"
)
RPID_ARG = typer.Argument(..., help="评论 ID（rpid）。")
FILE_OPT = typer.Option(None, "--file", "-f", help="从文件读取正文，`-` 表示 stdin。")
TYPE_OPT = typer.Option(
    "video", "--type", "-t", help="评论区类型：video / dynamic（纯文字动态）/ draw（图文动态）/ article。"
)

RESOURCE_TYPES = {
    "video": comment.CommentResourceType.VIDEO,
    "dynamic": comment.CommentResourceType.DYNAMIC,
    "draw": comment.CommentResourceType.DYNAMIC_DRAW,
    "article": comment.CommentResourceType.ARTICLE,
}
# Bilibili's numeric comment-area types, as seen in notifications (business_id).
TYPE_NAMES = {t.value: name for name, t in RESOURCE_TYPES.items()}


def resolve_target(target: str, type_: str) -> tuple[int, comment.CommentResourceType]:
    """Return (oid, resource type) for a comment area."""
    if type_ not in RESOURCE_TYPES:
        raise BiliError("invalid_argument", f"--type 只能是 {' / '.join(RESOURCE_TYPES)} 之一。")
    if type_ == "video":
        ids = parse_video_id(target)
        oid = ids["aid"] if "aid" in ids else video.Video(bvid=ids["bvid"]).get_aid()
        return oid, RESOURCE_TYPES[type_]
    if not target.isdigit():
        raise BiliError("invalid_argument", f"--type {type_} 时，目标必须是数字形式的评论区 ID（oid），收到 {target!r}。")
    return int(target), RESOURCE_TYPES[type_]


def _https(url: str) -> str:
    return "https:" + url[5:] if url.startswith("http:") else ("https:" + url if url.startswith("//") else url)


def comment_images(r: dict[str, Any]) -> list[str]:
    return [_https(p["img_src"]) for p in (r.get("content") or {}).get("pictures") or [] if p.get("img_src")]


CHARGED_ATTR_BIT = 32  # attr bit B 站 sets on comments posted together with a charge


def _reply(r: dict[str, Any], with_children: bool = True) -> dict[str, Any]:
    member = r.get("member") or {}
    item: dict[str, Any] = {
        "rpid": r.get("rpid"),
        "root": r.get("root") or None,
        "parent": r.get("parent") or None,
        "user": {"mid": r.get("mid"), "name": member.get("uname")},
        "content": (r.get("content") or {}).get("message"),
        "like": r.get("like"),
        "reply_count": r.get("rcount"),
        "time": iso_time(r.get("ctime")),
    }
    if images := comment_images(r):
        item["images"] = images  # view them with `bili comment images <url>…`
    if (r.get("attr") or 0) >> CHARGED_ATTR_BIT & 1:
        item["charged"] = True  # posted from the 充电评论 box; the amount is not in comment data
    # a comment area only shows the medal of its own uploader, which (without live gifts) comes from charging
    if medal := member.get("fans_detail"):
        item["fan_medal"] = {"name": medal.get("medal_name"), "level": medal.get("level")}
    if with_children and r.get("replies"):
        item["replies_preview"] = [_reply(c, with_children=False) for c in r["replies"]]
    return item


def needs_reply(rows: list[dict[str, Any]], up_mid: int | None) -> list[dict[str, Any]]:
    """Root comments the uploader has not answered yet (B 站 marks this in up_action.reply)."""
    return [r for r in rows if not (r.get("up_action") or {}).get("reply") and r.get("mid") != up_mid]


def _pinned(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Pinned comments, which B 站 returns outside `replies` (lazy API: top_replies; paged API: upper.top)."""
    tops = list(data.get("top_replies") or [])
    upper_top = (data.get("upper") or {}).get("top")
    if upper_top and all(t.get("rpid") != upper_top.get("rpid") for t in tops):
        tops.append(upper_top)
    return tops


@app.command("list")
def list_(
    target: str = TARGET_ARG,
    type_: str = TYPE_OPT,
    sort: str = typer.Option("hot", "--sort", help="排序：hot（按点赞）或 time（按时间）。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始；第 2 页起需要登录。"),
    unreplied: bool = typer.Option(False, "--unreplied", help="只看 UP 主还没回复过的评论（不含 UP 主自己发的），按页筛选。"),
    supporters: bool = typer.Option(False, "--supporters", help="只看充电评论和戴着本 UP 粉丝勋章的人（给你充过电的），按页筛选；适合优先回复。"),
) -> None:
    """列出评论（每页约 20 条），附带每条评论的前几条楼中楼。充电评论带 `charged`，充过电的人带 `fan_medal`。"""

    async def body():
        if sort not in ("hot", "time"):
            raise BiliError("invalid_argument", "--sort 只能是 hot 或 time。")
        order = comment.OrderType.LIKE if sort == "hot" else comment.OrderType.TIME
        cred = config.load_credential()
        if page > 1 and cred is None:
            raise BiliError("not_logged_in", "获取第 2 页及以后的评论需要登录。")
        oid, rtype = resolve_target(target, type_)
        data = await comment.get_comments(oid, rtype, page_index=page, order=order, credential=cred)
        if output.settings.raw:
            return data
        pg = data.get("page") or {}
        out: dict[str, Any] = {"oid": oid, "type": type_, "page": page, "total": pg.get("count") or pg.get("acount")}
        rows = data.get("replies") or []
        if unreplied:
            up_mid = (data.get("upper") or {}).get("mid")
            rows = needs_reply(rows, up_mid)
            out["unreplied"] = len(rows)
        elif page == 1 and (pinned := _pinned(data)):
            out["pinned"] = [_reply(r) for r in pinned]
        items = [_reply(r) for r in rows]
        if supporters:
            items = [i for i in items if i.get("charged") or i.get("fan_medal")]
            out["supporters"] = len(items)
        out["items"] = items
        return out

    run(body)


@app.command()
def replies(
    target: str = TARGET_ARG,
    rpid: int = RPID_ARG,
    type_: str = TYPE_OPT,
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, max=20, help="每页条数，最大 20。"),
) -> None:
    """列出某条评论下的楼中楼回复。"""

    async def body():
        oid, rtype = resolve_target(target, type_)
        c = comment.Comment(oid, rtype, rpid, credential=config.optional_credential())
        data = await c.get_sub_comments(page_index=page, page_size=size)
        if output.settings.raw:
            return data
        pg = data.get("page") or {}
        return {
            "root": _reply(data.get("root") or {}, with_children=False) if data.get("root") else None,
            "page": page,
            "total": pg.get("count"),
            "items": [_reply(r, with_children=False) for r in data.get("replies") or []],
        }

    run(body)


AT_OPT = typer.Option(
    [], "--at", help="要 @ 的人：UID、完整昵称，或 owner（主人，见 `bili owner set`）。可重复传入；正文里没写 @昵称 时会自动加在开头。"
)
MAX_MENTIONS = 3
IMAGE_OPT = typer.Option([], "--image", "-i", help="随评论发的图片路径，可重复传入，最多 9 张（只有根评论能带图）。")
MAX_IMAGES = 9


async def _mentions(values: list[str], limit: int = MAX_MENTIONS) -> dict[str, int]:
    """Resolve --at values to {exact current nickname: mid}; the web client sends this as at_name_to_mid."""
    from .owner import resolve_user
    from .user import resolve_mid

    if len(values) > limit:
        raise BiliError("invalid_argument", f"最多 @ {limit} 个人。")
    out: dict[str, int] = {}
    for v in values:
        if v == "owner":
            owner = config.load_owner()
            if not owner:
                raise BiliError("invalid_argument", "还没有设置主人。", hint="运行 `bili owner set <UID>`。")
            mid = int(owner["mid"])
        else:
            mid = await resolve_mid(v)
        name = (await resolve_user(mid)).get("name")  # nicknames change; always use the current one
        if not name:
            raise BiliError("not_found", f"找不到 UID {mid} 对应的用户。")
        out[name] = mid
    return out


def check_images(images: list[Path]) -> None:
    if len(images) > MAX_IMAGES:
        raise BiliError("invalid_argument", f"最多 {MAX_IMAGES} 张图片，收到 {len(images)} 张。")
    for p in images:
        if not p.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{p}")


async def _upload_pictures(cred, images: list[Path]) -> str:
    """Upload through the dynamic image endpoint, like the web comment box does; returns the `pictures` field."""
    from bilibili_api import Picture
    from bilibili_api.dynamic import upload_image

    pics = []
    for p in images:
        res = await upload_image(Picture.from_file(str(p)), cred)
        pics.append({"img_src": res["image_url"], "img_width": res["image_width"], "img_height": res["image_height"], "img_size": res["img_size"]})
    return json.dumps(pics)


async def _send(
    target: str, type_: str, text: str, root: int | None, parent: int | None, at: list[str] | None = None,
    images: list[Path] | None = None,
) -> dict[str, Any]:
    from bilibili_api.comment import API as COMMENT_API
    from bilibili_api.utils.network import Api

    cred = config.require_credential()
    oid, rtype = resolve_target(target, type_)
    # Replying to a root comment: the web client sends parent=root too.
    if root is not None and parent is None:
        parent = root
    images = images or []
    if images and root is not None:
        raise BiliError("invalid_argument", "B 站不允许回复（楼中楼）带图，只有直接发在评论区的根评论能带图。")
    check_images(images)
    mentions = await _mentions(at or [])
    # B 站 only turns "@name " into a real mention (with notification) when the
    # text contains it *and* at_name_to_mid maps the name to the UID.
    prefix = "".join(f"@{name} " for name in mentions if f"@{name}" not in text)
    message = prefix + text
    params = {"oid": oid, "type": type_, "text": message, "root": root, "parent": parent, "mentions": mentions or None,
              "images": [str(p) for p in images] or None}
    if output.settings.dry_run:
        return {"dry_run": True, "action": "comment.send", "params": params}
    data: dict[str, Any] = {
        "oid": oid, "type": rtype.value, "message": message, "plat": 1,
        "statistics": {"appId": 100, "platform": 5}, "gaia_source": "main_web",
    }
    if root is not None:
        data["root"], data["parent"] = root, parent
    if mentions:
        data["at_name_to_mid"] = json.dumps(mentions, ensure_ascii=False)
    guard.check("comment")
    if images:
        data["pictures"] = await _upload_pictures(cred, images)
    res = await Api(**COMMENT_API["comment"]["send"], credential=cred).update_data(**data).result
    guard.record("comment", params, {"rpid": res.get("rpid")})
    out: dict[str, Any] = {
        "rpid": res.get("rpid"),
        "oid": oid,
        "type": type_,
        "root": root,
        "parent": parent,
        "content": message,
        "success_toast": res.get("success_toast"),
    }
    if images:
        out["images"] = len(images)
    if mentions:
        members = {int(m.get("mid")) for m in ((res.get("reply") or {}).get("content") or {}).get("members") or [] if m.get("mid")}
        out["mentions"] = [{"name": n, "mid": m, "resolved": m in members} for n, m in mentions.items()]
    out["next"] = f"新号或敏感内容可能进审核，用 `bili comment check {target} {res.get('rpid')}{f' --root {root}' if root else ''}{'' if type_ == 'video' else f' --type {type_}'}` 确认别人能否看到。"
    return out


@app.command()
def post(
    target: str = TARGET_ARG,
    text: Optional[str] = typer.Argument(None, help="评论正文；也可用 --file。"),
    type_: str = TYPE_OPT,
    file: Optional[Path] = FILE_OPT,
    at: list[str] = AT_OPT,
    image: list[Path] = IMAGE_OPT,
) -> None:
    """发表一条评论，可用 --at 真正 @ 某人（对方会收到提醒），--image 附带图片。表情直接写 [笑哭] 这类文字，可用的见 `bili comment emotes`。"""

    async def body():
        return await _send(target, type_, read_text(text, file), None, None, at, image)

    run(body)


@app.command()
def check(
    target: str = TARGET_ARG,
    rpid: int = RPID_ARG,
    root: Optional[int] = typer.Option(None, "--root", help="楼中楼回复所在的根评论 ID。"),
    type_: str = TYPE_OPT,
) -> None:
    """检查自己的评论状态：是否在列表中、是否被折叠 / 隐藏。新号或敏感内容常被折叠或进审核，此时 @ 也不会发出提醒。"""

    async def body():
        # B 站 no longer shows comments to anonymous visitors, so the check reads the
        # logged-in list and relies on the status fields B 站 attaches to each comment.
        cred = config.require_credential()
        oid, rtype = resolve_target(target, type_)
        pinned_ids: set[int] = set()
        replies: list[dict[str, Any]] = []
        if root:
            for p in (1, 2):
                replies += (await comment.Comment(oid, rtype, root, credential=cred).get_sub_comments(page_index=p, page_size=20)).get("replies") or []
        else:
            offset = ""
            for _ in range(CHECK_PAGES):
                data = await comment.get_comments_lazy(oid, rtype, offset=offset, order=comment.OrderType.TIME, credential=cred)
                if not offset:  # pinned comments only come with the first page and never appear in `replies`
                    tops = _pinned(data)
                    pinned_ids = {t.get("rpid") for t in tops}
                    replies += tops
                replies += data.get("replies") or []
                offset = ((data.get("cursor") or {}).get("pagination_reply") or {}).get("next_offset") or ""
                if any(x.get("rpid") == rpid for x in replies) or not offset or (data.get("cursor") or {}).get("is_end"):
                    break
        return _check_result(rpid, replies, pinned_ids)

    run(body)


CHECK_PAGES = 3


def _check_result(rpid: int, replies: list[dict[str, Any]], pinned_ids: set[int]) -> dict[str, Any]:
    r = next((x for x in replies if x.get("rpid") == rpid), None)
    if r is None:
        return {
            "rpid": rpid,
            "found": False,
            "visible": False,
            "note": f"最新 {CHECK_PAGES} 页评论中找不到：可能已被删除、进入审核，或已被更新的评论挤到后面。",
        }
    folded = bool((r.get("folder") or {}).get("is_folded"))
    hidden = bool(r.get("invisible")) or r.get("state") not in (0, None)
    out: dict[str, Any] = {
        "rpid": rpid,
        "found": True,
        "visible": not (folded or hidden),
        "folded": folded,
        "hidden": hidden,
        "state": r.get("state"),
    }
    if rpid in pinned_ids:
        out["pinned"] = True
    out["note"] = "折叠 / 隐藏的评论别人通常看不到，@ 也可能不会发出提醒。" if (folded or hidden) else "从你自己的视角看评论正常显示。"
    return out


@app.command()
def pin(
    target: str = TARGET_ARG,
    rpid: int = RPID_ARG,
    type_: str = TYPE_OPT,
    undo: bool = typer.Option(False, "--undo", help="取消置顶。"),
) -> None:
    """置顶自己作品下的一条根评论（或用 --undo 取消）。每个评论区只能置顶一条，新置顶会替换旧的。"""

    async def body():
        cred = config.require_credential()
        oid, rtype = resolve_target(target, type_)
        params = {"oid": oid, "type": type_, "rpid": rpid, "pin": not undo}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "comment.pin", "params": params}
        guard.check("pin")
        await comment.Comment(oid, rtype, rpid, credential=cred).pin(status=not undo)
        guard.record("pin", params, "ok")
        return {"rpid": rpid, "pinned": not undo}

    run(body)


@app.command()
def reply(
    target: str = TARGET_ARG,
    rpid: int = typer.Argument(..., help="要回复的评论 ID。"),
    text: Optional[str] = typer.Argument(None, help="回复正文；也可用 --file。"),
    root: Optional[int] = typer.Option(
        None, "--root", help="当回复的是楼中楼时，填它所在的根评论 ID（即该回复的 root 字段）。"
    ),
    type_: str = TYPE_OPT,
    file: Optional[Path] = FILE_OPT,
    at: list[str] = AT_OPT,
) -> None:
    """回复一条评论。回复根评论直接传 rpid；回复楼中楼时额外传 --root。可带 --at；B 站不允许回复带图。"""

    async def body():
        content = read_text(text, file)
        if root is None or root == rpid:
            return await _send(target, type_, content, rpid, None, at)
        return await _send(target, type_, content, root, rpid, at)

    run(body)


@app.command()
def like(
    target: str = TARGET_ARG,
    rpid: int = RPID_ARG,
    type_: str = TYPE_OPT,
    undo: bool = typer.Option(False, "--undo", help="取消点赞。"),
) -> None:
    """给评论点赞（或用 --undo 取消）。"""

    async def body():
        cred = config.require_credential()
        oid, rtype = resolve_target(target, type_)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "comment.like", "params": {"oid": oid, "type": type_, "rpid": rpid, "like": not undo}}
        guard.check("like")
        await comment.Comment(oid, rtype, rpid, credential=cred).like(status=not undo)
        guard.record("like", {"oid": oid, "type": type_, "rpid": rpid, "like": not undo}, "ok")
        return {"rpid": rpid, "liked": not undo}

    run(body)


@app.command()
def delete(target: str = TARGET_ARG, rpid: int = RPID_ARG, type_: str = TYPE_OPT) -> None:
    """删除自己发表的评论（或自己作品下的评论）。"""

    async def body():
        cred = config.require_credential()
        oid, rtype = resolve_target(target, type_)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "comment.delete", "params": {"oid": oid, "type": type_, "rpid": rpid}}
        guard.check("delete")
        await comment.Comment(oid, rtype, rpid, credential=cred).delete()
        guard.record("delete", {"oid": oid, "type": type_, "rpid": rpid}, "ok")
        return {"rpid": rpid, "deleted": True}

    run(body)


EMOTE_SAMPLE = 8


def summarize_emotes(packages: list[dict], keyword: str | None, package: str | None) -> dict[str, Any]:
    """Packages with a few samples by default; every emote of one package, or every match of a keyword."""
    def texts(emotes):
        return [e.get("text") for e in emotes if e.get("text")]

    if keyword:
        k = keyword.lower()
        hits = [e for p in packages for e in p.get("emote") or []
                if k in (e.get("text") or "").lower() or k in ((e.get("meta") or {}).get("alias") or "").lower()]
        return {"keyword": keyword, "emotes": texts(hits)}
    if package:
        for p in packages:
            if package in (p.get("text"), str(p.get("id"))):
                return {"package": p.get("text"), "emotes": texts(p.get("emote") or [])}
        raise BiliError("not_found", f"没有名为 {package!r} 的表情包。", hint="不带参数运行 `bili comment emotes` 查看可用的表情包。")
    return {
        "packages": [{"name": p.get("text"), "count": len(p.get("emote") or []), "sample": texts(p.get("emote") or [])[:EMOTE_SAMPLE]}
                     for p in packages],
        "usage": "把表情文字原样写进评论或动态正文，如 `太强了[笑哭]`。看某个包的全部：--package 名称；按关键词找：-k 关键词。",
    }


@app.command()
def emotes(
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k", help="按关键词找表情，如 笑、doge。"),
    package: Optional[str] = typer.Option(None, "--package", "-p", help="列出某个表情包的全部表情，如 小黄脸、tv_小电视。"),
    for_: str = typer.Option("reply", "--for", help="使用场景：reply（评论）/ dynamic（动态）。"),
) -> None:
    """当前账号能用的 B 站表情。默认只给每个表情包的几个示例，用 -k 搜索或 --package 看全部。"""

    async def body():
        from bilibili_api import emoji

        if for_ not in ("reply", "dynamic"):
            raise BiliError("invalid_argument", "--for 只能是 reply 或 dynamic。")
        data = await emoji.get_emoji_list(for_, config.optional_credential())
        if output.settings.raw:
            return data
        return summarize_emotes(data.get("packages") or [], keyword, package)

    run(body)


IMAGE_HOST = "hdslb.com"
IMAGE_MAX_SIDE = 1280


def image_path(url: str, out_dir: Path) -> Path:
    import hashlib

    return out_dir / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.jpg"


@app.command()
def images(
    urls: list[str] = typer.Argument(..., help="评论里的图片地址（`comment list` 输出的 images），可传多个。"),
    out: Optional[Path] = typer.Option(None, "--out", help="保存目录，默认在配置目录的 images/ 下。"),
) -> None:
    """把评论里的图片存到本地（长边缩到 1280 像素），返回文件路径，方便多模态模型直接看图。只接受 B 站图床的地址。"""

    async def body():
        from io import BytesIO
        from urllib.parse import urlparse

        from PIL import Image

        from ..runtime import web_client

        for u in urls:
            host = urlparse(_https(u)).hostname or ""
            if not (host == IMAGE_HOST or host.endswith("." + IMAGE_HOST)):
                raise BiliError("invalid_argument", f"只接受 B 站图床（{IMAGE_HOST}）的图片地址，收到 {u!r}。")
        out_dir = (out or config.home() / "images").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        async with web_client() as client:
            for u in urls:
                path = image_path(_https(u), out_dir)
                if not path.exists():
                    resp = await client.get(_https(u))
                    resp.raise_for_status()
                    img = Image.open(BytesIO(resp.content))
                    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
                    img.convert("RGB").save(path, quality=88)
                with Image.open(path) as im:
                    saved.append({"url": _https(u), "path": str(path), "size": f"{im.width}x{im.height}"})
        return {"images": saved}

    run(body)
