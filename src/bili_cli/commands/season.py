"""bili season: manage my 合集 (seasons) — list, create, add / remove videos, delete."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer
from bilibili_api import Picture
from bilibili_api import video_uploader as vu
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import iso_time, parse_video_id, run

app = typer.Typer(help="合集：列出、创建、往合集里加 / 移出视频、删除合集。", no_args_is_help=True)

# Not wrapped by bilibili-api (it only has the old 系列); same requests as the creative center web page.
BASE = "https://member.bilibili.com/x2/creative/web"
SEASON_STATES = {0: "正常", -6: "审核中"}


def _api(path: str, method: str, cred, *, json_body: bool = False) -> Api:
    api = Api(url=f"{BASE}/{path}", method=method, verify=True, credential=cred, no_csrf=json_body, json_body=json_body)
    return api.update_params(csrf=cred.bili_jct) if json_body else api


def summarize_season(entry: dict[str, Any]) -> dict[str, Any]:
    s = entry.get("season") or {}
    sections = (entry.get("sections") or {}).get("sections") or []
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "state": SEASON_STATES.get(s.get("state"), s.get("state")),
        "videos": s.get("ep_count") if s.get("ep_count") else len(entry.get("part_episodes") or []),
        "sections": [{"id": x.get("id"), "title": x.get("title")} for x in sections],
        "episodes": [{"bvid": e.get("bvid"), "title": e.get("title")} for e in entry.get("part_episodes") or []],
        "updated": iso_time(s.get("mtime")),
    }


async def _seasons(cred) -> list[dict[str, Any]]:
    data = await _api("seasons", "GET", cred).update_params(pn=1, ps=50, order="mtime", sort="desc", draft=1).result
    return data.get("seasons") or []


async def _section_of(cred, season_id: int, section_id: int | None) -> int:
    """The section to work in: the given one (checked) or the season's first section."""
    for entry in await _seasons(cred):
        if (entry.get("season") or {}).get("id") != season_id:
            continue
        ids = [x.get("id") for x in (entry.get("sections") or {}).get("sections") or []]
        if not ids:
            raise BiliError("api_error", f"合集 {season_id} 没有小节信息。")
        if section_id is None:
            return ids[0]
        if section_id not in ids:
            raise BiliError("invalid_argument", f"小节 {section_id} 不属于合集 {season_id}。", detail={"sections": ids})
        return section_id
    raise BiliError("not_found", f"在你的合集里找不到 ID {season_id}。", hint="运行 `bili season list` 查看合集 ID。")


SECTION_OPT = typer.Option(None, "--section", help="小节 ID（合集分了小节时用）；默认第一个小节。")


async def own_episode(cred, video_id: str) -> dict[str, Any]:
    """aid / cid / title of one of my videos, read from the creator side so reviewing and scheduled videos work too
    (the public view API answers -404 until a video is published)."""
    from bilibili_api import video

    from .upload import _archive_view

    ref = parse_video_id(video_id)
    if ref.get("bvid"):
        try:
            view = await _archive_view(cred, ref["bvid"])
        except Exception as exc:  # not mine, or no such archive
            raise BiliError("invalid_argument", f"{ref['bvid']} 不是你自己的稿件，或者稿件不存在。", detail={"error": str(exc)}) from exc
        arc, pages = view.get("archive") or {}, view.get("videos") or []
        if not pages or not pages[0].get("cid"):
            raise BiliError("api_error", f"{ref['bvid']} 还没有分 P 信息，稍后再试。")
        return {"title": arc.get("title"), "aid": arc.get("aid"), "cid": pages[0]["cid"], "charging_pay": 0}
    info = await video.Video(**ref, credential=cred).get_info()
    if info.get("owner", {}).get("mid") != config.my_mid(cred):
        raise BiliError("invalid_argument", f"{info.get('bvid')} 不是你自己的视频，不能加进你的合集。")
    return {"title": info.get("title"), "aid": info.get("aid"), "cid": info.get("cid"), "charging_pay": 0}


async def add_episodes(cred, section_id: int, episodes: list[dict[str, Any]]) -> None:
    await _api("season/section/episodes/add", "POST", cred, json_body=True).update_data(
        sectionId=section_id, episodes=episodes, csrf=cred.bili_jct
    ).result


