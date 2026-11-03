"""Offline X text estimate before upload; the platform's own counter still decides."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, edits, metadata
from video_publisher.core import X_POST_LIMIT, VpubError, atomic_json, load_plan, x_weighted_length


class CountTests(unittest.TestCase):
    def test_startup_and_other_platforms_do_not_import_the_parser(self):
        code = "from video_publisher import cli, core, metadata; import sys; cli.parser(); assert 'twitter_text' not in sys.modules"
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'VPUB_HOME': str(Path(folder) / 'home'), 'BILI_CLI_HOME': str(Path(folder) / 'bili')}
            result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_latin_counts_one_and_chinese_two(self):
        self.assertEqual((3, 4, 5), (x_weighted_length("abc"), x_weighted_length("中文"), x_weighted_length("a, b!")))

    def test_new_lines_and_typographic_quotes_count_one(self):
        self.assertEqual((4, 3), (x_weighted_length("a\n\nb"), x_weighted_length("\u201ca\u201d")))

    def test_an_emoji_never_counts_less_than_on_x(self):
        self.assertEqual(2, x_weighted_length("\U0001f600"))
        # Recognized sequences count as one emoji, not separate code points.
        for emoji in ("\U0001f44d\U0001f3fd", "\u2764\ufe0f", "\U0001f468\u200d\U0001f469\u200d\U0001f467", "\U0001f1e8\U0001f1f3", "1\ufe0f\u20e3"):
            self.assertEqual(x_weighted_length(emoji), 2, emoji)

    def test_a_link_with_a_known_ending_counts_twenty_three_whatever_its_length(self):
        self.assertEqual((23, 23, 24), (x_weighted_length("https://example.com/a/very/long/path?with=query"), x_weighted_length("b23.tv"), x_weighted_length("b23.tv ")))
        self.assertEqual(24, x_weighted_length("example.com."))

    # Codex's counterexamples, XCOUNT96-01 and -02: none of these may count lower than X does.
    def test_a_joiner_never_swallows_what_follows_it(self):
        self.assertEqual((3, 5), (x_weighted_length("a\u200db"), x_weighted_length("\u2318\u200d\u2318")))

    def test_skin_tones_on_their_own_count_two_each(self):
        self.assertEqual(300, x_weighted_length("\U0001f3fb" * 150))
        self.assertGreaterEqual(x_weighted_length("\U0001f44d\U0001f3fd\U0001f3fd"), 4)

    def test_a_dotted_word_counts_its_full_length_when_that_is_more(self):
        self.assertEqual(302, x_weighted_length("a." + "a" * 300))

    def test_an_address_whose_ending_crosscast_does_not_know_counts_at_least_23(self):
        self.assertEqual((23, 23), (x_weighted_length("a.engineering"), x_weighted_length("foo.notatld")))

    def test_a_known_tld_does_not_turn_non_links_into_short_links(self):
        for text in ('a' * 300 + '.com', 'foo@example.com/' + 'a' * 300,
                     '@example.com/' + 'a' * 300, '_example.com/' + 'a' * 300):
            with self.subTest(text=text[:20]):
                self.assertEqual(len(text), x_weighted_length(text))

    def test_nfc_and_crlf_use_the_written_normalized_text(self):
        self.assertEqual(3, x_weighted_length('e\u0301\r\nx'))

    def test_missing_parser_stops_with_an_install_hint(self):
        with patch.dict('sys.modules', {'twitter_text': None}), self.assertRaises(VpubError) as error:
            x_weighted_length('hello')
        self.assertEqual('missing_dependency', error.exception.code)
        self.assertIn('Reinstall', error.exception.hint)

    def test_invalid_unicode_is_a_clear_preflight_error(self):
        with self.assertRaises(VpubError) as error:
            x_weighted_length('\ud800')
        self.assertEqual('invalid_description', error.exception.code)

    def test_parser_invalid_characters_are_rejected_even_inside_a_link(self):
        for char in ('\ufeff', '\ufffe', '\uffff'):
            for text in ('before' + char + 'after', 'https://example.com/' + char, 'x' * 281 + char):
                with self.subTest(char=ord(char), text_length=len(text)), self.assertRaises(VpubError) as error:
                    x_weighted_length(text)
                self.assertEqual('invalid_description', error.exception.code)

    def test_long_valid_text_is_counted_for_the_overlength_error(self):
        self.assertEqual(281, x_weighted_length('a' * 281))


class PlanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = patch.dict("os.environ", {"VPUB_HOME": str(self.folder / "home"), "BILI_CLI_HOME": str(self.folder / "bili")})
        patcher.start()
        self.addCleanup(patcher.stop)
        video = self.folder / "final.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))
        self.manifest = self.folder / "publish.json"
        self.value = {"schema_version": 1, "video": str(video), "content_language": "und", "title": "T", "description": "", "tags": [],
                      "platforms": {"x": {"account": "default", "method": "browser", "visibility": "account", "ai_generated": False}}}

    def plan(self, description, ai_generated=False, language="und"):
        self.value["platforms"]["x"].update(description=description, ai_generated=ai_generated)
        self.value["content_language"] = language
        atomic_json(self.manifest, self.value)
        return load_plan(self.manifest)

    def test_exactly_the_limit_goes_through_and_one_more_is_refused(self):
        # "T", two new lines, then the description.
        self.assertEqual(X_POST_LIMIT, x_weighted_length(self.plan("a" * (X_POST_LIMIT - 3))["platforms"]["x"]["post_text"]))
        with self.assertRaises(VpubError) as error:
            self.plan("a" * (X_POST_LIMIT - 2))
        self.assertEqual("x_text_too_long", error.exception.code)
        self.assertIn(f"comes to {X_POST_LIMIT + 1}, over X's {X_POST_LIMIT}", error.exception.message)
        self.assertIn("Nothing was uploaded", error.exception.hint)

    def test_the_ai_line_counts_too(self):
        fits = "a" * (X_POST_LIMIT - 3)
        self.plan(fits)
        with self.assertRaises(VpubError):
            self.plan(fits, ai_generated=True)

    def test_chinese_reaches_the_limit_at_half_the_characters(self):
        self.plan("中" * 138)  # 1 + 2 + 276 = 279
        with self.assertRaises(VpubError):
            self.plan("中" * 139)

    def test_the_field_runs_bilingual_post_is_refused_before_upload(self):
        chinese = "GPT替大肥鱼值班，第四张工单就破产了。" * 8
        english = "GPT covers the shift and goes broke on the fourth ticket. " * 6
        with self.assertRaises(VpubError) as error:
            self.plan(chinese + "\n\n" + english, ai_generated=True, language="zh-Hans")
        self.assertEqual("x_text_too_long", error.exception.code)

    def test_other_platforms_keep_their_own_limits(self):
        self.value["platforms"]["instagram"] = {"account": "default", "method": "browser", "visibility": "account", "ai_generated": False, "description": "中" * 600}
        self.value["platforms"]["x"]["description"] = "short"
        atomic_json(self.manifest, self.value)
        self.assertEqual({"x", "instagram"}, set(load_plan(self.manifest)["platforms"]))


class WriterTests(unittest.TestCase):
    def test_the_request_tells_the_writer_xs_real_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "final.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))
            manifest = folder / "publish.json"
            with patch.dict("os.environ", {"VPUB_HOME": str(folder / "home"), "BILI_CLI_HOME": str(folder / "bili")}):
                cli.run_command(cli.parser().parse_args(["init", "--video", str(video), "--out", str(manifest), "--platforms", "x,youtube", "--language", "zh-Hans"]))
                job = metadata.request(manifest)
        target = job["targets"]["x"]
        self.assertEqual((X_POST_LIMIT, X_POST_LIMIT), (target["post_limit"], target["description_limit"]))
        self.assertIn("count 2", target["post_counting"])
        self.assertNotIn("post_limit", job["targets"]["youtube"])
        self.assertTrue(any("post_limit" in line and "credits" in line for line in job["host_instructions"]))

    def test_an_x_edit_is_held_to_the_same_count_but_a_read_back_is_not(self):
        long = {"expected_identity": "owner", "title": "T", "description": "words " * 60, "content_language": "en", "metadata_language": "en", "metadata_mode": "target_only"}
        with self.assertRaises(VpubError) as error:
            edits.validate_patch("x", long, False, False)
        self.assertEqual("x_text_too_long", error.exception.code)
        self.assertIn("post_text", edits.validate_patch("x", long, True, False))


class TwoCommandTests(unittest.TestCase):
    def test_vpub_go_stops_before_any_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "final.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))
            with patch.dict("os.environ", {"VPUB_HOME": str(folder / "home"), "BILI_CLI_HOME": str(folder / "bili")}):
                answer = cli.execute(cli.parser().parse_args(["prepare", str(video), "--platforms", "x", "--language", "en", "--visibility", "public", "--ai-generated", "yes"]))
                work = Path(answer["folder"])
                words = answer["copy_template"]
                words["platforms"]["x"].update(title="A title", description="word " * 80, tags=[])
                (work / "copy.json").write_text(json.dumps(words), encoding="utf-8")
                with patch.object(adapters, "preflight", side_effect=AssertionError("No account access")), patch.object(adapters, "publish", side_effect=AssertionError("No upload")):
                    with self.assertRaises(VpubError) as error:
                        cli.execute(cli.parser().parse_args(["go", str(work)]))
        self.assertEqual("x_text_too_long", error.exception.code)


if __name__ == "__main__":
    unittest.main()
