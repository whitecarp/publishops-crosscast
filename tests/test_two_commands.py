"""One video in two commands: vpub prepare, then vpub go. Nothing here reaches a platform."""
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from video_publisher import adapters, cli
from video_publisher.core import Ledger, VpubError


def run(*words):
    args = cli.parser().parse_args([str(word) for word in words])
    return cli.execute(args)


class TwoCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve() / "my videos"
        self.folder.mkdir()
        patcher = patch.dict("os.environ", {"VPUB_HOME": str(self.folder.parent / "home"), "BILI_CLI_HOME": str(self.folder.parent / "bili")})
        patcher.start()
        self.addCleanup(patcher.stop)
        # This suite models a ready local toolchain; missing ffmpeg has its own tests.
        tool = patch("video_publisher.rehearsal.shutil.which", return_value="ffmpeg.exe")
        tool.start()
        self.addCleanup(tool.stop)
        # These workflows belong to a returning customer; first-run review has
        # separate coverage. This prior publication is unrelated to final.mp4.
        ledger = Ledger()
        try:
            prior = {
                "options": {"submission_mode": "publish", "visibility": "public"},
                "receipt": {"state": "SUBMISSION_VERIFIED", "platform": "youtube", "id": "abcdefghijk"},
            }
            for platform in ('bilibili', 'youtube', 'douyin', 'x', 'instagram'):
                key = ledger.claim(platform, "returning-fixture", "f" * 64, prior)
                ledger.save(key, "SUBMISSION_VERIFIED", prior)
        finally:
            ledger.close()
        self.video = self.folder / "final.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))
        self.work = self.folder / "final-crosscast"
        self.sent = []

    def picture(self, name, size):
        path = self.folder / name
        Image.new("RGB", size, "#336699").save(path)
        return path

    def prepare(self, *more, platforms="bilibili,youtube", visibility="public"):
        return run("prepare", self.video, "--platforms", platforms, "--language", "zh-Hans", "--ai-generated", "yes", *(["--visibility", visibility] if visibility else []), *more)

    def write_copy(self, **changes):
        path = self.work / "copy.json"
        words = json.loads(path.read_text(encoding="utf-8"))
        for name, text in words["platforms"].items():
            text.update(title="一个完整的标题", description="说明这条视频", tags=["视频"])
            if "description_parts" in text:
                text.update(description_parts=["说明这条视频", "About this video"], description="说明这条视频\n\nAbout this video")
            if "category_id" in text:
                text["category_id"] = "28"
            text.update(changes)
        path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    def go(self, *more):
        def publish(platform, plan, *_):
            self.sent.append((platform, plan["platforms"][platform]["title"]))
            return {"state": "SUBMISSION_VERIFIED", "receipt": {"platform": platform}}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=publish):
            return run("go", self.work, *more)

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(VpubError) as error:
            call(*args, **kwargs)
        self.assertEqual(code, error.exception.code)
        return error.exception

    def test_prepare_says_everything_needed_to_write_the_words(self):
        answer = self.prepare("--about", "一条讲猫的视频")
        self.assertEqual(("NEEDS_COPY", 0), (answer["state"], answer["platform_writes"]))
        self.assertEqual({"bilibili", "youtube"}, set(answer["copy_template"]["platforms"]))
        self.assertEqual(answer["copy_template"], json.loads(Path(answer["copy_file"]).read_text(encoding="utf-8")))
        self.assertIn("title_limit", answer["targets"]["youtube"])
        self.assertTrue(answer["instructions"])
        self.assertEqual("public", answer["publishing"]["youtube"]["visibility"])
        self.assertIn("一条讲猫的视频", (self.work / "source.txt").read_text(encoding="utf-8"))
        self.assertEqual(["go", str(self.work)], shlex.split(answer["next"].split("run: ")[1])[1:])

    def test_about_and_a_script_are_both_kept(self):
        script = self.folder / "script.txt"
        script.write_text("完整的台词", encoding="utf-8")
        self.prepare("--about", "一句话", "--source", script)
        self.assertEqual("一句话\n\n完整的台词", (self.work / "source.txt").read_text(encoding="utf-8"))

    def test_a_refusal_leaves_nothing_behind(self):
        before = sorted(self.folder.iterdir())
        self.refused("invalid_platform", self.prepare, platforms="bilibili,tiktok")
        self.refused("visibility_required", self.prepare, visibility=None)
        self.refused("invalid_cover", self.prepare, "--cover", self.folder / "missing.jpg")
        self.refused("cover_required", self.prepare, "--portrait", self.picture("tall.png", (900, 1600)))
        self.assertEqual(before + [self.folder / "tall.png"], sorted(self.folder.iterdir()))

    def test_a_folder_is_never_overwritten(self):
        self.prepare()
        answer = self.refused("already_exists", self.prepare)
        self.assertIn("vpub go", answer.hint)

    def test_a_place_it_cannot_write_says_how_to_choose_another(self):
        with patch("video_publisher.quick.tempfile.mkdtemp", side_effect=PermissionError):
            self.assertIn("--folder", self.refused("folder_not_writable", self.prepare).hint)

    def test_douyin_needs_its_portrait_before_any_words_are_written(self):
        wide = self.picture("wide.png", (1600, 900))
        self.refused("portrait_required", self.prepare, "--cover", wide, platforms="douyin")
        self.refused("portrait_required", self.prepare, "--cover", wide, "--portrait", wide, platforms="douyin")
        not_a_picture = self.folder / "broken.png"
        not_a_picture.write_text("text", encoding="utf-8")
        self.refused("invalid_cover", self.prepare, "--cover", wide, "--portrait", not_a_picture, platforms="douyin")

    def test_a_schedule_reaches_each_platform_and_keeps_youtube_private_until_then(self):
        when = "2030-01-01T12:00:00+08:00"
        answer = self.prepare("--schedule", when)
        self.assertEqual((when, "private"), (answer["publishing"]["youtube"]["schedule"], answer["publishing"]["youtube"]["visibility"]))
        self.assertEqual((when, "public"), (answer["publishing"]["bilibili"]["schedule"], answer["publishing"]["bilibili"]["visibility"]))

    def test_a_schedule_that_cannot_work_is_refused_at_once(self):
        when = "2030-01-01T12:00:00+08:00"
        self.refused("unsupported_schedule", self.prepare, "--schedule", when, platforms="bilibili,instagram")
        self.refused("invalid_schedule", self.prepare, "--schedule", when, visibility="private")
        self.refused("invalid_schedule", self.prepare, "--schedule", when, "--draft-only", visibility=None)
        self.assertFalse(self.work.exists())

    def test_go_refuses_until_the_words_are_written_and_names_them(self):
        self.prepare()
        error = self.refused("copy_not_written", self.go)
        self.assertIn("youtube.title", error.message)
        self.assertIn("youtube.category_id", error.message)
        self.assertEqual([], self.sent)

    def test_go_on_a_folder_that_was_never_prepared(self):
        self.work.mkdir()
        self.refused("not_prepared", self.go)

    def test_go_publishes_with_the_words_that_were_written(self):
        self.prepare()
        self.write_copy()
        answer = self.go()
        self.assertTrue(answer["all_submissions_verified"])
        self.assertEqual([("bilibili", "一个完整的标题"), ("youtube", "一个完整的标题")], self.sent)
        self.assertTrue(cli.finished(answer))

    def test_go_forwards_opt_in_parallelism_and_keeps_serial_default(self):
        self.prepare(platforms="youtube,x")
        self.write_copy()
        with patch.object(cli, "publish_plan", return_value={"results": {}}) as publish:
            run("go", self.work, "--workers", "2")
            self.assertEqual(2, publish.call_args.kwargs["workers"])
            run("go", self.work)
            self.assertEqual(1, publish.call_args.kwargs["workers"])
            before = publish.call_count
            run("go", self.work, "--workers", "2", "--dry-run")
            self.assertEqual(before, publish.call_count)

    def test_a_dry_run_stops_before_the_upload(self):
        self.prepare()
        self.write_copy()
        answer = self.go("--dry-run")
        self.assertEqual(0, answer["platform_writes"])
        self.assertEqual([], self.sent)

    def test_go_rechecks_cover_tool_if_it_disappeared_after_prepare(self):
        self.prepare()
        self.write_copy()
        with patch("video_publisher.rehearsal.shutil.which", return_value=None):
            answer = self.go("--dry-run")
        self.assertFalse(answer["ready"])
        self.assertIn("cover_tool_missing", [b["code"] for b in answer["blockers"]])
        self.assertEqual([], self.sent)

    def test_blank_prepared_titles_become_authored_final_in_a_dry_run(self):
        self.prepare(platforms="bilibili,youtube,douyin,instagram,x")
        manifest = self.work / "publish.json"
        prepared = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("", prepared["title"])
        self.assertTrue(all("title" not in options for options in prepared["platforms"].values()))
        self.assertEqual("", json.loads((self.work / "request.json").read_text(encoding="utf-8"))["source"]["title"])
        error = self.refused("copy_not_written", run, "go", self.work, "--dry-run")
        self.assertIn("douyin.title", error.message)
        self.write_copy(title="Final")
        with patch.object(adapters, "preflight", side_effect=AssertionError("No account access")), \
                patch.object(adapters, "auth_status", side_effect=AssertionError("No account access")), \
                patch.object(adapters, "browser_call", side_effect=AssertionError("No browser access")), \
                patch.object(adapters, "bili", side_effect=AssertionError("No platform access")), \
                patch.object(adapters, "publish", side_effect=AssertionError("No upload")):
            answer = run("go", self.work, "--dry-run")
        self.assertEqual(0, answer["platform_writes"])
        self.assertEqual({"Final"}, {options["title"] for options in answer["platforms"].values()})
        self.assertEqual(prepared, json.loads(manifest.read_text(encoding="utf-8")))
        (built,) = (self.work / "build").glob("*/localized.json")
        localized = json.loads(built.read_text(encoding="utf-8"))
        self.assertEqual("", localized["title"])
        self.assertEqual({"Final"}, {options["title"] for options in localized["platforms"].values()})

    def test_covers_are_fitted_on_the_way(self):
        self.prepare("--cover", self.picture("wide.png", (1600, 900)), "--portrait", self.picture("tall.png", (900, 1600)), platforms="bilibili,douyin")
        self.write_copy()
        seen = {}
        def publish(platform, plan, *_):
            seen[platform] = plan["covers"][platform]
            return {"state": "SUBMISSION_VERIFIED", "receipt": {"platform": platform}}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=publish):
            answer = run("go", self.work)
        self.assertTrue(answer["all_submissions_verified"])
        self.assertEqual({"landscape"}, set(seen["bilibili"]))
        self.assertEqual({"landscape", "portrait"}, set(seen["douyin"]))

    def test_go_keeps_scratch_for_explicit_cleanup_without_inline_deletions(self):
        import os
        from video_publisher import overview, runtime_files
        from video_publisher.core import home, file_sha
        self.prepare('--cover', self.picture('wide.png', (1600, 900)))
        self.write_copy()
        original = file_sha(self.video)
        with patch('os.unlink', side_effect=AssertionError('inline file deletion')) as unlink, \
                patch('os.rmdir', side_effect=AssertionError('inline directory deletion')) as rmdir, \
                patch('shutil.rmtree', side_effect=AssertionError('inline tree deletion')) as rmtree:
            self.assertTrue(self.go()['all_submissions_verified'])
            self.assertEqual({'SKIPPED_EXISTING'}, {part['state'] for part in self.go()['results'].values()})
            unlink.assert_not_called(); rmdir.assert_not_called(); rmtree.assert_not_called()
        self.assertTrue(list((home() / 'runtime-cleanup').glob('*.json')))
        with patch.object(runtime_files, 'alive', return_value=False):
            result = overview.cleanup(apply=True)
        if os.name == 'nt':
            self.assertTrue(result['runtime_files']['removed'])
            self.assertFalse(result['runtime_files']['kept'])
        else:
            self.assertFalse(result['runtime_files']['removed'])
            self.assertTrue(result['runtime_files']['kept'])
        self.assertEqual(original, file_sha(self.video))
        self.assertTrue((self.work / 'copy.json').exists())

    def test_running_go_again_never_posts_twice(self):
        self.prepare("--cover", self.picture("wide.png", (1600, 900)))
        self.write_copy()
        self.go()
        self.sent.clear()
        answer = self.go()
        self.assertEqual([], self.sent)
        self.assertEqual({"SKIPPED_EXISTING"}, {part["state"] for part in answer["results"].values()})
        self.assertTrue(answer["all_submissions_verified"])

    def test_changed_words_only_reach_what_has_not_gone_out(self):
        self.prepare()
        self.write_copy()
        self.go()
        self.sent.clear()
        self.write_copy(title="改过的标题")
        answer = self.go()
        self.assertEqual([], self.sent)
        self.assertEqual(2, len(list((self.work / "build").iterdir())))
        self.assertEqual({"SKIPPED_EXISTING"}, {part["state"] for part in answer["results"].values()})

    def test_a_failed_try_can_be_fixed_and_run_again(self):
        self.prepare()
        self.write_copy(title="x" * 500)
        with self.assertRaises(VpubError):
            self.go()
        self.assertEqual([], self.sent)
        self.write_copy()
        self.assertTrue(self.go()["all_submissions_verified"])

    def test_the_same_failure_twice_reports_the_same_thing(self):
        self.prepare()
        self.write_copy(title="x" * 500)
        first = self.refused_any(self.go)
        self.assertEqual(first, self.refused_any(self.go))

    def refused_any(self, call):
        with self.assertRaises(VpubError) as error:
            call()
        return error.exception.code

    def published_visibility(self):
        seen = {}
        def publish(platform, plan, *_):
            seen[platform] = plan["platforms"][platform]["visibility"]
            return {"state": "SUBMISSION_VERIFIED", "receipt": {"platform": platform}}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=publish):
            run("go", self.work)
        return seen

    def change_built(self, name):
        (built,) = (self.work / "build").iterdir()
        path = built / name
        value = json.loads(path.read_text(encoding="utf-8"))
        value["platforms"]["youtube"]["visibility"] = "public"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return built

    def test_a_hand_changed_plan_in_the_build_is_never_used(self):
        self.prepare(platforms="youtube", visibility="private")
        self.write_copy()
        self.go("--dry-run")
        self.change_built("localized.json")
        self.assertEqual({"youtube": "private"}, self.published_visibility())

    def test_hand_changed_covers_are_set_aside_and_made_again(self):
        self.prepare("--cover", self.picture("wide.png", (1600, 900)), platforms="youtube", visibility="private")
        self.write_copy()
        self.go("--dry-run")
        built = self.change_built("ready.json")
        self.assertEqual({"youtube": "private"}, self.published_visibility())
        self.assertEqual(1, len(list(built.glob("changed-*/ready.json"))))

    def test_cached_media_platform_and_ai_label_cannot_override_prepared_inputs(self):
        self.prepare(platforms="youtube", visibility="private")
        self.write_copy()
        self.go("--dry-run")
        path = next((self.work / "build").glob("*/localized.json"))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["video"] = str(self.folder / "different.mp4")
        value["platforms"]["youtube"].update(ai_generated=False, account="different-account")
        value["platforms"]["x"] = {"method": "browser", "account": "different-account"}
        path.write_text(json.dumps(value), encoding="utf-8")
        seen = []
        def publish(platform, plan, *_):
            seen.append((platform, Path(plan["video"]).read_bytes(), plan["platforms"][platform]["ai_generated"], plan["platforms"][platform]["account"]))
            return {"state": "SUBMISSION_VERIFIED", "receipt": {"platform": platform}}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=publish):
            run("go", self.work)
        self.assertEqual([("youtube", self.video.read_bytes(), True, "default")], seen)

    def test_the_confirmation_code_stays_the_same_from_one_run_to_the_next(self):
        self.prepare("--cover", self.picture("wide.png", (1600, 900)), platforms="youtube")
        self.write_copy(title="联系我 13812345678")
        first = self.go()["confirmation"]
        self.assertEqual(first, self.go()["confirmation"])
        self.assertEqual('NEEDS_A_PERSON', self.go("--confirm", first)['state'])
        self.assertTrue(self.go("--confirm", first, '--person-approved')["all_submissions_verified"])

    def test_options_added_to_the_folders_plan_are_kept(self):
        self.prepare()
        self.write_copy()
        manifest = self.work / "publish.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["platforms"]["youtube"]["playlist_titles"] = ["合集"]
        manifest.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        seen = {}
        def publish(platform, plan, *_):
            seen[platform] = plan["platforms"][platform]
            return {"state": "SUBMISSION_VERIFIED", "receipt": {"platform": platform}}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=publish):
            run("go", self.work)
        self.assertEqual(["合集"], seen["youtube"]["playlist_titles"])

    def test_chapters_added_to_the_folders_plan_ask_for_their_titles_too(self):
        self.prepare(platforms="youtube")
        self.write_copy()
        chapters = [{"start_seconds": 0, "title": "开始"}, {"start_seconds": 60, "title": "中间"}, {"start_seconds": 120, "title": "结束"}]
        manifest = self.work / "publish.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["platforms"]["youtube"].update(chapters=chapters, duration_seconds=300)
        manifest.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.assertIn("chapters", self.refused("invalid_metadata", self.go).hint)
        self.write_copy(chapters=[{"start_seconds": chapter["start_seconds"], "title": "Part " + chapter["title"]} for chapter in chapters])
        self.assertTrue(self.go()["all_submissions_verified"])

    def test_the_skill_leads_with_the_two_commands(self):
        skill = (Path(__file__).resolve().parents[1] / "skills/publishops-crosscast/SKILL.md").read_text(encoding="utf-8")
        recipe = skill.split("```sh\n")[1].split("```")[0]
        self.assertEqual(["vpub prepare", "vpub go"], [" ".join(line.split()[:2]) for line in recipe.splitlines() if line.startswith("vpub")])

    def test_words_cannot_change_who_can_see_it(self):
        self.prepare()
        self.write_copy(visibility="private")
        error = self.refused("invalid_metadata", self.go)
        self.assertIn("needs exactly these fields", error.hint)
        self.assertEqual([], self.sent)

    def test_something_flagged_stops_until_a_person_says_so(self):
        self.prepare()
        self.write_copy(title="联系我 13812345678")
        answer = self.go()
        self.assertEqual("NEEDS_A_PERSON", answer["state"])
        self.assertFalse(cli.finished(answer))
        self.assertEqual([], self.sent)
        self.assertIn(answer["confirmation"], answer["next"])
        self.refused("confirmation_required", self.go, "--confirm", "wrong")
        self.assertEqual([], self.sent)
        self.assertEqual('NEEDS_A_PERSON', self.go("--confirm", answer['confirmation'])['state'])
        self.assertEqual([], self.sent)
        self.assertTrue(self.go("--confirm", answer["confirmation"], '--person-approved')["all_submissions_verified"])
        self.assertEqual(2, len(self.sent))

    def test_a_draft_needs_no_visibility(self):
        answer = self.prepare("--draft-only", visibility=None)
        self.assertEqual("draft_only", answer["publishing"]["youtube"]["submission_mode"])

    def test_the_declared_label_is_shown_back(self):
        self.assertTrue(self.prepare()["publishing"]["youtube"]["ai_generated"])
        ordinary = run("prepare", self.video, "--platforms", "youtube", "--language", "en", "--visibility", "public", "--ai-generated", "no", "--folder", self.folder / "ordinary")
        self.assertEqual((False, False), (ordinary["publishing"]["youtube"]["ai_generated"], ordinary["publishing"]["youtube"]["made_for_kids"]))

    def test_the_ai_label_has_to_be_said(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            run("prepare", self.video, "--platforms", "youtube", "--language", "en", "--visibility", "public")
        self.assertFalse(self.work.exists())

    def test_a_clean_dry_run_says_how_to_publish(self):
        self.prepare()
        self.write_copy()
        saved = {"method": "browser", "saved_record_present": True, "state": "SAVED_SIGN_IN_UNVERIFIED", "next_command": "vpub auth status"}
        with patch("video_publisher.rehearsal.sign_in", return_value=saved):
            answer = self.go("--dry-run")
        self.assertTrue(cli.finished(answer))
        self.assertIn(f'vpub go "{self.work}"', answer["next"])

    def test_a_draft_dry_run_says_nothing_will_be_published(self):
        self.prepare("--draft-only", visibility=None)
        self.write_copy()
        saved = {"method": "browser", "saved_record_present": True, "state": "SAVED_SIGN_IN_UNVERIFIED", "next_command": "vpub auth status"}
        with patch("video_publisher.rehearsal.sign_in", return_value=saved):
            answer = self.go("--dry-run")
        self.assertIn("unpublished, as a draft or preview", answer["next"])
        self.assertNotIn("publish it", answer["next"])

    def test_a_blocked_dry_run_does_not_say_go_ahead(self):
        self.prepare()
        self.write_copy()
        answer = self.go("--dry-run")
        self.assertFalse(cli.finished(answer))
        self.assertNotIn("To publish it", answer.get("next", ""))
        self.assertTrue(answer["blockers"])

    def test_a_draft_rehearsal_next_step_keeps_the_draft_mode(self):
        self.prepare("--draft-only", platforms="youtube", visibility=None)
        self.write_copy()
        with patch("video_publisher.rehearsal.sign_in", return_value={"saved_record_present": True}):
            answer = self.go("--dry-run")
        self.assertNotIn("To publish it", answer["next"])
        self.assertIn("draft", answer["next"])
        seen = []
        def preview(platform, plan, *_):
            seen.append(plan["platforms"][platform]["submission_mode"])
            return {"state": "PREVIEW_READY", "platform": platform}
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=preview):
            result = run("go", self.work)
        self.assertEqual(["draft_only"], seen)
        self.assertIsNone(result["all_submissions_verified"])


if __name__ == "__main__":
    unittest.main()
