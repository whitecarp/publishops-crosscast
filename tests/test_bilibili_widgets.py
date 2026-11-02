"""Bilibili's widgets through a stand-in for bili-cli that records what would have gone out. Nothing here contacts Bilibili, and this route hasn't run on a real account."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, edits_bilibili, overview, widgets_bilibili as widgets
from video_publisher.core import VpubError, atomic_json, part_left_undone

WORK = "BV1xx411c7mD"
OTHER = "BV1yy4y1k7yy"
LINK = {"type": "link", "time": 10, "target": OTHER, "text": "下一集"}
FOLLOW = {"type": "follow", "time": 20}
VOTE = {"type": "vote", "time": 30, "question": "选哪个", "options": ["甲", "乙"]}
RATING = {"type": "rating", "time": 40, "title": "打个分"}
ALL_FOUR = [LINK, FOLLOW, VOTE, RATING]


class FakeBili:
    """Lists what it's been sent, or answers as told."""

    def __init__(self):
        self.items, self.sent, self.read_errors, self.write_errors, self.loses_writes = [], [], [], {}, False

    def __call__(self, args, timeout=210, **options):
        if args[:3] == ["danmaku", "interact", "list"]:
            if self.read_errors:
                raise self.read_errors.pop(0)
            return {"items": list(self.items)}
        if args[:3] == ["danmaku", "interact", "remove"]:
            self.sent.append(args)
            self.items = [item for item in self.items if item["id"] != args[4]]
            return {"removed": True}
        if args[:2] == ["danmaku", "interact"]:
            if "--dry-run" in args:
                return {"dry_run": True, "action": args[2]}
            self.sent.append(args)
            error = self.write_errors.pop(args[2], None)
            if error:
                raise error
            if not self.loses_writes:
                self.items.append(self.listed_as(args))
            return {"id": "1"}
        if args[:2] == ["upload", "status"]:
            return {"bvid": args[2]}
        return {}

    def listed_as(self, args):
        kind, moment = args[2], float(args[args.index("--time") + 1])
        item = {"id": str(len(self.items) + 100), "kind": kind, "time": moment, "text": None}
        if kind == "link":
            item.update(target={"bvid": args[4]}, text=args[args.index("--text") + 1])
        if kind == "follow":
            item["style"] = args[args.index("--style") + 1]
        if kind == "vote":
            item["text"] = args[args.index("--question") + 1]
        return item


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for started in (patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")}),):
            started.start()
            self.addCleanup(started.stop)
        self.bili = FakeBili()
        stand_in = patch.object(adapters, "bili", self.bili)
        stand_in.start()
        self.addCleanup(stand_in.stop)
        sleeping = patch.object(widgets.time, "sleep")
        self.sleep = sleeping.start()
        self.addCleanup(sleeping.stop)
        self.marks = []

    def kinds_sent(self):
        return [args[2] for args in self.bili.sent]

    def checkpoint(self, value):
        self.marks.append(json.loads(json.dumps(value)))

    def receipt(self, *chosen):
        return {"id": WORK, "pending_widgets": widgets.validate(list(chosen or ALL_FOUR)), "widgets_state": "PENDING"}

    def refused(self, *chosen, length=None):
        with self.assertRaises(VpubError) as caught:
            widgets.validate(list(chosen), length)
        self.assertEqual("invalid_widget", caught.exception.code)
        return caught.exception.message


class PlanTests(Base):
    def test_all_four_kinds_get_their_defaults(self):
        found = widgets.validate(ALL_FOUR)
        self.assertEqual(["link", "follow", "vote", "rating"], [widget["type"] for widget in found])
        self.assertEqual(("both", 300, 150, 5, 1), tuple(found[1][name] for name in ("style", "x", "y", "duration", "page")))
        self.assertEqual((False, "其他"), (found[2]["allow_custom"], found[2]["custom_label"]))
        self.assertEqual("star", found[3]["skin"])

    def test_a_reserve_widget_is_refused_and_says_why(self):
        self.assertIn("notifies everyone", self.refused({"type": "reserve", "time": 1, "title": "下期更精彩敬请期待"}))

    def test_what_bili_cli_would_refuse_fails_before_anything_goes_up(self):
        for widget in ({"type": "gift", "time": 1}, {"time": 1}, {**RATING, "time": -1}, {**RATING, "time": True}, {**RATING, "time": "1"}, {**RATING, "page": 0}, {**RATING, "colour": "red"},
                       {**LINK, "target": "not-a-bv"}, {**LINK, "text": ""}, {**LINK, "text": "two\nlines"}, {**FOLLOW, "style": "wave"}, {**FOLLOW, "x": 1}, {**FOLLOW, "y": 999}, {**FOLLOW, "duration": 1},
                       {**VOTE, "question": "这个问题太长了超过了十二个字"}, {**VOTE, "options": ["甲"]}, {**VOTE, "options": ["甲", "甲"]}, {**VOTE, "options": ["甲", "乙;"]}, {**VOTE, "options": ["这个选项太长了"]},
                       {**VOTE, "options": ["甲", "乙", "丙", "丁"], "allow_custom": True}, {**VOTE, "allow_custom": "yes"}, {**RATING, "title": "这个标题太长了超过了十二个字"}, {**RATING, "skin": "fish"}):
            with self.subTest(widget=widget):
                self.refused(widget)

    def test_two_at_one_moment_or_two_votes_on_a_video_are_refused(self):
        self.assertIn("one moment", self.refused({**RATING, "time": 10}, LINK))
        self.assertIn("one vote", self.refused(VOTE, {**VOTE, "time": 50}))
        self.assertEqual(2, len(widgets.validate([VOTE, {**VOTE, "time": 50, "page": 2}])))

    def test_a_time_too_near_the_end_is_refused_when_the_length_is_known(self):
        self.assertIn("before the video ends", self.refused({**RATING, "time": 56}, length=60))
        self.assertEqual(1, len(widgets.validate([{**RATING, "time": 55}], 60)))

    def manifest(self, **options):
        video = self.root / "film.mp4"
        video.write_bytes(b"synthetic")
        value = {"schema_version": 1, "video": str(video), "title": "T", "description": "D", "tags": ["a"],
                 "platforms": {"bilibili": {"method": "cli", "visibility": "public", "ai_generated": True, **options}}}
        path = self.root / "plan.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_a_plan_keeps_them_and_its_confirmation_covers_them(self):
        from video_publisher.core import load_plan
        plain = load_plan(self.manifest())
        chosen = load_plan(self.manifest(widgets=[RATING]))
        self.assertEqual("star", chosen["platforms"]["bilibili"]["widgets"][0]["skin"])
        self.assertNotEqual(plain["confirmation"], chosen["confirmation"])
        self.assertNotEqual(chosen["confirmation"], load_plan(self.manifest(widgets=[{**RATING, "time": 41}]))["confirmation"])

    def test_a_draft_or_another_platform_takes_none(self):
        from video_publisher.core import load_plan
        with self.assertRaises(VpubError) as caught:
            load_plan(self.manifest(widgets=[RATING], submission_mode="draft_only"))
        self.assertEqual("invalid_submission_mode", caught.exception.code)
        path = self.manifest()
        value = json.loads(path.read_text(encoding="utf-8"))
        value["platforms"] = {"douyin": {"method": "browser", "visibility": "public", "ai_generated": True, "widgets": [RATING]}}
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(VpubError) as caught:
            load_plan(path)
        self.assertEqual("invalid_manifest", caught.exception.code)


class FinishTests(Base):
    def test_before_a_video_can_be_read_nothing_goes_and_it_says_to_come_back(self):
        """From a field read: 62003 on three approved videos waiting for release, and two reads that failed with no reason given."""
        self.bili.read_errors = [VpubError("api_error", "稿件已审核通过，等待发布中")]
        result = widgets.finish(self.receipt(), self.checkpoint)
        self.assertEqual("WAITING_FOR_PUBLIC", result["widgets_state"])
        self.assertIn("public", result["widgets_next"])
        self.assertEqual([], self.bili.sent)
        self.assertTrue(part_left_undone(result))

    def test_all_four_go_once_spaced_and_read_back(self):
        result = widgets.finish(self.receipt(), self.checkpoint)
        self.assertEqual("VERIFIED", result["widgets_state"])
        self.assertEqual(["link", "follow", "vote", "rating"], self.kinds_sent())
        self.assertEqual(3, self.sleep.call_count)
        self.sleep.assert_called_with(widgets.GAP_SECONDS)
        self.assertEqual(4, len(self.marks))
        self.assertFalse(part_left_undone(result))
        self.assertEqual(result, widgets.finish(result, self.checkpoint))
        self.assertEqual(4, len(self.bili.sent))

    def test_the_attempt_is_on_record_before_it_goes(self):
        def looks(args, timeout=210):
            if args[2] == "rating" and args[:2] == ["danmaku", "interact"]:
                self.assertEqual("SUBMITTING", self.marks[-1]["widgets"][0]["state"])
            return original(args, timeout)
        original = self.bili
        with patch.object(adapters, "bili", looks):
            widgets.finish(self.receipt(RATING), self.checkpoint)

    def test_one_already_on_the_video_is_not_sent_again(self):
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0, "text": None}]
        result = widgets.finish(self.receipt(), self.checkpoint)
        self.assertEqual("VERIFIED", result["widgets_state"])
        self.assertEqual(["link", "follow", "vote"], self.kinds_sent())
        self.assertEqual("9", result["widgets"][3]["id"])

    def test_a_different_one_at_that_time_is_not_taken_for_this_one(self):
        self.bili.items = [{"id": "9", "kind": "follow", "time": 20.0, "style": "triple"}, {"id": "8", "kind": "link", "time": 10.0, "target": {"bvid": "BV1aa411c7mD"}}]
        result = widgets.finish(self.receipt(LINK, FOLLOW), self.checkpoint)
        self.assertEqual(["link", "follow"], self.kinds_sent())
        self.assertEqual("VERIFIED", result["widgets_state"])

    def test_a_video_that_has_another_vote_gets_no_second_one(self):
        self.bili.items = [{"id": "9", "kind": "vote", "time": 5.0, "text": "别的问题"}]
        result = widgets.finish(self.receipt(VOTE), self.checkpoint)
        self.assertEqual(("PARTIAL", "CONFLICT"), (result["widgets_state"], result["widgets"][0]["state"]))
        self.assertEqual([], self.bili.sent)

    def test_a_clash_with_another_time_is_kept_and_not_sent_again_and_the_others_go_on(self):
        """79046, which bili-cli reports as a bad argument. The same words would get the same answer."""
        self.bili.write_errors["follow"] = VpubError("invalid_argument", "互动组件存在时间冲突")
        result = widgets.finish(self.receipt(), self.checkpoint)
        self.assertEqual(("REFUSED", "互动组件存在时间冲突"), (result["widgets"][1]["state"], result["widgets"][1]["reason"]))
        self.assertEqual(["link", "follow", "vote", "rating"], self.kinds_sent())
        again = widgets.finish(result, self.checkpoint)
        self.assertEqual(4, len(self.bili.sent))
        self.assertEqual("PARTIAL", again["widgets_state"])

    def test_bilibili_saying_slow_down_stops_the_rest_and_a_later_run_finishes(self):
        """36703 within a minute, by bili-cli's own note."""
        self.bili.write_errors["follow"] = VpubError("rate_limited", "发送弹幕的频率过快")
        result = widgets.finish(self.receipt(), self.checkpoint)
        self.assertEqual(["link", "follow"], self.kinds_sent())
        self.assertEqual(["VERIFIED", "NOT_APPLIED", "PENDING", "PENDING"], [row["state"] for row in result["widgets"]])
        done = widgets.finish(result, self.checkpoint)
        self.assertEqual("VERIFIED", done["widgets_state"])
        self.assertEqual(["link", "follow", "follow", "vote", "rating"], self.kinds_sent())

    def test_bili_clis_own_spacing_is_not_called_a_failure(self):
        self.bili.write_errors["rating"] = VpubError("write_spacing", "interact 操作间隔至少 30 秒。")
        result = widgets.finish(self.receipt(RATING), self.checkpoint)
        self.assertEqual("PENDING", result["widgets"][0]["state"])

    def test_an_unclear_ending_is_never_sent_again_and_a_later_read_settles_it(self):
        self.bili.write_errors["rating"] = VpubError("backend_timeout", "bili-cli did not finish in time.")
        result = widgets.finish(self.receipt(RATING), self.checkpoint)
        self.assertEqual("UNVERIFIED", result["widgets"][0]["state"])
        result = widgets.finish(result, self.checkpoint)
        self.assertEqual(1, len(self.bili.sent))
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0}]
        self.assertEqual("VERIFIED", widgets.finish(result, self.checkpoint)["widgets_state"])
        self.assertEqual(1, len(self.bili.sent))

    def test_a_second_verify_that_loaded_the_receipt_earlier_sends_nothing_more(self):
        """From a review: two verifies load the same receipt, the first's send ends unclear with the list still empty, and the second then finds nothing there."""
        self.bili.loses_writes = True
        self.bili.write_errors["rating"] = VpubError("backend_timeout", "bili-cli did not finish in time.")
        loaded_by_both = self.receipt(RATING)
        self.assertEqual("UNVERIFIED", widgets.finish(loaded_by_both, self.checkpoint)["widgets"][0]["state"])
        second = widgets.finish(loaded_by_both, self.checkpoint)
        self.assertEqual(1, len(self.bili.sent))
        self.assertEqual(("UNVERIFIED", "PARTIAL"), (second["widgets"][0]["state"], second["widgets_state"]))

    def test_one_that_finds_another_mid_send_reports_how_the_record_stands(self):
        loaded = self.receipt(RATING)
        atomic_json(widgets.record_path(WORK), {"work": WORK, "rows": [{"widget": widgets.validate([RATING])[0], "state": "SUBMITTING"}]})
        with patch.object(widgets, "receipt_turn", side_effect=VpubError("attempt_busy", "Another step is moving this attempt on, so nothing was sent.")):
            result = widgets.finish(loaded, self.checkpoint)
        self.assertEqual([], self.bili.sent)
        self.assertEqual(("SUBMITTING", "SUBMITTING"), (result["widgets"][0]["state"], result["widgets_state"]))
        self.assertIn("Another command", result["widgets_next"])

    def test_a_plans_widgets_and_vpub_widgets_share_one_record(self):
        self.bili.loses_writes = True
        self.bili.write_errors["rating"] = VpubError("backend_timeout", "bili-cli did not finish in time.")
        widgets.finish(self.receipt(RATING), self.checkpoint)
        self.assertEqual("UNVERIFIED", widgets.verify(WORK)["widgets"][0]["state"])
        self.assertEqual(1, len(self.bili.sent))

    def test_an_attempt_a_crash_left_on_record_counts_as_maybe_gone(self):
        receipt = {**self.receipt(RATING), "widgets": [{"widget": widgets.validate([RATING])[0], "state": "SUBMITTING"}]}
        result = widgets.finish(receipt, self.checkpoint)
        self.assertEqual("UNVERIFIED", result["widgets"][0]["state"])
        self.assertEqual([], self.bili.sent)

    def test_a_failed_read_after_sending_leaves_it_unverified_and_never_sends_again(self):
        self.bili.loses_writes = True
        self.bili.read_errors = []
        original = self.bili.__call__
        reads = []

        def reading(args, timeout=210):
            if args[:3] == ["danmaku", "interact", "list"]:
                reads.append(1)
                if len(reads) == 2:
                    raise VpubError("api_error", "解析响应数据错误")
            return original(args, timeout)
        with patch.object(adapters, "bili", reading):
            result = widgets.finish(self.receipt(RATING), self.checkpoint)
            self.assertEqual(("UNVERIFIED", "api_error"), (result["widgets"][0]["state"], result["widgets"][0]["error"]))
            widgets.finish(result, self.checkpoint)
        self.assertEqual(1, len(self.bili.sent))

    def test_one_that_goes_missing_is_said_and_not_sent_again(self):
        result = widgets.finish(self.receipt(RATING), self.checkpoint)
        self.assertEqual("VERIFIED", result["widgets_state"])
        result = {**result, "widgets_state": "PARTIAL"}
        self.bili.items = []
        result = widgets.finish(result, self.checkpoint)
        self.assertEqual("MISSING", result["widgets"][0]["state"])
        self.assertEqual(1, len(self.bili.sent))

    def test_with_nowhere_to_record_an_attempt_nothing_goes(self):
        receipt = self.receipt(RATING)
        self.assertEqual(receipt, widgets.finish(receipt))
        self.assertEqual([], self.bili.sent)


