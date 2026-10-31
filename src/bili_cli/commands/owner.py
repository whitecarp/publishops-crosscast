"""bili owner: the human this agent works for (used by `--at owner` and `dm send owner`)."""

from __future__ import annotations

import typer
from bilibili_api import user

from .. import config
from ..errors import BiliError
from ..runtime import run

app = typer.Typer(help="设置“主人”：Agent 为谁服务。之后可用 `--at owner` 在评论里 @ 主人、`dm send owner` 给主人发私信。", no_args_is_help=True)


async def resolve_user(mid: int) -> dict:
    info = await user.User(mid, credential=config.optional_credential()).get_user_info()
    return {"mid": mid, "name": info.get("name")}


@app.command("set")
def set_(mid: int = typer.Argument(..., help="主人的 B 站 UID（个人空间链接里的数字）。")) -> None:
    """设置主人。会读取并保存主人当前的昵称（@ 时需要昵称完全一致）。"""

    async def body():
        owner = await resolve_user(mid)
        if not owner.get("name"):
            raise BiliError("not_found", f"找不到 UID {mid} 对应的用户。")
        config.save_owner(owner)
        return {"owner": owner}

    run(body)


@app.command()
def show() -> None:
    """查看当前设置的主人，并刷新其昵称（昵称变了 @ 会失效）。"""

    async def body():
        owner = config.load_owner()
        if not owner:
            raise BiliError("not_found", "还没有设置主人。", hint="运行 `bili owner set <UID>`。")
        fresh = await resolve_user(owner["mid"])
        if fresh.get("name") and fresh["name"] != owner.get("name"):
            config.save_owner(fresh)
            return {"owner": fresh, "renamed_from": owner.get("name")}
        return {"owner": owner}

    run(body)


@app.command()
def clear() -> None:
    """清除主人设置。"""

    async def body():
        config.owner_path().unlink(missing_ok=True)
        return {"owner": None}

    run(body)
