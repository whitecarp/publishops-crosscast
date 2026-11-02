"""Titles, descriptions and tags go public under the owner's name. What is a secret on sight is refused, and a link is only pointed out, so ordinary titles aren't blocked. Nothing is contacted."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, collections, edits, safety, widgets_bilibili
from video_publisher.core import VpubError, atomic_json, load_plan

SECRETS = ["password: x", "my password: hunter2", "密码：123456", "我的密码：123456，别告诉别人", "password=abc123 then log in", "passcode = 8675309",
           "sk-abcdefgh12345678abcd", "ghp_abcdefghijklmnopqrstuvwxyz", "github_pat_abcdefghijklmnopqrstuvwxyz", "AKIAABCDEFGHIJKLMNOP", "AIzaabcdefghijklmnopqrstuvwxyz123456",
           "xoxb-1234567890-abc", "ya29.abcdefghij12345", "eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY3ODkw", "-----BEGIN RSA PRIVATE KEY-----",
           "SESSDATA=abc123def456", "bili_jct: 0123456789abcdef", "api_key = abc123def456", "client_secret=abc123def456", "Authorization: Bearer abc123def456", "Set-Cookie: sid=abc"]
ORDINARY = ["API key: how to get one", "Password: what makes a good one", "Password: 10 tips for stronger logins", "Passwords: 10 tips", "密码：忘记了怎么办", "密码：如何设置 8 位密码",
            "How cookies work", "Set-Cookie explained", "SESSDATA explained", "Authorization: Bearer tokens explained", "Begin with a private key", "SK-II facial treatment essence review",
            "sk-learn-tutorial-for-beginners", "task-management-for-beginners-2026", "Token: ETH", "淘口令：abc123", "提取码: abcd", "pan.baidu.com/s/xx?pwd=1234", "example.ru/login",
            r"C:\Users\me\clip.mp4", "Node.js", "我的原创视频"]


class WordsTests(unittest.TestCase):
    def test_what_is_a_secret_on_sight_is_refused(self):
        for text in SECRETS:
            with self.subTest(text=text), self.assertRaises(VpubError) as caught:
                safety.check_public_text(text)
            self.assertEqual("unsafe_text", caught.exception.code)

    def test_ordinary_text_goes_through(self):
        for text in ORDINARY:
            with self.subTest(text=text):
                safety.check_public_text(text)

    def test_the_refusal_never_repeats_the_words(self):
        with self.assertRaises(VpubError) as caught:
            safety.check_public_text("password: hunter2")
        self.assertNotIn("hunter2", caught.exception.message + (caught.exception.hint or ""))

    def test_a_hidden_character_or_full_width_letters_hide_nothing(self):
        for text in ("pass\u200bword: hunter2", "ｐａｓｓｗｏｒｄ： hunter2"):
            with self.subTest(text=text), self.assertRaises(VpubError):
                safety.check_public_text(text)

    def test_it_looks_inside_lists_and_rows_and_leaves_numbers_alone(self):
        with self.assertRaises(VpubError):
            safety.check_public_text(None, ["fine", {"title": "password: x", "start_seconds": 0}])
        safety.check_public_text(None, 12, ["fine", {"title": "Intro", "start_seconds": 0}])

    def test_a_link_or_a_private_folder_is_only_pointed_out(self):
        self.assertEqual(["link_or_contact"], safety.public_text_flags("See example.ru/login"))
        self.assertEqual(["private_path"], safety.public_text_flags(r"C:\Users\me\clip.mp4"))
        self.assertEqual(["link_or_contact", "private_path"], safety.public_text_flags("Mail me@example.com", ["/home/me/clip"]))
        self.assertEqual([], safety.public_text_flags("Node.js in ten minutes", ["AI"], None))


class Workspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)


class PlanTests(Workspace):
    def setUp(self):
        super().setUp()
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"synthetic test fixture, not a playable upload")
        self.manifest = self.root / "publish.json"
        self.value = cli.template(self.video)
        self.value["platforms"]["youtube"]["category_id"] = "1"

    def plan(self, **changes):
        atomic_json(self.manifest, {**self.value, **changes})
        return load_plan(self.manifest)

    def answer(self, **changes):
        atomic_json(self.manifest, {**self.value, **changes})
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.execute(cli.parser().parse_args(["plan", str(self.manifest)]))

    def test_a_plan_with_a_secret_in_its_words_is_refused(self):
        for changes in ({"title": "password: x"}, {"description": "密码：123456"}, {"tags": ["AI", "sk-abcdefgh12345678abcd"]}):
            with self.subTest(changes=changes), self.assertRaises(VpubError) as caught:
                self.plan(**changes)
            self.assertEqual("unsafe_text", caught.exception.code)

    def test_one_platforms_own_words_count(self):
        self.value["platforms"]["bilibili"]["description"] = "password: x"
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual("unsafe_text", caught.exception.code)

    def test_chapter_titles_count_too(self):
        self.value["platforms"]["bilibili"].update(chapters=[{"start_seconds": 0, "title": "Intro"}, {"start_seconds": 60, "title": "password: x"}], duration_seconds=120)
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual("unsafe_text", caught.exception.code)

    def test_an_ordinary_plan_says_nothing_extra(self):
        answer = self.answer(title="An ordinary title", description="An ordinary description")
        self.assertNotIn("text_flags", answer)

    def test_a_link_is_named_and_refuses_nothing(self):
        answer = self.answer(description="More at example.ru/login")
        self.assertEqual(["link_or_contact"], answer["text_flags"]["bilibili"])
        self.assertIn("Not refused", answer["text_flags_note"])

    def test_a_flag_does_not_change_what_was_approved(self):
        plain = self.plan(description="More at example.ru/login")
        self.assertNotIn("text_flags", plain)
        self.assertEqual(plain["confirmation"], self.answer(description="More at example.ru/login")["confirmation"])


class EditTests(Workspace):
    TEXT = {"expected_identity": "42", "title": "New title", "description": "New text", "content_language": "zh-Hans", "metadata_language": "zh-Hans", "metadata_mode": "target_only"}

    def test_a_correction_with_a_secret_is_refused(self):
        for changes in ({"title": "password: x"}, {"description": "密码：123456"}):
            with self.subTest(changes=changes), self.assertRaises(VpubError) as caught:
                edits.validate_patch("bilibili", {**self.TEXT, **changes})
            self.assertEqual("unsafe_text", caught.exception.code)

    def test_a_read_back_compares_what_the_work_already_says(self):
        self.assertEqual("password: x", edits.validate_patch("bilibili", {**self.TEXT, "description": "password: x"}, verifying=True)["description"])

    def test_a_dotted_word_is_named_on_a_dry_run_and_an_address_stays_refused(self):
        patch_ = edits.validate_patch("bilibili", {**self.TEXT, "description": "More at example.ru/login"})
        answer = edits.send("bilibili", "default", "42", {"id": "BV1xx411c7mD"}, "key", patch_, True, False)
        self.assertEqual(["link_or_contact"], answer["text_flags"])
        with self.assertRaises(VpubError) as caught:
            edits.validate_patch("bilibili", {**self.TEXT, "description": "More at https://example.ru/login"})
        self.assertEqual("links_disabled", caught.exception.code)


class OtherPublicTextTests(Workspace):
    def test_a_collection_or_playlist_title_is_checked_before_anything_else(self):
        for platform, options in (("x", {"title": "password: x"}), ("youtube", {"title": "Mine", "description": "password: x"})):
            with self.subTest(platform=platform), patch.object(collections, "call") as call, self.assertRaises(VpubError) as caught:
                collections.change(platform, "create", request_id="one", **options)
            self.assertEqual("unsafe_text", caught.exception.code)
            call.assert_not_called()

    def test_a_widgets_words_are_checked_on_both_of_its_routes(self):
        with self.assertRaises(VpubError) as caught:
            widgets_bilibili.validate([{"type": "vote", "time": 30, "question": "password: x", "options": ["甲", "乙"]}])
        self.assertEqual("unsafe_text", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
