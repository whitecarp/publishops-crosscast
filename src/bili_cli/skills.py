"""Install the bundled Agent Skill into the AI agents found on this machine.

One canonical copy goes to ~/.agents/skills/bili-cli (the cross-agent Agent
Skills location read by Codex, Cursor, Gemini CLI, Copilot, Windsurf, Roo,
OpenCode, OpenClaw...). Agents that only read their own folder (Claude Code,
Qwen Code, Kiro, Cline...) get a copy too, but only when their config folder
already exists — we never create folders for agents the user does not have.

Every installed folder carries a marker file, so uninstall and upgrades only
ever touch folders this tool wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__

SKILL_NAME = "bili-cli"
MARKER = ".bili-cli-install.json"


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _env_dir(var: str, default: Path) -> Path:
    return Path(os.environ[var]) if os.environ.get(var) else default


@dataclass
class Target:
    agent: str
    root: Callable[[], Path]  # the agent's config folder; must exist unless always=True
    skills: str = "skills"
    always: bool = False

    def config_dir(self) -> Path:
        return self.root()

    def skill_dir(self) -> Path:
        return self.root() / self.skills / SKILL_NAME

    def detected(self) -> bool:
        return self.always or self.root().is_dir()


def HOME() -> Path:  # noqa: N802 - resolved at call time so tests and $HOME changes take effect
    return Path.home()


TARGETS: list[Target] = [
    # read by Codex, Cursor, Gemini CLI, GitHub Copilot, Windsurf, Roo Code, OpenCode, OpenClaw, Kimi...
    Target("agents", lambda: HOME() / ".agents", always=True),
    Target("claude-code", lambda: _env_dir("CLAUDE_CONFIG_DIR", HOME() / ".claude")),
    Target("openclaw", lambda: _env_dir("OPENCLAW_HOME", HOME() / ".openclaw")),
    Target("qwen-code", lambda: HOME() / ".qwen"),
    Target("kiro", lambda: HOME() / ".kiro"),
    Target("cline", lambda: HOME() / ".cline"),
    Target("trae", lambda: HOME() / ".trae"),
    Target("trae-cn", lambda: HOME() / ".trae-cn"),
    Target("codebuddy", lambda: HOME() / ".codebuddy"),
    Target("lingma", lambda: HOME() / ".lingma"),
    Target("qoder", lambda: HOME() / ".qoder"),
    Target("iflow", lambda: HOME() / ".iflow"),
    Target("goose", lambda: _xdg_config() / "goose"),
    # DeepSeek Harness (dsh) also reads ~/.agents/skills; its own folder ranks higher
    Target("deepseek-harness", lambda: _env_dir("DSH_HOME", HOME() / ".dsh")),
]
# Agents that pick the skill up from ~/.agents/skills; listed so status can say who is covered.
COVERED_BY_STANDARD = {
    "codex": lambda: _env_dir("CODEX_HOME", HOME() / ".codex"),
    "cursor": lambda: HOME() / ".cursor",
    "gemini-cli": lambda: HOME() / ".gemini",
    "github-copilot": lambda: HOME() / ".copilot",
    "windsurf": lambda: HOME() / ".codeium" / "windsurf",
    "roo-code": lambda: HOME() / ".roo",
    "opencode": lambda: _xdg_config() / "opencode",
}


def project_targets(cwd: Path) -> list[Target]:
    return [
        Target("project-agents", lambda: cwd / ".agents", always=True),
        Target("project-claude", lambda: cwd / ".claude", always=True),
    ]


def bundled_dir() -> Path:
    """The skill shipped with this package (wheel) or, in a source checkout, the repo's skills/ folder."""
    packaged = Path(__file__).parent / "_skills" / SKILL_NAME
    if (packaged / "SKILL.md").exists():
        return packaged
    repo = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME
    if (repo / "SKILL.md").exists():
        return repo
    raise FileNotFoundError("bundled skill not found")


