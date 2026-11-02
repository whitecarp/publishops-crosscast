"""bili me: view and edit my own profile."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import typer
from bilibili_api import Credential
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import run, web_client

app = typer.Typer(help="我的账号：资料、创作中心数据；修改昵称、签名、头像。", no_args_is_help=True)

ACCOUNT_URL = "https://api.bilibili.com/x/member/web/account"
MYINFO_URL = "https://api.bilibili.com/x/space/myinfo"
UPDATE_URL = "https://api.bilibili.com/x/member/web/update"
# Not wrapped by bilibili-api; same request as the web settings page and PiliPlus.
FACE_UPDATE_URL = "https://api.bilibili.com/x/member/web/face/update"

SIGN_MAX_LEN = 70
AVATAR_SIZE = 512


async def _account(cred: Credential) -> dict[str, Any]:
    return await Api(url=ACCOUNT_URL, method="GET", credential=cred).result


async def _update_profile(cred: Credential, **changes: str) -> None:
    """The update endpoint wants every field, so resend the current values with the changes applied."""
    acc = await _account(cred)
    data = {"uname": acc.get("uname"), "usersign": acc.get("sign"), "sex": acc.get("sex"), "birthday": acc.get("birthday")}
    data.update(changes)
    await Api(url=UPDATE_URL, method="POST", verify=True, credential=cred).update_data(**data).result


@app.command()
def show() -> None:
    """我的资料：UID、昵称、签名、等级、硬币、头像、改名是否免费。"""

    async def body():
        cred = config.require_credential()
        info = await Api(url=MYINFO_URL, method="GET", credential=cred).result
        acc = await _account(cred)
        if output.settings.raw:
            return {"myinfo": info, "account": acc}
        return {
            "mid": info.get("mid"),
            "name": info.get("name"),
            "sign": info.get("sign"),
            "level": info.get("level"),
            "coins": info.get("coins"),
            "face": info.get("face"),
            "sex": acc.get("sex"),
            "rename_free": bool(acc.get("nick_free")),
            "url": f"https://space.bilibili.com/{info.get('mid')}",
        }

    run(body)


@app.command()
def favs() -> None:
    """我的收藏夹：ID、名称、视频数（`bili video fav --folder` 用）。"""

    async def body():
        from .video import _folders

        return {"folders": await _folders(config.require_credential())}

    run(body)


STATS_PERIODS = {"yesterday": -1, "week": 0, "month": 1, "3months": 2}


def _pct(basis_points: Any) -> float | None:
    """Creator-center ratios come in 1/10000 (9695 -> 96.95%)."""
    return None if basis_points is None else round(basis_points / 100, 2)


def summarize_stats(overview: dict, fans: dict, source: dict, compare: dict, survey: dict) -> dict[str, Any]:
    # a new account with no data gets null for overview / compare / audience and {"<date>": null} for survey
    overview, fans, source, compare, survey = overview or {}, fans or {}, source or {}, compare or {}, survey or {}

    def pair(key: str) -> dict[str, Any]:
        return {"now": overview.get(key), "previous": overview.get(f"{key}_last")}

    platforms = {k.removeprefix("new_"): v for k, v in (source.get("play_proportion") or {}).items() if k.startswith("new_") and v}
    audience = source.get("audience_proportion") or {}
    viewers = (audience.get("fans") or 0) + (audience.get("guest") or 0)
    day = next(iter(survey.values()), None) or {}
    return {
        "data_date": str(overview.get("log_date") or fans.get("log_date") or "") or None,
        "plays": pair("play"),
        "visitors": pair("visitor"),
        "new_fans": pair("fan"),
        # this endpoint currently reports interactions as 0; leave them out rather than mislead
        **({"interactions": inter} if any((inter := {k: overview.get(k) for k in ("like", "fav", "coin", "comment", "dm", "share")}).values()) else {}),
        "fans": {
            "total": fans.get("all_fans"),
            "followed": fans.get("follow"),
            "unfollowed": fans.get("unfollow"),
            "interacting_percent": _pct(fans.get("interact_fans_per")),
        },
        "play_platforms_percent": {k: _pct(v) for k, v in sorted(platforms.items(), key=lambda kv: -kv[1])},
        "plays_from_fans_percent": round(audience.get("fans", 0) * 100 / viewers, 2) if viewers else None,
        "beats_similar_ups_percent": {k: _pct(compare.get(f"{k}_pass_peer")) for k in ("play", "like", "new_fans", "replydm")},
        "top_videos_yesterday": [{"bvid": a.get("bvid"), "title": a.get("title"), "plays": a.get("incr")} for a in (day.get("arc_inc") or [])[:5]],
    }


@app.command()
def stats(
    period: str = typer.Option("week", "--period", help="统计周期：yesterday / week / month / 3months（和上一周期对比）。"),
) -> None:
    """创作中心数据：播放、访客、涨粉、互动、播放端分布、粉丝占比、超过多少同类 UP、昨日涨得最多的视频。数据次日更新。累计总量和昨日增量看 `bili data overview`。"""

    async def body():
        from bilibili_api import creative_center as cc

        if period not in STATS_PERIODS:
            raise BiliError("invalid_argument", f"--period 只能是 {' / '.join(STATS_PERIODS)} 之一。")
        cred = config.require_credential()
        p = STATS_PERIODS[period]
        overview = await cc.get_overview(cred, cc.GraphPeriod(p))
        fans = await cc.get_fan_overview(cred, cc.FanGraphPeriod(p))
        source, compare, survey = await cc.get_video_source(cred), await cc.get_compare(cred), await cc.get_video_survey(cred)
        if output.settings.raw:
            return {"overview": overview, "fans": fans, "source": source, "compare": compare, "survey": survey}
        return {"period": period, **summarize_stats(overview, fans, source, compare, survey), "per_video_with": "bili upload stats <BV>"}

    run(body)


@app.command("set-sign")
def set_sign(sign: str = typer.Argument(..., help=f"新的个性签名，最多 {SIGN_MAX_LEN} 字；传空字符串可清空。")) -> None:
    """修改个性签名。"""

    async def body():
        cred = config.require_credential()
        if len(sign) > SIGN_MAX_LEN:
            raise BiliError("invalid_argument", f"签名最多 {SIGN_MAX_LEN} 字，当前 {len(sign)} 字。")
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_sign", "params": {"sign": sign}}
        guard.check("profile")
        await _update_profile(cred, usersign=sign)
        guard.record("profile", {"sign": sign}, "ok")
        return {"sign": sign, "updated": True}

    run(body)


@app.command("set-name")
def set_name(
    name: str = typer.Argument(..., help="新的昵称。"),
    allow_cost: bool = typer.Option(
        False, "--allow-cost", help="改名需要消耗硬币时，必须显式传入此参数才会执行。"
    ),
) -> None:
    """修改昵称。非免费改名会消耗硬币，需要 --allow-cost；昵称是否可用会先检查。"""

    async def body():
        from bilibili_api.user import check_nickname

        cred = config.require_credential()
        ok, reason = await check_nickname(name)
        if not ok:
            raise BiliError("invalid_argument", f"昵称不可用：{reason}")
        acc = await _account(cred)
        free = bool(acc.get("nick_free"))
        if not free and not allow_cost:
            raise BiliError(
                "invalid_argument",
                "本次改名需要消耗硬币。",
                hint="确认愿意花费硬币后，加上 --allow-cost 重新执行。",
                detail={"rename_free": False},
            )
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_name", "params": {"name": name, "rename_free": free}}
        guard.check("profile")
        await _update_profile(cred, uname=name)
        guard.record("profile", {"name": name}, "ok")
        return {"name": name, "updated": True, "rename_free": free}

    run(body)


def _square_png(path: Path) -> bytes:
    """Center-crop to a square and downscale, like the web uploader does."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(path)
        img.load()
    except (OSError, UnidentifiedImageError) as exc:
        raise BiliError("invalid_argument", f"无法读取图片 {path}：{exc}") from exc
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    side = min(img.size)
    left, upper = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, upper, left + side, upper + side))
    if side > AVATAR_SIZE:
        img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.command("set-avatar")
def set_avatar(image: Path = typer.Argument(..., help="头像图片路径（会居中裁成正方形）。")) -> None:
    """修改头像。"""

    async def body():
        cred = config.require_credential()
        png = _square_png(image)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_avatar", "params": {"image": str(image), "bytes": len(png)}}
        guard.check("profile")
        async with web_client(cred, referer="https://account.bilibili.com/", timeout=30) as client:
            resp = await client.post(
                FACE_UPDATE_URL,
                params={"csrf": cred.bili_jct},
                data={"dopost": "save", "DisplayRank": "10000"},
                files={"face": ("avatar.png", png, "image/png")},
            )
            resp.raise_for_status()
            res = resp.json()
        if res.get("code") != 0:
            from bilibili_api.exceptions import ResponseCodeException

            raise ResponseCodeException(res.get("code"), res.get("message"), res)
        guard.record("profile", {"avatar": str(image)}, "ok")
        return {
            "updated": True,
            "face": res.get("data"),
            "note": "新头像需要通过 B 站审核后才会显示，`bili me show` 在此之前仍返回旧头像。",
        }

    run(body)
