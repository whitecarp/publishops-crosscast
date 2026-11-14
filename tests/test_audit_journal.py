"""Offline journal acceptance: real stores and CLI, fake platform boundaries."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import audit_journal as journal, cli, core, progress, adapters
from video_publisher.browser_backends import Transaction
import test_work_delete as deletion


class JournalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="journal-offline-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, VPUB_HOME=str(self.root / "vpub"), BILI_CLI_HOME=str(self.root / "bili"))
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, journal, "RUN", None)
        journal.RUN = None
        self.directory = core.home() / "audit"

    def events(self, kind=None):
        records = [json.loads(line) for line in (self.directory / "journal.jsonl").read_text().splitlines()]
        return [r for r in records if kind is None or r["type"] == kind]

    def run_cli(self, args, execute):
        output = io.StringIO()
        with patch.object(sys, "argv", ["vpub", *args]), patch.object(cli, "execute", side_effect=execute), redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                cli.main()
        return result.exception.code, output.getvalue()

    def test_edit_unavailable_and_prepared_survive_export(self):
        from video_publisher.audit_export import project_event
        for state in ['EDIT_UNAVAILABLE', 'EDIT_PREPARED']:
            row = project_event({'type': 'transition', 'state': state, 'receipt_state': state}, lambda x: None)
            self.assertEqual(state, row['state'])
            self.assertEqual(state, row['receipt_state'])

    def test_publish_approval_alias_is_recorded_without_its_confirmation(self):
        args = cli.parser().parse_args(["publish", "PRIVATE-plan", "--person-approved", "--confirm", "PRIVATE-code"])
        journal.command_start(args)
        flags = self.events("command_start")[-1]["flags"]
        self.assertTrue(flags["person_approved"])
        self.assertTrue(flags["confirm_given"])
        self.assertNotIn("PRIVATE-", (self.directory / "journal.jsonl").read_text())

    def test_refusal_and_all_error_exit_paths_are_recorded(self):
        for error, exit_code, code in [
            (core.VpubError("stopped", "PRIVATE-message"), 2, "stopped"),
            (FileNotFoundError(), 2, "file_not_found"),
            (ValueError("PRIVATE-message"), 3, "internal_error"),
            (KeyboardInterrupt(), 3, "interrupted"),
        ]:
            with self.subTest(code=code):
                result, _ = self.run_cli(["doctor"], error)
                self.assertEqual(exit_code, result)
                end = self.events()[-1]
                self.assertEqual((exit_code, code), (end["exit_code"], end["error_code"]))
                self.assertEqual(self.events()[-2]["run"], end["run"])
        self.assertTrue(journal.verify_chain(self.directory)["ok"])
        self.assertNotIn("PRIVATE-message", (self.directory / "journal.jsonl").read_text())

    def test_broken_audit_does_not_change_stdout_or_exit(self):
        failure = core.VpubError("stopped", "same message")
        expected = self.run_cli(["doctor"], failure)
        with patch.object(journal, "home", side_effect=PermissionError()):
            actual = self.run_cli(["doctor"], failure)
        self.assertEqual(expected, actual)

    def test_lock_timeout_is_bounded_and_reported_on_next_event(self):
        journal.append("stage", lambda _: {"stage": "uploading"})
        with core.brief_lock(self.directory / "journal.lock", "busy", "busy", 1), patch.object(journal, 'LOCK_WAIT_SECONDS', 0.2):
            start = time.monotonic()
            self.assertFalse(journal.append("stage", lambda _: {"stage": "pressing"}))
            self.assertLess(time.monotonic() - start, 1.2)
            self.assertEqual('lock_timeout', journal.last_append_result()['reason'])
            self.assertTrue(journal.last_append_result()['loss_persisted'])
        journal.append("stage", lambda _: {"stage": "reading back"})
        self.assertEqual(1, self.events()[-1]["dropped_before"])
        from video_publisher.audit_export import export_data
        exported = export_data()
        self.assertEqual(1, exported['integrity']['dropped_events'])
        self.assertEqual(1, len(exported['losses']))

    def test_last_worker_loss_survives_exit_without_another_successful_append(self):
        from video_publisher.audit_export import export_data
        code = """import json
