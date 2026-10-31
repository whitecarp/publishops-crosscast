import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, native_browser, notifications as mail
from video_publisher.core import Ledger, VpubError, atomic_json, load_plan


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, VPUB_HOME=str(self.root / "runtime"))
        self.env.start()
        self.inventory = self.root / "inventory.json"
        self.set_connection("gmail", "creator@example.com")
        ledger = Ledger()
        self.key = ledger.claim("youtube", "default", "fixture", {"user_action_episode": 1})
        ledger.save(self.key, "WAITING_FOR_USER", {"user_action_episode": 1})
        ledger.close()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def set_connection(self, provider, address):
        atomic_json(self.inventory, {"tools": [mail.SEND_TOOLS[provider]],
                                   "plugins": {provider: {"installed": True}},
                                   "profiles": {provider: {"structuredContent": {"email": address}}}})

    def test_optional_default_and_remembered_off(self):
        self.assertFalse(mail.settings()["enabled"])
        self.assertIsNone(mail.onboarding()["question"])
        self.assertEqual(["gmail"], mail.onboarding()["supported_providers"])
        self.assertEqual("DISABLED", mail.enqueue(self.key, "youtube", 1)["state"])
        self.assertEqual([], mail.pending())
        mail.configure("off")
        self.assertIsNone(mail.onboarding()["question"])
        self.assertFalse(mail.onboarding()["blocks_publication"])

    def test_rejects_sms_and_header_injection(self):
        for provider, recipient in [("sms", "123"), ("gmail", "a@example.com\nBcc: b@example.com"), ("outlook", None)]:
            with self.assertRaises(VpubError):
                mail.configure(provider, recipient)

    def test_same_blocker_deduplicated_and_claim_is_single_use(self):
        mail.configure("gmail", "creator@example.com")
        event = mail.enqueue(self.key, "youtube", 1)
        self.assertEqual(event["id"], mail.enqueue(self.key, "youtube", 1)["id"])
        self.assertEqual(1, len(mail.pending()))
        with self.assertRaises(VpubError):
            mail.claim(event["id"], "gmail_read_email", self.inventory)
        request = mail.claim(event["id"], mail.SEND_TOOLS["gmail"], self.inventory)
        self.assertFalse(request["email_sent"])
        self.assertNotIn("https://", json.dumps(request["arguments"]))
        with self.assertRaises(VpubError):
            mail.claim(event["id"], mail.SEND_TOOLS["gmail"])

    def test_disabling_cancels_pending_and_never_redirects_it(self):
        mail.configure("gmail", "creator@example.com")
        event = mail.enqueue(self.key, "youtube", 1)
        mail.configure("off")
        self.assertEqual("CANCELLED", mail.pending()[0]["state"])
        with self.assertRaises(VpubError):
            mail.claim(event["id"], mail.SEND_TOOLS["gmail"])

    def test_resumed_publication_cancels_unsent_alert(self):
        mail.configure("gmail", "creator@example.com")
        mail.enqueue(self.key, "youtube", 1)
        ledger = Ledger()
        ledger.save(self.key, "AGENT_REPORTED_VERIFIED", {"user_action_episode": 1})
        ledger.close()
        self.assertEqual("CANCELLED", mail.pending()[0]["state"])

    def test_connector_results_distinguish_sent_and_unknown(self):
        cases = [("gmail", {"id": "actual-fixture-id"}, "SENT"),
                 ("gmail", {"result": None}, "UNKNOWN"),
                 ("gmail", {"draft_id": "draft"}, "UNKNOWN"),
                 ("gmail", {"error": "timeout", "id": "untrusted"}, "UNKNOWN")]
        for episode, (provider, result, expected) in enumerate(cases, 2):
            self.set_connection(provider, "creator@example.com")
            ledger = Ledger()
            ledger.save(self.key, "WAITING_FOR_USER", {"user_action_episode": episode})
            ledger.close()
            mail.configure(provider, "creator@example.com")
            event = mail.enqueue(self.key, "youtube", episode)
            claim = mail.claim(event["id"], mail.SEND_TOOLS[provider], self.inventory)
            path = self.root / "response.json"
            atomic_json(path, {"tool_name": claim["tool_name"], "response": {"structuredContent": result}})
            receipt = mail.record(event["id"], claim["claim_token"], path)
            self.assertEqual(expected, receipt["state"])
            self.assertFalse(receipt["delivery_verified"])
            self.assertFalse(receipt["automatic_retry"])

    def test_auto_detection_and_remembered_self_only_choice(self):
        for address, provider in [("Person@GMAIL.COM", "gmail"), ("a@googlemail.com", "gmail")]:
            result = mail.configure("auto", address)
            self.assertEqual(provider, result["provider"])
            self.assertEqual(address.casefold(), result["recipient"])
            self.assertTrue(result["self_only"])
        with self.assertRaises(VpubError):
            mail.configure("outlook", "a@gmail.com")

    def test_missing_installed_disconnected_and_wrong_account_are_distinct(self):
        address = "creator@gmail.com"
        self.assertEqual("NEEDS_DISCOVERY", mail.inspect_mailbox(address)["state"])
        for installed, state in [(False, "NEEDS_INSTALL"), (True, "NEEDS_CONNECTION")]:
            atomic_json(self.inventory, {"plugins": {"gmail": {"installed": installed}}})
            result = mail.inspect_mailbox(address, self.inventory)
            self.assertEqual(state, result["state"])
            self.assertFalse(result["blocks_publication"])
        self.set_connection("gmail", "other@gmail.com")
        self.assertEqual("ACCOUNT_MISMATCH", mail.inspect_mailbox(address, self.inventory)["state"])

    def test_qq_unknown_domain_and_spoofed_suffix_never_guess_a_provider(self):
        for address in ["person@gmail.com.example.org", "person@company.example"]:
            result = mail.inspect_mailbox(address)
            self.assertEqual("UNSUPPORTED_OR_UNRESOLVED", result["state"])
            with self.assertRaises(VpubError):
                mail.configure("auto", address)
        self.set_connection("gmail", "person@vip.qq.com")
        self.assertEqual("UNSUPPORTED_PROVIDER", mail.inspect_mailbox("person@vip.qq.com", self.inventory)["state"])
        with self.assertRaises(VpubError):
            mail.configure("gmail", "person@vip.qq.com", self.inventory)

    def test_test_is_idempotent_does_not_enable_alerts_or_fake_a_publication(self):
        before = mail.settings()
        event = mail.prepare_test("creator@example.com", "test-once", self.inventory)
        self.assertEqual(event, mail.prepare_test("creator@example.com", "test-once", self.inventory))
        self.assertEqual(before, mail.settings())
        self.assertEqual("PENDING", mail.pending()[0]["state"])
        claim = mail.claim(event["id"], mail.SEND_TOOLS["gmail"], self.inventory)
        self.assertIn("[CrossCast TEST]", claim["arguments"]["subject"])
        self.assertEqual("creator@example.com", claim["arguments"]["to"])
        self.assertEqual("CLAIMED", mail.prepare_test("creator@example.com", "test-once", self.inventory)["state"])
        with self.assertRaises(VpubError):
            mail.claim(event["id"], mail.SEND_TOOLS["gmail"], self.inventory)
        ledger = Ledger()
        try:
            self.assertEqual(1, len(ledger.rows()))
        finally:
            ledger.close()

    def test_account_switch_between_preparation_and_claim_blocks_send(self):
        event = mail.prepare_test("creator@example.com", "switch", self.inventory)
        self.set_connection("gmail", "other@example.com")
        with self.assertRaises(VpubError):
            mail.claim(event["id"], mail.SEND_TOOLS["gmail"], self.inventory)
        self.assertEqual("PENDING", mail.pending()[0]["state"])

    def test_unavailable_test_returns_setup_without_queueing(self):
        result = mail.prepare_test("person@gmail.com", "no-tools", self.root / "inventory.json")
        self.assertEqual("ACCOUNT_MISMATCH", result["state"])
        self.assertEqual([], mail.pending())

    def test_profile_error_and_invalid_inventory_cannot_claim(self):
        atomic_json(self.inventory, {"profiles": []})
        with self.assertRaises(VpubError):
            mail.inspect_mailbox("a@gmail.com", self.inventory)
        atomic_json(self.inventory, {"tools": [mail.SEND_TOOLS["gmail"]],
                                   "profiles": {"gmail": {"isError": True, "structuredContent": {"email": "a@gmail.com"}}}})
        self.assertEqual("NEEDS_CONNECTION", mail.inspect_mailbox("a@gmail.com", self.inventory)["state"])

    def test_cli_email_setup_and_test_arguments(self):
        args = cli.parser().parse_args(["notify", "configure", "--to", "a@gmail.com"])
        self.assertEqual("auto", args.provider)
        args = cli.parser().parse_args(["notify", "test", "--email", "creator@example.com", "--test-id", "cli-test", "--inventory", str(self.inventory)])
        self.assertEqual("PENDING", cli.execute(args)["state"])

    def test_unsupported_mailboxes_and_legacy_preference_do_not_send(self):
        self.assertNotIn("outlook", mail.SEND_TOOLS)
        for address in ["a@outlook.com", "a@hotmail.com", "a@qq.com", "a@163.com", "a@126.com", "a@yeah.net"]:
            self.assertEqual("UNSUPPORTED_PROVIDER", mail.inspect_mailbox(address)["state"])
            with self.assertRaises(VpubError):
                mail.configure("auto", address)
        with self.assertRaises(VpubError):
            mail.configure("smtp", "a@qq.com")
        from video_publisher.core import home
        atomic_json(home() / "notifications.json", {"configured": True, "enabled": True,
                                                   "provider": "outlook", "recipient": "a@outlook.com"})
        self.assertFalse(mail.settings()["enabled"])
        self.assertEqual("DISABLED", mail.enqueue(self.key, "youtube", 1)["state"])
        with mail.database() as db:
            db.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?)", ('legacy', self.key, 1, 'PENDING',
                       json.dumps({'provider': 'outlook', 'recipient': 'a@outlook.com'}), 'historical'))
        self.assertEqual('CANCELLED', mail.pending()[0]['state'])

    def test_native_wait_preserves_work_and_never_reuploads(self):
        video = self.root / "test.mp4"
        video.write_bytes(b"synthetic, not playable")
        manifest = self.root / "publish.json"
        value = cli.template(video)
        value['platforms']['youtube']['category_id'] = '1'
        atomic_json(manifest, value)
        plan = load_plan(manifest, ["youtube"])
        key = cli.publish_plan(plan, plan["confirmation"])["results"]["youtube"]["key"]
        identity = "UC" + "a" * 22
        native_browser.begin(key, identity)
        evidence = self.root / "evidence.json"
        atomic_json(evidence, {"identity": identity, "id": "abcdefghijk", "outcome": "action_required",
                              "page_url": "https://studio.youtube.com/video/abcdefghijk/edit",
                              "observation": "Synthetic official verification dialog."})
        result = native_browser.record(key, evidence)
        self.assertEqual("WAITING_FOR_USER", result["state"])
        self.assertEqual("DISABLED", result["notification"]["state"])
        self.assertTrue(native_browser.request(key)["resume_existing_attempt"])
        with self.assertRaises(VpubError):
            native_browser.begin(key, identity)
        self.assertEqual("SKIPPED_EXISTING", cli.publish_plan(plan, plan["confirmation"])["results"]["youtube"]["state"])
