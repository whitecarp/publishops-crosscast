"""bili skills: install the bili-cli Agent Skill into the AI agents on this machine."""

from __future__ import annotations

from pathlib import Path

import typer

from .. import output
from .. import skills as sk
from ..errors import BiliError
from ..runtime import run

app = typer.Typer(
    help="把 bili-cli 的使用技能（Agent Skill）装进本机的 AI Agent：Claude Code、Codex、Cursor、Gemini CLI、Copilot、OpenClaw、Qwen Code、DeepSeek Harness 等。",
    no_args_is_help=True,
)

AGENT_OPT = typer.Option([], "--agent", "-a", help="只处理指定的 Agent（可重复），如 claude-code、agents、openclaw。默认全部检测到的。")
PROJECT_OPT = typer.Option(False, "--project", help="装到当前目录的项目里（.agents/skills 和 .claude/skills），而不是用户目录。")


def _targets(project: bool) -> list[sk.Target]:
    return sk.project_targets(Path.cwd()) if project else sk.TARGETS


def _check_agents(agents: list[str], targets: list[sk.Target]) -> None:
    known = {t.agent for t in targets}
    unknown = [a for a in agents if a not in known]
    if unknown:
        raise BiliError("invalid_argument", f"未知的 Agent：{', '.join(unknown)}。", hint=f"可选：{', '.join(sorted(known))}")


@app.command()
def install(
    agent: list[str] = AGENT_OPT,
    project: bool = PROJECT_OPT,
    yes: bool = typer.Option(False, "--yes", "-y", help="确认写入。不加时只显示将要写到哪里。"),
    force: bool = typer.Option(False, "--force", help="覆盖不是由 bili-cli 安装的同名技能目录。"),
) -> None:
    """安装或更新技能。默认写入 ~/.agents/skills（多数 Agent 共用），并给检测到的其他 Agent 各写一份。不加 --yes 只预览。"""

    async def body():
        targets = _targets(project)
        _check_agents(agent, targets)
        if not yes or output.settings.dry_run:
            steps = sk.plan(targets, agent or None)
            return {
                "applied": False,
                **_split(steps),
                "next": "确认无误后加 --yes 执行：`bili skills install --yes`",
            }
        results = sk.install(targets, agent or None, force)
        return {
            "applied": True,
            **_split(results),
            "note": "新开一个 Agent 会话即可使用；Claude Code 会自动加载新技能。",
        }

    run(body)


def _split(steps: list[dict]) -> dict:
    """Keep the JSON short: full rows for what is (or would be) written, just names for skipped agents."""
    act = [{k: v for k, v in s.items() if k != "installed_version" or v} for s in steps if not s["action"].startswith("skip")]
    conflicts = [s["path"] for s in steps if s["action"].startswith("skip: a different")]
    out: dict = {"targets": act, "agents_not_found": [s["agent"] for s in steps if s["action"] == "skip: agent not installed"]}
    if conflicts:
        out["conflicts"] = conflicts
        out["conflict_hint"] = "这些位置已有同名但不是 bili-cli 安装的技能，默认不覆盖；确认要替换加 --force。"
    return out


@app.command()
def uninstall(
    agent: list[str] = AGENT_OPT,
    project: bool = PROJECT_OPT,
    yes: bool = typer.Option(False, "--yes", "-y", help="确认删除。不加时只显示将删除哪些目录。"),
) -> None:
    """卸载技能。只会删除由 bili-cli 安装的目录（带安装标记的）。"""

    async def body():
        targets = _targets(project)
        _check_agents(agent, targets)
        if not yes or output.settings.dry_run:
            rows = [r for r in sk.status(targets)["targets"] if r["installed"] and (not agent or r["agent"] in agent)]
            return {"applied": False, "would_remove": [r["path"] for r in rows], "next": "加 --yes 执行删除。"}
        return {"applied": True, "removed": sk.uninstall(targets, agent or None)}

    run(body)


@app.command()
def status(project: bool = PROJECT_OPT) -> None:
    """查看每个 Agent 是否检测到、是否已安装技能、是否是最新版本。"""

    async def body():
        data = sk.status(_targets(project))
        stale = [t["agent"] for t in data["targets"] if t["installed"] and not t["up_to_date"]]
        if stale:
            data["hint"] = f"{', '.join(stale)} 里的技能不是最新版本，运行 `bili skills install --yes` 更新。"
        elif not any(t["installed"] for t in data["targets"]):
            data["hint"] = "还没有安装技能，运行 `bili skills install` 预览，再加 --yes 安装。"
        return data

    run(body)


@app.command()
def show() -> None:
    """输出技能正文（SKILL.md）和所在目录，方便手动复制给不支持技能目录的 Agent。"""

    async def body():
        src = sk.bundled_dir()
        return {"path": str(src), "skill_md": (src / "SKILL.md").read_text(encoding="utf-8")}

    run(body)
