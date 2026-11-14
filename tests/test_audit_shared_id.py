"""C26 correlation only: isolated journals, no account/browser/platform calls."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_publisher import audit_journal as journal, audit_export as export, core


class SharedIdTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c26-offline-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, VPUB_HOME=str(self.root / "vpub"),
                         BILI_CLI_HOME=str(self.root / "bili"), ROUND2_HOME=str(self.root / "lab"))
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AWL_RUN_ID", None)
        self.addCleanup(setattr, journal, "RUN", None)
        journal.RUN = None

    def record(self):
        args = SimpleNamespace(command="publish")
        started = journal.command_start(args)
        run = journal.RUN
        journal.command_end(args, started, 2, "confirmation_required", {})
        return run

    def test_valid_id_survives_start_and_both_export_time_modes(self):
        os.environ["AWL_RUN_ID"] = "a" * 32
        run = self.record()
        self.assertNotEqual(run, os.environ["AWL_RUN_ID"])
        for mode in ("relative", "exact"):
            data = export.export_data(times=mode)
            self.assertTrue(data["integrity"]["chain_ok"])
            self.assertEqual(data["runs"][0]["awl_run_id"], "a" * 32)
            self.assertEqual(data["events"][0]["awl_run_id"], "a" * 32)
            self.assertEqual(data["runs"][0]["run"], run)
            self.assertEqual(data["runs"][0]["exit_code"], 2)

    def test_missing_id_is_explicitly_unbound(self):
        self.record()
        self.assertIsNone(export.export_data()["runs"][0]["awl_run_id"])

    def test_invalid_environment_never_enters_journal_or_export(self):
        for value in ("PRIVATE_CANARY_/user/token", "a" * 31, "a" * 33, "A" * 32, "a" * 32 + "\n", ""):
            with self.subTest(value=value):
                os.environ["AWL_RUN_ID"] = value
                self.record()
                raw = [json.loads(line) for line in (core.home() / "audit/journal.jsonl").read_text().splitlines()]
                self.assertIsNone(raw[-2]["awl_run_id"])
                self.assertTrue(all(r["awl_run_id"] is None for r in export.export_data()["runs"]))
                self.assertNotIn("PRIVATE_CANARY", json.dumps(raw))

    def test_each_command_samples_environment_without_reusing_prior_id(self):
        ids = []
        for value in ("a" * 32, "b" * 32, None):
            if value is None:
                os.environ.pop("AWL_RUN_ID")
            else:
                os.environ["AWL_RUN_ID"] = value
            ids.append(self.record())
        rows = {r["run"]: r for r in export.export_data()["runs"]}
        self.assertEqual([rows[r]["awl_run_id"] for r in ids], ["a" * 32, "b" * 32, None])

    def test_legacy_start_and_missing_start_are_not_backfilled_from_export_environment(self):
        journal.RUN = "c" * 32
        journal.append("command_start", lambda _: dict(command="publish", flags={}))
        journal.RUN = "d" * 32
        journal.append("command_end", lambda _: dict(exit_code=0, ok=True))
        os.environ["AWL_RUN_ID"] = "a" * 32
        self.assertTrue(all(r["awl_run_id"] is None for r in export.export_data()["runs"]))

    def test_projection_rejects_forged_id_types_and_private_text(self):
        for value in (True, [], {}, 123, "PRIVATE_CANARY", "a" * 32 + "\n"):
            clean = export.project_event(dict(type="command_start", awl_run_id=value), lambda _: None)
            self.assertIsNone(clean["awl_run_id"])
            self.assertNotIn("PRIVATE_CANARY", json.dumps(clean))

    def test_export_is_deterministic_and_does_not_modify_input(self):
        os.environ["AWL_RUN_ID"] = "e" * 32
        self.record()
        before = {p: p.read_bytes() for p in core.home().rglob("*") if p.is_file()}
        self.assertEqual(export.export_data(), export.export_data())
        self.assertEqual(before, {p: p.read_bytes() for p in core.home().rglob("*") if p.is_file()})
