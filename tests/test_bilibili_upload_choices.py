"""Bilibili's topic, 4:3 cover and mentions, chosen in the plan and set at upload. Stand-ins only; nothing contacts Bilibili.

A topic can only be joined at upload, an activity topic's mission_id is what enters its contest, and bili upload status reports
neither, so the receipt keeps them. Whether Bilibili has the topic and finds each @nickname depends on Bilibili, so bili-cli's
own dry run is asked first, before the attempt is recorded, where a refusal sends nothing and the same video can be tried again.
"""
import asyncio
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from bili_cli.commands.upload import build_desc_v2
from bili_cli.errors import BiliError
from video_publisher import adapters, cli, metadata
from video_publisher.core import Ledger, NothingSent, VpubError, atomic_json, load_plan, never_sent

WORK = "BV1xx411c7mD"
TOPIC = {"id": 1340873, "name": "AI MV 大赛", "mission_id": 4071698, "activity": 1}
PEOPLE = {111: "小白", 222: "Example"}
WORDS = "谢谢 @小白 和 @Example 帮忙"


def values(args, flag):
    return [args[i + 1] for i, word in enumerate(args[:-1]) if word == flag]


class FakeBili:
    """bili-cli's upload as CrossCast sees it: its dry run says what it would send, and the upload what it entered."""

    def __init__(self):
        self.calls, self.dry_answer, self.dry_refusal = [], None, None
        # The archive view under bili upload status, which holds the topic and contest the work is in.
        self.archive = {"topic_id": TOPIC["id"], "mission_id": TOPIC["mission_id"]}

    def __call__(self, args, timeout=210, program=None):
        self.calls.append(list(args))
        dry = args[:1] == ["--dry-run"]
        words = list(args[1:] if dry else args)
        if words[:2] == ["upload", "video"]:
            return self.upload(words, dry)
        if words[:2] == ["upload", "status"]:
            return {"archive": {"bvid": WORK, **self.archive}} if "--raw" in words else {"bvid": WORK, "ai_declare": True, "private": False}
        if words[:3] == ["danmaku", "interact", "list"]:
            return {"items": []}
        raise AssertionError(f"Unexpected bili call: {args}")

    def upload(self, words, dry):
        if dry and self.dry_refusal:
            raise self.dry_refusal
        topic = None
        for query in values(words, "--topic"):
            # Like bili-cli's find_topic: the name with spaces and capitals ignored, or the ID.
            if query.replace(" ", "").lower() != TOPIC["name"].replace(" ", "").lower() and query != str(TOPIC["id"]):
                raise VpubError("not_found", f"找不到话题 {query!r}。")
            topic = dict(TOPIC)
        mentions = {}
        for uid in values(words, "--desc-at"):
            if int(uid) not in PEOPLE:
                raise VpubError("not_found", f"找不到 UID {uid} 对应的用户。")
            mentions[PEOPLE[int(uid)]] = int(uid)
        desc, segments = build_desc_v2(values(words, "--desc")[0].strip(), mentions)
        if dry:
            return self.dry_answer or {"dry_run": True, "action": "upload.video", "params": {"topic": topic}, "meta": {"desc": desc, **({"desc_v2": segments} if segments else {})}}
        return {"bvid": WORK, "aid": 1, "topic": topic}

    def uploads(self):
        return [call for call in self.calls if call[:2] == ["upload", "video"]]

    def dry_runs(self):
        return [call for call in self.calls if call[:3] == ["--dry-run", "upload", "video"]]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)
        self.bili = FakeBili()
        target = patch.object(adapters, "bili", self.bili)
        target.start()
        self.addCleanup(target.stop)

    def picture(self, name, size, colour="#336699"):
        path = self.root / name
        Image.new("RGB", size, colour).save(path)
        return path

    def manifest(self, covers=None, others=None, **bilibili):
        video = self.root / "v.mp4"
        if not video.exists():
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(1024))
        entry = {"account": "default", "title": "标题", "description": WORDS, "tags": ["音乐"], "visibility": "public", "ai_generated": True,
                 "topic": "AI MV 大赛", "mention_uids": [111, 222], **bilibili}
        path = self.root / "publish.json"
        atomic_json(path, {"schema_version": 1, "video": "v.mp4", "cover": "wide.png", "content_language": "zh-Hans",
                           "covers": {"bilibili": {"cover43": "c43.png"}} if covers is None else covers,
                           "platforms": {"bilibili": {key: value for key, value in entry.items() if value is not None}, **(others or {})}})
        return path


