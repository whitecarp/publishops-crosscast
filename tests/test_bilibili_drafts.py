"""Bilibili drafts: the requests in a separate process with bili-cli's request maker replaced by a recorder, and the receipts around them.

The request's address and fields come from what Bilibili's own upload page sent in a field check. The answers' shapes have not
been seen yet, so anything unexpected stays unconfirmed. Nothing here contacts Bilibili.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, drafts_bilibili as drafts
from video_publisher.core import Ledger, VpubError, atomic_json, load_plan

TITLE = "CC test draft"


class FakeBili:
    def __init__(self):
        self.drafts = []
        self.calls = []
        self.keep = True
        self.fail = None
        self.shape = None
        self.cid = 9

    def __call__(self, args, timeout=210, program=None):
        self.calls.append(list(args))
        if program != drafts.HELPER:
            raise AssertionError(f"unexpected bili call: {args}")
        if args[0] == "list":
            return {"drafts": self.shape if self.shape is not None else list(self.drafts)}
        if self.fail:
            raise VpubError(self.fail, "No.")
        if self.keep:
            self.drafts.append({"id": 500 + len(self.drafts), "cid": self.cid, "title": json.loads(args[1])["title"]})
        return {"cid": 9}

    def sent(self):
        return [json.loads(call[1]) for call in self.calls if call[0] == "add"]


class Saving(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.bili = FakeBili()
        target = patch.object(adapters, "bili", self.bili)
        target.start()
        self.addCleanup(target.stop)
        self.marks = []

    def publish(self, **options):
        settings = {"title": TITLE, "description": "D", "ai_generated": True, "tags": ["a"], "visibility": "private", "account": "default", "submission_mode": "draft_only", **options}
        return adapters.publish_bilibili({"video": "v.mp4", "cover": "c.jpg"}, settings, lambda value: self.marks.append(dict(value)))

    def test_it_saves_once_and_reads_the_draft_back(self):
        result = self.publish()
        self.assertEqual(("DRAFT_SAVED", "draft", TITLE, 500, 9), (result["state"], result["phase"], result["draft_title"], result["draft_id"], result["uploaded_cid"]))
        self.assertEqual(1, len(self.bili.sent()))
        self.assertEqual({"video": "v.mp4", "cover": "c.jpg", "title": TITLE, "description": "D", "tags": ["a"], "tid": None, "private": True, "ai_generated": True}, self.bili.sent()[0])

    def test_the_attempt_is_recorded_before_anything_is_sent(self):
        seen = []

        def mark(value):
            seen.append((value["state"], len(self.bili.sent())))
        adapters.publish_bilibili({"video": "v.mp4"}, {"title": TITLE, "description": "D", "ai_generated": False, "tags": ["a"], "visibility": "public", "submission_mode": "draft_only"}, mark)
        self.assertEqual(("UNKNOWN", 0), seen[0])

    def test_an_older_draft_with_the_same_title_and_video_does_not_count_as_this_one(self):
        self.bili.drafts = [{"id": 1, "cid": 9, "title": TITLE}]
        self.bili.keep = False
        self.assertEqual("SUBMITTED_UNVERIFIED", self.publish()["state"])
        self.bili.drafts.append({"id": 2, "cid": 9, "title": TITLE})
        self.assertEqual(("DRAFT_SAVED", 2), tuple(adapters.verify("bilibili", "default", self.marks[-1])[key] for key in ("state", "draft_id")))

    def test_a_new_draft_with_another_video_is_only_a_candidate(self):
        self.bili.cid = 8
        result = self.publish()
        self.assertEqual(("SUBMITTED_UNVERIFIED", 500), (result["state"], result["candidate_id"]))

    def test_with_no_upload_number_a_new_draft_is_only_a_candidate(self):
        receipt = {"state": "UNKNOWN", "draft_title": TITLE, "drafts_before": []}
        self.bili.drafts = [{"id": 7, "cid": 9, "title": TITLE}]
        result = adapters.verify("bilibili", "default", receipt)
        self.assertEqual(("SUBMITTED_UNVERIFIED", 7), (result["state"], result["candidate_id"]))

    def test_a_list_in_a_shape_it_does_not_know_stays_unconfirmed(self):
        self.bili.shape = {"something": "else"}
        result = self.publish()
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertIn("do not save it again", result["note"])

    def test_a_refusal_goes_up_as_it_is_and_the_receipt_says_nothing_was_confirmed(self):
        self.bili.fail = "invalid_argument"
        with self.assertRaises(VpubError) as caught:
            self.publish()
        self.assertEqual("invalid_argument", caught.exception.code)
        self.assertEqual("UNKNOWN", self.marks[-1]["state"])

    def test_verifying_only_reads(self):
        self.publish()
        self.bili.calls.clear()
        adapters.verify("bilibili", "default", self.marks[-1])
        self.assertEqual([["list"]], self.bili.calls)

    def test_publishing_normally_is_untouched(self):
        self.assertFalse(self.bili.calls)
        with patch.object(adapters, "bili", return_value={"bvid": "BV1xx411c7mD"}) as sent, patch.object(adapters, "verify", return_value={"state": "SUBMISSION_VERIFIED"}):
            adapters.publish_bilibili({"video": "v.mp4"}, {"title": TITLE, "description": "D", "ai_generated": False, "tags": ["a"], "visibility": "public", "account": "default"}, self.marks.append)
        self.assertEqual(["upload", "video"], sent.call_args.args[0][:2])

    def test_the_verify_command_finds_a_draft_receipt(self):
        ledger = Ledger()
        key = ledger.claim("bilibili", "default", "a" * 64, {})
        ledger.save(key, "SUBMITTED_UNVERIFIED", {"receipt": {"state": "SUBMITTED_UNVERIFIED", "draft_title": TITLE, "drafts_before": [], "uploaded_cid": 9}})
        ledger.close()
        self.bili.drafts = [{"id": 1, "cid": 9, "title": TITLE}]
        self.assertEqual("DRAFT_SAVED", cli.run_command(cli.parser().parse_args(["verify", key]))["state"])


class PlanTests(unittest.TestCase):
    def setUp(self):
        tool = patch("video_publisher.rehearsal.shutil.which", return_value="ffmpeg.exe")
        tool.start()
        self.addCleanup(tool.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        (self.root / "clip.mp4").write_bytes(b"synthetic")
        self.value = cli.template(self.root / "clip.mp4", "zh-Hans")
        self.value.update(tags=["tag"])
        self.path = self.root / "publish.json"

    def plan(self, **options):
        self.value["platforms"] = {"bilibili": {"account": "default", "method": "cli", "visibility": "public", "ai_generated": True, "title": "标题", **options}}
        atomic_json(self.path, self.value)
        return load_plan(self.path)["platforms"]["bilibili"]

    def refused(self, **options):
        with self.assertRaises(VpubError) as caught:
            self.plan(**options)
        self.assertEqual("invalid_submission_mode", caught.exception.code)

    def test_a_draft_is_a_choice_in_the_plan(self):
        self.assertEqual("draft_only", self.plan(submission_mode="draft_only")["submission_mode"])

    def test_a_draft_keeps_only_what_a_draft_can_hold(self):
        self.refused(submission_mode="draft_only", season_id=7)
        self.refused(submission_mode="draft_only", schedule="2030-01-01T00:00:00+00:00")
        self.refused(submission_mode="draft_only", method="native")
        self.refused(submission_mode="something")

    def run_plan(self, state, others=None):
        self.plan(submission_mode="draft_only")
        self.value["platforms"].update(others or {})
        atomic_json(self.path, self.value)
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", side_effect=lambda platform, *_: {"state": state if platform == "bilibili" else "SUBMISSION_VERIFIED"}):
            return cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)

    def test_a_saved_draft_finishes_the_plan_without_claiming_anything_was_published(self):
        result = self.run_plan("DRAFT_SAVED")
        self.assertEqual((True, ["bilibili"]), (result["all_drafts_ready"], result["drafts_read_back"]))
        self.assertIsNone(result["all_submissions_verified"])

    def test_a_preview_is_ready_but_nothing_says_it_was_kept(self):
        result = self.run_plan("PREVIEW_READY")
        self.assertEqual((True, []), (result["all_drafts_ready"], result["drafts_read_back"]))

    def test_seeing_an_offer_of_a_draft_does_not_count_as_reading_this_one_back(self):
        self.plan(submission_mode="draft_only")
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "PREVIEW_READY", "draft_offer_seen": True}):
            self.assertEqual([], cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)["drafts_read_back"])

    def test_a_draft_that_did_not_stop_cleanly_has_not_finished(self):
        self.assertIs(False, self.run_plan("SUBMITTED_UNVERIFIED")["all_drafts_ready"])

    def test_running_it_again_finds_the_same_draft_and_saves_nothing_more(self):
        self.run_plan("DRAFT_SAVED")
        result = self.run_plan("DRAFT_SAVED")
        self.assertEqual("SKIPPED_EXISTING", result["results"]["bilibili"]["state"])
        self.assertEqual((True, ["bilibili"]), (result["all_drafts_ready"], result["drafts_read_back"]))

    def publish_after(self, state):
        self.run_plan(state)
        self.plan()
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED"}) as sent:
            return cli.publish_plan(loaded, loaded["confirmation"], reviewed=True), sent

    def test_publishing_the_same_video_after_its_draft_goes_ahead(self):
        """From a field run: a dry run otherwise stopped the real post of the same file."""
        result, sent = self.publish_after("DRAFT_SAVED")
        self.assertEqual("SUBMISSION_VERIFIED", result["results"]["bilibili"]["state"])
        sent.assert_called_once()

    def test_publishing_the_same_video_after_its_preview_goes_ahead(self):
        result, sent = self.publish_after("PREVIEW_READY")
        self.assertEqual("SUBMISSION_VERIFIED", result["results"]["bilibili"]["state"])
        sent.assert_called_once()

    def test_a_preview_whose_final_press_was_tried_still_stops_a_publish(self):
        self.plan(submission_mode="draft_only")
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "PREVIEW_READY", "final_submit_attempted": True}):
            cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)
        self.plan()
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED"}) as sent:
            result = cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)
        self.assertEqual("SKIPPED_EXISTING", result["results"]["bilibili"]["state"])
        sent.assert_not_called()

    def test_a_draft_that_did_not_stop_cleanly_still_stops_a_publish(self):
        result, sent = self.publish_after("SUBMITTED_UNVERIFIED")
        self.assertEqual("SKIPPED_EXISTING", result["results"]["bilibili"]["state"])
        sent.assert_not_called()

    def test_a_published_video_is_not_published_again(self):
        self.plan()
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED"}) as sent:
            cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)
            self.assertEqual("SKIPPED_EXISTING", cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)["results"]["bilibili"]["state"])
        sent.assert_called_once()

    def test_a_plan_with_a_draft_and_a_publication_answers_for_each(self):
        result = self.run_plan("DRAFT_SAVED", {"douyin": {"account": "default", "method": "browser", "visibility": "public", "ai_generated": True, "title": "标题"}})
        self.assertEqual((True, True), (result["all_drafts_ready"], result["all_submissions_verified"]))

    def test_a_plan_with_no_drafts_says_nothing_about_them(self):
        self.plan()
        loaded = load_plan(self.path)
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), patch.object(adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED"}):
            result = cli.publish_plan(loaded, loaded["confirmation"], reviewed=True)
        self.assertIs(True, result["all_submissions_verified"])
        self.assertNotIn("all_drafts_ready", result)

    def test_the_exit_code_follows_the_draft_and_the_publication(self):
        for value, code in (({"all_submissions_verified": None, "all_drafts_ready": True}, 0), ({"all_submissions_verified": None, "all_drafts_ready": False}, 4),
                            ({"all_submissions_verified": False, "all_drafts_ready": True}, 4), ({"all_submissions_verified": True}, 0), ({"all_submissions_verified": False}, 4)):
            with self.subTest(value=value), patch.object(cli, "execute", return_value=value), patch.object(sys, "argv", ["vpub", "status"]), patch.object(sys, "stdout", io.StringIO()), self.assertRaises(SystemExit) as stopped:
                cli.main()
            self.assertEqual(code, stopped.exception.code)


class HelperTests(unittest.TestCase):
    """The requests themselves, in a separate process, with bili-cli's request maker and upload steps replaced by recorders."""
    RECORDER = '''import json, sys
import bilibili_api.utils.network as network
from bili_cli import config, guard
from bili_cli.commands import upload
from bilibili_api import Picture
from bilibili_api import video_uploader as vu


class Call:
    _request = None  # bili-cli wraps this at start-up.

    def __init__(self, url, method, **options):
        self.url, self.method, self.options, self.params, self.sent = url, method, options, {}, {}
    def update_params(self, **values): self.params.update(values); return self
    def update_data(self, **values): self.sent.update(values); return self
    def update_headers(self, **values): return self
    @property
    def result(self):
        async def answer():
            print("SENT " + json.dumps({"url": self.url, "method": self.method, "json": self.options.get("json_body"), "csrf_in_query": "csrf" in self.params,
                                        "body": {k: v for k, v in self.sent.items() if k != "csrf"}}, ensure_ascii=False), file=sys.stderr)
            return [{"title": "One"}] if self.url.endswith("/list") else None
        return answer()


async def zone(cred, title): return ZONE
async def no_rejects(cred, values): return REJECTS
async def nothing(cred, video): print("UPLOADED " + str(video), file=sys.stderr)
async def part(cred, video, line): return {"filename": "f1", "cid": 9}
async def cover(picture, cred): return "http://i0/c.jpg"
ZONE, REJECTS = {"tid": 21, "tid_name": "a", "zone": 1002, "zone_name": "b"}, []
network.Api = Call
config.require_credential = lambda: type("Cred", (), {"bili_jct": "x"})()
guard.check = lambda action: print("GUARD " + action, file=sys.stderr)
guard.record = lambda *args: None
upload._predict_zone, upload._check_tags, upload._preflight, upload._upload_file = zone, no_rejects, nothing, part
upload._extract_cover = lambda video: "auto.jpg"
vu.upload_cover = cover
Picture.from_file = staticmethod(lambda path: path)
'''
    DRAFT = {"video": "v.mp4", "cover": "c.jpg", "title": TITLE, "description": "D", "tags": ["a", "b"], "tid": None, "private": True, "ai_generated": True}

    def run_helper(self, *args, before=""):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, BILI_CLI_HOME=folder, PYTHONIOENCODING="utf-8")
            done = subprocess.run([sys.executable, "-c", self.RECORDER + before + drafts.HELPER, *args], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env)
        sent = [json.loads(line[5:]) for line in done.stderr.splitlines() if line.startswith("SENT ")]
        return json.loads(done.stdout), sent, done.stderr

    def test_saving_uploads_the_clip_then_sends_one_draft(self):
        answer, sent, log = self.run_helper("add", json.dumps(self.DRAFT))
        self.assertTrue(answer["ok"], answer)
        self.assertEqual({"cid": 9}, answer["data"])
        self.assertEqual(1, len(sent))
        self.assertEqual(("https://member.bilibili.com/x/vupre/web/draft/add", "POST", True, True), (sent[0]["url"], sent[0]["method"], sent[0]["json"], sent[0]["csrf_in_query"]))
        body = sent[0]["body"]
        self.assertEqual((TITLE, "D", "a,b", 21, 1002, 1, "http://i0/c.jpg"), (body["title"], body["desc"], body["tag"], body["tid"], body["human_type2"], body["is_only_self"], body["cover"]))
        self.assertEqual(1, body["creation_statement"]["id"])
        self.assertEqual([{"filename": "f1", "cid": 9, "title": TITLE, "desc": ""}], body["videos"])
        self.assertIn("GUARD upload", log)
        self.assertIn("UPLOADED", log)

    def test_listing_reads_only(self):
        answer, sent, log = self.run_helper("list")
        self.assertEqual([{"title": "One"}], answer["data"]["drafts"])
        self.assertEqual(("https://member.bilibili.com/x/vupre/web/draft/list", "GET"), (sent[0]["url"], sent[0]["method"]))
        self.assertNotIn("UPLOADED", log)

    def test_a_tag_bilibili_refuses_stops_before_any_upload(self):
        answer, sent, log = self.run_helper("add", json.dumps(self.DRAFT), before='REJECTS = ["b"]\n')
        self.assertEqual("invalid_argument", answer["error"]["code"])
        self.assertEqual([], sent)
        self.assertNotIn("UPLOADED", log)

    def test_no_zone_and_no_category_stops_before_any_upload(self):
        answer, sent, log = self.run_helper("add", json.dumps(self.DRAFT), before="ZONE = None\n")
        self.assertEqual("invalid_argument", answer["error"]["code"])
        self.assertNotIn("UPLOADED", log)

    def test_crosscast_itself_still_never_loads_the_gpl_library(self):
        code = ("import sys\nfrom video_publisher import drafts_bilibili\n"
                "print([name for name in sys.modules if name.split('.')[0] == 'bilibili_api'])\n")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual("[]", done.stdout.strip(), done.stderr)


if __name__ == "__main__":
    unittest.main()
