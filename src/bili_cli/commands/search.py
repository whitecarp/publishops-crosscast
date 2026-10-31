"""bili search: find videos and users, e.g. for topic research."""

from __future__ import annotations

import typer
from bilibili_api import search

from .. import output
from ..errors import BiliError
from ..runtime import iso_time, run, strip_html

app = typer.Typer(help="搜索视频与用户。", no_args_is_help=True)

VIDEO_ORDERS = {
    "default": search.OrderVideo.TOTALRANK,
    "views": search.OrderVideo.CLICK,
    "new": search.OrderVideo.PUBDATE,
    "danmaku": search.OrderVideo.DM,
    "favorites": search.OrderVideo.STOW,
    "comments": search.OrderVideo.SCORES,
}
USER_ORDERS = {
    "default": None,
    "fans": search.OrderUser.FANS,
    "level": search.OrderUser.LEVEL,
}


def _order(table: dict, value: str):
    if value not in table:
        raise BiliError("invalid_argument", f"--order 只能是 {', '.join(table)} 之一。")
    return table[value]


@app.command("video")
def video_(
    keyword: str = typer.Argument(..., help="搜索关键词。"),
    order: str = typer.Option("default", "--order", help="排序：default / views / new / danmaku / favorites / comments。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, max=50, help="每页条数。"),
) -> None:
    """搜索视频。"""

    async def body():
        data = await search.search_by_type(
            keyword,
            search_type=search.SearchObjectType.VIDEO,
            order_type=_order(VIDEO_ORDERS, order),
            page=page,
            page_size=size,
        )
        if output.settings.raw:
            return data
        return {
            "keyword": keyword,
            "page": page,
            "total": data.get("numResults"),
            "items": [
                {
                    "bvid": r.get("bvid"),
                    "title": strip_html(r.get("title")),
                    "author": {"mid": r.get("mid"), "name": r.get("author")},
                    "duration": r.get("duration"),
                    "play": r.get("play"),
                    "danmaku": r.get("video_review"),
                    "favorites": r.get("favorites"),
                    "pubdate": iso_time(r.get("pubdate")),
                    "desc": strip_html(r.get("description")),
                }
                for r in data.get("result") or []
            ],
        }

    run(body)


@app.command("user")
def user_(
    keyword: str = typer.Argument(..., help="搜索关键词。"),
    order: str = typer.Option("default", "--order", help="排序：default / fans / level。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
) -> None:
    """搜索用户（UP 主）。"""

    async def body():
        data = await search.search_by_type(
            keyword,
            search_type=search.SearchObjectType.USER,
            order_type=_order(USER_ORDERS, order),
            page=page,
        )
        if output.settings.raw:
            return data
        return {
            "keyword": keyword,
            "page": page,
            "total": data.get("numResults"),
            "items": [
                {
                    "mid": r.get("mid"),
                    "name": strip_html(r.get("uname")),
                    "sign": r.get("usign"),
                    "fans": r.get("fans"),
                    "videos": r.get("videos"),
                    "level": r.get("level"),
                }
                for r in data.get("result") or []
            ],
        }

    run(body)