class UploadTests(Temporary):
    OPTIONS = {"title": "标题", "description": WORDS, "ai_generated": True, "tags": ["音乐"], "visibility": "public", "account": "default"}
    COVERS = {"bilibili": {"landscape": {"path": "wide.jpg", "sha256": "1"}, "cover43": {"path": "c43.jpg", "sha256": "2"}}}

    def publish(self, plan=None, **options):
        self.marks = []
        return adapters.publish("bilibili", plan or {"video": "v.mp4", "covers": self.COVERS}, {**self.OPTIONS, **options}, self.marks.append)

    def refused(self, code, **options):
        with self.assertRaises(NothingSent) as refused:
            self.publish(**options)
        self.assertEqual(code, refused.exception.code)
        self.assertTrue(never_sent(refused.exception))
        self.assertEqual([], self.bili.uploads())
        return refused.exception

    def go(self, **bilibili):
        """vpub publish of an approved plan with the topic, both covers and two mentions."""
        self.picture("wide.png", (1600, 900))
        self.picture("c43.png", (1200, 900))
        with patch.object(adapters, "preflight", return_value={"identity": "42"}):
            plan = load_plan(self.manifest(**bilibili))
            return cli.publish_plan(plan, plan["confirmation"], reviewed=True)["results"]["bilibili"]

    def records(self):
        ledger = Ledger()
        try:
            return ledger.rows("bilibili")
        finally:
            ledger.close()

    def test_the_upload_gets_the_topic_the_43_cover_and_each_mention_after_its_own_dry_run(self):
        result = self.publish(topic="AI MV 大赛", mention_uids=[111, 222])
        upload = ["upload", "video", "v.mp4", "--title", "标题", "--desc", WORDS, "--ai-declare", "--tag", "音乐", "--cover", "wide.jpg",
                  "--topic", "AI MV 大赛", "--cover43", "c43.jpg", "--desc-at", "111", "--desc-at", "222"]
        self.assertEqual([["--dry-run", *upload], upload], self.bili.calls[:2])
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])

    def test_the_receipt_keeps_the_topic_and_its_contest(self):
        result = self.publish(topic="AI MV 大赛", mention_uids=[111, 222])
        entered = {"name": "AI MV 大赛", "id": 1340873, "mission_id": 4071698}
        self.assertEqual((entered, [111, 222]), (result["topic"], result["mention_uids"]))
        # Kept before the read-back, which can't report them.
        self.assertEqual(("SUBMITTED", entered), (self.marks[0]["state"], self.marks[0]["topic"]))
        self.assertEqual(["upload", "status", WORK], self.bili.calls[2])

    def test_verify_says_whether_bilibili_kept_the_topic_and_its_contest(self):
        kept = self.publish(topic="AI MV 大赛")
        self.assertEqual((True, "SUBMISSION_VERIFIED"), (kept["topic_kept"], kept["state"]))
        self.assertIn("Bilibili's record keeps the topic", kept["note"])
        self.bili.archive = {"topic_id": TOPIC["id"]}  # The topic without its contest.
        dropped = self.publish(topic="AI MV 大赛")
        self.assertEqual(False, dropped["topic_kept"])
        self.assertIn("doesn't show the topic this upload sent, or its contest", dropped["note"])
        self.assertNotIn("大赛", kept["note"] + dropped["note"])

    def test_without_them_the_upload_is_as_it_was(self):
        self.publish({"video": "v.mp4"})
        self.assertEqual([["upload", "video", "v.mp4", "--title", "标题", "--desc", WORDS, "--ai-declare", "--tag", "音乐"]], self.bili.uploads())
        self.assertEqual([], self.bili.dry_runs())
        # With no topic, verify reads nothing more than before.
        self.assertFalse(any("--raw" in call for call in self.bili.calls))

    def test_a_43_cover_alone_needs_no_dry_run(self):
        result = self.publish({"video": "v.mp4", "covers": {"bilibili": {"cover43": {"path": "c43.jpg", "sha256": "2"}}}})
        self.assertEqual([], self.bili.dry_runs())
        self.assertEqual(["--cover43", "c43.jpg"], self.bili.uploads()[0][-2:])
        self.assertNotIn("topic", result)

    def test_a_topic_bilibili_names_another_way_stops_before_the_upload(self):
        error = self.refused("topic_name_differs", topic="ai mv大赛")
        self.assertNotIn("大赛", error.message + error.hint)

    def test_a_topic_bilibili_cannot_find_stops_before_the_upload(self):
        error = self.refused("topic_not_found", topic="没有这个话题")
        self.assertNotIn("没有这个话题", error.message + error.hint)

    def test_each_person_must_be_written_as_at_nickname_and_a_space(self):
        for words, places in (("谢谢 @小白 帮忙", "#2"), ("谢谢 @小白，@Example 帮忙", "#1"), ("谢谢大家", "#1, #2")):
            with self.subTest(words=words):
                error = self.refused("mention_not_written", description=words, mention_uids=[111, 222])
                self.assertIn(f"Check mention_uids {places}.", error.hint)
                self.assertIn("then an ordinary space", error.hint)
                for said in ("小白", "Example", "111", "222"):
                    self.assertNotIn(said, error.message + error.hint)

    def test_a_full_width_at_or_space_is_named_where_it_is(self):
        # A real contest entry on 2026-11-05 mixed up the ordinary @ and space with these look-alikes.
        error = self.refused("mention_not_written", description="谢谢 ＠小白 和 @Example\u3000帮忙", mention_uids=[111, 222])
        self.assertIn("Check mention_uids #1 (full-width ＠), #2 (no ordinary space after it).", error.hint)
        self.assertIn("the ordinary half-width @ (not the full-width ＠)", error.hint)
        for said in ("小白", "Example"):
            self.assertNotIn(said, error.message + error.hint)

    def test_a_mention_that_ends_the_description_needs_no_space_after_it(self):
        self.assertEqual("SUBMISSION_VERIFIED", self.publish(description="谢谢 @小白", mention_uids=[111])["state"])

    def test_a_uid_bilibili_has_no_account_for_stops_before_the_upload(self):
        error = self.refused("mention_not_found", mention_uids=[111, 333])
        self.assertNotIn("333", error.message + error.hint)

    def test_any_other_refusal_of_the_dry_run_sent_nothing_either(self):
        self.bili.dry_refusal = VpubError("invalid_argument", "B 站不接受这些标签：音乐", "")
        error = self.refused("invalid_argument", topic="AI MV 大赛")
        self.assertIn("Nothing was uploaded", error.hint)

    def test_a_dry_run_that_did_not_answer_as_one_is_never_tried_again(self):
        # bili-cli may then have uploaded it, so this is no refusal that sent nothing.
        self.bili.dry_answer = {"bvid": WORK}
        with self.assertRaises(VpubError) as caught:
            self.publish(topic="AI MV 大赛")
        self.assertFalse(never_sent(caught.exception))
        self.assertEqual([], self.bili.uploads())

    def test_vpub_publish_asks_the_dry_run_before_it_records_the_attempt_and_only_once(self):
        claim = Ledger.claim

        def claimed(ledger, *args):
            self.bili.calls.append("claimed")
            return claim(ledger, *args)
        with patch.object(Ledger, "claim", claimed):
            result = self.go()
        steps = [call if call == "claimed" else "dry run" if call[0] == "--dry-run" else " ".join(call[:2]) for call in self.bili.calls]
        self.assertEqual(["dry run", "claimed", "upload video", "upload status"], steps[:4])
        self.assertEqual(("SUBMISSION_VERIFIED", 1), (result["state"], steps.count("dry run")))

    def test_a_dry_run_cut_off_midway_leaves_no_record_so_the_video_can_go_again(self):
        # It can take minutes, and an attempt recorded before it would look like an upload that may have gone out.
        self.bili.dry_refusal = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.go()
        self.assertEqual([], self.records())
        self.bili.dry_refusal = None
        self.assertEqual("SUBMISSION_VERIFIED", self.go()["state"])
        self.assertEqual(1, len(self.bili.uploads()))

    def test_vpub_publish_records_a_dry_run_that_did_not_answer_as_one_and_never_tries_it_again(self):
        self.bili.dry_answer = {"bvid": WORK}
        first, second = self.go(), self.go()
        self.assertEqual(("UNKNOWN", "backend_error", "SKIPPED_EXISTING"), (first["state"], first["code"], second["state"]))
        self.assertEqual((1, 1, []), (len(self.records()), len(self.bili.dry_runs()), self.bili.uploads()))

    def test_a_refusal_leaves_the_video_free_to_go_again_once_the_plan_is_right(self):
        self.picture("wide.png", (1600, 900))
        self.picture("c43.png", (1200, 900))
        with patch.object(adapters, "preflight", return_value={"identity": "42"}):
            plan = load_plan(self.manifest(topic="ai mv大赛"))
            first = cli.publish_plan(plan, plan["confirmation"], reviewed=True)["results"]["bilibili"]
            ledger = Ledger()
            try:
                left = ledger.rows("bilibili")
            finally:
                ledger.close()
            plan = load_plan(self.manifest())
            second = cli.publish_plan(plan, plan["confirmation"], reviewed=True)["results"]["bilibili"]
        self.assertEqual(("NOT_STARTED", "topic_name_differs", []), (first["state"], first["code"], left))
        self.assertEqual(("SUBMISSION_VERIFIED", 1340873, 4071698), (second["state"], second["topic"]["id"], second["topic"]["mission_id"]))
        self.assertEqual(1, len(self.bili.uploads()))
        # The 4:3 cover that went up is the attempt's own copy of the approved file.
        sent = self.bili.uploads()[0]
        self.assertEqual((self.root / "c43.png").read_bytes(), Path(sent[sent.index("--cover43") + 1]).read_bytes())


