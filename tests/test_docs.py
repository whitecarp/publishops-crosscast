"""The docs are what assistants follow, so every documented command and link must really exist."""
import contextlib
import io
from pathlib import Path
import re
import shlex
import unittest

from video_publisher import __version__, cli, progress

ROOT = Path(__file__).resolve().parents[1]
DOCS = [*sorted(ROOT.glob("README*.md")), *sorted((ROOT / "docs").glob("*.md")), ROOT / "CONTRIBUTING.md", ROOT / "NOTICE.md",
        ROOT / "skills/publishops-crosscast/SKILL.md", ROOT / "skills/publishops-crosscast/references/details.md"]
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

    def test_the_skill_stays_short_enough_to_read_every_time(self):
        """An assistant reads it in every fresh conversation, on its user's own quota. The detail belongs in references/details.md.
        It's counted with Windows line endings, as the Windows checks check it out: a skill that passed here at 7977 bytes failed there at 8036."""
        data = (ROOT / "skills/publishops-crosscast/SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        self.assertLess(len(data) + data.count(b"\n"), 8000)

    def test_feedback_excludes_both_data_homes_and_sign_in_files(self):
        paths = [ROOT / "skills/publishops-crosscast/SKILL.md", *sorted((ROOT / "docs").glob("GUIDE*.md"))]
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                for excluded in ("VPUB_HOME", "BILI_CLI_HOME", "%APPDATA%\\bili-cli",
                                 "$XDG_CONFIG_HOME/bili-cli", "~/.config/bili-cli",
                                 "credential.json", "pending_login.json"):
                    self.assertIn(excluded, text)

    def test_review_examples_pair_current_confirmation_with_separate_approval(self):
        paths = [*sorted((ROOT / "docs").glob("GUIDE*.md")),
                 ROOT / "skills/publishops-crosscast/references/details.md"]
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("--reviewed", text)
                self.assertIn("vpub go final-crosscast --confirm CODE --person-approved", text)
                self.assertIn("vpub publish ready.json --confirm CODE --person-approved", text)
                self.assertIn("vpub browser authorize-publish KEY --identity ID`", text)
                self.assertIn("vpub browser authorize-publish KEY --identity ID --confirm CODE --person-approved", text)

    def test_help_lists_every_command_under_a_heading(self):
        words = cli.parser().format_help()
        names = {action.dest for action in cli.parser()._subparsers._group_actions[0]._choices_actions}
        self.assertEqual(names, {name for _, group in cli.HELP_GROUPS for name in group})
        self.assertLess(words.index("doctor"), words.index("Paid add-on"))
        self.assertIn("publish", words)

    def test_every_stage_a_browser_command_writes_is_listed_where_readers_look(self):
        """The help agents read and both operations guides give the stages as a complete list. From round 1's final check, they missed the
        wait for a cover slot to clear, and the wait for platform confirmation before it."""
        listed = {"help": cli.capabilities()["browser_progress"],
                  **{name: (ROOT / "docs" / name).read_text(encoding="utf-8") for name in ("OPERATIONS.md", "OPERATIONS_EN.md")}}
        for where, words in listed.items():
            for stage in sorted(progress.STAGES):
                with self.subTest(where=where, stage=stage):
                    self.assertIn(stage if where == "help" else f"`{stage}`", words)

    def test_every_word_on_the_site_is_in_every_language(self):
        page = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        script = (ROOT / "docs/site.js").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n(?:-html|-aria)?="([^"]+)"', page))
        self.assertGreater(len(used), 100)
        for name, start, end in (("TW", "var TW = {", "var EN = {"), ("EN", "var EN = {", "var DICTS")):
            with self.subTest(name=name):
                words = script.split(start, 1)[1].split(end, 1)[0]
                have = set(re.findall(r'(?m)(?:^\s*|, )([A-Za-z_][A-Za-z0-9_]*): "', words))
                self.assertEqual(set(), used - have)
                self.assertEqual(set(), {key for key in have - used if not key.startswith("_")})

    def test_the_package_version_is_the_one_the_project_declares(self):
        declared = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
        self.assertEqual(declared, __version__)

    def test_readers_are_only_told_the_current_version(self):
        for name in ("README.md", "README_EN.md", "README_ZH_TW.md", "assets/hero.svg", "assets/hero-en.svg", "assets/hero-zh-tw.svg",
                     "docs/index.html", "docs/site.js", "docs/FEATURES.md", "docs/FEATURES_EN.md"):
            with self.subTest(name=name):
                stated = re.findall(r"(\d+\.\d+\.\d+)(?:%20| )alpha", (ROOT / name).read_text(encoding="utf-8"))
                self.assertTrue(stated)
                self.assertEqual({__version__}, set(stated))


if __name__ == "__main__":
    unittest.main()