def commands_reference() -> str:
    """Full command reference generated from the live CLI, so it never drifts from the code."""
    from .main import ERROR_TABLE, _describe, _describe_params, app
    import typer

    group = typer.main.get_command(app)
    lines = [
        "# 命令参考",
        "",
        f"由 `bili skills install` 根据 bili-cli {__version__} 自动生成。也可以随时运行 `bili schema` 获取 JSON 版本。",
        "",
        "全局参数（可写在任意位置）：" + "、".join(f"`{p['flags'][0]}`" for p in _describe_params(group.params) if p.get("flags")),
        "",
    ]
    for cmd in _describe(group, []):
        lines.append(f"## `{cmd['command']}`")
        lines.append("")
        lines.append(cmd["help"].split("\n")[0])
        lines.append("")
        for p in cmd["params"]:
            if p["kind"] == "argument":
                name = f"`<{p['name']}>`" + ("" if p["required"] else "（可选）")
            else:
                name = " / ".join(f"`{f}`" for f in p["flags"] or [])
            default = "" if p["required"] or p["default"] in (None, False, [], ()) else f"（默认 {p['default']}）"
            lines.append(f"- {name}{default}：{p.get('help') or ''}")
        lines.append("")
    lines.append("## 错误码")
    lines.append("")
    for code, (exit_code, _hint) in ERROR_TABLE.items():
        lines.append(f"- `{code}`：退出码 {exit_code}")
    return "\n".join(lines) + "\n"


def _source_hash(src: Path, commands_md: str) -> str:
    h = hashlib.sha256(commands_md.encode())
    for f in sorted(src.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(src).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def read_marker(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads((path / MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def plan(targets: list[Target], agents: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for t in targets:
        if agents and t.agent not in agents:
            continue
        dest = t.skill_dir()
        marker = read_marker(dest)
        if not t.detected():
            action = "skip: agent not installed"
        elif dest.exists() and marker is None:
            action = "skip: a different skill with this name exists (use --force to replace)"
        else:
            action = "update" if marker else "install"
        out.append({"agent": t.agent, "path": str(dest), "action": action, "installed_version": (marker or {}).get("version")})
    return out


def install(targets: list[Target], agents: list[str] | None, force: bool) -> list[dict[str, Any]]:
    src = bundled_dir()
    commands_md = commands_reference()
    digest = _source_hash(src, commands_md)
    results = []
    for step in plan(targets, agents):
        dest = Path(step["path"])
        if step["action"].startswith("skip: a different") and force:
            step["action"] = "replace"
        if step["action"].startswith("skip"):
            results.append(step)
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        (dest / "references").mkdir(exist_ok=True)
        (dest / "references" / "commands.md").write_text(commands_md, encoding="utf-8", newline="\n")
        (dest / MARKER).write_text(
            json.dumps({"version": __version__, "hash": digest, "installed_at": int(time.time())}, indent=2),
            encoding="utf-8",
        )
        results.append({**step, "done": True})
    return results


def uninstall(targets: list[Target], agents: list[str] | None) -> list[dict[str, Any]]:
    results = []
    for t in targets:
        if agents and t.agent not in agents:
            continue
        dest = t.skill_dir()
        if read_marker(dest) is None:
            continue  # never delete a folder we did not write
        shutil.rmtree(dest)
        results.append({"agent": t.agent, "path": str(dest), "removed": True})
    return results


def status(targets: list[Target]) -> dict[str, Any]:
    src = bundled_dir()
    current = _source_hash(src, commands_reference())
    rows = []
    for t in targets:
        marker = read_marker(t.skill_dir())
        rows.append({
            "agent": t.agent,
            "detected": t.detected(),
            "installed": marker is not None,
            "version": (marker or {}).get("version"),
            "up_to_date": bool(marker and marker.get("hash") == current),
            "path": str(t.skill_dir()),
        })
    covered = [name for name, root in COVERED_BY_STANDARD.items() if root().is_dir()]
    return {"targets": rows, "agents_using_standard_dir": covered}
