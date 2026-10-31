"""The docs are what assistants follow, so every documented command and link must really exist."""
import contextlib
import io
from pathlib import Path
import re
import shlex
import unittest

from video_publisher import __version__, cli

ROOT = Path(__file__).resolve().parents[1]
DOCS = [*sorted(ROOT.glob("README*.md")), *sorted((ROOT / "docs").glob("*.md")), ROOT / "CONTRIBUTING.md", ROOT / "NOTICE.md",
        ROOT / "skills/publishops-crosscast/SKILL.md"]
FENCE = re.compile(r"^```")


def documented_commands(path):
    """vpub commands in code blocks and inline code. Inline ones may just name a command."""
    in_code = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line.strip()):
            in_code = not in_code
        elif in_code:
            if line.strip().startswith("vpub "):
                yield number, line.strip(), True
        else:
            for text in re.findall(r"`(vpub [^`]+)`", line):
                yield number, text, False


def parse_problem(parser, text, complete):
    text = re.sub(r"\s+#.*$", "", text).rstrip("\\ ").strip()
    if any(mark in text for mark in ("...", "…", "|", "<", "[", "{")):
        return None  # A usage pattern rather than a command.
    try:
        argv = [part.strip('"') for part in shlex.split(text, posix=False)[1:]]
    except ValueError:
        return None
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            parser.parse_args(["x" if part == "PLATFORM" else part for part in argv])
    except SystemExit as stop:
        message = errors.getvalue().strip().splitlines()[-1] if errors.getvalue().strip() else ""
        if stop.code and (complete or "the following arguments are required" not in message):
            return message or "rejected"
    return None


def github_anchor(title):
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", re.sub(r"[`*]", "", title.strip())).lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title, flags=re.U))


def anchors_of(path):
    found, seen, in_code = set(), {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.strip()):
            in_code = not in_code
            continue
        match = None if in_code else re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if match:
            name = github_anchor(match[1])
            count = seen.get(name, 0)
            seen[name] = count + 1
            found.add(name if not count else f"{name}-{count}")
    return found


class DocsTests(unittest.TestCase):
    def test_every_documented_command_parses(self):
        parser, problems, checked = cli.parser(), [], 0
        for path in DOCS:
            for number, text, complete in documented_commands(path):
                checked += 1
                problem = parse_problem(parser, text, complete)
                if problem:
                    problems.append(f"{path.name}:{number}: {text[:80]} -> {problem[:80]}")
        self.assertGreater(checked, 100)
        self.assertEqual([], problems)

    def test_every_relative_link_and_anchor_resolves(self):
        problems, checked, cache = [], 0, {}
        for path in DOCS:
            text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            for target in re.findall(r"\]\(([^)\s]+)", text) + re.findall(r"""(?:href|src)=["']([^"']+)""", text):
                if re.match(r"^[a-z]+:", target) or target.startswith("//"):
                    continue
                checked += 1
                file_part, _, fragment = target.partition("#")
                destination = (path.parent / file_part).resolve() if file_part else path
                if not destination.exists():
                    problems.append(f"{path.name}: missing file {target}")
                elif fragment and destination.suffix == ".md" and fragment not in cache.setdefault(destination, anchors_of(destination)):
                    problems.append(f"{path.name}: missing anchor {target}")
        self.assertGreater(checked, 50)
        self.assertEqual([], problems)

    def test_the_site_only_links_to_files_that_exist(self):
        site = (ROOT / "docs/index.html").read_text(encoding="utf-8") + (ROOT / "docs/site.js").read_text(encoding="utf-8")
        problems = []
        for target in set(re.findall(r"""(?:href|src)=["']([^"'#][^"']*)""", site)):
            if re.match(r"^[a-z]+:", target) or target.startswith("//"):
                if "github.com/whitecarp/publishops-crosscast/blob/" in target:
                    local = target.split("/blob/", 1)[1].split("/", 1)[1].split("#")[0]
                    if not (ROOT / local).exists():
                        problems.append(target)
            elif not (ROOT / "docs" / target.split("?")[0]).exists():
                problems.append(target)
        self.assertEqual([], problems)

    def test_the_package_version_is_the_one_the_project_declares(self):
        declared = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
        self.assertEqual(declared, __version__)


if __name__ == "__main__":
    unittest.main()
