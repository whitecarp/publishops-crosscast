"""bili dm autoreply: B 站's own private-message auto-reply (needs 1000+ followers).

Unlike an agent answering DMs itself, these replies are sent by B 站 even when no
agent is running, to everyone who triggers them — so every change is shown with
--dry-run first and the skill asks the user to confirm. Same requests as the web
message center (message.bilibili.com/#/autoreply).
"""

from __future__ import annotations

from typing import Any, Optional

import typer
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import read_text, run

app = typer.Typer(help="B 站自带的私信自动回复：被关注、收到消息、大航海上船、关键词回复（需 1000 粉以上）。", no_args_is_help=True)

VC = "https://api.vc.bilibili.com"
SETTING_URL = f"{VC}/link_setting/v1/link_setting/get"
SETTING_SET_URL = f"{VC}/link_setting/v1/link_setting/set"
TEXT_URL = f"{VC}/x/im/auto_reply/get_reply_text"
TEXT_SET_URL = f"{VC}/x/im/auto_reply/set_reply_text"
TEXT_DEL_URL = f"{VC}/x/im/auto_reply/del_reply_text"

# kind -> (reply type, switch field in link_setting)
KINDS = {"follow": (1, "followed_reply"), "keyword": (2, "keys_reply"), "message": (3, "recv_reply"), "voyage": (5, "voyage_reply")}
SIMPLE_KINDS = ("follow", "message", "voyage")
REPLY_MAX = 500
RULE_NAME_MAX = 30
KEYWORDS_MAX = 20
WEB = {"build": 0, "mobi_app": "web"}


def _kind(kind: str, allowed=tuple(KINDS)) -> tuple[int, str]:
    if kind not in allowed:
        raise BiliError("invalid_argument", f"类型只能是 {' / '.join(allowed)} 之一。")
    return KINDS[kind]


async def _texts(cred, reply_type: int) -> list[dict[str, Any]]:
    data = await Api(url=TEXT_URL, method="GET", credential=cred).update_params(**{"type[]": reply_type}, **WEB).result
    return (data or {}).get("texts") or []


