"""What a newcomer and their assistant meet first. A first-time-assistant test found these gaps."""
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import categories, cli, metadata
from video_publisher.core import VpubError, atomic_json, load_plan, read_json


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = patch.dict("os.environ", {"VPUB_HOME": str(self.folder / "home"), "BILI_CLI_HOME": str(self.folder / "bili")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.video = self.folder / "final.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))

    def init(self, *more):
        out = self.folder / "publish.json"
        args = cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(out), "--platforms", "bilibili,youtube,douyin,instagram,x", *more])
        return cli.run_command(args), json.loads(out.read_text(encoding="utf-8"))

    def test_doctor_names_the_next_step_and_where_the_skill_is(self):
        answer = cli.doctor()
        self.assertIn("vpub", answer["next"])
        self.assertTrue(answer["skill"] is None or answer["skill"].endswith("SKILL.md"))

    def test_doctor_finds_the_skill_after_pip_install_user(self):
        base = self.folder / "user base"
        skill = base / "share/publishops-crosscast/skills/publishops-crosscast/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("skill", encoding="utf-8")
        with patch.object(cli.sys, "prefix", str(self.folder / "environment")), patch.object(cli.site, "getuserbase", return_value=str(base)):
            self.assertEqual(str(skill), cli.skill_file())

    def test_the_package_installs_the_skill_and_its_details_where_doctor_looks(self):
        built = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"share/publishops-crosscast/skills/publishops-crosscast" = ["skills/publishops-crosscast/SKILL.md"]', built)
        self.assertIn('"share/publishops-crosscast/skills/publishops-crosscast/references" = ["skills/publishops-crosscast/references/details.md"]', built)

    def test_the_next_step_after_init_runs_from_a_nested_folder_with_spaces(self):
        out = self.folder / "my videos" / "publish.json"
        source = self.folder / "script.txt"
        source.write_text("说明", encoding="utf-8")
        answer = cli.run_command(cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(out), "--platforms", "bilibili", "--language", "zh-Hans"]))
        words = [str(source) if word == "SCRIPT_OR_SUMMARY.txt" else word for word in shlex.split(answer["next"])]
        self.assertEqual("vpub", words[0])
        self.assertEqual("HOST_MODEL_REQUIRED", cli.execute(cli.parser().parse_args(words[1:]))["generation_state"])
        self.assertTrue((out.parent / "request.json").is_file())

    def test_doctor_points_at_the_first_problem_before_anything_else(self):
        with patch("video_publisher.overview.chrome_path", return_value=None):
            self.assertEqual("vpub setup --install-browser", cli.doctor()["next"])

    def test_saying_public_reaches_youtube(self):
        answer, plan = self.init("--visibility", "public")
        self.assertEqual("public", plan["platforms"]["youtube"]["visibility"])
        self.assertEqual("public", answer["visibility"]["youtube"])

    def test_saying_private_reaches_the_platforms_that_have_it_and_not_the_ones_that_follow_the_account(self):
        _, plan = self.init("--visibility", "private")
        self.assertEqual(["private"] * 3, [plan["platforms"][name]["visibility"] for name in ("bilibili", "youtube", "douyin")])
        self.assertEqual(["account"] * 2, [plan["platforms"][name]["visibility"] for name in ("instagram", "x")])

    def test_saying_nothing_keeps_youtube_private(self):
        _, plan = self.init()
        self.assertEqual("private", plan["platforms"]["youtube"]["visibility"])

    def test_init_gives_a_command_that_parses(self):
        answer, _ = self.init()
        self.assertIn(f'metadata request "{(self.folder / "publish.json").resolve()}"', answer["next"])
        cli.parser().parse_args(shlex.split(answer["next"])[1:])

    def test_init_final_requires_a_written_title_on_every_platform(self):
        _, value = self.init()
        manifest = self.folder / "publish.json"
        self.assertEqual("", value["title"])
        self.assertTrue(all("title" not in options for options in value["platforms"].values()))
        # Supply the unrelated required category so every platform reaches its title check.
        value["platforms"]["youtube"]["category_id"] = "1"
        atomic_json(manifest, value)
        for platform in value["platforms"]:
            with self.subTest(platform=platform), self.assertRaises(VpubError) as caught:
                cli.execute(cli.parser().parse_args(["plan", str(manifest), "--platforms", platform]))
            self.assertEqual("invalid_title", caught.exception.code)
            self.assertIn(f"platforms.{platform}.title", caught.exception.hint)
            self.assertIn("root title", caught.exception.hint)
            self.assertIn("vpub metadata request", caught.exception.hint)
            self.assertIn("vpub metadata apply", caught.exception.hint)

    def test_deliberately_written_titles_are_not_banned_by_their_words(self):
        _, value = self.init()
        manifest = self.folder / "publish.json"
        value["platforms"]["youtube"]["category_id"] = "1"
        for title in ("Final", "final", "我的原创视频"):
            with self.subTest(title=title):
                value["title"] = title
                atomic_json(manifest, value)
                plan = load_plan(manifest)
                self.assertEqual({title}, {options["title"] for options in plan["platforms"].values()})

    def test_platform_titles_can_fill_a_blank_root_but_blank_override_is_refused(self):
        _, value = self.init()
        manifest = self.folder / "publish.json"
        value["platforms"]["youtube"]["category_id"] = "1"
        for options in value["platforms"].values():
            options["title"] = "Final"
        atomic_json(manifest, value)
        self.assertEqual({"Final"}, {options["title"] for options in load_plan(manifest)["platforms"].values()})
        value["title"] = "Written root title"
        value["platforms"]["douyin"]["title"] = "   "
        atomic_json(manifest, value)
        with self.assertRaises(VpubError) as caught:
            load_plan(manifest, ["douyin"])
        self.assertEqual("invalid_title", caught.exception.code)
        self.assertIn("platforms.douyin.title", caught.exception.hint)

    def test_a_missing_category_names_the_choices(self):
        with self.assertRaises(VpubError) as error:
            categories.validate_youtube(None)
        self.assertIn("28 Science", error.exception.hint)

    def test_the_request_holds_a_template_that_matches_what_apply_wants(self):
        self.init("--language", "zh-Hans")
        job = metadata.request(self.folder / "publish.json")
        template = job["response_template"]["platforms"]
        self.assertEqual(set(job["targets"]), set(template))
        self.assertIn("description_parts", template["youtube"])
        self.assertNotIn("description_parts", template["bilibili"])
        self.assertIn("category_id", template["youtube"])
        self.assertNotIn("category_id", template["douyin"])
        self.assertEqual(job["targets"]["youtube"]["language"], template["youtube"]["metadata_language"])
        self.assertEqual("Science", job["targets"]["youtube"]["category_selection"]["choices"]["28"])

    def test_a_filled_in_template_applies(self):
        self.init("--language", "zh-Hans")
        manifest = self.folder / "publish.json"
        job = metadata.request(manifest)
        self.assertEqual("", job["source"]["title"])
        request = self.folder / "request.json"
        request.write_text(json.dumps(job), encoding="utf-8")
        response = {"request_id": job["request_id"], "platforms": json.loads(json.dumps(job["response_template"]["platforms"]))}
        for name, words in response["platforms"].items():
            words.update(title="短标题", description="说明", tags=["视频"])
            if "description_parts" in words:
                words["description_parts"] = ["说明", "A description"]
                words["description"] = "说明\n\nA description"
        response["platforms"]["youtube"]["category_id"] = "28"
        answer = self.folder / "response.json"
        answer.write_text(json.dumps(response), encoding="utf-8")
        result = metadata.apply(manifest, request, answer, self.folder / "localized.json")
        self.assertEqual(0, result["platform_writes"])
        self.assertEqual("", read_json(manifest)["title"])
        self.assertEqual("", read_json(result["manifest"])["title"])
        plan = load_plan(result["manifest"])
        self.assertEqual({"短标题"}, {options["title"] for options in plan["platforms"].values()})


if __name__ == "__main__":
    unittest.main()
