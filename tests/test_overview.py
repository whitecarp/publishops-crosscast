"""vpub status, cleanup and doctor. Offline; no platform or browser is contacted."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, overview
from video_publisher.core import Ledger, VpubError, atomic_json, digest, home, load_plan, publication_lock


class OverviewFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        self.env.start()
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"synthetic test fixture, not a playable upload")
        self.manifest = self.root / "publish.json"
        value = cli.template(self.video)
        value["title"] = "Overview fixture"
        value["platforms"]["youtube"]["category_id"] = "1"
        atomic_json(self.manifest, value)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def publish(self, platforms=("bilibili",)):
        """A real publish on the hand-off route, which leaves a snapshot and a ledger row."""
        plan = load_plan(self.manifest, list(platforms))
        return plan, cli.publish_plan(plan, plan["confirmation"], reviewed=True)["results"]

    def finish(self, key, state="AGENT_REPORTED_VERIFIED"):
        ledger = Ledger()
        row = ledger.get(key)
        ledger.save(key, state, {**row["data"], "receipt": {"state": state, "id": "abc"}})
        ledger.close()

    def row(self, state, method="browser", receipt=None, sha="sha", platform="x"):
        ledger = Ledger()
        data = {"options": {"method": method}, "title": "Title", "plan_hash": "plan-" + sha}
        if receipt:
            data["receipt"] = receipt
        key = ledger.claim(platform, "default", sha, data)
        ledger.save(key, state, data)
        ledger.close()
        return key

    def attention(self):
        return {item["key"]: item for item in overview.status()["publications"]["needs_attention"]}


class UnconfirmedActionTests(OverviewFixture):
    """Replies, messages and likes nobody has confirmed were found only by whoever kept the key. Local records only."""
    HEX = "a" * 32
    LONG = "b" * 64

    def receipt(self, folder, name, **fields):
        atomic_json(home() / folder / (name + ".json"), fields)

    def actions(self):
        return overview.status()["actions"]["unconfirmed"]

    def test_each_kind_gets_the_command_that_settles_it(self):
        self.receipt("operations/replies", self.LONG, state="REPLY_UNVERIFIED", platform="x", work_id="1", at="2026-11-01T01:00:00+00:00")
        self.receipt("operations/dm-actions", self.HEX, state="DM_SUBMITTING", request={"platform": "x"}, at="2026-11-01T02:00:00+00:00")
        self.receipt("operations/comment-actions", self.HEX, state="COMMENT_UNVERIFIED", request={"platform": "youtube", "work_id": "2"}, at="2026-11-01T03:00:00+00:00")
        self.receipt("engagement/receipts", self.HEX, state="ACTION_UNVERIFIED", platform="douyin", work_id="3", at="2026-11-01T04:00:00+00:00")
        self.receipt("operations/collections", self.LONG, state="UNVERIFIED", platform="douyin", started_at="2026-11-01T05:00:00+00:00")
        self.receipt("operations/collections", "c" * 64, state="SUBMITTING", platform="youtube", started_at="2026-11-01T06:00:00+00:00")
        self.receipt("operations/playlists", "d" * 64, state="UNVERIFIED", started_at="2026-11-01T07:00:00+00:00")
        rows = {(row["kind"], row["next_command"]) for row in self.actions()}
        self.assertEqual({("reply", "vpub comments verify-reply " + self.LONG), ("message", "vpub dm verify " + self.HEX), ("comment_action", "vpub comments verify-action " + self.HEX),
                          ("like_or_save", "vpub engage verify " + self.HEX), ("collection_change", "vpub collection verify " + self.LONG), ("collection_change", "vpub playlist verify " + "c" * 64),
                          ("playlist_change", "vpub playlist verify " + "d" * 64)}, rows)

    def test_every_command_it_names_is_one_the_tool_takes(self):
        self.test_each_kind_gets_the_command_that_settles_it()
        self.receipt("widgets", "BV1xx411c7mD", rows=[{"state": "UNVERIFIED"}])
        key = self.row("SUBMISSION_VERIFIED", platform="x", receipt={"id": "9"})
        self.receipt("edits", "e" * 64, state="EDIT_SUBMITTED_UNVERIFIED", key=key, id="9")
        for row in self.actions():
            with self.subTest(command=row["next_command"]):
                cli.parser().parse_args(row["next_command"].split()[1:])

    def test_the_states_are_the_ones_each_module_treats_as_unfinished(self):
        from video_publisher import comment_actions, direct_messages, engagement
        states = {kind: states for kind, _, states, _ in overview.UNCONFIRMED}
        self.assertEqual(direct_messages.PENDING, states["message"])
        self.assertEqual(comment_actions.PENDING, states["comment_action"])
        self.assertEqual(engagement.PENDING, states["like_or_save"])

    def test_what_finished_or_never_went_is_left_out(self):
        for number, (folder, state) in enumerate((("operations/replies", "REPLY_VERIFIED"), ("operations/replies", "REPLY_READY"), ("operations/dm-actions", "DM_VISIBLE"), ("operations/dm-actions", "DM_READY"),
                                                  ("operations/dm-actions", "DM_FAILED"), ("operations/comment-actions", "COMMENT_VERIFIED"), ("engagement/receipts", "ACTION_VERIFIED"),
                                                  ("engagement/receipts", "NO_CHANGE"), ("operations/collections", "CONFIRMED"), ("operations/collections", "READY"), ("edits", "EDIT_VERIFIED"), ("edits", "EDIT_MISMATCH"))):
            self.receipt(folder, str(number), state=state)
        self.receipt("widgets", "BV1xx411c7mD", rows=[{"state": "VERIFIED"}])
        result = overview.status()
        self.assertEqual({"unconfirmed_total": 0, "unconfirmed": []}, result["actions"])
        self.assertNotIn("unconfirmed", result["summary"])

    def test_no_message_words_or_recipient_appear(self):
        self.receipt("operations/dm-actions", self.HEX, state="DM_UNVERIFIED", at="2026-11-01T02:00:00+00:00",
                     request={"platform": "x", "recipient": "some_viewer", "text": "Private words", "work_id": "hidden"}, before={"items": [{"text": "Their private words"}]})
        self.assertEqual({"kind": "message", "key": self.HEX, "platform": "x", "state": "DM_UNVERIFIED", "at": "2026-11-01T02:00:00+00:00", "next_command": "vpub dm verify " + self.HEX}, self.actions()[0])

    def test_an_edit_on_a_recorded_work_or_an_adopted_one_gets_its_own_form(self):
        key = self.row("SUBMISSION_VERIFIED", platform="x", receipt={"id": "9"})
        self.receipt("edits", "e" * 64, state="EDIT_SUBMITTED_UNVERIFIED", key=key, id="9", at="2026-11-01T01:00:00+00:00")
        self.assertEqual(f"vpub edit {key} FILE --verify-only", self.actions()[0]["next_command"])
        (home() / "edits" / ("e" * 64 + ".json")).unlink()
        atomic_json(home() / "operations/adopted" / (digest(["x", "77"]) + ".json"), {"platform": "x", "work_id": "77", "identity": "me"})
        self.receipt("edits", "f" * 64, state="EDIT_SUBMITTING", key=digest(["adopted", "x", "77"]), at="2026-11-01T01:00:00+00:00")
        row = self.actions()[0]
        self.assertEqual(("x", "77", "vpub edit-work x 77 FILE --verify-only"), (row["platform"], row["work_id"], row["next_command"]))

    def test_the_workers_own_record_has_the_work_and_no_key(self):
        key = self.row("SUBMISSION_VERIFIED", platform="x", receipt={"id": "9"})
        self.receipt("edits", "e" * 64, state="EDIT_SUBMITTING", id="9", platform="x", identity="me", at="2026-11-01T01:00:00+00:00")
        self.assertEqual(f"vpub edit {key} FILE --verify-only", self.actions()[0]["next_command"])
        (home() / "edits" / ("e" * 64 + ".json")).unlink()
        atomic_json(home() / "operations/adopted" / (digest(["instagram", "Abc"]) + ".json"), {"platform": "instagram", "work_id": "Abc", "identity": "me"})
        self.receipt("edits", "f" * 64, state="EDIT_SUBMITTING", id="Abc", platform="instagram", identity="me", at="2026-11-01T01:00:00+00:00")
        self.assertEqual("vpub edit-work instagram Abc FILE --verify-only", self.actions()[0]["next_command"])

    def test_an_edit_it_cant_place_still_shows_up(self):
        self.receipt("edits", "e" * 64, state="EDIT_SUBMITTING", id="9", platform="x", at="2026-11-01T01:00:00+00:00")
        row = self.actions()[0]
        self.assertEqual(("edit", None), (row["kind"], row["next_command"]))

    def test_widgets_not_yet_confirmed_are_counted_by_state(self):
        self.receipt("widgets", "BV1xx411c7mD", rows=[{"state": "VERIFIED"}, {"state": "PENDING"}, {"state": "UNVERIFIED"}, {"state": "UNVERIFIED"}], updated_at="2026-11-01T01:00:00+00:00")
        self.assertEqual({"kind": "widgets", "key": "BV1xx411c7mD", "platform": "bilibili", "work_id": "BV1xx411c7mD", "state": "PARTIAL", "widgets": {"PENDING": 1, "UNVERIFIED": 2},
                          "at": "2026-11-01T01:00:00+00:00", "next_command": "vpub widgets verify BV1xx411c7mD"}, self.actions()[0])

    def test_newest_first_with_a_limit_and_the_full_count(self):
        for number in range(5):
            self.receipt("operations/dm-actions", f"{number:032x}", state="DM_UNVERIFIED", request={"platform": "x"}, at=f"2026-11-01T0{number}:00:00+00:00")
        result = overview.status(limit=2)["actions"]
        self.assertEqual(5, result["unconfirmed_total"])
        self.assertEqual([f"{4:032x}", f"{3:032x}"], [row["key"] for row in result["unconfirmed"]])

    def test_the_summary_mentions_them(self):
        self.receipt("operations/dm-actions", self.HEX, state="DM_UNVERIFIED", request={"platform": "x"})
        self.assertTrue(any(words in overview.status()["summary"] for words in ("1 other action is still unconfirmed", "1 个回复")))

    def test_a_damaged_record_is_skipped_and_status_still_answers(self):
        (home() / "operations/dm-actions").mkdir(parents=True)
        (home() / "operations/dm-actions" / (self.HEX + ".json")).write_text("broken")
        self.receipt("operations/replies", self.LONG, state="REPLY_UNVERIFIED", platform="x", work_id="1")
        self.assertEqual(["reply"], [row["kind"] for row in self.actions()])

    def test_nothing_is_contacted(self):
        self.receipt("operations/replies", self.LONG, state="REPLY_UNVERIFIED", platform="x", work_id="1")
        with patch.object(adapters.subprocess, "run") as run, patch.object(adapters.subprocess, "Popen") as popen:
            overview.status()
        run.assert_not_called()
        popen.assert_not_called()


class StatusTests(OverviewFixture):
    def test_empty_workspace_reports_nothing_and_contacts_nothing(self):
        with patch.object(adapters.subprocess, "run") as run, patch.object(adapters.subprocess, "Popen") as popen:
            result = overview.status()
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(0, result["platform_writes"])
        self.assertEqual([], result["publications"]["needs_attention"])
        self.assertFalse(result["stop"]["active"])
        self.assertFalse(result["workspace_busy"])
        self.assertIn(result["summary"], {"No publication needs attention.", "没有需要处理的发布。"})

    def test_stop_reason_is_shown_and_the_file_stays(self):
        atomic_json(home() / "STOP.json", {"reason": "Paused for review", "at": "2026-10-31T00:00:00+00:00"})
        stop = overview.status()["stop"]
        self.assertTrue(stop["active"])
        self.assertEqual("Paused for review", stop["reason"])
        self.assertIn("operator", stop["hint"])
        self.assertTrue((home() / "STOP.json").exists())

    def test_damaged_stop_file_still_counts(self):
        (home() / "STOP.json").write_text("broken")
        self.assertTrue(overview.stop_state()["active"])

    def test_handoff_job_gets_its_exact_next_command(self):
        _, results = self.publish(["youtube"])
        key = results["youtube"]["key"]
        item = self.attention()[key]
        self.assertEqual("vpub browser request " + key, item["next_command"])
        self.assertEqual("WAITING_FOR_BROWSER", item["state"])

    def test_every_unfinished_state_points_at_a_safe_next_step(self):
        expected = {
            "IN_PROGRESS": "vpub resume ",
            "PREPARATION_INCOMPLETE": "vpub resume ",
            "UPLOAD_PAUSED": "vpub resume ",
            "SUBMITTED_UNVERIFIED": "vpub verify ",
            "UNKNOWN": "vpub verify ",
        }
        keys = {state: self.row(state, sha=state) for state in expected}
        items = self.attention()
        for state, command in expected.items():
            self.assertEqual(command + keys[state], items[keys[state]]["next_command"])

    def test_verification_wait_asks_for_sign_in_first(self):
        key = self.row("WAITING_FOR_USER")
        item = self.attention()[key]
        self.assertEqual("vpub auth login x", item["next_command"])
        self.assertIn("vpub resume " + key, item["note"])

    def test_a_thumbnail_youtube_wants_a_phone_number_for_names_the_persons_choice(self):
        """From a review: a plain resume would only send the same thumbnail to the same request again."""
        key = self.row("PREPARATION_INCOMPLETE", method="browser", platform="youtube", receipt={"error": {"code": "cover_unavailable"}})
        item = self.attention()[key]
        self.assertIsNone(item["next_command"])
        self.assertIn(f"vpub resume {key} --no-cover", item["note"])
        self.assertIn("verifies it in YouTube Studio", item["note"])

    def test_a_youtube_press_that_left_a_draft_is_sent_to_resume(self):
        key = self.row("SUBMITTED_UNVERIFIED", method="browser", platform="youtube", receipt={"id": "abc", "still_a_draft": True})
        item = self.attention()[key]
        self.assertEqual("vpub resume " + key, item["next_command"])
        self.assertIn("uploads nothing", item["note"])

    def test_bilibili_attempt_without_an_id_is_never_sent_to_resume(self):
        key = self.row("IN_PROGRESS", method="cli", platform="bilibili")
        item = self.attention()[key]
        self.assertEqual("vpub works bilibili --limit 20", item["next_command"])
        self.assertIn("keep UNKNOWN", item["note"])
        self.assertIn("Do not re-encode", item["note"])
        with_id = self.row("SUBMITTED_UNVERIFIED", method="cli", platform="bilibili", receipt={"id": "BV1234567890"}, sha="other")
        self.assertEqual("vpub verify " + with_id, self.attention()[with_id]["next_command"])

    def test_api_upload_without_an_id_resumes_rather_than_verifies(self):
        key = self.row("MEDIA_PROCESSING", method="api", platform="instagram")
        self.assertEqual("vpub resume " + key, self.attention()[key]["next_command"])

    def test_running_attempt_is_not_mistaken_for_an_interrupted_one(self):
        key = self.row("IN_PROGRESS")
        self.assertEqual("vpub resume " + key, self.attention()[key]["next_command"])
        with publication_lock():
            result = overview.status()
            item = {i["key"]: i for i in result["publications"]["needs_attention"]}[key]
            self.assertTrue(result["workspace_busy"])
        self.assertIsNone(item["next_command"])
        self.assertEqual("Running now.", item["note"])

    def test_looking_never_takes_the_publication_lock(self):
        with publication_lock():
            overview.status()
            self.assertTrue((home() / "publisher.lock").exists())
        self.assertFalse(overview.workspace_busy())

    def test_upload_progress_comes_from_the_durable_receipts(self):
        browser = self.row("IN_PROGRESS", sha="browser")
        atomic_json(home() / "browser-receipts" / "plan-browser-x.json",
                    {"phase": "uploading", "updated_at": "2026-10-31T00:00:00+00:00", "final_submit_attempted": False})
        api = self.row("UPLOADING", method="api", platform="youtube", sha="api", receipt={"state": "UPLOADING", "offset": 50, "bytes": 200})
        items = self.attention()
        self.assertEqual("uploading", items[browser]["progress"]["phase"])
        self.assertEqual(25.0, items[api]["progress"]["percent"])

    def test_finished_and_draft_attempts_are_counted_but_need_no_attention(self):
        self.row("SUBMISSION_VERIFIED", sha="done")
        self.row("PREVIEW_READY", method="native", sha="draft")
        publications = overview.status()["publications"]
        self.assertEqual({"SUBMISSION_VERIFIED": 1, "PREVIEW_READY": 1}, publications["by_state"])
        self.assertEqual([], publications["needs_attention"])
        self.assertEqual(1, len(publications["drafts"]))

    def test_limit_caps_the_list_but_not_the_total(self):
        for number in range(3):
            self.row("UNKNOWN", sha=str(number))
        publications = overview.status(limit=2)["publications"]
        self.assertEqual(2, len(publications["needs_attention"]))
        self.assertEqual(3, publications["needs_attention_total"])
        for limit in (0, 201, "5"):
            with self.assertRaises(VpubError):
                overview.status(limit=limit)

    def test_background_session_key_is_never_shown_and_dead_sessions_are_hidden(self):
        folder = home() / "browser-brokers"
        atomic_json(folder / "x-default.json", {"address": "pipe", "key": "secret-session-key", "pid": os.getpid()})
        atomic_json(folder / "douyin-default.json", {"address": "pipe", "key": "another", "pid": 999999})
        atomic_json(folder / "youtube-default.json", {"state": "closed", "pid": os.getpid()})
        result = overview.status()
        self.assertEqual([{"session": "x-default", "pid": os.getpid()}], result["background_browsers"])
        self.assertNotIn("secret-session-key", json.dumps(result))

    def test_saved_sign_in_reads_only_our_own_identity_record(self):
        atomic_json(home() / "accounts/default/x-identity.json", {"identity": "creator", "verified_at": "2026-10-31T00:00:00+00:00", "method": "browser"})
        atomic_json(home() / "accounts/default/x-browser-session.json", {"cookies": [{"name": "auth_token", "value": "cookie-value"}]})
        with patch.object(adapters.importlib.util, "find_spec", return_value=None):
            result = overview.status()
        self.assertEqual("creator", result["platforms"]["x"]["sign_in"]["identity"])
        self.assertFalse(result["platforms"]["douyin"]["sign_in"]["saved"])
        self.assertFalse(result["platforms"]["youtube"]["sign_in"]["saved"])
        self.assertIsNone(result["platforms"]["bilibili"]["sign_in"]["saved"])
        self.assertNotIn("cookie-value", json.dumps(result))

    def test_damaged_side_records_do_not_break_the_summary(self):
        (home() / "operations/sync-runs").mkdir(parents=True)
        (home() / "operations/sync-runs" / ("a" * 32 + ".json")).write_text("broken")
        (home() / "operations/inbox").mkdir(parents=True)
        (home() / "operations/inbox" / "broken.json").write_text("broken")
        result = overview.status()
        self.assertIsNone(result["last_sync"])
        self.assertIsNone(result["messages_pending"])

    def test_cli_runs_status_and_saves_with_out(self):
        out = self.root / "status.json"
        result = cli.execute(cli.parser().parse_args(["status", "--limit", "5", "--out", str(out)]))
        self.assertEqual(result["summary"], json.loads(out.read_text(encoding="utf-8"))["summary"])


class CleanupTests(OverviewFixture):
    def test_preview_deletes_nothing_and_apply_frees_a_finished_copy(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        folder = home() / "jobs" / plan["confirmation"]
        preview = overview.cleanup()
        self.assertEqual("PREVIEW", preview["mode"])
        self.assertEqual([str(folder)], [item["folder"] for item in preview["removable"]])
        self.assertEqual(self.video.stat().st_size, preview["reclaimable_bytes"])
        self.assertEqual("vpub cleanup --apply", preview["next"])
        self.assertTrue(folder.exists())
        applied = overview.cleanup(apply=True)
        self.assertEqual("APPLIED", applied["mode"])
        self.assertEqual(self.video.stat().st_size, applied["freed_bytes"])
        self.assertFalse(folder.exists())
        self.assertTrue(self.video.exists())

    def test_status_shows_what_cleanup_would_free(self):
        _, results = self.publish()
        self.finish(results["bilibili"]["key"])
        storage = overview.status()["storage"]
        self.assertEqual(self.video.stat().st_size, storage["reclaimable_bytes"])
        self.assertEqual("vpub cleanup", storage["next"])

    def test_unfinished_publication_keeps_its_copy(self):
        plan, _ = self.publish()
        result = overview.cleanup(apply=True)
        self.assertEqual([], result["removed"])
        self.assertIn("not finished", result["kept"][0]["kept_because"])
        self.assertTrue((home() / "jobs" / plan["confirmation"]).exists())

    def test_one_unfinished_platform_keeps_a_shared_copy(self):
        plan, results = self.publish(["bilibili", "youtube"])
        self.finish(results["bilibili"]["key"])
        self.assertEqual([], overview.cleanup(apply=True)["removed"])
        self.assertTrue((home() / "jobs" / plan["confirmation"]).exists())

    def test_missing_original_keeps_what_may_be_the_only_copy(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        self.video.unlink()
        result = overview.cleanup(apply=True)
        self.assertEqual([], result["removed"])
        self.assertIn("only exact copy", result["kept"][0]["kept_because"])
        self.assertTrue((home() / "jobs" / plan["confirmation"]).exists())

    def test_changed_original_keeps_the_approved_copy(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        same_size = bytes(reversed(self.video.read_bytes()))
        self.video.write_bytes(same_size)
        self.assertEqual([], overview.cleanup(apply=True)["removed"])
        self.assertTrue((home() / "jobs" / plan["confirmation"]).exists())

    def test_unknown_folder_is_left_alone(self):
        folder = home() / "jobs" / "nothing-points-here"
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"synthetic")
        result = overview.cleanup(apply=True)
        self.assertEqual([], result["removed"])
        self.assertTrue(folder.exists())

    def test_files_outside_the_jobs_folder_are_never_touched(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        keep = home() / "browser-jobs" / "keep.json"
        atomic_json(keep, {"keep": True})
        overview.cleanup(apply=True)
        self.assertTrue(keep.exists())
        self.assertTrue(self.manifest.exists())

    def test_another_publication_command_blocks_cleanup(self):
        with publication_lock():
            with self.assertRaises(VpubError) as e:
                overview.cleanup(apply=True)
        self.assertEqual("busy", e.exception.code)

    def test_publishing_again_after_cleanup_never_reuploads(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        overview.cleanup(apply=True)
        again = cli.publish_plan(plan, plan["confirmation"], reviewed=True)["results"]["bilibili"]
        self.assertEqual("SKIPPED_EXISTING", again["state"])

    def test_another_platform_later_makes_a_new_copy_from_the_original(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        overview.cleanup(apply=True)
        plan, later = self.publish(["douyin"])
        self.assertEqual("WAITING_FOR_BROWSER", later["douyin"]["state"])
        self.assertEqual(self.video.read_bytes(), (home() / "jobs" / plan["confirmation"] / "video.mp4").read_bytes())

    def test_hard_linked_upload_name_is_counted_once(self):
        folder = home() / "jobs" / "linked"
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"12345")
        os.link(folder / "video.mp4", folder / "crosscast-name.mp4")
        self.assertEqual(5, overview.folder_bytes(folder))

    def test_registered_temporary_file_keeps_snapshot_until_separate_cleanup_finishes(self):
        from video_publisher import runtime_files
        plan, results = self.publish()
        self.finish(results['bilibili']['key'])
        folder = home() / 'jobs' / plan['confirmation']
        temporary = folder / '.vpub-abcdefgh'
        temporary.write_bytes(b'failed copy')
        runtime_files.defer(temporary)
        with patch.object(runtime_files, 'alive', return_value=False):
            preview = overview.cleanup()
            self.assertEqual([], preview['removable'])
            self.assertEqual(temporary.stat().st_size, preview['reclaimable_bytes'])
            applied = overview.cleanup(apply=True)
            self.assertEqual([], applied['removed'])
            self.assertTrue(folder.exists())
            if os.name == 'nt':
                self.assertEqual(len(b'failed copy'), applied['freed_bytes'])
                self.assertFalse(temporary.exists())
                self.assertEqual([str(folder)], [item['folder'] for item in overview.cleanup(apply=True)['removed']])
            else:
                self.assertEqual(0, applied['freed_bytes'])
                self.assertTrue(temporary.exists())

    def test_snapshot_cleanup_cannot_bypass_changed_runtime_or_unavailable_primitive(self):
        from video_publisher import runtime_files, runtime_delete
        plan, results = self.publish()
        self.finish(results['bilibili']['key'])
        folder = home() / 'jobs' / plan['confirmation']
        temporary = folder / '.vpub-abcdefgh'
        temporary.write_bytes(b'failed copy')
        runtime_files.defer(temporary)
        for changed in (False, True):
            with self.subTest(changed=changed):
                if changed:
                    replacement = self.root / 'replacement'
                    replacement.write_bytes(b'unknown replacement')
                    os.replace(replacement, temporary)
                expected = temporary.read_bytes()
                with patch.object(runtime_files, 'alive', return_value=False), \
                        patch.object(runtime_delete, 'remove', side_effect=OSError('safe primitive unavailable')), \
                        patch.object(overview.shutil, 'rmtree', side_effect=AssertionError('runtime bypass')) as bulk:
                    preview = overview.cleanup()
                    result = overview.cleanup(apply=True)
                bulk.assert_not_called()
                self.assertEqual([], preview['removable'])
                self.assertEqual([], result['removed'])
                self.assertTrue(result['runtime_files']['kept'])
                self.assertIn('runtime', result['kept'][0]['kept_because'])
                self.assertEqual(expected, temporary.read_bytes())
                self.assertTrue((folder / 'video.mp4').exists())

    def test_registered_scratch_and_unreadable_registry_keep_finished_snapshot(self):
        from video_publisher import runtime_files, runtime_scratch
        plan, results = self.publish()
        self.finish(results['bilibili']['key'])
        folder = home() / 'jobs' / plan['confirmation']
        with runtime_scratch.scratch(folder) as scratch:
            (scratch / 'owned.json').write_bytes(b'owned')
        (scratch / 'unknown.json').write_bytes(b'unknown')
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                if unreadable:
                    next((home() / 'runtime-cleanup').glob('*.json')).write_text('{broken')
                with patch.object(runtime_files, 'alive', return_value=False), \
                        patch.object(overview.shutil, 'rmtree', side_effect=AssertionError('runtime bypass')):
                    result = overview.cleanup(apply=True)
                self.assertEqual([], result['removed'])
                self.assertTrue(result['runtime_files']['kept'])
                self.assertEqual(b'unknown', (scratch / 'unknown.json').read_bytes())

    def test_unreadable_registry_directory_cannot_hide_registered_runtime_entries(self):
        from video_publisher import runtime_files
        plan, results = self.publish()
        self.finish(results['bilibili']['key'])
        folder = home() / 'jobs' / plan['confirmation']
        temporary = folder / '.vpub-abcdefgh'
        temporary.write_bytes(b'owned runtime data')
        runtime_files.defer(temporary)
        registry, scandir = home() / 'runtime-cleanup', os.scandir
        def denied(path):
            if not isinstance(path, int) and Path(path) == registry:
                raise PermissionError('registry inaccessible')
            return scandir(path)
        with patch.object(os, 'scandir', side_effect=denied), \
                patch.object(overview.shutil, 'rmtree', side_effect=AssertionError('runtime bypass')) as bulk:
            self.assertTrue(runtime_files.registered_in(folder))
            self.assertEqual([], overview.cleanup()['removable'])
            result = overview.cleanup(apply=True)
        bulk.assert_not_called()
        self.assertEqual([], result['removed'])
        self.assertEqual(b'owned runtime data', temporary.read_bytes())
        self.assertTrue((folder / 'video.mp4').exists())

    def test_cli_cleanup_only_deletes_with_apply(self):
        plan, results = self.publish()
        self.finish(results["bilibili"]["key"])
        folder = home() / "jobs" / plan["confirmation"]
        cli.execute(cli.parser().parse_args(["cleanup"]))
        self.assertTrue(folder.exists())
        cli.execute(cli.parser().parse_args(["cleanup", "--apply"]))
        self.assertFalse(folder.exists())


class OperatorCommandTests(OverviewFixture):
    def test_doctor_names_what_is_missing_and_the_fix(self):
        with patch.object(overview, "chrome_path", return_value=None):
            result = cli.doctor()
        self.assertFalse(result["ready"])
        missing = {issue["code"]: issue for issue in result["issues"]}
        self.assertEqual("vpub setup --install-browser", missing["chrome_missing"]["fix"])

    def test_doctor_is_ready_when_everything_is_there(self):
        with patch.object(overview, "chrome_path", return_value="chrome.exe"), patch.object(cli, "separate_bili_agent", return_value=None):
            result = cli.doctor()
        self.assertEqual([], result["issues"])
        self.assertTrue(result["ready"])
        self.assertEqual("chrome.exe", result["google_chrome"])

    def doctor_command(self, chrome):
        out = io.StringIO()
        with patch.object(overview, "chrome_path", return_value=chrome), patch.object(cli, "separate_bili_agent", return_value=None), patch("sys.argv", ["vpub", "doctor"]), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ended:
            cli.main()
        return ended.exception.code, json.loads(out.getvalue())

    def test_a_doctor_with_something_to_fix_does_not_end_as_a_success(self):
        """From a first-run review: missing Chrome showed only inside the answer, so anything looking at the exit code went on without it."""
        code, answer = self.doctor_command(None)
        self.assertEqual((4, False, False), (code, answer["ok"], answer["data"]["ready"]))
        self.assertEqual("vpub setup --install-browser", answer["data"]["issues"][0]["fix"])

    def test_a_ready_doctor_ends_as_a_success(self):
        code, answer = self.doctor_command("chrome.exe")
        self.assertEqual((0, True, True), (code, answer["ok"], answer["data"]["ready"]))

    def test_the_installer_says_where_the_command_is_even_when_the_check_finds_something(self):
        source = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")
        self.assertLess(source.index("Write-Output ('Command: '"), source.index("found something to fix"))

    def test_doctor_reports_an_active_stop(self):
        atomic_json(home() / "STOP.json", {"reason": "test"})
        with patch.object(overview, "chrome_path", return_value="chrome.exe"), patch.object(cli, "separate_bili_agent", return_value=None):
            result = cli.doctor()
        self.assertFalse(result["ready"])
        self.assertEqual("stopped", result["issues"][0]["code"])

    def test_stop_says_how_to_resume(self):
        result = cli.execute(cli.parser().parse_args(["stop", "--reason", "Testing"]))
        self.assertEqual(str(home() / "STOP.json"), result["stop_file"])
        self.assertIn("vpub stop --clear", result["hint"])

    def test_init_rejects_a_missing_video_at_once(self):
        out = self.root / "new.json"
        with self.assertRaises(VpubError) as e:
            cli.execute(cli.parser().parse_args(["init", "--video", str(self.root / "missing.mp4"), "--out", str(out)]))
        self.assertEqual("invalid_video", e.exception.code)
        self.assertFalse(out.exists())

    def test_removed_commands_and_flags_are_gone(self):
        parser = cli.parser()
        for arguments in (["inbox", "list"], ["auth", "login", "youtube", "--manage-playlists"], ["metadata", "request", "m.json", "--bilingual", "x", "--out", "o.json"]):
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                parser.parse_args(arguments)

    def test_comments_are_listed_through_messages(self):
        arguments = cli.parser().parse_args(["messages", "list", "--kind", "comments", "--status", "all"])
        self.assertEqual({"items": [], "matching_total": 0}, {k: v for k, v in cli.execute(arguments).items() if k in {"items", "matching_total"}})


if __name__ == "__main__":
    unittest.main()