def summarize(settings: dict[str, Any], texts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in SIMPLE_KINDS:
        rows = texts.get(kind) or []
        out[kind] = {"on": settings.get(KINDS[kind][1]) == 1, "reply": rows[0].get("reply") if rows else None}
    out["keyword"] = {
        "on": settings.get("keys_reply") == 1,
        "rules": [
            {"id": r.get("id"), "name": r.get("title"), "exact": _split(r.get("key1")), "fuzzy": _split(r.get("key2")), "reply": r.get("reply")}
            for r in texts.get("keyword") or []
        ],
    }
    return out


def _split(keys: str | None) -> list[str]:
    return [k.strip() for k in (keys or "").replace(",", "，").split("，") if k.strip()]


def _check_reply(text: str) -> None:
    if not text.strip():
        raise BiliError("invalid_argument", "回复内容不能为空。")
    if len(text) > REPLY_MAX:
        raise BiliError("invalid_argument", f"回复内容最多 {REPLY_MAX} 字，当前 {len(text)} 字。")


@app.command()
def show() -> None:
    """当前的自动回复：各类开关、回复内容、关键词规则。"""

    async def body():
        cred = config.require_credential()
        settings = await Api(url=SETTING_URL, method="GET", credential=cred).update_params(**WEB).result
        texts = {kind: await _texts(cred, t) for kind, (t, _) in KINDS.items()}
        if output.settings.raw:
            return {"settings": settings, "texts": texts}
        return summarize(settings or {}, texts)

    run(body)


async def _switch(cred, field: str, on: bool) -> None:
    await Api(url=SETTING_SET_URL, method="POST", verify=True, credential=cred).update_data(**{field: 1 if on else 0}, **WEB).result


@app.command("set")
def set_(
    kind: str = typer.Argument(..., help="follow（被关注时）/ message（收到私信时）/ voyage（大航海上船时）。"),
    text: Optional[str] = typer.Argument(None, help=f"回复内容，最多 {REPLY_MAX} 字；表情写 [笑哭] 这类文字。也可用 --file。"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="从文件读取回复内容，`-` 表示 stdin。"),
    keep_off: bool = typer.Option(False, "--keep-off", help="只保存内容，不打开这个开关。"),
) -> None:
    """设置一类自动回复的内容，并打开它的开关。对同一个人一天只自动回复一次。"""

    async def body():
        from pathlib import Path

        cred = config.require_credential()
        reply_type, field = _kind(kind, SIMPLE_KINDS)
        content = read_text(text, Path(file) if file else None)
        _check_reply(content)
        params = {"kind": kind, "reply": content, "switch_on": not keep_off}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dm.autoreply.set", "params": params}
        guard.check("autoreply")
        await Api(url=TEXT_SET_URL, method="POST", verify=True, credential=cred).update_data(type=reply_type, reply=content, **WEB).result
        if not keep_off:
            await _switch(cred, field, True)
        guard.record("autoreply", params, "ok")
        return {**params, "note": "设置立即生效：B 站会以你的名义自动发出这段内容，对同一个人一天一次。"}

    run(body)


@app.command("on")
def on_(kind: str = typer.Argument(..., help="follow / message / voyage / keyword。")) -> None:
    """打开一类自动回复。"""
    _toggle(kind, True)


@app.command("off")
def off_(kind: str = typer.Argument(..., help="follow / message / voyage / keyword。")) -> None:
    """关闭一类自动回复（内容和关键词规则保留）。"""
    _toggle(kind, False)


def _toggle(kind: str, on: bool) -> None:
    async def body():
        cred = config.require_credential()
        _, field = _kind(kind)
        params = {"kind": kind, "on": on}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dm.autoreply.switch", "params": params}
        guard.check("autoreply")
        await _switch(cred, field, on)
        guard.record("autoreply", params, "ok")
        return params

    run(body)


keyword_app = typer.Typer(help="关键词回复规则：私信里出现关键词时自动回复。", no_args_is_help=True)
app.add_typer(keyword_app, name="keyword")


def build_rule(name: str, exact: list[str], fuzzy: list[str], reply: str, rule_id: int = 0) -> dict[str, Any]:
    if not name.strip() or len(name) > RULE_NAME_MAX:
        raise BiliError("invalid_argument", f"规则名称需为 1 到 {RULE_NAME_MAX} 字。")
    exact, fuzzy = [k.strip() for k in exact if k.strip()], [k.strip() for k in fuzzy if k.strip()]
    if not exact and not fuzzy:
        raise BiliError("invalid_argument", "精确匹配（--exact）和模糊匹配（--fuzzy）至少填一个关键词。")
    if len(exact) > KEYWORDS_MAX or len(fuzzy) > KEYWORDS_MAX:
        raise BiliError("invalid_argument", f"每种匹配最多 {KEYWORDS_MAX} 个关键词。")
    _check_reply(reply)
    # B 站 separates keywords with the full-width comma
    return {"type": 2, "id": rule_id, "title": name, "key1": "，".join(exact), "key2": "，".join(fuzzy), "reply": reply}


@keyword_app.command("add")
def keyword_add(
    reply: str = typer.Argument(..., help=f"回复内容，最多 {REPLY_MAX} 字。"),
    name: str = typer.Option(..., "--name", help=f"规则名称，最多 {RULE_NAME_MAX} 字。"),
    exact: list[str] = typer.Option([], "--exact", help="精确匹配的关键词（私信内容正好是这个词），可重复传入。"),
    fuzzy: list[str] = typer.Option([], "--fuzzy", help="模糊匹配的关键词（私信里包含这个词），可重复传入。"),
) -> None:
    """新增一条关键词回复规则。关键词回复的总开关用 `bili dm autoreply on keyword` 打开。"""

    async def body():
        cred = config.require_credential()
        rule = build_rule(name, exact, fuzzy, reply)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dm.autoreply.keyword.add", "params": rule}
        guard.check("autoreply")
        await Api(url=TEXT_SET_URL, method="POST", verify=True, credential=cred).update_data(**rule, **WEB).result
        guard.record("autoreply", rule, "ok")
        rules = await _texts(cred, 2)
        added = next((r for r in rules if r.get("title") == name and r.get("reply") == reply), None)
        return {"id": added.get("id") if added else None, "name": name, "exact": _split(rule["key1"]), "fuzzy": _split(rule["key2"]),
                "next": "删除：`bili dm autoreply keyword delete <id>`；总开关：`bili dm autoreply on keyword`。"}

    run(body)


@keyword_app.command("delete")
def keyword_delete(rule_id: int = typer.Argument(..., help="规则 ID（见 `bili dm autoreply show`）。")) -> None:
    """删除一条关键词回复规则。"""

    async def body():
        cred = config.require_credential()
        rule = next((r for r in await _texts(cred, 2) if r.get("id") == rule_id), None)
        if rule is None:
            raise BiliError("not_found", f"没有 ID 为 {rule_id} 的关键词规则。", hint="用 `bili dm autoreply show` 查看规则 ID。")
        params = {"id": rule_id, "name": rule.get("title")}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "dm.autoreply.keyword.delete", "params": params}
        guard.check("autoreply")
        await Api(url=TEXT_DEL_URL, method="POST", verify=True, credential=cred).update_data(id=rule_id, **WEB).result
        guard.record("autoreply", params, "ok")
        return {**params, "deleted": True}

    run(body)