class PlanTests(Temporary):
    def setUp(self):
        super().setUp()
        self.picture("wide.png", (1600, 900))
        self.picture("c43.png", (1200, 900))

    def refused(self, code, covers=None, others=None, **bilibili):
        with self.assertRaises(VpubError) as refused:
            load_plan(self.manifest(covers, others, **bilibili))
        self.assertEqual(code, refused.exception.code)

    def test_all_three_are_in_the_plan(self):
        plan = load_plan(self.manifest())
        self.assertEqual(("AI MV 大赛", [111, 222]), (plan["platforms"]["bilibili"]["topic"], plan["platforms"]["bilibili"]["mention_uids"]))
        self.assertEqual(str(self.root / "c43.png"), plan["covers"]["bilibili"]["cover43"]["path"])

    def test_the_plan_code_changes_when_any_of_them_does(self):
        before = load_plan(self.manifest())["confirmation"]
        self.assertEqual(before, load_plan(self.manifest())["confirmation"])
        codes = {before}
        for change in ({"topic": "另一个话题"}, {"mention_uids": [222, 111]}, {"mention_uids": [111]}, {"topic": None}, {"mention_uids": None}):
            with self.subTest(change=change):
                code = load_plan(self.manifest(**change))["confirmation"]
                self.assertNotIn(code, codes)
                codes.add(code)
        self.picture("c43.png", (1200, 900), "#996633")
        self.assertNotIn(load_plan(self.manifest())["confirmation"], codes)
        self.assertNotIn(load_plan(self.manifest(covers={}))["confirmation"], codes)

    def test_what_a_plan_refuses(self):
        for change, code in (({"topic": "两行\n话题"}, "invalid_topic"), ({"topic": " 话题"}, "invalid_topic"), ({"topic": 1340873}, "invalid_topic"),
                             ({"mention_uids": ["111"]}, "invalid_mention"), ({"mention_uids": [111, 111]}, "invalid_mention"), ({"mention_uids": []}, "invalid_mention"),
                             ({"mention_uids": [True]}, "invalid_mention"), ({"mention_uids": list(range(1, 12))}, "invalid_mention"),
                             ({"description": "谢谢 @小白 帮忙"}, "invalid_mention"), ({"method": "native"}, "invalid_method"),
                             ({"submission_mode": "draft_only", "mention_uids": None}, "invalid_submission_mode"),
                             ({"submission_mode": "draft_only", "topic": None}, "invalid_submission_mode")):
            with self.subTest(change=change):
                self.refused(code, **change)

    def test_a_43_cover_must_be_a_43_jpg_or_png_on_bilibili(self):
        self.picture("wide43.png", (1600, 1000))
        (self.root / "c43.gif").write_bytes(b"GIF89a")
        douyin = {"douyin": {"account": "default", "title": "标题", "description": "说明", "tags": [], "visibility": "public", "ai_generated": True}}
        for covers in ({"bilibili": {"cover43": "wide43.png"}}, {"bilibili": {"cover43": "missing.png"}}, {"bilibili": {"cover43": "c43.gif"}},
                       {"bilibili": {"cover43": 7}}, {"douyin": {"cover43": "c43.png"}}):
            with self.subTest(covers=covers):
                self.refused("invalid_cover", covers, douyin)
        self.assertEqual({"cover43"}, set(load_plan(self.manifest(others=douyin))["covers"]["bilibili"]))
        self.refused("invalid_submission_mode", submission_mode="draft_only", topic=None, mention_uids=None)

    def test_a_43_cover_past_the_pixel_limit_the_others_have_is_refused_and_left_as_it_is(self):
        huge = self.root / "huge43.png"
        Image.new("1", (8000, 6000)).save(huge)  # 4:3 and 48 megapixels, yet only kilobytes at one bit a pixel.
        before = huge.read_bytes()
        with self.assertRaises(VpubError) as refused:
            load_plan(self.manifest(covers={"bilibili": {"cover43": "huge43.png"}}))
        self.assertEqual(("invalid_cover", before), (refused.exception.code, huge.read_bytes()))
        self.assertIn("40-megapixel", refused.exception.message)

    def test_the_copy_request_says_where_each_at_nickname_goes(self):
        target = metadata.request(self.manifest())["targets"]["bilibili"]
        self.assertEqual([111, 222], target["mention_uids"])
        self.assertIn("the ordinary half-width @ (not the full-width ＠), their current Bilibili nickname, then an ordinary space (not a full-width one)", target["mention_note"])
        self.assertIn("bili user info UID", target["mention_note"])
        self.assertNotIn("mention_note", metadata.request(self.manifest(mention_uids=None))["targets"]["bilibili"])

    def test_a_full_width_at_is_named_before_anything_asks_bilibili(self):
        with self.assertRaises(VpubError) as refused:
            load_plan(self.manifest(description="谢谢 ＠小白 和 @Example 帮忙"))
        self.assertEqual("invalid_mention", refused.exception.code)
        self.assertIn("A full-width ＠ doesn't count", refused.exception.message)
        self.assertNotIn("小白", refused.exception.message + refused.exception.hint)
        with self.assertRaises(VpubError) as refused:
            load_plan(self.manifest(description="谢谢 @小白 帮忙"))
        self.assertNotIn("full-width", refused.exception.message)

    def test_the_person_is_shown_the_topic_and_who_will_be_notified_before_saying_yes(self):
        from video_publisher.publication_review import check
        both = "On Bilibili, also show its topic, which can enter the video in a contest, and the people in its mention_uids, who will be notified."
        self.assertIn(both, check(load_plan(self.manifest()), None)["next"])
        self.assertIn("On Bilibili, also show its topic, which can enter the video in a contest.", check(load_plan(self.manifest(mention_uids=None)), None)["next"])
        self.assertNotIn("On Bilibili", check(load_plan(self.manifest(topic=None, mention_uids=None)), None)["next"])


