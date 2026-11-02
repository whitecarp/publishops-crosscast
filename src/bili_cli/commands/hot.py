"""bili hot: what's trending — hot search, popular, weekly picks, all-time classics, rankings."""

from __future__ import annotations

from typing import Optional

import typer
from bilibili_api import hot, rank, search
from bilibili_api.utils.network import Api

from .. import config, output
from ..cards import filter_unseen, video_card
from ..errors import BiliError
from ..runtime import run

app = typer.Typer(help="热门：热搜词、综合热门、每周必看、入站必刷、排行榜。", no_args_is_help=True)

UNSEEN_OPT = typer.Option(False, "--unseen", help="过滤掉已经用 `bili video watch` 看过的视频。")
HOT_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/square"

RANK_ZONES = {
    "all": rank.RankType.All, "anime": rank.RankType.Douga, "music": rank.RankType.Music,
    "dance": rank.RankType.Dance, "game": rank.RankType.Game, "knowledge": rank.RankType.Knowledge,
    "tech": rank.RankType.Technology, "sports": rank.RankType.Sports, "car": rank.RankType.Car,
    "life": rank.RankType.Life, "food": rank.RankType.Food, "animal": rank.RankType.Animal,
    "kichiku": rank.RankType.Kichiku, "fashion": rank.RankType.Fashion, "ent": rank.RankType.Ent,
    "film": rank.RankType.Cinephile, "original": rank.RankType.Original, "rookie": rank.RankType.Rookie,
}


@app.command("search")
def search_(limit: int = typer.Option(10, "--limit", min=1, max=50, help="返回条数。")) -> None:
    """B 站热搜榜（搜索框下的“bilibili热搜”）。"""

    async def body():
        try:
            data = await Api(url=HOT_SEARCH_URL, method="GET", wbi=True, credential=config.optional_credential()).update_params(limit=limit).result
            items = (data.get("trending") or {}).get("list") or []
            source = "search/square"
        except Exception:  # noqa: BLE001 - older endpoint as fallback
            data = await search.get_hot_search_keywords()
            items, source = data.get("list") or [], "main/hotword"
        if output.settings.raw:
            return data
        return {
            "source": source,
            "items": [
                {"rank": i + 1, "keyword": it.get("keyword"), "title": it.get("show_name") or it.get("keyword"), "heat": it.get("heat_score")}
                for i, it in enumerate(items[:limit])
            ],
            "next": "用 `bili search video <关键词>` 查看相关视频。",
        }

    run(body)


@app.command()
def popular(
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, max=50, help="每页条数。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """综合热门：当前全站热门视频，附推荐理由（如“百万播放”）。"""

    async def body():
        data = await hot.get_hot_videos(pn=page, ps=size)
        if output.settings.raw:
            return data
        return {"page": page, "items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen), "no_more": data.get("no_more")}

    run(body)


@app.command()
def weekly(
    issue: Optional[int] = typer.Option(None, "--issue", help="期数，默认最新一期。"),
    list_issues: bool = typer.Option(False, "--list", help="只列出最近的期数和主题。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """每周必看：B 站官方每周精选。"""

    async def body():
        issues = (await hot.get_weekly_hot_videos_list()).get("list") or []
        if list_issues:
            return {"issues": [{"issue": i.get("number"), "name": i.get("name"), "subject": i.get("subject")} for i in issues[:20]]}
        number = issue or (issues[0]["number"] if issues else None)
        if number is None:
            raise BiliError("not_found", "没有找到每周必看。")
        data = await hot.get_weekly_hot_videos(number)
        if output.settings.raw:
            return data
        conf = data.get("config") or {}
        return {"issue": number, "name": conf.get("name"), "subject": conf.get("subject"), "items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen)}

    run(body)


@app.command()
def precious(unseen: bool = UNSEEN_OPT) -> None:
    """入站必刷：B 站官方收录的经典视频。"""

    async def body():
        data = await hot.get_history_popular_videos()
        if output.settings.raw:
            return data
        return {"items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen)}

    run(body)


@app.command("rank")
def rank_(
    zone: str = typer.Option("all", "--zone", help=f"分区：{' / '.join(RANK_ZONES)}。"),
    limit: int = typer.Option(30, "--limit", min=1, max=100, help="返回条数。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """排行榜：全站或各分区最近三天的排行。"""

    async def body():
        if zone not in RANK_ZONES:
            raise BiliError("invalid_argument", f"--zone 只能是 {' / '.join(RANK_ZONES)} 之一。")
        data = await rank.get_rank(RANK_ZONES[zone])
        if output.settings.raw:
            return data
        cards = [{"rank": i + 1, **video_card(v)} for i, v in enumerate(data.get("list") or [])]
        return {"zone": zone, "items": filter_unseen(cards, unseen)[:limit]}

    run(body)
