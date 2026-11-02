"""bili mcp: run bili-cli as an MCP server and register it in MCP clients."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from .. import output
from ..errors import BiliError
from ..runtime import run

app = typer.Typer(help="MCP 服务：给不能执行命令的客户端（Claude Desktop、Cherry Studio 等）用。", no_args_is_help=True)

SERVER_NAME = "bili-cli"


def _server_command() -> tuple[str, list[str]]:
    """Absolute path to the executable: GUI clients do not inherit the shell's PATH."""
    exe = shutil.which("bili")
    if exe:
        return str(Path(exe).resolve()), ["mcp", "serve"]
    return sys.executable, ["-m", "bili_cli.main", "mcp", "serve"]


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def _app_support() -> Path:
    return Path.home() / "Library" / "Application Support"


def _xdg() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _json_client_path(client: str) -> Path:
    if client == "claude-desktop":
        base = _appdata() if sys.platform == "win32" else _app_support() if sys.platform == "darwin" else _xdg()
        return base / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return Path.home() / ".cursor" / "mcp.json"
    if client == "gemini":
        return Path.home() / ".gemini" / "settings.json"
    if client == "vscode":
        base = _appdata() if sys.platform == "win32" else _app_support() if sys.platform == "darwin" else _xdg()
        return base / "Code" / "User" / "mcp.json"
    raise KeyError(client)


# client -> (how it is registered, key in the JSON file)
CLIENTS = {
    "claude-desktop": ("json", "mcpServers"),
    "cursor": ("json", "mcpServers"),
    "gemini": ("json", "mcpServers"),
    "vscode": ("json", "servers"),
    "claude-code": ("cli", "claude"),
    "codex": ("cli", "codex"),
}


def _entry(key: str) -> dict[str, Any]:
    command, args = _server_command()
    entry: dict[str, Any] = {"command": command, "args": args}
    if key == "servers":  # VS Code wants an explicit transport type
        entry = {"type": "stdio", **entry}
    return entry


def _cli_argv(tool: str, remove: bool) -> list[str]:
    command, args = _server_command()
    if tool == "claude":
        return ["claude", "mcp", "remove", "-s", "user", SERVER_NAME] if remove else ["claude", "mcp", "add", "-s", "user", SERVER_NAME, "--", command, *args]
    return ["codex", "mcp", "remove", SERVER_NAME] if remove else ["codex", "mcp", "add", SERVER_NAME, "--", command, *args]


def _plan(client: str, remove: bool) -> dict[str, Any]:
    kind, key = CLIENTS[client]
    if kind == "cli":
        found = shutil.which(key) is not None
        return {"client": client, "method": "command", "argv": _cli_argv(key, remove), "available": found}
    path = _json_client_path(client)
    return {
        "client": client,
        "method": "config_file",
        "path": str(path),
        "available": path.exists() or path.parent.exists(),
        "entry": None if remove else {key: {SERVER_NAME: _entry(key)}},
    }


def _apply(step: dict[str, Any], remove: bool) -> dict[str, Any]:
    if step["method"] == "command":
        proc = subprocess.run(step["argv"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return {**step, "done": proc.returncode == 0, "output": (proc.stdout or proc.stderr).strip()[-400:]}
    path = Path(step["path"])
    key = CLIENTS[step["client"]][1]
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise BiliError("invalid_argument", f"{path} 不是合法的 JSON，没有改动它。", detail={"error": str(exc)}) from exc
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    servers = data.setdefault(key, {})
    if remove:
        removed = servers.pop(SERVER_NAME, None) is not None
        if not removed:
            return {**step, "done": False, "output": "没有找到 bili-cli 的配置"}
    else:
        servers[SERVER_NAME] = _entry(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**step, "done": True, "backup": str(path.with_suffix(path.suffix + ".bak")) if path.with_suffix(path.suffix + ".bak").exists() else None}


def _clients(values: list[str]) -> list[str]:
    unknown = [c for c in values if c not in CLIENTS]
    if unknown:
        raise BiliError("invalid_argument", f"未知的客户端：{', '.join(unknown)}。", hint=f"可选：{', '.join(CLIENTS)}")
    return values


CLIENT_OPT = typer.Option([], "--client", "-c", help=f"要配置的客户端（可重复）：{' / '.join(CLIENTS)}。默认全部已安装的。")


@app.command()
def serve() -> None:
    """以 stdio 方式运行 MCP 服务（由 MCP 客户端启动，不需要手动运行）。"""
    try:
        from ..mcp_server import serve as _serve

        import mcp  # noqa: F401
    except ImportError:
        output.fail(BiliError(
            "invalid_argument",
            "MCP 模式需要额外安装 mcp 依赖。",
            hint='重新安装：uv tool install --force "bili-agent[mcp]"（或 pip install "mcp>=2.3"）',
        ))
    _serve()


def _mcp_command(remove: bool, clients: list[str], yes: bool) -> dict[str, Any]:
    targets = _clients(clients) or list(CLIENTS)
    steps = [_plan(c, remove) for c in targets]
    if not clients:
        steps = [s for s in steps if s["available"]]
    if not yes or output.settings.dry_run:
        return {"applied": False, "plan": steps, "next": f"确认无误后加 --yes：`bili mcp {'uninstall' if remove else 'install'} --yes`"}
    results = [_apply(s, remove) for s in steps if s["available"]]
    return {
        "applied": True,
        "results": results,
        "note": "重启对应的客户端（Claude Desktop 需要完全退出再打开）后生效。",
    }


@app.command()
def install(clients: list[str] = CLIENT_OPT, yes: bool = typer.Option(False, "--yes", "-y", help="确认写入。不加时只显示计划。")) -> None:
    """把 bili-cli MCP 服务注册到 MCP 客户端。写配置文件前会备份为 .bak，只增改 bili-cli 自己的条目。"""

    async def body():
        return _mcp_command(False, clients, yes)

    run(body)


@app.command()
def uninstall(clients: list[str] = CLIENT_OPT, yes: bool = typer.Option(False, "--yes", "-y", help="确认删除。")) -> None:
    """从 MCP 客户端移除 bili-cli。"""

    async def body():
        return _mcp_command(True, clients, yes)

    run(body)