class RouteTests(Base):
    def options(self, **more):
        return {"title": "T", "description": "D", "tags": ["a"], "ai_generated": True, "visibility": "public", "account": "default", **more}

    def test_publishing_records_them_and_reports_what_is_on_the_video(self):
        with patch.object(adapters, "bili", self.answering({"bvid": WORK})):
            result = adapters.publish_bilibili({"video": "v.mp4"}, self.options(widgets=widgets.validate([RATING])), self.checkpoint)
        self.assertEqual("VERIFIED", result["widgets_state"])
        self.assertEqual("PENDING", self.marks[0]["widgets_state"])
        self.assertEqual({"state": "READ", "count": 1}, {name: result["widgets_seen"][name] for name in ("state", "count")})

    def answering(self, upload):
        def call(args, timeout=210, **options):
            return upload if args[:2] == ["upload", "video"] else self.bili(args, timeout)
        return call

    def test_a_plan_with_none_sends_none_and_still_reports_the_video(self):
        result = adapters.verify("bilibili", "default", {"id": WORK, "state": "SUBMITTED"}, self.checkpoint)
        self.assertEqual([], self.bili.sent)
        self.assertEqual(("READ", 0), (result["widgets_seen"]["state"], result["widgets_seen"]["count"]))
        self.assertNotIn("widgets_state", result)

    def test_a_verify_or_an_edit_says_which_widget_a_video_has_lost(self):
        """An edit sends a video back for review, which is why the owner wants this looked at."""
        done = widgets.finish(self.receipt(RATING), self.checkpoint)
        self.bili.items = []
        self.assertEqual("rating", adapters.verify("bilibili", "default", done, self.checkpoint)["widgets_seen"]["missing"][0]["type"])
        with patch.object(edits_bilibili, "change", return_value={"platform_writes": 1}):
            self.assertEqual("rating", edits_bilibili.run("default", "1", done, {}, None, False)["widgets_seen"]["missing"][0]["type"])

    def test_two_commands_at_once_take_turns(self):
        """vpub verify holds no other lock, and a video should get one of each."""
        from video_publisher.core import receipt_turn
        path = widgets.record_path(WORK)
        path.parent.mkdir(parents=True, exist_ok=True)
        with receipt_turn(path), patch("video_publisher.core.time.monotonic", side_effect=iter(range(0, 100000, 100))):
            result = widgets.finish(self.receipt(RATING), self.checkpoint)
        self.assertIn("Another command", result["widgets_next"])
        self.assertEqual([], self.bili.sent)

    def test_a_failed_read_is_reported_as_unknown_not_as_none(self):
        self.bili.read_errors = [VpubError("api_error", "解析响应数据错误")]
        result = adapters.verify("bilibili", "default", {"id": WORK, "state": "SUBMITTED"}, self.checkpoint)
        self.assertEqual({"state": "UNKNOWN", "error": "api_error"}, result["widgets_seen"])

    def test_verify_finishes_them_once_the_video_is_public(self):
        self.bili.read_errors = [VpubError("not_found", "稿件审核中")]
        first = adapters.verify("bilibili", "default", self.receipt(RATING), self.checkpoint)
        self.assertEqual("WAITING_FOR_PUBLIC", first["widgets_state"])
        self.assertEqual([], self.bili.sent)
        second = adapters.verify("bilibili", "default", first, self.checkpoint)
        self.assertEqual("VERIFIED", second["widgets_state"])
        self.assertEqual(["rating"], self.kinds_sent())

    def test_an_edit_reports_whats_on_the_video_and_a_no_change_read_does_not_bother(self):
        """The owner asked for this after every update."""
        with patch.object(edits_bilibili, "change", return_value={"state": "EDIT_VERIFIED", "platform_writes": 1}):
            self.assertEqual("READ", edits_bilibili.run("default", "1", {"id": WORK}, {}, None, False)["widgets_seen"]["state"])
        with patch.object(edits_bilibili, "change", return_value={"state": "EDIT_VERIFIED", "platform_writes": 0}):
            self.assertNotIn("widgets_seen", edits_bilibili.run("default", "1", {"id": WORK}, {}, None, False))

    def test_status_says_where_they_stand(self):
        from video_publisher.core import Ledger
        ledger = Ledger()
        data = {"options": {"method": "cli"}, "title": "T", "plan_hash": "p", "receipt": {"id": WORK, "widgets_state": "WAITING_FOR_PUBLIC"}}
        key = ledger.claim("bilibili", "default", "sha", data)
        ledger.save(key, "SUBMISSION_VERIFIED", data)
        ledger.close()
        attention = {item["key"]: item for item in overview.status()["publications"]["needs_attention"]}
        self.assertEqual("WAITING_FOR_PUBLIC", attention[key]["widgets_state"])
        self.assertEqual(f"vpub verify {key}", attention[key]["next_command"])


