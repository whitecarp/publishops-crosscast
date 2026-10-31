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
        ledger = Ledger()
        self.key = ledger.claim("youtube", "default", "fixture", {"user_action_episode": 1})
        ledger.save(self.key, "WAITING_FOR_USER", {"user_action_episode": 1})
        ledger.close()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_optional_default_and_remembered_off(self):
        self.assertFalse(mail.settings()["enabled"])
        self.assertIsNotNone(mail.onboarding()["question"])
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
            mail.claim(event["id"], "gmail_read_email")
        request = mail.claim(event["id"], mail.SEND_TOOLS["gmail"])
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
        mail.configure("outlook", "creator@example.com")
        mail.enqueue(self.key, "youtube", 1)
        ledger = Ledger()
        ledger.save(self.key, "AGENT_REPORTED_VERIFIED", {"user_action_episode": 1})
        ledger.close()
        self.assertEqual("CANCELLED", mail.pending()[0]["state"])

    def test_connector_results_distinguish_sent_accepted_and_unknown(self):
        cases = [("gmail", {"id": "actual-fixture-id"}, "SENT"),
                 ("outlook", {"result": None}, "ACCEPTED"),
                 ("gmail", {"draft_id": "draft"}, "UNKNOWN"),
                 ("outlook", {"error": "timeout", "result": None}, "UNKNOWN")]
        for episode, (provider, result, expected) in enumerate(cases, 2):
            ledger = Ledger()
            ledger.save(self.key, "WAITING_FOR_USER", {"user_action_episode": episode})
            ledger.close()
            mail.configure(provider, "creator@example.com")
            event = mail.enqueue(self.key, "youtube", episode)
            claim = mail.claim(event["id"], mail.SEND_TOOLS[provider])
            path = self.root / "response.json"
            atomic_json(path, {"tool_name": claim["tool_name"], "response": {"structuredContent": result}})
            receipt = mail.record(event["id"], claim["claim_token"], path)
            self.assertEqual(expected, receipt["state"])
            self.assertFalse(receipt["delivery_verified"])
            self.assertFalse(receipt["automatic_retry"])

    def claimed(self, provider, tool, episode):
        ledger = Ledger()
        ledger.save(self.key, "WAITING_FOR_USER", {"user_action_episode": episode})
        ledger.close()
        mail.configure(provider, "creator@example.com")
        event = mail.enqueue(self.key, "youtube", episode)
        return event, mail.claim(event["id"], tool)

    def test_any_host_send_tool_can_claim(self):
        event, claim = self.claimed("gmail", "mcp__claude_ai_Gmail__send_email", 2)
        self.assertEqual("mcp__claude_ai_Gmail__send_email", claim["tool_name"])
        self.assertIsNone(claim["arguments"])
        self.assertEqual({"to", "subject", "text", "html"}, set(claim["message"]))
        self.assertEqual("creator@example.com", claim["message"]["to"])
        self.assertNotIn("https://", json.dumps(claim["message"]))
        _, codex = self.claimed("outlook", mail.SEND_TOOLS["outlook"], 3)
        self.assertIsNotNone(codex["arguments"])

    def test_read_draft_and_other_provider_tools_cannot_claim(self):
        mail.configure("gmail", "creator@example.com")
        event = mail.enqueue(self.key, "youtube", 1)
        for tool, code in (("gmail_read_email", "send_tool_unavailable"), ("gmail_create_draft", "send_tool_unavailable"),
                           ("gmail send email", "send_tool_unavailable"), ("outlook_send_mail", "wrong_email_tool")):
            with self.assertRaises(VpubError) as e:
                mail.claim(event["id"], tool)
            self.assertEqual(code, e.exception.code)
        self.assertEqual("PENDING", mail.pending()[0]["state"])

    def test_record_needs_the_claimed_tool_and_reads_common_responses(self):
        cases = [({"messageId": "fixture-message"}, "SENT"),
                 ({"content": [{"type": "text", "text": '{"success": true}'}]}, "ACCEPTED"),
                 ({"status": "queued"}, "UNKNOWN"),
                 ({"isError": True, "content": [{"type": "text", "text": "quota"}]}, "UNKNOWN")]
        for episode, (response, expected) in enumerate(cases, 4):
            event, claim = self.claimed("gmail", "gmail_send_message", episode)
            path = self.root / "response.json"
            atomic_json(path, {"tool_name": "mcp__codex_apps__gmail_send_email", "response": response})
            with self.assertRaises(VpubError) as e:
                mail.record(event["id"], claim["claim_token"], path)
            self.assertEqual("wrong_email_tool", e.exception.code)
            atomic_json(path, {"tool_name": "gmail_send_message", "response": response})
            receipt = mail.record(event["id"], claim["claim_token"], path)
            self.assertEqual(expected, receipt["state"])
            self.assertEqual("gmail_send_message", receipt["tool_name"])
            self.assertFalse(receipt["delivery_verified"])

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
