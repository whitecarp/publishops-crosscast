"""bili danmaku: send danmaku. Reading danmaku lives in `bili video danmaku`."""

from __future__ import annotations

import json
import re
import time as _time
from datetime import datetime
from typing import Optional

import typer
from bilibili_api import video
from bilibili_api.utils.danmaku import Danmaku, DmFontSize, DmMode
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import CST, iso_time, parse_video_id, run

app = typer.Typer(help="发送弹幕（读取弹幕用 `bili video danmaku`）。", no_args_is_help=True)

MODES = {"scroll": DmMode.FLY, "top": DmMode.TOP, "bottom": DmMode.BOTTOM}
MAX_LEN = 100
RECALL_URL = "https://api.bilibili.com/x/dm/recall"
_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


@app.command()
def send(
    video_id: str = typer.Argument(..., help="BV 号、av 号或视频链接。"),
    text: str = typer.Argument(..., help=f"弹幕内容，最多 {MAX_LEN} 字。"),
    time: float = typer.Option(..., "--time", min=0, help="弹幕出现的视频时间点，单位秒。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
    mode: str = typer.Option("scroll", "--mode", help="位置：scroll（滚动）/ top（顶部）/ bottom（底部）。"),
    color: str = typer.Option("ffffff", "--color", help="十六进制颜色，如 ffffff。"),
) -> None:
    """在指定时间点发送一条弹幕。"""

    async def body():
        cred = config.require_credential()
        content = text.strip()
        if not content or len(content) > MAX_LEN:
            raise BiliError("invalid_argument", f"弹幕内容需为 1 到 {MAX_LEN} 字。")
        if mode not in MODES:
            raise BiliError("invalid_argument", f"--mode 只能是 {' / '.join(MODES)} 之一。")
        if not _COLOR_RE.match(color):
            raise BiliError("invalid_argument", "--color 必须是 6 位十六进制颜色，如 ffffff。")
        v = video.Video(**parse_video_id(video_id), credential=cred)
        info = await v.get_info()
        pages = info.get("pages") or []
        if page > len(pages):
            raise BiliError("invalid_argument", f"该视频只有 {len(pages)} 个分 P。")
        duration = pages[page - 1].get("duration") or 0
        if duration and time > duration:
            raise BiliError("invalid_argument", f"--time {time} 超出视频时长 {duration} 秒。")
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "text": content, "mode": mode, "color": color.lstrip("#").lower()}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.send", "params": params}
        dm = Danmaku(content, dm_time=time, color=params["color"], mode=MODES[mode], font_size=DmFontSize.NORMAL)
        guard.check("danmaku")
        res = await v.send_danmaku(cid=pages[page - 1]["cid"], danmaku=dm)
        guard.record("danmaku", params, res)
        return {**params, "dmid": (res or {}).get("dmid_str") or (res or {}).get("dmid"), "sent": True}

    run(body)


