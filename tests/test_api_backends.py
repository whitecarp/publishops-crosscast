"""X and Instagram API routes against a fake HTTP layer. Offline; no platform is contacted."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import requests

from video_publisher import adapters, api_backends as api, cli
from video_publisher.core import Ledger, VpubError, atomic_json, load_plan


class Response:
    def __init__(self, data=None, status=200):
        self.status_code, self.data = status, data or {}
        self.content = b"{}"

    def json(self):
        return self.data


class FakeHttp:
    """Answers by URL suffix and records every write, the way a real server would see it."""

    def __init__(self):
        self.calls, self.fail = [], {}
        self.container_status = "FINISHED"
        self.bytes_transferred = 0

    def answer(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for suffix, result in self.fail.items():
            if url.endswith(suffix):
                if isinstance(result, Exception):
                    raise result
                return result
        if url.endswith("/users/me"):
            return Response({"data": {"id": "42", "username": "demo", "name": "Demo"}})
        if url.endswith("/media/upload/initialize"):
            return Response({"data": {"id": "m1", "media_key": "k"}})
        if url.endswith("/append"):
            return Response()
        if url.endswith("/finalize"):
            return Response({"data": {"processing_info": {"state": "pending", "check_after_secs": 0}}})
        if url.endswith("/media/upload"):
            return Response({"data": {"processing_info": {"state": "succeeded"}}})
        if url.endswith("/tweets"):
            return Response({"data": {"id": "t1"}})
        if "/tweets/" in url:
            return Response({"data": {"id": "t1", "author_id": "42", "attachments": {"media_keys": ["k"]}}})
        if url.endswith("/ig1/media"):
            return Response({"id": "c1"})
        if "rupload" in url:
            return Response({"success": True})
        if url.endswith("/c1"):
            state = self.container_status
            if kw.get("params", {}).get("fields") == "status_code,video_status" and state == "FINISHED":
                state = "IN_PROGRESS"
            return Response({"status_code": state,
                             "video_status": {"uploading_phase": {"bytes_transferred": self.bytes_transferred}}})
        if url.endswith("/media_publish"):
            return Response({"id": "r1"})
        if url.endswith("/ig1"):
            return Response({"id": "ig1", "username": "demo"})
        if url.endswith("/r1"):
            return Response({"id": "r1", "media_product_type": "REELS", "username": "demo", "permalink": "https://www.instagram.com/reel/r1/"})
        raise AssertionError("Unexpected request: " + url)

    def writes(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]


class ApiBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        self.env.start()
        self.http = FakeHttp()
        self.patches = [patch.object(api.http, "get", lambda url, **kw: self.http.answer("GET", url, **kw)),
                        patch.object(api.http, "post", lambda url, **kw: self.http.answer("POST", url, **kw)),
                        patch.object(time, "sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        self.video = self.root / "final.mp4"
        self.video.write_bytes(b"v" * (api.X_SEGMENT + 10))
        atomic_json(api.token_path("x", "default"), {"client": {"client_id": "a"}, "access_token": "SECRET", "refresh_token": "r", "expires_at": time.time() + 3600})
        atomic_json(api.token_path("instagram", "default"), {"access_token": "SECRET", "ig_user_id": "ig1"})
        self.saved = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.temp.cleanup()

    def plan(self, platform, ai_generated=True, method="api"):
        path = self.root / "publish.json"
        atomic_json(path, {"schema_version": 1, "video": str(self.video), "title": "T", "description": "D", "tags": ["a"],
                           "platforms": {platform: {"method": method, "visibility": "account", "ai_generated": ai_generated}}})
        return load_plan(path)

    def run_publish(self, platform, ai_generated=True, receipt=None):
        plan = self.plan(platform, ai_generated)
        return api.publish(platform, {**plan, "cover": None}, plan["platforms"][platform],
                           lambda r: self.saved.append(json.loads(json.dumps(r))), receipt)

    def test_x_uploads_every_segment_then_posts_once_and_verifies(self):
        result = self.run_publish("x")
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual("https://x.com/demo/status/t1", result["url"])
        self.assertEqual(2, len(self.http.writes("/append")))
        post = self.http.writes("/tweets")
        self.assertEqual(1, len(post))
        self.assertEqual({"media_ids": ["m1"]}, post[0][2]["json"]["media"])
        self.assertIn("AI-generated", post[0][2]["json"]["text"])
        self.assertEqual("POST_SUBMITTING", self.saved[-2]["state"])

    def test_x_resume_continues_the_same_media_object(self):
        result = self.run_publish("x", receipt={"method": "api", "state": "UPLOADING", "media_id": "m1", "media_key": "k", "next_segment": 1})
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual([], self.http.writes("/initialize"))
        self.assertEqual(1, len(self.http.writes("/append")))

    def test_x_uncertain_post_is_never_sent_again(self):
        self.http.fail["/tweets"] = requests.ConnectionError()
        result = self.run_publish("x")
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertEqual(1, len(self.http.writes("/tweets")))
        again = self.run_publish("x", receipt=self.saved[-1])
        self.assertEqual("SUBMITTED_UNVERIFIED", again["state"])
        self.assertEqual(1, len(self.http.writes("/tweets")))

    def test_x_server_error_on_post_is_uncertain(self):
        self.http.fail["/tweets"] = Response(status=503)
        self.assertEqual("SUBMITTED_UNVERIFIED", self.run_publish("x")["state"])

    def test_x_rejected_post_is_recorded_and_typed(self):
        self.http.fail["/tweets"] = Response(status=403)
        with self.assertRaises(VpubError) as e:
            self.run_publish("x")
        self.assertEqual("api_access_denied", e.exception.code)
        self.assertEqual("PLATFORM_REJECTED", self.saved[-1]["state"])

    def test_x_read_back_under_another_author_stays_unverified(self):
        self.http.fail["/tweets/t1"] = Response({"data": {"id": "t1", "author_id": "99", "attachments": {"media_keys": ["k"]}}})
        self.assertEqual("SUBMITTED_UNVERIFIED", self.run_publish("x")["state"])

    def test_x_refreshes_and_stores_a_rotated_token(self):
        atomic_json(api.token_path("x", "default"), {"client": {"client_id": "a"}, "access_token": "old", "refresh_token": "r", "expires_at": 0})
        self.http.fail["/oauth2/token"] = Response({"access_token": "new", "refresh_token": "r2", "expires_in": 7200})
        self.assertEqual({"Authorization": "Bearer new"}, api.x_headers("default"))
        self.assertEqual("r2", json.loads(api.token_path("x", "default").read_text(encoding="utf-8"))["refresh_token"])

    def test_errors_never_echo_tokens(self):
        self.http.fail["/users/me"] = Response(status=401)
        with self.assertRaises(VpubError) as e:
            api.x_status("default")
        self.assertEqual("auth_expired", e.exception.code)
        self.assertNotIn("SECRET", e.exception.message + e.exception.hint)

    def test_instagram_publishes_a_reel_and_verifies(self):
        result = self.run_publish("instagram")
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual("https://www.instagram.com/reel/r1/", result["url"])
        body = self.http.writes("/ig1/media")[0][2]["json"]
        self.assertEqual(("REELS", "resumable", True), (body["media_type"], body["upload_type"], body["is_ai_generated"]))
        upload = [c for c in self.http.calls if "rupload" in c[1]][0][2]["headers"]
        self.assertEqual("0", upload["offset"])
        self.assertEqual(1, len(self.http.writes("/media_publish")))

    def test_instagram_ordinary_video_has_no_ai_label(self):
        self.run_publish("instagram", ai_generated=False)
        body = self.http.writes("/ig1/media")[0][2]["json"]
        self.assertNotIn("is_ai_generated", body)
        self.assertNotIn("AI-generated", body["caption"])

    def test_instagram_resume_uses_the_acknowledged_offset(self):
        self.http.bytes_transferred = 100
        self.run_publish("instagram", receipt={"method": "api", "state": "UPLOADING", "container_id": "c1"})
        self.assertEqual([], self.http.writes("/ig1/media"))
        self.assertEqual("100", [c for c in self.http.calls if "rupload" in c[1]][0][2]["headers"]["offset"])

    def test_instagram_still_processing_pauses_without_publishing(self):
        self.http.container_status = "IN_PROGRESS"
        result = self.run_publish("instagram")
        self.assertEqual("UPLOAD_PAUSED", result["state"])
        self.assertEqual([], self.http.writes("/media_publish"))

    def test_instagram_failed_container_is_not_published(self):
        self.http.container_status = "ERROR"
        self.assertEqual("PLATFORM_REJECTED", self.run_publish("instagram")["state"])
        self.assertEqual([], self.http.writes("/media_publish"))

    def test_instagram_uncertain_publish_is_never_sent_again(self):
        self.http.fail["/media_publish"] = requests.Timeout()
        self.assertEqual("SUBMITTED_UNVERIFIED", self.run_publish("instagram")["state"])
        self.run_publish("instagram", receipt=self.saved[-1])
        self.assertEqual(1, len(self.http.writes("/media_publish")))

    def test_instagram_rejects_oversized_video_before_any_request(self):
        with patch.object(api, "IG_MAX_BYTES", 1):
            with self.assertRaises(VpubError) as e:
                self.run_publish("instagram")
        self.assertEqual("video_too_large", e.exception.code)
        self.assertEqual([], self.http.calls)

    def test_plan_rules(self):
        self.assertEqual("api", self.plan("x")["platforms"]["x"]["method"])
        path = self.root / "draft.json"
        atomic_json(path, {"schema_version": 1, "video": str(self.video), "title": "T", "description": "D", "tags": ["a"],
                           "platforms": {"x": {"method": "api", "visibility": "account", "ai_generated": True, "submission_mode": "draft_only"}}})
        with self.assertRaises(VpubError) as e:
            load_plan(path)
        self.assertEqual("invalid_submission_mode", e.exception.code)

    def test_default_routes_stay_browser(self):
        self.assertEqual("browser", adapters.default_method("x"))
        self.assertEqual("browser", adapters.default_method("instagram"))

    def test_cli_publish_and_resume_use_the_api_route(self):
        plan = self.plan("x")
        self.http.fail["/tweets"] = None
        del self.http.fail["/tweets"]
        self.http.fail["/finalize"] = Response(status=500)
        first = cli.publish_plan(plan, plan["confirmation"])["results"]["x"]
        self.assertEqual("FINALIZING", first["state"])
        del self.http.fail["/finalize"]
        args = cli.parser().parse_args(["resume", first["key"]])
        result = cli.execute(args)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(1, len(self.http.writes("/media/upload/initialize")))
        self.assertEqual(1, len(self.http.writes("/tweets")))
        ledger = Ledger()
        try:
            self.assertEqual("SUBMISSION_VERIFIED", ledger.get(first["key"])["state"])
        finally:
            ledger.close()

    def test_cli_repeated_resume_never_repeats_uncertain_publication(self):
        for platform, suffix in [("x", "/tweets"), ("instagram", "/media_publish")]:
            with self.subTest(platform=platform):
                self.http.fail[suffix] = requests.Timeout()
                plan = self.plan(platform)
                first = cli.publish_plan(plan, plan["confirmation"])["results"][platform]
                self.assertEqual("SUBMITTED_UNVERIFIED", first["state"])
                del self.http.fail[suffix]
                for _ in range(2):
                    result = cli.execute(cli.parser().parse_args(["resume", first["key"]]))
                    self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertEqual(1, len(self.http.writes(suffix)))

    def test_durable_work_id_survives_readback_failure_and_resume_only_reads(self):
        for platform, suffix, work_id in [("x", "/tweets/t1", "t1"), ("instagram", "/r1", "r1")]:
            with self.subTest(platform=platform):
                self.http.fail[suffix] = requests.ConnectionError()
                plan = self.plan(platform)
                first = cli.publish_plan(plan, plan["confirmation"])["results"][platform]
                self.assertEqual(work_id, first["id"])
                del self.http.fail[suffix]
                before = len([c for c in self.http.calls if c[0] == "POST"])
                result = cli.execute(cli.parser().parse_args(["resume", first["key"]]))
                self.assertEqual("SUBMISSION_VERIFIED", result["state"])
                self.assertEqual(before, len([c for c in self.http.calls if c[0] == "POST"]))

    def test_resume_after_api_account_change_makes_no_writes(self):
        for platform in ["x", "instagram"]:
            with self.subTest(platform=platform):
                receipt = {"state": "UPLOADING", "user_id": "someone-else", "identity": "other"}
                with self.assertRaises(VpubError) as caught:
                    self.run_publish(platform, receipt=receipt)
                self.assertEqual("account_mismatch", caught.exception.code)
                self.assertFalse(any(c[0] == "POST" for c in self.http.calls))

    def test_rejected_attempt_does_not_resume_a_publication(self):
        for platform in ["x", "instagram"]:
            with self.subTest(platform=platform):
                result = self.run_publish(platform, receipt={"state": "PLATFORM_REJECTED"})
                self.assertEqual("PLATFORM_REJECTED", result["state"])
                self.assertFalse(any(c[0] == "POST" for c in self.http.calls))

    def test_wrong_x_media_or_work_id_does_not_verify(self):
        for work_id, media_key in [("t1", "wrong"), ("other", "k")]:
            with self.subTest(work_id=work_id, media_key=media_key):
                self.http.fail["/tweets/t1"] = Response({"data": {"id": work_id, "author_id": "42", "attachments": {"media_keys": [media_key]}}})
                self.assertEqual("SUBMITTED_UNVERIFIED", self.run_publish("x")["state"])

    def test_x_processing_is_bounded_and_resume_does_not_refinalize(self):
        self.http.fail["/media/upload"] = Response({"data": {"processing_info": {"state": "pending"}}})
        with patch.object(api, "X_POLL_ATTEMPTS", 2):
            first = self.run_publish("x")
        self.assertEqual("UPLOAD_PAUSED", first["state"])
        self.assertEqual([], self.http.writes("/tweets"))
        del self.http.fail["/media/upload"]
        self.assertEqual("SUBMISSION_VERIFIED", self.run_publish("x", receipt=first)["state"])
        self.assertEqual(1, len(self.http.writes("/finalize")))

    def test_instagram_finished_container_never_resends_bytes(self):
        self.http.fail["/c1"] = Response({"status_code": "FINISHED"})
        result = self.run_publish("instagram", receipt={"state": "UPLOAD_PAUSED", "container_id": "c1", "transfer_started": True})
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertFalse(any("rupload" in c[1] for c in self.http.calls))

    def test_instagram_uncertain_byte_offset_stops_without_reupload(self):
        self.http.fail["/c1"] = Response({"status_code": "IN_PROGRESS"})
        with self.assertRaises(VpubError) as caught:
            self.run_publish("instagram", receipt={"state": "UPLOADING", "container_id": "c1", "transfer_started": True})
        self.assertEqual("upload_resume_unknown", caught.exception.code)
        self.assertFalse(any(c[0] == "POST" for c in self.http.calls))

    def test_instagram_wrong_work_id_does_not_verify(self):
        self.http.fail["/r1"] = Response({"id": "another", "username": "demo", "media_product_type": "REELS"})
        self.assertEqual("SUBMITTED_UNVERIFIED", self.run_publish("instagram")["state"])


if __name__ == "__main__":
    unittest.main()