class CommandTests(Base):
    def run_cli(self, *arguments):
        return cli.execute(cli.parser().parse_args(["widgets", *arguments]))

    def file(self, *chosen):
        path = self.root / "widgets.json"
        path.write_text(json.dumps(list(chosen or ALL_FOUR), ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_list_only_reads(self):
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0}]
        self.assertEqual(("9", 0), (self.run_cli("list", WORK)["items"][0]["id"], self.run_cli("list", WORK)["platform_writes"]))
        self.assertEqual([], self.bili.sent)

    def test_only_a_bv_number_is_taken(self):
        with self.assertRaises(VpubError) as caught:
            self.run_cli("list", "https://example.com/x")
        self.assertEqual("invalid_id", caught.exception.code)

    def test_a_dry_run_sends_nothing_and_checks_what_it_would(self):
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0}]
        result = self.run_cli("add", WORK, self.file(), "--dry-run")
        self.assertEqual(("DRY_RUN", 1, 3, 0), (result["state"], result["already_there"], len(result["would_send"]), result["platform_writes"]))
        self.assertEqual([], self.bili.sent)

    def test_add_sends_whats_missing_and_a_second_run_sends_nothing(self):
        first = self.run_cli("add", WORK, self.file())
        self.assertEqual(("VERIFIED", 4), (first["state"], first["platform_writes"]))
        second = self.run_cli("add", WORK, self.file())
        self.assertEqual(("VERIFIED", 0), (second["state"], second["platform_writes"]))
        self.assertEqual(4, len(self.bili.sent))

    def test_a_video_that_cannot_be_read_gets_nothing(self):
        self.bili.read_errors = [VpubError("api_error", "稿件已审核通过，等待发布中")]
        self.assertEqual("WAITING_FOR_PUBLIC", self.run_cli("add", WORK, self.file(RATING))["state"])
        self.assertEqual([], self.bili.sent)

    def test_again_is_what_it_takes_to_send_a_maybe_gone_one_and_only_if_it_isnt_there(self):
        self.bili.write_errors["rating"] = VpubError("backend_timeout", "bili-cli did not finish in time.")
        self.assertEqual("PARTIAL", self.run_cli("add", WORK, self.file(RATING))["state"])
        self.assertEqual(1, len(self.bili.sent))
        self.assertEqual("PARTIAL", self.run_cli("add", WORK, self.file(RATING))["state"])
        self.assertEqual(1, len(self.bili.sent))
        self.assertEqual("VERIFIED", self.run_cli("add", WORK, self.file(RATING), "--again")["state"])
        self.assertEqual(2, len(self.bili.sent))
        self.run_cli("add", WORK, self.file(RATING), "--again")
        self.assertEqual(2, len(self.bili.sent))

    def test_another_files_widgets_stay_on_record(self):
        self.run_cli("add", WORK, self.file(RATING))
        self.run_cli("add", WORK, self.file(LINK))
        self.assertEqual({"rating", "link"}, {row["widget"]["type"] for row in self.run_cli("verify", WORK)["widgets"]})

    def test_verify_only_reads_and_says_what_went_missing(self):
        self.run_cli("add", WORK, self.file(RATING))
        self.bili.items = []
        result = self.run_cli("verify", WORK)
        self.assertEqual(("PARTIAL", "MISSING", 0), (result["state"], result["widgets"][0]["state"], result["platform_writes"]))
        self.assertEqual(1, len(self.bili.sent))

    def test_verify_with_nothing_on_record_just_lists(self):
        self.assertEqual("NOTHING_ON_RECORD", self.run_cli("verify", WORK)["state"])

    def test_remove_reads_back_and_a_dry_run_or_a_wrong_id_removes_nothing(self):
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0}]
        self.assertEqual("DRY_RUN", self.run_cli("remove", WORK, "9", "--dry-run")["state"])
        with self.assertRaises(VpubError) as caught:
            self.run_cli("remove", WORK, "7")
        self.assertEqual("not_found", caught.exception.code)
        self.assertEqual([], self.bili.sent)
        self.assertEqual("REMOVED", self.run_cli("remove", WORK, "9")["state"])
        self.assertEqual(1, len(self.bili.sent))

    def test_a_reserve_widget_is_never_removed_here(self):
        """Removing one notifies everyone who booked it."""
        self.bili.items = [{"id": "9", "kind": "reserve", "time": 40.0}]
        with self.assertRaises(VpubError) as caught:
            self.run_cli("remove", WORK, "9")
        self.assertEqual("reserve_not_handled", caught.exception.code)
        self.assertEqual([], self.bili.sent)

    def test_a_remove_whose_ending_is_unclear_says_so(self):
        self.bili.items = [{"id": "9", "kind": "rating", "time": 40.0}]

        def lost(args, timeout=210):
            if args[:3] == ["danmaku", "interact", "remove"]:
                raise VpubError("backend_timeout", "bili-cli did not finish in time.")
            return self.bili(args, timeout)
        with patch.object(adapters, "bili", lost):
            self.assertEqual("REMOVE_UNVERIFIED", self.run_cli("remove", WORK, "9")["state"])

    def test_these_unfinished_states_exit_as_unfinished(self):
        self.assertFalse(cli.finished({"state": "WAITING_FOR_PUBLIC"}))
        self.assertFalse(cli.finished({"state": "PARTIAL"}))
        self.assertFalse(cli.finished({"state": "REMOVE_UNVERIFIED"}))
        self.assertTrue(cli.finished({"state": "VERIFIED"}))


if __name__ == "__main__":
    unittest.main()