class IncludedBiliTests(unittest.TestCase):
    """The dry run CrossCast reads is the included bili-cli's own, run in its own process with its requests replaced, so a renamed
    field shows here. Nothing is contacted: the topic search, the nickname lookup and the zone and tag checks answer from here."""
    STAND_IN = '''import bilibili_api.utils.network as network
from bilibili_api import user
from bilibili_api.exceptions import ResponseCodeException
from bili_cli import config
from bili_cli.commands import upload


class Nowhere:
    _request = None  # bili-cli wraps this at start-up.

    def __init__(self, *args, **kwargs):
        raise AssertionError("This test contacts nothing.")


NAMES = {111: "小白", 222: "Example"}
async def topic(cred, query): return {"id": 1340873, "name": "AI MV 大赛", "mission_id": 4071698, "activity": 1}
async def info(self):
    # Bilibili answers a UID with no account with an error code (-404 in its API notes), which bilibili-api raises.
    if self.get_uid() not in NAMES: raise ResponseCodeException(-404, "啥都木有")
    return {"mid": self.get_uid(), "name": NAMES[self.get_uid()]}
async def zone(cred, title): return {"tid": 21, "tid_name": "a / b", "zone": 1011, "zone_name": "c"}
async def no_rejects(cred, tags): return []
network.Api, upload.web_client = Nowhere, Nowhere
config.require_credential = lambda: type("Cred", (), {"bili_jct": "x"})()
user.User.get_user_info = info
upload.find_topic, upload._predict_zone, upload._check_tags = topic, zone, no_rejects
from bili_cli.main import main
main()
'''

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(root / "vpub"), "BILI_CLI_HOME": str(root / "bili")})
        env.start()
        self.addCleanup(env.stop)
        self.video, self.cover43 = root / "v.mp4", root / "c43.png"
        self.video.write_bytes(bytes(64))
        Image.new("RGB", (1200, 900)).save(self.cover43)
        real = adapters.bili
        target = patch.object(adapters, "bili", lambda args, timeout=210, program=None: real(args, timeout=timeout, program=self.STAND_IN))
        target.start()
        self.addCleanup(target.stop)

    def check(self, **options):
        settings = {"title": "标题", "description": WORDS, "ai_generated": True, "tags": ["音乐"], "visibility": "public", **options}
        return adapters.check_bilibili_upload(adapters.bilibili_upload({"video": str(self.video), "cover43": str(self.cover43)}, settings), settings)

    def test_the_exact_topic_and_written_mentions_pass(self):
        answer = self.check(topic="AI MV 大赛", mention_uids=[111, 222])
        self.assertEqual((4071698, WORDS, str(self.cover43)), (answer["params"]["topic"]["mission_id"], answer["meta"]["desc"], answer["params"]["cover43"]))

    def test_what_would_change_is_refused(self):
        for options, code in (({"topic": "AI MV大赛"}, "topic_name_differs"), ({"description": "谢谢小白", "mention_uids": [111]}, "mention_not_written")):
            with self.subTest(code=code), self.assertRaises(NothingSent) as refused:
                self.check(**options)
            self.assertEqual(code, refused.exception.code)

    def test_a_uid_with_no_account_comes_back_as_the_code_the_skill_says(self):
        # bili-cli's own words for a person it can't find come only from an answer without a name, which this never is.
        with self.assertRaises(NothingSent) as refused:
            self.check(mention_uids=[111, 333])
        details = (Path(__file__).resolve().parents[1] / "skills/publishops-crosscast/references/details.md").read_text(encoding="utf-8")
        said = re.search(r"for a UID with no account, bili-cli's own code, such as `(\w+)`", details)
        self.assertEqual(("not_found", "not_found"), (refused.exception.code, said and said[1]))
        self.assertIn("Check each UID in mention_uids", refused.exception.hint)
        self.assertNotIn("333", refused.exception.message + refused.exception.hint)

    def test_its_words_for_what_it_cannot_find_are_the_ones_crosscast_puts_another_way(self):
        from bili_cli.commands import comment, owner, upload

        class Empty:
            async def __aenter__(self): return self
            async def __aexit__(self, *problem): return False
            async def get(self, *args, **kwargs): return self
            def json(self): return {"data": {"result": {"topics": []}}}

        async def nobody(mid):
            return {}
        with patch.object(upload, "web_client", lambda *args, **kwargs: Empty()), patch.object(owner, "resolve_user", nobody):
            for search, code in ((lambda: upload.find_topic(None, "没有这个话题"), "topic_not_found"), (lambda: comment._mentions(["111"]), "mention_not_found")):
                with self.subTest(code=code), self.assertRaises(BiliError) as refused:
                    asyncio.run(search())
                self.assertEqual(code, adapters.dry_run_refused(VpubError(refused.exception.code, refused.exception.message)).code)


if __name__ == "__main__":
    unittest.main()