from video_publisher import audit_journal as j
j.RUN='a'*32
j.LOCK_WAIT_SECONDS=0.2
ok=j.append('stage',lambda _:dict(stage='uploading'))
print(json.dumps(dict(ok=ok,**j.last_append_result())))
"""
        with core.brief_lock(self.directory / 'journal.lock', 'busy', 'busy', 1):
            child = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=10)
        self.assertEqual(0, child.returncode, child.stderr)
        result = json.loads(child.stdout)
        self.assertFalse(result['ok'])
        self.assertTrue(result['loss_persisted'])
        exported = export_data()
        self.assertEqual(1, exported['integrity']['dropped_events'])
        self.assertEqual('a'*32, exported['runs'][0]['run'])
        self.assertEqual(1, exported['runs'][0]['dropped_events'])
        self.assertFalse(exported['runs'][0]['ended'])
        self.assertEqual('lock_timeout', exported['losses'][0]['reason'])

    def test_three_writer_study_shape_loses_no_events(self):
        journal.RUN = 'b'*32
        code = """import json,time
from video_publisher import audit_journal as j
j.RUN='b'*32
results=[]
for i in range(8):
    ok=j.append('stage',lambda _:dict(stage='uploading'))
    results.append(dict(attempt=i,ok=ok,**j.last_append_result()))
    time.sleep(0.25)