@app.command()
def recall(
    video_id: str = typer.Argument(..., help="BV 号、av 号或视频链接。"),
    dmid: int = typer.Argument(..., help="弹幕 ID（`danmaku send` 返回的 dmid）。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """撤回自己刚发的弹幕（B 站只允许在发送后短时间内撤回）。"""

    async def body():
        cred = config.require_credential()
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.recall", "params": {"video": video_id, "dmid": dmid, "page": page}}
        v = video.Video(**parse_video_id(video_id), credential=cred)
        pages = (await v.get_info()).get("pages") or []
        if page > len(pages):
            raise BiliError("invalid_argument", f"该视频只有 {len(pages)} 个分 P。")
        guard.check("delete")
        # bilibili-api declares this endpoint as GET, which the server rejects with 405.
        res = await Api(url=RECALL_URL, method="POST", verify=True, credential=cred).update_data(
            dmid=dmid, cid=pages[page - 1]["cid"], type=1
        ).result
        guard.record("delete", {"video": video_id, "dmid": dmid}, res)
        return {"dmid": dmid, "recalled": True, "message": res.get("message") if isinstance(res, dict) else res}

    run(body)


# ---- Interactive components (互动弹幕): only the uploader can add them to their own videos.
# Not wrapped by bilibili-api; endpoints verified against a live private video.
interact = typer.Typer(help="互动弹幕（UP 主在自己视频里添加）：投票、关联视频卡片、关注 / 三连按钮。", no_args_is_help=True)
app.add_typer(interact, name="interact")

COMMAND_POST_URL = "https://api.bilibili.com/x/v2/dm/command/post"
COMMAND_REMOVE_URL = "https://api.bilibili.com/x/v2/dm/command/del"
COMMAND_LINK = 2
COMMAND_FOLLOW = 5
COMMAND_VOTE = 9
COMMAND_RESERVE = 10
COMMAND_RATING = 11
RATING_TITLE_MAX = 12
RATING_SKINS = {"star": 1, "heart": 2, "lemon": 3}
RESERVE_TITLE_MIN = 4
RESERVE_VIDEO = 1
RESERVE_LIVE = 2
RESERVE_CANCEL_URL = "https://api.bilibili.com/x/activity/up/reserve/cancel"
VOTE_QUESTION_MAX = 12  # limits reported by /x/v2/dm/command/panel
VOTE_OPTION_MAX = 6
VOTE_DURATION_MS = 7000
FOLLOW_STYLES = {"follow": 0, "triple": 1, "both": 2}  # 仅关注 / 仅三连 / 关注+三连
FOLLOW_X_RANGE = (118, 549)  # position of the button in the player, per the web editor
FOLLOW_Y_RANGE = (82, 293)
MIN_COMPONENT_MS = 5000


async def own_video_page(video_id: str, page: int, cred) -> tuple[dict, dict]:
    ref = parse_video_id(video_id)
    if ref.get("bvid"):
        # creator-side view first: it also covers my videos that are still in review or scheduled
        from .upload import _archive_view

        try:
            view = await _archive_view(cred, ref["bvid"])
        except Exception:
            view = None
        if view and (view.get("archive") or {}).get("aid"):
            arc = view["archive"]
            pages = [{"cid": x.get("cid"), "duration": x.get("duration") or 0} for x in view.get("videos") or []]
            if page > len(pages):
                raise BiliError("invalid_argument", f"该视频只有 {len(pages)} 个分 P。")
            return {"aid": arc["aid"], "bvid": arc.get("bvid"), "owner": {"mid": arc.get("mid")}}, pages[page - 1]
    v = video.Video(**ref, credential=cred)
    info = await v.get_info()
    if str((info.get("owner") or {}).get("mid")) != str(cred.dedeuserid):
        raise BiliError("invalid_argument", "互动弹幕只能加在自己的视频里。")
    pages = info.get("pages") or []
    if page > len(pages):
        raise BiliError("invalid_argument", f"该视频只有 {len(pages)} 个分 P。")
    return info, pages[page - 1]


async def _post_command(cred, info: dict, pg: dict, type_: int, time_s: float, data: dict) -> dict:
    if time_s * 1000 > (pg.get("duration") or 0) * 1000 - MIN_COMPONENT_MS:
        raise BiliError("invalid_argument", f"互动组件至少显示 {MIN_COMPONENT_MS // 1000} 秒，--time 需早于视频结束前 {MIN_COMPONENT_MS // 1000} 秒。")
    guard.check("interact")
    res = await Api(url=COMMAND_POST_URL, method="POST", verify=True, credential=cred).update_data(
        type=type_, aid=info["aid"], cid=pg["cid"], progress=int(time_s * 1000), plat=1, data=json.dumps(data, ensure_ascii=False)
    ).result
    guard.record("interact", {"bvid": info.get("bvid"), "type": type_, "time": time_s, "data": data}, res)
    return res or {}


@interact.command("link")
def interact_link(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    target: str = typer.Argument(..., help="要关联的另一个视频的 BV 号。"),
    text: str = typer.Option(..., "--text", help="卡片上显示的文字。"),
    time: float = typer.Option(..., "--time", min=0, help="出现的时间点，单位秒。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """添加关联视频卡片，点击跳转到另一个视频。"""

    async def body():
        cred = config.require_credential()
        target_bvid = parse_video_id(target).get("bvid")
        if not target_bvid:
            raise BiliError("invalid_argument", "关联视频需要用 BV 号指定。")
        info, pg = await own_video_page(video_id, page, cred)
        if target_bvid == info.get("bvid"):
            raise BiliError("invalid_argument", "不能关联视频自身。")
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "target": target_bvid, "text": text}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.link", "params": params}
        res = await _post_command(cred, info, pg, COMMAND_LINK, time, {"msg": text, "bvid": target_bvid})
        return {**params, "id": res.get("idStr") or res.get("id")}

    run(body)


@interact.command("follow")
def interact_follow(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    time: float = typer.Option(..., "--time", min=0, help="出现的时间点，单位秒。"),
    style: str = typer.Option("both", "--style", help="follow（仅关注）/ triple（仅三连）/ both（关注+三连）。"),
    x: int = typer.Option(300, "--x", help=f"按钮横坐标，{FOLLOW_X_RANGE[0]} 到 {FOLLOW_X_RANGE[1]}。"),
    y: int = typer.Option(150, "--y", help=f"按钮纵坐标，{FOLLOW_Y_RANGE[0]} 到 {FOLLOW_Y_RANGE[1]}。"),
    duration: float = typer.Option(5, "--duration", min=5, help="显示时长，单位秒，最少 5 秒。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """在视频画面里添加关注 / 一键三连按钮。"""

    async def body():
        cred = config.require_credential()
        if style not in FOLLOW_STYLES:
            raise BiliError("invalid_argument", f"--style 只能是 {' / '.join(FOLLOW_STYLES)} 之一。")
        if not FOLLOW_X_RANGE[0] <= x <= FOLLOW_X_RANGE[1] or not FOLLOW_Y_RANGE[0] <= y <= FOLLOW_Y_RANGE[1]:
            raise BiliError("invalid_argument", f"按钮坐标需在 x {FOLLOW_X_RANGE}、y {FOLLOW_Y_RANGE} 范围内。")
        info, pg = await own_video_page(video_id, page, cred)
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "style": style, "x": x, "y": y, "duration": duration}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.follow", "params": params}
        data = {"duration": int(duration * 1000), "posX": x, "posY": y, "type": FOLLOW_STYLES[style]}
        res = await _post_command(cred, info, pg, COMMAND_FOLLOW, time, data)
        return {**params, "id": res.get("idStr") or res.get("id")}

    run(body)


@interact.command("vote")
def interact_vote(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    question: str = typer.Option(..., "--question", help=f"投票问题，最多 {VOTE_QUESTION_MAX} 字。"),
    option: list[str] = typer.Option(..., "--option", help=f"选项，可重复传入 2 到 4 次，每个最多 {VOTE_OPTION_MAX} 字。"),
    time: float = typer.Option(..., "--time", min=0, help="出现的时间点，单位秒。"),
    allow_custom: bool = typer.Option(False, "--allow-custom", help="最后加一个自定义选项：观众点它后发弹幕写自己的答案（占一个选项名额）。"),
    custom_label: str = typer.Option("其他", "--custom-label", help="自定义选项的文字（配合 --allow-custom）。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """在视频里添加投票弹幕（每个视频 1 个）。"""

    async def body():
        cred = config.require_credential()
        q = question.strip()
        opts = [o.strip() for o in option]
        if allow_custom:
            # B 站 marks the self-defined answer on one option (has_self_def), so it gets its own last slot
            if len(opts) > 3:
                raise BiliError("invalid_argument", "开了 --allow-custom 时固定选项最多 3 个（自定义选项占第 4 个）。")
            opts.append(custom_label.strip())
        if not q or len(q) > VOTE_QUESTION_MAX:
            raise BiliError("invalid_argument", f"投票问题需为 1 到 {VOTE_QUESTION_MAX} 字。")
        if not 2 <= len(opts) <= 4:
            raise BiliError("invalid_argument", "投票选项需要 2 到 4 个。")
        if any(not o or len(o) > VOTE_OPTION_MAX for o in opts):
            raise BiliError("invalid_argument", f"每个选项需为 1 到 {VOTE_OPTION_MAX} 字。")
        if any(";" in o for o in opts) or len(set(opts)) != len(opts):
            raise BiliError("invalid_argument", "选项不能包含分号，也不能重复。")
        info, pg = await own_video_page(video_id, page, cred)
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "question": q, "options": opts, "allow_custom": allow_custom}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.vote", "params": params}
        # Field names follow B 站's own interactive-danmaku editor (player/editor bundle).
        data = {
            "duration": VOTE_DURATION_MS, "posX": 300, "posY": 150, "posX_2": 30, "posY_2": 40,
            "pub_dynamic": False, "vote_type": 0, "has_self_def": "1" if allow_custom else "0", "question": q,
        }
        for key, text in zip(("optionA", "optionB", "optionC", "optionD"), opts):
            data[key] = text
        res = await _post_command(cred, info, pg, COMMAND_VOTE, time, data)
        extra = json.loads(res.get("extra") or "{}")
        return {**params, "id": res.get("idStr") or res.get("id"), "vote_id": extra.get("vote_id")}

    run(body)


@interact.command("rating")
def interact_rating(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    title: str = typer.Option(..., "--title", help=f"评分标题，最多 {RATING_TITLE_MAX} 字，如“给这期打个分”。"),
    time: float = typer.Option(..., "--time", min=0, help="出现的时间点，单位秒。"),
    skin: str = typer.Option("star", "--skin", help="图标样式：star（星星）/ heart（爱心）/ lemon（柠檬）。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """在视频里添加评分弹幕，观众可以打 1 到 5 分。"""

    async def body():
        cred = config.require_credential()
        t = title.strip()
        if not t or len(t) > RATING_TITLE_MAX:
            raise BiliError("invalid_argument", f"评分标题需为 1 到 {RATING_TITLE_MAX} 字。")
        if skin not in RATING_SKINS:
            raise BiliError("invalid_argument", f"--skin 只能是 {' / '.join(RATING_SKINS)} 之一。")
        info, pg = await own_video_page(video_id, page, cred)
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "title": t, "skin": skin}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.rating", "params": params}
        data = {"msg": t, "skin": RATING_SKINS[skin], "duration": MIN_COMPONENT_MS, "posX_2": 30, "posY_2": 40}
        res = await _post_command(cred, info, pg, COMMAND_RATING, time, data)
        extra = json.loads(res.get("extra") or "{}")
        return {**params, "id": res.get("idStr") or res.get("id"), "grade_id": extra.get("grade_id")}

    run(body)


@interact.command("reserve")
def interact_reserve(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    title: str = typer.Option(..., "--title", help=f"预告标题，至少 {RESERVE_TITLE_MIN} 个字，如“下期更精彩敬请期待”。"),
    time: float = typer.Option(..., "--time", min=0, help="出现的时间点，单位秒。"),
    live: Optional[str] = typer.Option(
        None, "--live", help="改为直播预约，值为开播时间（ISO，默认北京时间）；不传则为下一个稿件的预约。"
    ),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """添加“下期预告”：观众可以点预约，下个稿件发布（或开播）时收到提醒。会同时在账号上创建一个预约。"""

    async def body():
        cred = config.require_credential()
        t = title.strip()
        if len(t) < RESERVE_TITLE_MIN:
            raise BiliError("invalid_argument", f"预告标题至少 {RESERVE_TITLE_MIN} 个字。")
        live_ts = None
        if live:
            try:
                dt = datetime.fromisoformat(live)
            except ValueError as exc:
                raise BiliError("invalid_argument", f"--live 需要 ISO 时间，如 2026-10-25T20:00，收到 {live!r}。") from exc
            live_ts = int((dt if dt.tzinfo else dt.replace(tzinfo=CST)).timestamp())
            if live_ts <= _time.time():
                raise BiliError("invalid_argument", "直播时间必须在未来。")
        info, pg = await own_video_page(video_id, page, cred)
        params = {"bvid": info.get("bvid"), "page": page, "time": time, "title": t, "kind": "live" if live_ts else "video", "live_time": iso_time(live_ts)}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.reserve", "params": params}
        data = {"msg": t, "reserve_type": RESERVE_LIVE if live_ts else RESERVE_VIDEO, "duration": MIN_COMPONENT_MS, "posX_2": 30, "posY_2": 40}
        if live_ts:
            data["live_stime"] = live_ts
        res = await _post_command(cred, info, pg, COMMAND_RESERVE, time, data)
        extra = json.loads(res.get("extra") or "{}")
        return {
            **params,
            "id": res.get("idStr") or res.get("id"),
            "reserve_id": extra.get("reserve_id"),
            "note": "已在账号上创建预约；用 `interact remove` 删除时会一并撤销预约，已预约的观众会收到提醒。",
        }

    run(body)


@interact.command("list")
def interact_list(
    video_id: str = typer.Argument(..., help="BV 号、av 号或视频链接。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """列出视频里的互动弹幕组件（投票、关联视频、关注按钮等）。"""

    async def body():
        v = video.Video(**parse_video_id(video_id), credential=config.optional_credential())
        view = await v.get_danmaku_view(page_index=page - 1)
        if output.settings.raw:
            return view.get("command_dms") or []
        names = {"#LINK#": "link", "#ATTENTION#": "follow", "#UP#": "up", "#VOTE#": "vote", "#GRADE#": "grade", "#RESERVE#": "reserve"}
        items = []
        for d in view.get("command_dms") or []:
            extra = d.get("extra") or {}
            item = {
                "id": str(d.get("id")),
                "kind": names.get(d.get("commend") or d.get("command"), d.get("commend") or d.get("command")),
                "time": (d.get("progress") or 0) / 1000,
                "text": d.get("content") or None,
                "duration": (extra.get("duration") or 0) / 1000 or None,
            }
            if item["kind"] == "link":
                item["target"] = {"bvid": extra.get("bvid"), "title": extra.get("title")}
            if item["kind"] == "follow":
                item["style"] = {v: k for k, v in FOLLOW_STYLES.items()}.get(extra.get("type"))
            if item["kind"] == "vote":
                item["text"] = extra.get("question")
                item["vote"] = {
                    "vote_id": extra.get("vote_id"),
                    "total": extra.get("cnt"),
                    "options": [{"text": o.get("desc"), "count": o.get("cnt")} for o in extra.get("options") or []],
                }
            if item["kind"] == "grade":
                item["kind"] = "rating"
                item["rating"] = {"grade_id": extra.get("grade_id"), "count": extra.get("count"), "average": extra.get("avg_score")}
            if item["kind"] == "reserve":
                item["reserve"] = {"reserve_id": extra.get("reserve_id"), "count": extra.get("reserve_count"), "kind": "live" if extra.get("reserve_type") == RESERVE_LIVE else "video"}
            items.append(item)
        return {"items": sorted(items, key=lambda i: i["time"])}

    run(body)


@interact.command("remove")
def interact_remove(
    video_id: str = typer.Argument(..., help="自己的视频：BV 号、av 号或链接。"),
    component_id: int = typer.Argument(..., help="组件 ID（`interact list` 返回的 id）。"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="分 P 序号，从 1 开始。"),
) -> None:
    """删除自己视频里的一个互动弹幕组件。下期预告会连同账号上的预约一起撤销（已预约的观众会收到提醒）。"""

    async def body():
        cred = config.require_credential()
        info, pg = await own_video_page(video_id, page, cred)
        view = await video.Video(bvid=info["bvid"], credential=cred).get_danmaku_view(page_index=page - 1)
        target = next((d for d in view.get("command_dms") or [] if str(d.get("id")) == str(component_id)), None)
        if target is None:
            raise BiliError("not_found", f"视频里没有 ID 为 {component_id} 的互动组件。", hint="用 `bili danmaku interact list` 查看。")
        reserve_id = (target.get("extra") or {}).get("reserve_id") if (target.get("commend") or target.get("command")) == "#RESERVE#" else None
        params = {"bvid": info.get("bvid"), "id": component_id, "cancel_reserve": reserve_id}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "danmaku.interact.remove", "params": params}
        guard.check("delete")
        if reserve_id:
            # Deleting only the component would leave the reservation on the account;
            # cancelling the reservation removes its component too (same as the web editor).
            await Api(url=RESERVE_CANCEL_URL, method="POST", verify=True, credential=cred).update_data(**{"from": 2, "id": reserve_id}).result
        else:
            await Api(url=COMMAND_REMOVE_URL, method="POST", verify=True, credential=cred).update_data(dmid=component_id, oid=pg["cid"]).result
        guard.record("delete", params, "ok")
        return {"id": str(component_id), "removed": True, "reserve_cancelled": reserve_id}

    run(body)