@app.command("list")
def list_() -> None:
    """我的合集：ID、标题、审核状态、小节，以及里面的视频。"""

    async def body():
        cred = config.require_credential()
        seasons = await _seasons(cred)
        if output.settings.raw:
            return {"seasons": seasons}
        return {"seasons": [summarize_season(e) for e in seasons]}

    run(body)


@app.command()
def create(
    title: str = typer.Argument(..., help="合集标题。"),
    cover: Path = typer.Option(..., "--cover", help="合集封面图片（B 站要求必须有）。"),
    desc: str = typer.Option("", "--desc", help="合集简介。"),
) -> None:
    """新建合集。B 站会人工审核，审核通过前状态为“审核中”。"""

    async def body():
        cred = config.require_credential()
        if not title.strip():
            raise BiliError("invalid_argument", "合集标题不能为空。")
        if not cover.is_file():
            raise BiliError("invalid_argument", f"封面文件不存在：{cover}")
        params = {"title": title, "cover": str(cover), "desc": desc}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "season.create", "params": params}
        guard.check("season")
        cover_url = await vu.upload_cover(Picture.from_file(str(cover)), cred)
        season_id = await _api("season/add", "POST", cred).update_data(title=title, desc=desc, cover=cover_url, season_price=0).result
        guard.record("season", params, {"id": season_id})
        return {"id": season_id, "title": title, "next": f"审核通过前状态为“审核中”。加视频：`bili season add {season_id} <BV号>`"}

    run(body)


@app.command()
def add(
    season_id: int = typer.Argument(..., help="合集 ID（见 `bili season list`）。"),
    videos: list[str] = typer.Argument(..., help="要加入的自己的视频：BV 号、av 号或链接，可传多个。"),
    section: Optional[int] = SECTION_OPT,
) -> None:
    """把自己的视频加进合集（加在小节末尾）。一个视频只能属于一个合集；审核中、定时发布的稿件也能加。"""

    async def body():
        cred = config.require_credential()
        section_id = await _section_of(cred, season_id, section)
        episodes = [await own_episode(cred, v) for v in videos]
        params = {"season_id": season_id, "section_id": section_id, "videos": [e["title"] for e in episodes]}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "season.add", "params": params}
        guard.check("season")
        await add_episodes(cred, section_id, episodes)
        guard.record("season", params, "ok")
        return {"season_id": season_id, "section_id": section_id, "added": [e["title"] for e in episodes]}

    run(body)


@app.command()
def remove(
    season_id: int = typer.Argument(..., help="合集 ID。"),
    video_id: str = typer.Argument(..., help="要移出的视频：BV 号、av 号或链接。"),
    section: Optional[int] = SECTION_OPT,
) -> None:
    """把一个视频移出合集（视频本身不受影响）。"""

    async def body():
        from bilibili_api import video

        cred = config.require_credential()
        section_id = await _section_of(cred, season_id, section)
        aid = (await video.Video(**parse_video_id(video_id), credential=cred).get_info()).get("aid")
        detail = await _api("season/section", "GET", cred).update_params(id=section_id).result
        episode = next((e for e in detail.get("episodes") or [] if e.get("aid") == aid), None)
        if episode is None:
            raise BiliError("not_found", f"合集 {season_id} 的这个小节里没有 {video_id}。")
        params = {"season_id": season_id, "section_id": section_id, "episode_id": episode.get("id"), "title": episode.get("title")}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "season.remove", "params": params}
        guard.check("season")
        await _api("season/section/episode/del", "POST", cred).update_data(id=episode.get("id")).result
        guard.record("season", params, "ok")
        return {"season_id": season_id, "removed": episode.get("title")}

    run(body)


@app.command()
def delete(season_id: int = typer.Argument(..., help="要删除的合集 ID。")) -> None:
    """删除整个合集（里面的视频不受影响，只是不再属于这个合集）。"""

    async def body():
        cred = config.require_credential()
        await _section_of(cred, season_id, None)  # make sure it's one of mine
        if output.settings.dry_run:
            return {"dry_run": True, "action": "season.delete", "params": {"id": season_id}}
        guard.check("season")
        await _api("season/del", "POST", cred).update_data(id=season_id).result
        guard.record("season", {"id": season_id}, "ok")
        return {"id": season_id, "deleted": True}

    run(body)