print(json.dumps(results))
"""
        children = [subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        parent = []
        for i in range(8):
            ok = journal.append('stage', lambda _: dict(stage='reading back'))
            parent.append(dict(attempt=i, ok=ok, **journal.last_append_result()))
            time.sleep(0.25)
        results = [parent]
        for child in children:
            out, err = child.communicate(timeout=40)
            self.assertEqual(0, child.returncode, err.decode())
            results.append(json.loads(out))
        from video_publisher.audit_export import export_data
        exported = export_data()
        written = journal.verify_chain(self.directory)['records']
        lost = exported['integrity']['dropped_events']
        diagnostic = json.dumps(dict(written=written, reported_lost=lost, writers=results))
        print('study_shape_diagnostic: ' + diagnostic)
        self.assertEqual(24, written + lost, diagnostic)
        self.assertEqual(24, written, diagnostic)
        self.assertTrue(all(a['ok'] for writer in results for a in writer), diagnostic)
        self.assertTrue(exported['integrity']['chain_ok'])

    def test_projection_failure_reports_only_closed_reason_and_survives_export(self):
        from video_publisher.audit_export import export_data
        def fail(_):
            raise ValueError('PRIVATE secret https://private.invalid/account')
        self.assertFalse(journal.append('stage', fail))
        result = export_data()
        self.assertEqual(1, result['integrity']['dropped_events'])
        self.assertEqual('projection_failed', result['losses'][0]['reason'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))

    def test_loss_write_failure_falls_back_without_changing_callers(self):
        from video_publisher.audit_export import export_data
        original = Path.open
        def deny_loss(path, *args, **kwargs):
            if 'losses' in path.parts:
                raise PermissionError()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', deny_loss), patch.object(journal, 'brief_lock', side_effect=core.VpubError('audit_busy','busy')):
            self.assertFalse(journal.append('stage', lambda _: {}))
        self.assertFalse(journal.last_append_result()['loss_persisted'])
        self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))
        result = export_data()
        self.assertEqual(1, result['integrity']['dropped_events'])
        self.assertFalse(result['losses'][0]['persistent'])

    def test_missing_end_is_visible_and_chain_tampering_fails(self):
        start = journal.command_start(SimpleNamespace(command="doctor"))
        journal.append("stage", lambda _: {"stage": "uploading"})
        journal.append("stage", lambda _: {"stage": "reading back"})
        self.assertEqual([journal.RUN], journal.verify_chain(self.directory)["not_ended"])
        path = self.directory / "journal.jsonl"
        data = path.read_bytes()
        lines = data.splitlines(keepends=True)
        for damaged, first_bad in [(b"".join([lines[0], lines[2]]), 2),
                                   (data.replace(b"uploading", b"pressing"), 3),
                                   (b"".join(lines[:-1]), 3), (data[:-2], 3)]:
            path.write_bytes(damaged)
            self.assertEqual(first_bad, journal.verify_chain(self.directory)["first_bad_seq"])
        path.write_bytes(data)
        journal.command_end(SimpleNamespace(), start, 0, None, {})
        self.assertEqual([], journal.verify_chain(self.directory)["not_ended"])

    def test_window_retains_unpersisted_loss_with_unknown_time(self):
        from video_publisher.audit_export import export_data
        original = Path.open
        def denied(path, *args, **kwargs):
            if 'losses' in path.parts:
                raise PermissionError()
            return original(path, *args, **kwargs)
        journal.RUN = 'c' * 32
        with patch.object(Path, 'open', denied), patch.object(journal, 'brief_lock', side_effect=core.VpubError('audit_busy', 'busy')):
            self.assertFalse(journal.append('stage', lambda _: {}))
        loss_id = journal.last_append_result()['loss_id']
        with patch.object(journal, 'utc', return_value='2026-11-11T12:00:00Z'):
            self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))
            # Simulate a later report of the same give-up. Export deduplicates it.
            journal._losses[self.directory] = 1
            journal._loss_ids[self.directory] = [loss_id]
            self.assertTrue(journal.append('stage', lambda _: dict(stage='reading back')))
        result = export_data(since='2026-11-11T00:00:00Z', until='2026-11-12T00:00:00Z')
        self.assertEqual(1, result['integrity']['dropped_events'])
        self.assertEqual(1, result['integrity']['unplaced_dropped_events'])
        self.assertEqual(1, result['runs'][0]['dropped_events'])
        self.assertIsNone(result['losses'][0]['at'])
        self.assertTrue(result['losses'][0]['time_unplaced'])
        self.assertIsNotNone(result['losses'][0]['reported_at'])

    def test_window_excludes_readable_loss_with_known_earlier_time(self):
        from video_publisher.audit_export import export_data
        with patch.object(journal, 'utc', return_value='2026-11-10T12:00:00Z'), \
             patch.object(journal, 'brief_lock', side_effect=core.VpubError('audit_busy', 'busy')):
            self.assertFalse(journal.append('stage', lambda _: {}))
        with patch.object(journal, 'utc', return_value='2026-11-11T12:00:00Z'):
            self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))
        result = export_data(since='2026-11-11T00:00:00Z')
        self.assertEqual(0, result['integrity']['dropped_events'])
        self.assertEqual([], result['losses'])

    def test_unreadable_loss_records_are_flagged_without_blocking_export(self):
        from video_publisher.audit_export import export_data
        self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))
        folder = self.directory / 'losses'
        folder.mkdir()
        damaged = folder / ('d' * 32 + '.json')
        damaged.write_text('{"at":"2020-01-01T00:00:00Z",')
        denied_file = folder / ('e' * 32 + '.json')
        denied_file.write_bytes(b'PRIVATE LOSS FILE')
        original = Path.read_bytes
        def denied(path):
            if path == denied_file:
                raise PermissionError('PRIVATE failure')
            return original(path)
        with patch.object(Path, 'read_bytes', denied):
            result = export_data(since='2026-01-01T00:00:00Z')
        self.assertEqual(2, result['integrity']['unreadable_loss_records'])
        self.assertFalse(result['integrity']['loss_records_complete'])
        self.assertTrue(result['integrity']['dropped_count_is_lower_bound'])
        self.assertEqual(1, len(result['events']))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(b'PRIVATE LOSS FILE', denied_file.read_bytes())

    def test_unreadable_loss_directory_is_not_reported_complete(self):
        from video_publisher import audit_export as exp
        journal.append('stage', lambda _: dict(stage='uploading'))
        with patch.object(exp.os, 'scandir', side_effect=PermissionError('PRIVATE')):
            result = exp.export_data()
        self.assertTrue(result['integrity']['loss_folder_unreadable'])
        self.assertFalse(result['integrity']['loss_records_complete'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        folder = self.directory / 'losses'
        folder.write_bytes(b'not a folder')
        result = exp.export_data()
        self.assertTrue(result['integrity']['loss_folder_unreadable'])
        self.assertFalse(result['integrity']['loss_records_complete'])
        self.assertEqual(b'not a folder', folder.read_bytes())

    def test_deep_and_oversized_loss_records_leave_other_evidence_exportable(self):
        from video_publisher import audit_export as exp
        journal.append('stage', lambda _: dict(stage='uploading'))
        folder = self.directory / 'losses'; folder.mkdir()
        (folder / ('a' * 32 + '.json')).write_text('[' * 2000 + '0' + ']' * 2000)
        (folder / ('b' * 32 + '.json')).write_bytes(b'X' * (exp.LOSS_MAX_BYTES + 1))
        (folder / ('c' * 32 + '.json')).write_text(json.dumps(dict(
            v=1,id='c' * 32,run=None,reason='lock_timeout',at='0001-01-01T00:00:00+23:00')))
        result = exp.export_data()
        self.assertEqual(3, result['integrity']['unreadable_loss_records'])
        self.assertFalse(result['integrity']['loss_records_complete'])
        self.assertEqual(1, len(result['events']))

    def test_unreadable_file_and_its_report_are_one_loss_with_two_distinct_diagnostics(self):
        from video_publisher.audit_export import export_data
        with patch.object(journal, 'brief_lock', side_effect=core.VpubError('audit_busy', 'busy')):
            self.assertFalse(journal.append('stage', lambda _: {}))
        loss_id = journal.last_append_result()['loss_id']
        (self.directory / 'losses' / (loss_id + '.json')).write_bytes(b'{')
        self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))
        result = export_data()
        self.assertEqual(1, result['integrity']['dropped_events'])
        self.assertEqual(1, result['integrity']['unreadable_loss_records'])
        self.assertEqual(1, result['integrity']['unplaced_dropped_events'])
        self.assertFalse(result['integrity']['loss_records_complete'])
        self.assertEqual(1, len(result['losses']))
        self.assertTrue(result['losses'][0]['file_unreadable'])
        self.assertEqual('file_unreadable', result['losses'][0]['reason'])

    def test_loss_reporter_run_is_sanitized_for_ids_and_legacy_counts(self):
        from video_publisher import audit_export as exp
        journal.append('stage', lambda _: dict(stage='uploading'))
        for raw_run in ['PRIVATE PATH', {'private': 'PRIVATE'}, ['PRIVATE']]:
            event = dict(type='stage', seq=1, run=raw_run, at='2026-11-11T12:00:00Z',
                         stage='uploading', dropped_before=2, dropped_ids=['a' * 32])
            with patch.object(exp, 'events_from', return_value=([event], dict(ok=True, head='0' * 64))):
                result = exp.export_data()
            self.assertEqual(2, result['integrity']['dropped_events'])
            self.assertIsNone(result['losses'][0]['run'])
            self.assertEqual([], result['runs'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_stuck_lock_only_spends_one_long_wait_per_process_and_home(self):
        from video_publisher.audit_export import export_data
        with core.brief_lock(self.directory / 'journal.lock', 'busy', 'busy', 1), \
             patch.object(journal, 'LOCK_WAIT_SECONDS', 0.4):
            started = time.monotonic()
            for _ in range(40):
                self.assertFalse(journal.append('stage', lambda _: dict(stage='uploading')))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 4, f'40 permanently blocked appends took {elapsed:.3f}s')
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertEqual(40, export_data()['integrity']['dropped_events'])
        self.assertTrue(journal.append('stage', lambda _: dict(stage='reading back')))
        self.assertNotIn(self.directory, journal._lock_degraded)
        with core.brief_lock(self.directory / 'journal.lock', 'busy', 'busy', 1), \
             patch.object(journal, 'LOCK_WAIT_SECONDS', 0.4):
            started = time.monotonic()
            self.assertFalse(journal.append('stage', lambda _: {}))
            self.assertGreaterEqual(time.monotonic() - started, 0.35)

    def test_concurrent_processes_append_gapless_chain(self):
        code = """import json
