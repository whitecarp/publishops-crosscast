"""Receipt checkpoint regression tests; no browser, network, or account access."""
from contextlib import nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, core


class PublishCheckpointDedupTests(unittest.TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="publish-checkpoint-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.start_patch(patch.dict(os.environ, VPUB_HOME=str(self.home)))
        self.build = {"source_digest": "a" * 64}
        self.start_patch(patch("video_publisher.audit.code_stamp", return_value=self.build))
        self.receipt = {
            "state": "SUBMISSION_VERIFIED", "method": "browser", "platform": "x",
            "identity": "fixture_owner", "id": "fixture_work", "final_submit_attempted": True,
        }
        self.options = {
            "account": "default", "method": "browser", "title": "fixture",
            "submission_mode": "publish", "content_language": "und",
            "expected_identity": "fixture_owner",
        }
        self.identity = {"logged_in": True, "identity": "fixture_owner", "account": "default"}
        self.plan = {
            "confirmation": "fixture_confirmation", "video_sha256": "fixture_hash",
            "video": str(self.home / "never-opened.mp4"), "platforms": {"x": self.options},
        }
        self.key = core.Ledger.key("x", "default", self.plan["video_sha256"])
        self.attempts, self.saved, self.closed = [], [], []
        self.fail_attempts = set()
        self.original_save, self.original_close = core.Ledger.save, core.Ledger.close

        def save(ledger, key, state, data):
            self.attempts.append(state)
            if len(self.attempts) in self.fail_attempts:
                raise OSError("synthetic checkpoint failure")
            self.original_save(ledger, key, state, data)
            self.saved.append(state)

        def close(ledger):
            self.closed.append(True)
            return self.original_close(ledger)

        self.start_patch(patch.object(core.Ledger, "save", save))
        self.start_patch(patch.object(core.Ledger, "close", close))
        self.start_patch(patch.object(cli, "publication_lock", lambda: nullcontext()))
        self.start_patch(patch.object(cli, "snapshot_plan", lambda plan, platform: deepcopy(plan)))
        self.start_patch(patch.object(cli, "now", lambda: "fixed_fixture_start"))
        self.start_patch(patch.object(adapters, "preflight", lambda *args: deepcopy(self.identity)))
        self.start_patch(patch.object(adapters, "browser_call", lambda *args, **kwargs: deepcopy(self.receipt)))

    def assert_json_equal(self, actual, expected):
        # Plain equality considers 1 == True; serialized JSON preserves the type.
        self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(expected, sort_keys=True))

    def publish(self, adapter=None):
        original = deepcopy(self.plan)
        context = patch.object(adapters, "publish", adapter) if adapter else nullcontext()
        with context:
            result = cli.publish_plan(deepcopy(self.plan), self.plan["confirmation"], reviewed=True)
        self.assertEqual(len(self.closed), 1, "The CLI finally must close its ledger")
        self.assert_json_equal(self.plan, original)
        ledger = core.Ledger()
        try:
            row = ledger.get(self.key)
        finally:
            self.original_close(ledger)
        return result, row

    def assert_record(self, result, row, receipt, failed=False):
        expected_record = {
            "code": self.build,
            "plan_hash": self.plan["confirmation"], "title": self.options["title"],
            "identity": self.identity, "options": self.options,
            "approved_plan": self.plan, "upload_plan": self.plan,
            "started_at": "fixed_fixture_start", "receipt": receipt,
        }
        expected_result = {"key": self.key, **receipt}
        if failed:
            expected_record["error"] = {"code": "backend_error", "message": "OSError"}
            expected_result.update(
                state="SUBMITTED_UNVERIFIED", code="backend_error",
                hint="Inspect/verify the original work; do not create another submission.",
            )
        self.assertEqual(row["state"], "SUBMITTED_UNVERIFIED" if failed else receipt["state"])
        self.assert_json_equal(row["data"], expected_record)
        self.assert_json_equal(result, {
            "all_submissions_verified": not failed and receipt["state"] == "SUBMISSION_VERIFIED",
            "results": {"x": expected_result},
        })

    def test_successful_browser_receipt_saved_once(self):
        # Uses actual imported adapters.publish with only browser transport mocked.
        result, row = self.publish()
        self.assertEqual((len(self.attempts), len(self.saved)), (1, 1))
        self.assert_record(result, row, self.receipt)

    def test_failed_first_save_preserves_error_recovery(self):
        self.fail_attempts = {1}
        result, row = self.publish()
        self.assertEqual((len(self.attempts), len(self.saved)), (2, 1))
        self.assert_record(result, row, self.receipt, failed=True)

    def test_final_fallback_when_adapter_did_not_checkpoint(self):
        result, row = self.publish(lambda platform, plan, options, checkpoint: deepcopy(self.receipt))
        self.assertEqual((len(self.attempts), len(self.saved)), (1, 1))
        self.assert_record(result, row, self.receipt)

    def test_in_place_nested_mutation_is_saved_again(self):
        def adapter(platform, plan, options, checkpoint):
            receipt = deepcopy(self.receipt)
            receipt["readback"] = {"verified": False}
            checkpoint(receipt)
            receipt["readback"]["verified"] = True
            receipt["new_field"] = "must_survive"
            return receipt

        result, row = self.publish(adapter)
        expected = {**self.receipt, "readback": {"verified": True}, "new_field": "must_survive"}
        self.assertEqual((len(self.attempts), len(self.saved)), (2, 2))
        self.assert_record(result, row, expected)

    def test_caught_failed_checkpoint_does_not_suppress_final_save(self):
        self.fail_attempts = {1}

        def adapter(platform, plan, options, checkpoint):
            receipt = deepcopy(self.receipt)
            try:
                checkpoint(receipt)
            except OSError:
                pass
            return receipt

        result, row = self.publish(adapter)
        self.assertEqual((len(self.attempts), len(self.saved)), (2, 1))
        self.assert_record(result, row, self.receipt)

    def test_integer_to_boolean_mutation_is_saved_again(self):
        def adapter(platform, plan, options, checkpoint):
            receipt = deepcopy(self.receipt)
            receipt["readback"] = 1
            checkpoint(receipt)
            receipt["readback"] = True
            return receipt

        result, row = self.publish(adapter)
        self.assertEqual((len(self.attempts), len(self.saved)), (2, 2))
        self.assertIs(row["data"]["receipt"]["readback"], True)
        self.assert_record(result, row, {**self.receipt, "readback": True})


if __name__ == "__main__":
    unittest.main()
