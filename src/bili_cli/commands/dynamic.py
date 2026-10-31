"""bili dynamic: publish, list and delete dynamics (动态)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer
from bilibili_api import Picture, dynamic, user

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import iso_time, read_text, run
from .comment import AT_OPT, TYPE_NAMES, _mentions

app = typer.Typer(help="动态：发布文字 / 图文动态、列出、删除。", no_args_is_help=True)

MAX_IMAGES = 9
VOTE_OPTIONS = (2, 20)
VOTE_DAYS = (1, 30)
# bilibili_api's vote module still targets vote_svr, which now answers 404; these are what the web client uses
VOTE_CREATE_URL = "https://api.bilibili.com/x/vote/create"
VOTE_INFO_URL = "https://api.bilibili.com/x/vote/vote_info"


def vote_payload(title: str, options: list[str], multi: int, days: int, publisher: int) -> dict[str, Any]:
    return {"vote_info": {
        "title": title, "desc": "", "type": 0, "choice_cnt": multi, "duration": days * 86400,
        "options": [{"opt_desc": o, "img_url": ""} for o in options], "only_fans_level": 0, "vote_publisher": publisher,
    }}
TYPE_SHORT = {
    "DYNAMIC_TYPE_WORD": "text",
    "DYNAMIC_TYPE_DRAW": "image",
    "DYNAMIC_TYPE_AV": "video",
    "DYNAMIC_TYPE_FORWARD": "forward",
    "DYNAMIC_TYPE_ARTICLE": "article",
    "DYNAMIC_TYPE_LIVE_RCMD": "live",
}


def _text_of(mod_dyn: dict) -> str | None:
    # Plain dynamics keep their text in desc; image (opus) dynamics in major.opus.summary.
    desc = (mod_dyn.get("desc") or {}).get("text")
    opus = (((mod_dyn.get("major") or {}).get("opus") or {}).get("summary") or {}).get("text")
    return desc or opus or None


def _vote_card(mod_dyn: dict) -> dict[str, Any] | None:
    vote = ((mod_dyn.get("additional") or {}).get("vote")) or None
    if not vote:
        return None
    return {"vote_id": vote.get("vote_id"), "title": vote.get("title"), "joined": vote.get("join_num") or 0,
            "ends": iso_time(vote.get("end_time")), "results_with": f"bili dynamic vote {vote.get('vote_id')}"}


def check_vote(title: str | None, options: list[str], multi: int, days: int) -> None:
    if not title:
        if options:
            raise BiliError("invalid_argument", "传了 --option 但没有 --vote 投票标题。")
        return
    lo, hi = VOTE_OPTIONS
    if not lo <= len(options) <= hi:
        raise BiliError("invalid_argument", f"投票需要 {lo} 到 {hi} 个 --option，收到 {len(options)} 个。")
    if not 1 <= multi <= len(options):
        raise BiliError("invalid_argument", f"--vote-multi 需在 1 到 {len(options)} 之间。")
    if not VOTE_DAYS[0] <= days <= VOTE_DAYS[1]:
        raise BiliError("invalid_argument", f"--vote-days 需在 {VOTE_DAYS[0]} 到 {VOTE_DAYS[1]} 之间。")


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    """Reduce a dynamic card to what an agent needs, including how to reach its comment area."""
    mods = item.get("modules") or {}
    author = mods.get("module_author") or {}
    mod_dyn = mods.get("module_dynamic") or {}
    major = mod_dyn.get("major") or {}
    stat = mods.get("module_stat") or {}
    basic = item.get("basic") or {}
    archive = major.get("archive") or {}
    pics = ((major.get("opus") or {}).get("pics") or []) or ((major.get("draw") or {}).get("items") or [])

    comment_type = TYPE_NAMES.get(basic.get("comment_type"))
    comment_oid = basic.get("comment_id_str")
    if comment_type == "video" and archive.get("bvid"):
        comment_target = archive["bvid"]
    else:
        comment_target = comment_oid
    out: dict[str, Any] = {
        "id": item.get("id_str"),
        "type": TYPE_SHORT.get(item.get("type"), item.get("type")),
        "time": iso_time(int(author.get("pub_ts") or 0)),
        "author": {"mid": author.get("mid"), "name": author.get("name")},
        "text": _text_of(mod_dyn),
        "images": [p.get("url") or p.get("src") for p in pics],
        "video": {"bvid": archive.get("bvid"), "title": archive.get("title")} if archive else None,
        "topic": (mod_dyn.get("topic") or {}).get("name"),
        "vote": _vote_card(mod_dyn),
        "stat": {k: (stat.get(k) or {}).get("count") for k in ("comment", "like", "forward")},
        "url": f"https://t.bilibili.com/{item.get('id_str')}",
        "comments": None,
    }
    if comment_type and comment_target:
        type_flag = "" if comment_type == "video" else f" --type {comment_type}"
        out["comments"] = {
            "target": comment_target,
            "type": comment_type,
            "list_with": f"bili comment list {comment_target}{type_flag}",
        }
    if (mods.get("module_tag") or {}).get("text") == "置顶":
        out["pinned"] = True
    if item.get("orig"):
        out["original"] = normalize(item["orig"])
    return out


@app.command()
def post(
    text: Optional[str] = typer.Argument(None, help="动态正文；也可用 --file。正文里的 @昵称 会自动转成 @ 提及。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="从文件读取正文，`-` 表示 stdin。"),
    image: list[Path] = typer.Option([], "--image", "-i", help=f"图片路径，可重复传入，最多 {MAX_IMAGES} 张。"),
    topic: Optional[int] = typer.Option(None, "--topic", help="话题 ID。"),
    at: list[str] = AT_OPT,
    vote_title: Optional[str] = typer.Option(None, "--vote", help="附带一个投票：投票标题。配合 --option 使用。"),
    vote_option: list[str] = typer.Option([], "--option", help="投票选项，可重复传入，2 到 20 个。"),
    vote_multi: int = typer.Option(1, "--vote-multi", help="每人最多选几项，1 为单选。"),
    vote_days: int = typer.Option(7, "--vote-days", help="投票持续天数，1 到 30。"),
) -> None:
    """发布一条文字或图文动态。--image 附图，--at 真正 @ 某人，--vote/--option 附带投票；表情直接写 [笑哭] 这类文字（见 `bili comment emotes --for dynamic`）。"""

    async def body():
        cred = config.require_credential()
        content = read_text(text, file)
        if len(image) > MAX_IMAGES:
            raise BiliError("invalid_argument", f"最多 {MAX_IMAGES} 张图片，收到 {len(image)} 张。")
        for p in image:
            if not p.is_file():
                raise BiliError("invalid_argument", f"图片不存在：{p}")
        check_vote(vote_title, vote_option, vote_multi, vote_days)
        mentions = await _mentions(at)
        params = {"text": content, "images": [str(p) for p in image], "topic": topic, "mentions": mentions or None}
        if vote_title:
            params["vote"] = {"title": vote_title, "options": vote_option, "multi": vote_multi, "days": vote_days}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dynamic.post", "params": params}
        pics = [Picture.from_file(str(p)) for p in image]
        build = dynamic.BuildDynamic()
        for name, mid in mentions.items():
            if f"@{name}" not in content:  # already in the text: add_text turns it into a mention in place
                build.add_at(mid, name).add_plain_text(" ")
        build.add_text(content).add_image(pics)
        if topic:
            build.set_topic(topic)
        guard.check("dynamic")
        if vote_title:
            from bilibili_api.utils.network import Api

            created = await (
                Api(url=VOTE_CREATE_URL, method="POST", verify=True, credential=cred, no_csrf=True, json_body=True)
                .update_params(csrf=cred.bili_jct)
                .update_data(**vote_payload(vote_title, vote_option, vote_multi, vote_days, config.my_mid(cred)))
                .result
            )
            build.add_vote(created["vote_id"])
            params["vote"]["vote_id"] = created["vote_id"]
        res = await dynamic.send_dynamic(build, cred)
        guard.record("dynamic", params, {"id": res.get("dyn_id_str") or res.get("dyn_id")})
        dyn_id = str(res.get("dyn_id_str") or res.get("dyn_id"))
        return {"id": dyn_id, "url": f"https://t.bilibili.com/{dyn_id}", "type": "image" if pics else "text", **params}

    run(body)


@app.command("list")
def list_(
    mid: Optional[int] = typer.Option(None, "--mid", help="用户 UID，默认是自己。"),
    offset: str = typer.Option("", "--offset", help="翻页游标，取上一页返回的 next_offset。"),
) -> None:
    """列出某个用户（默认自己）发布的动态，每页约 12 条。"""

    async def body():
        cred = config.load_credential()
        uid = mid
        if uid is None:
            if cred is None:
                raise BiliError("not_logged_in", "未登录时需要用 --mid 指定用户。")
            uid = config.my_mid(cred)
        data = await user.User(uid, credential=cred).get_dynamics_new(offset=offset)
        if output.settings.raw:
            return data
        return {
            "mid": uid,
            "items": [normalize(i) for i in data.get("items") or []],
            "next_offset": data.get("offset") if data.get("has_more") else None,
        }

    run(body)


@app.command()
def show(dynamic_id: str = typer.Argument(..., help="动态 ID（t.bilibili.com/ 后面的数字）。")) -> None:
    """查看一条动态的详情。"""

    async def body():
        info = await dynamic.Dynamic(int(dynamic_id), credential=config.optional_credential()).get_info()
        return info if output.settings.raw else normalize(info.get("item") or info)

    run(body)


@app.command()
def delete(dynamic_id: str = typer.Argument(..., help="要删除的动态 ID。")) -> None:
    """删除自己的一条动态。"""

    async def body():
        cred = config.require_credential()
        if not dynamic_id.isdigit():
            raise BiliError("invalid_argument", f"动态 ID 必须是数字：{dynamic_id!r}")
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dynamic.delete", "params": {"id": dynamic_id}}
        guard.check("delete")
        await dynamic.Dynamic(int(dynamic_id), credential=cred).delete()
        guard.record("delete", {"dynamic_id": dynamic_id}, "ok")
        return {"id": dynamic_id, "deleted": True}

    run(body)


@app.command("vote")
def vote_(vote_id: int = typer.Argument(..., help="投票 ID（动态卡片里 vote.vote_id，或发投票动态时返回的 vote_id）。")) -> None:
    """查看一个动态投票的结果：每个选项的票数、参与人数、截止时间。"""

    async def body():
        from bilibili_api.utils.network import Api

        data = await Api(url=VOTE_INFO_URL, method="GET", credential=config.optional_credential()).update_params(vote_id=vote_id).result
        if output.settings.raw:
            return data
        info = data.get("vote_info") or {}
        return {
            "vote_id": vote_id,
            "title": info.get("title"),
            "joined": info.get("join_num") or 0,
            "multi": info.get("choice_cnt"),
            "ends": iso_time(info.get("end_time")),
            "options": [{"text": o.get("opt_desc"), "votes": o.get("cnt") or 0} for o in info.get("options") or []],
        }

    run(body)