from video_publisher import audit_journal as j
results=[]
for i in range(8):
    ok=j.append('stage',lambda _:dict(stage='uploading'))
    results.append(dict(attempt=i,ok=ok,**j.last_append_result()))
print(json.dumps(results))
"""
        children = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        results = []
        for child in children:
            out, err = child.communicate(timeout=20)
            self.assertEqual(0, child.returncode, err.decode())
            results.append(json.loads(out))
        lost = sum(not attempt['ok'] for writer in results for attempt in writer)
        written = journal.verify_chain(self.directory)["records"]
        diagnostic = json.dumps(dict(written=written, reported_lost=lost, writers=results))
        print('concurrent_append_diagnostic: ' + diagnostic)
        self.assertEqual(24, written + lost, diagnostic)
        self.assertEqual(24, written, diagnostic)
        self.assertTrue(journal.verify_chain(self.directory)["ok"])

    def test_recover_fsynced_line_after_head_write_failure(self):
        journal.append("stage", lambda _: {"stage": "uploading"})
        with patch.object(journal, "atomic_json", side_effect=PermissionError()):
            self.assertTrue(journal.append("stage", lambda _: {"stage": "pressing"}))
            self.assertEqual('head_repair_needed', journal.last_append_result()['reason'])
        journal.append("stage", lambda _: {"stage": "reading back"})
        self.assertTrue(journal.verify_chain(self.directory)["ok"])
        self.assertEqual([1, 2, 3], [r["seq"] for r in self.events()])
        self.assertEqual([0, 0, 0], [r['dropped_before'] for r in self.events()])

    def test_salt_stability_and_strict_projection(self):
        key = "a" * 64
        secret = "PRIVATE-account-title-url-path"
        data = {"state": secret, "phase": secret, "id": secret, "error": {"code": secret},
                "receipt": {"id": secret, "phase": secret, "title": secret, "final_presses": True}}
        journal.transition("ledger", key, "x", data)
        journal.transition("ledger", key, "x", data)
        records = self.events()
        self.assertEqual(records[0]["ref"], records[1]["ref"])
        self.assertEqual(32, len((self.directory / "salt").read_bytes()))
        self.assertIsNone(records[0]["final_presses"])
        text = (self.directory / "journal.jsonl").read_text()
        for value in (key, secret, str(self.root)):
            self.assertNotIn(value, text)
        first_ref = records[0]["ref"]
        with patch.dict(os.environ, VPUB_HOME=str(self.root / "different")):
            journal.transition("ledger", key, "x", data)
            other = json.loads((core.home() / "audit/journal.jsonl").read_text())
        self.assertNotEqual(first_ref, other["ref"])

    def test_before_after_snapshots_and_forget_events(self):
        ledger = core.Ledger()
        self.addCleanup(ledger.close)
        key = ledger.claim("x", "PRIVATE-owner", "PRIVATE-sha", {})
        args = SimpleNamespace(command="verify", key=key)
        start = journal.command_start(args)
        ledger.save(key, "PREVIEW_READY", {"receipt": {"state": "PREVIEW_READY", "final_presses": 0}})
        journal.command_end(args, start, 0, None, {})
        self.assertEqual("IN_PROGRESS", self.events("command_start")[-1]["before"]["state"])
        self.assertEqual("PREVIEW_READY", self.events("command_end")[-1]["after"]["state"])
        ledger.forget_preview(key)
        self.assertEqual("forgotten", self.events()[-1]["state"])
        key = ledger.claim("youtube", "PRIVATE-owner", "PRIVATE-sha", {})
        ledger.forget(key)
        self.assertEqual("forgotten", self.events()[-1]["state"])
        count = len(self.events())
        ledger.forget(key)
        self.assertEqual(count, len(self.events()))

    def test_compare_and_swap_does_not_log_failed_update(self):
        ledger = core.Ledger()
        self.addCleanup(ledger.close)
        key = ledger.claim("x", "default", "sha", {})
        count = len(self.events())
        self.assertFalse(ledger.save_unless_changed(key, "UNKNOWN", {}, "wrong-time"))
        self.assertEqual(count, len(self.events()))
        self.assertTrue(ledger.save_unless_changed(key, "UNKNOWN", {}, ledger.get(key)["updated_at"]))
        self.assertEqual("UNKNOWN", self.events()[-1]["state"])

    def test_stages_run_context_and_invalid_word(self):
        run = "f" * 32
        progress.attach({"audit_run": run, "platform": "youtube", "action": "publish"})
        for word in sorted(progress.STAGES):
            progress.stage(word)
        progress.stage("PRIVATE-not-stage")
        events = self.events("stage")
        self.assertEqual(len(progress.STAGES), len(events))
        self.assertTrue(all(e["run"] == run and e["platform"] == "youtube" for e in events))
        with patch.object(journal, "home", side_effect=OSError()):
            progress.stage("uploading")

    def test_duplicate_publish_has_unchanged_press_count_and_no_canaries(self):
        video = self.root / "PRIVATE-source.mp4"
        video.write_bytes(b"synthetic")
        plan = {"confirmation": "PRIVATE-confirm", "video": str(video), "video_sha256": core.file_sha(video),
                "platforms": {"x": {"account": "PRIVATE-owner", "method": "browser", "title": "PRIVATE-title", "content_language": "und"}}}
        args = SimpleNamespace(command="publish", confirm="PRIVATE-confirm")
        with patch.object(adapters, "preflight", return_value={"identity": "PRIVATE-identity"}), patch.object(
                adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED", "id": "PRIVATE-work", "final_presses": 1}) as publish:
            for _ in range(2):
                started = journal.command_start(args)
                result = cli.publish_plan(plan, plan["confirmation"], reviewed=True)
                journal.command_end(args, started, 0, None, result)
            self.assertEqual(1, publish.call_count)
        ends = self.events("command_end")
        self.assertEqual("SKIPPED_EXISTING", ends[-1]["platforms"]["x"]["state"])
        self.assertEqual(1, ends[-1]["platforms"]["x"]["final_presses"])
        self.assertEqual(ends[0]["platforms"]["x"]["ref"], ends[1]["platforms"]["x"]["ref"])
        self.assertNotIn("PRIVATE-", (self.directory / "journal.jsonl").read_text())

    def test_bilibili_stage_order_around_fake_boundary(self):
        seen = []
        def upload(*args, **kwargs):
            seen.append(self.events("stage")[-1]["stage"])
            return {"bvid": "PRIVATE-id"}
        def verify(*args, **kwargs):
            seen.append(self.events("stage")[-1]["stage"])
            return {"state": "SUBMISSION_VERIFIED"}
        with patch.object(adapters, "bilibili_upload", return_value=["upload", "video"]), patch.object(adapters, "bili", side_effect=upload), patch.object(adapters, "verify", side_effect=verify):
            adapters.publish_bilibili({}, {"ai_generated": True, "visibility": "private", "account": "PRIVATE-owner"}, lambda _: None)
        self.assertEqual(["uploading", "reading back"], seen)

    def test_calls_do_not_record_arguments(self):
        with patch.object(adapters, "_bili", return_value={}):
            adapters.bili(["upload", "video", "PRIVATE-path", "--title", "PRIVATE-title"])
        self.assertEqual("upload", self.events("call")[-1]["action"])
        with self.assertRaises(core.VpubError):
            with journal.call("x", "browser", "publish"):
                raise core.VpubError("readback_failed", "PRIVATE-message")
        self.assertEqual("readback_failed", self.events("call")[-1]["error_code"])
        self.assertNotIn("PRIVATE-", (self.directory / "journal.jsonl").read_text())

    def test_prepress_order_timeout_and_no_repress(self):
        plan = {"video": str(self.root / "PRIVATE-video.mp4"), "video_sha256": "b" * 64,
                "confirmation": "c" * 64, "options": {"title": "PRIVATE-title", "account": "PRIVATE-account"}}
        tx = Transaction("x", "PRIVATE-identity", plan)
        calls = []
        async def click(**kwargs):
            records = self.events()
            marker = [r for r in records if r["type"] == "transition"][-1]
            self.assertEqual(("UNKNOWN", "submitting", 1), (marker["state"], marker["phase"], marker["final_presses"]))
            self.assertEqual("pressing", records[-1]["stage"])
            self.assertEqual("UNKNOWN", core.read_json(tx.path)["state"])
            calls.append(1)
            raise TimeoutError("PRIVATE-timeout")
        with patch("video_publisher.browser_backends.risk_check", new=AsyncMock()):
            with self.assertRaises(TimeoutError):
                asyncio.run(tx.submit(SimpleNamespace(url="https://private.invalid"), SimpleNamespace(click=click)))
            tx.save(phase="blocked", error={"code": "TimeoutError"})
            with self.assertRaises(core.VpubError):
                asyncio.run(tx.submit(SimpleNamespace(url=""), SimpleNamespace(click=click)))
        self.assertEqual([1], calls)
        self.assertEqual("TimeoutError", self.events("transition")[-1]["error_code"])
        resumed = Transaction("x", "PRIVATE-identity", plan, resume=True)
        resumed.save(phase="reading_back")
        self.assertEqual(1, self.events("transition")[-1]["final_presses"])
        self.assertNotIn("PRIVATE-", (self.directory / "journal.jsonl").read_text())

    def test_draft_evidence_precedes_second_press_and_each_selection_logged(self):
        video = self.root / "fake.mp4"
        video.write_bytes(b"fake")
        plan = {"video": str(video), "video_sha256": core.file_sha(video), "confirmation": "d"*64,
                "options": {"title": "T"}}
        tx = Transaction("youtube", "fixture", plan)
        tx.media()
        tx.media()
        self.assertEqual(2, len([r for r in self.events() if r.get("phase") == "selecting_media"]))
        tx.save(state="UNKNOWN", phase="blocked", final_submit_attempted=True, final_presses=1)
        tx.save(still_a_draft=True, phase="reading_back")
        button = SimpleNamespace(click=AsyncMock())
        with patch("video_publisher.browser_backends.risk_check", new=AsyncMock()):
            asyncio.run(tx.submit(SimpleNamespace(url=""), button, again=True))
        states = self.events("transition")
        self.assertTrue(states[-2]["still_a_draft"])
        self.assertEqual(2, states[-1]["final_presses"])


class DeletionJournalTests(unittest.TestCase):
    # Reuse only fixtures, without rediscovering the old test methods.
    setUp = deletion.WorkDeletionTests.setUp
    seed = deletion.WorkDeletionTests.seed
    code = deletion.WorkDeletionTests.code
    owned = deletion.WorkDeletionTests.owned
    deleted = deletion.WorkDeletionTests.deleted
    def test_delete_transition_and_stage_before_write_and_no_second_delete(self):
        key, _ = self.seed()
        confirmation = self.code(key)
        path = core.home() / "audit/journal.jsonl"
        def events():
            return [json.loads(s) for s in path.read_text().splitlines()]
        def fake(target, op, receipt_path):
            if op == "inspect":
                return self.owned(target)
            if op == "delete":
                deletion.state.begin_press(receipt_path, target)
                self.assertTrue(core.read_json(receipt_path)["delete_attempted"])
                self.assertEqual("pressing", events()[-1]["stage"])
                self.assertEqual("DELETE_PENDING", events()[-2]["state"])
            return self.deleted(target, platform_writes=1 if op == "delete" else 0)
        with patch.object(deletion.wd, "backend", side_effect=fake):
            deletion.wd.run(key, confirm=confirmation, person_approved=True)
            transitions = [r for r in events() if r.get("kind") == "deletion"]
            self.assertEqual(["DELETE_READY", "DELETE_PENDING", "DELETED_VERIFIED"], [r["state"] for r in transitions])
            count = len(events())
            deletion.wd.run(key, confirm=confirmation, person_approved=True)
            self.assertEqual(count, len(events()))
