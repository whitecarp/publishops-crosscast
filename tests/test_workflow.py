"""Offline contract tests. These do not establish live platform compatibility."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, native_browser
from video_publisher.core import (Ledger, VpubError, atomic_json, home, load_plan,
                                  publication_lock, read_json, stop_check)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        self.env.start()
        self.video = self.root / "complete.mp4"
        self.video.write_bytes(b"synthetic test fixture, not a playable upload")
        self.manifest = self.root / "publish.json"
        self.value = cli.template(self.video)
        self.value["platforms"]["youtube"]["category_id"] = "1"  # Explicit synthetic fixture choice.
        self.save()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def save(self):
        atomic_json(self.manifest, self.value)

    def plan(self, platforms=None):
        return load_plan(self.manifest, platforms)

    def code(self, expected, func, *args, **kwargs):
        with self.assertRaises(VpubError) as e:
            func(*args, **kwargs)
        self.assertEqual(expected, e.exception.code)

    def native_job(self, platform="youtube"):
        plan = self.plan([platform])
        result = cli.publish_plan(plan, plan["confirmation"])["results"][platform]
        self.assertEqual("WAITING_FOR_BROWSER", result["state"])
        return result["key"]

    def evidence(self, **updates):
        opts = self.plan(["youtube"])["platforms"]["youtube"]
        e = {k: opts[k] for k in ("title", "description", "tags", "visibility", "ai_generated", "made_for_kids", "category_id")}
        e.update(id="abcdefghijk", identity="UC" + "a" * 22,
                 page_url="https://studio.youtube.com/video/abcdefghijk/edit",
                 observation="Synthetic test only: expected fields visible in a test fixture.", outcome="verified")
        e.update(updates)
        path = self.root / "evidence.json"
        atomic_json(path, e)
        return path

    def test_changed_text_invalidates_confirmation(self):
        old = self.plan()
        self.value["description"] = "changed"
        self.save()
        new = self.plan()
        self.assertNotEqual(old["confirmation"], new["confirmation"])
        with patch.object(adapters, "publish") as upload:
            self.code("confirmation_required", cli.publish_plan, new, old["confirmation"])
            upload.assert_not_called()

    def test_douyin_native_identity_guard_before_upload(self):
        self.value["platforms"]["douyin"]["expected_identity"] = "123456789"
        self.save()
        key = self.native_job("douyin")
        self.code("wrong_account", native_browser.begin, key, "another-account")
        self.assertTrue(native_browser.request(key)["can_begin"])
        self.assertEqual("BROWSER_IN_PROGRESS", native_browser.begin(key, "123456789")["state"])
        self.value["platforms"]["douyin"]["method"] = "browser"
        self.save()
        self.assertEqual('123456789', self.plan(['douyin'])['platforms']['douyin']['expected_identity'])

    def test_instagram_caption_readback_includes_ai_disclosure(self):
        self.value["platforms"] = {"instagram": {"method": "native", "visibility": "account", "ai_generated": True,
                                                  "expected_identity": "demo_creator"}}
        self.save()
        opts = self.plan()["platforms"]["instagram"]
        self.assertIn("AI-generated", opts["post_text"])
        key = self.native_job("instagram")
        native_browser.begin(key, "demo_creator")
        evidence = {k: opts[k] for k in ("title", "description", "tags", "visibility", "ai_generated", "post_text")}
        evidence.update(id="Demo123", page_url="https://www.instagram.com/reel/Demo123/", identity="demo_creator",
                        outcome="verified", observation="Synthetic fixture, not live evidence.")
        path = self.root / "ig-evidence.json"
        atomic_json(path, {**evidence, "post_text": "missing disclosure"})
        self.code("metadata_mismatch", native_browser.record, key, path)
        atomic_json(path, evidence)
        self.assertEqual("AGENT_REPORTED_VERIFIED", native_browser.record(key, path)["state"])

    def test_changed_video_invalidates_confirmation(self):
        old = self.plan()
        self.video.write_bytes(b"new bytes")
        self.assertNotEqual(old["confirmation"], self.plan()["confirmation"])

    def test_draft_only_preview_and_saved_receipts_never_publish_or_reupload(self):
        for platform, identity, url in (("instagram", "demo_creator", "https://www.instagram.com/"),
                                        ("x", "demo_creator", "https://x.com/compose/post")):
            with self.subTest(platform=platform):
                self.value["platforms"] = {platform: {"method": "native", "visibility": "account", "ai_generated": True,
                    "expected_identity": identity, "submission_mode": "draft_only"}}
                self.save()
                plan = self.plan()
                key = self.native_job(platform)
                self.assertFalse(native_browser.request(key)["final_submission_allowed"])
                self.assertFalse(native_browser.begin(key, identity)["final_submission_allowed"])
                evidence = {"outcome": "draft", "identity": identity, "page_url": url,
                    "post_text": plan["platforms"][platform]["post_text"], "media_preview": True, "draft_saved": False,
                    "observation": "Synthetic draft fixture only; never a live upload."}
                path = self.root / "draft.json"
                for changes, code in (({"outcome": "verified"}, "draft_only"),
                                      ({"media_preview": False}, "invalid_draft_evidence"),
                                      ({"draft_saved": "yes"}, "invalid_draft_evidence"),
                                      ({"post_text": "different"}, "invalid_draft_evidence"),
                                      ({"id": "123"}, "invalid_draft_evidence"),
                                      ({"page_url": "https://example.com/"}, "invalid_draft_evidence"),
                                      ({"identity": "wrong"}, "wrong_account")):
                    atomic_json(path, {**evidence, **changes})
                    self.code(code, native_browser.record, key, path)
                atomic_json(path, evidence)
                preview = native_browser.record(key, path)
                self.assertEqual("PREVIEW_READY", preview["state"])
                self.assertFalse(preview["published"])
                atomic_json(path, {**evidence, "draft_saved": True})
                saved = native_browser.record(key, path)
                self.assertEqual("DRAFT_SAVED", saved["state"])
                self.assertFalse(saved["published"])
                self.assertEqual("SKIPPED_EXISTING", cli.publish_plan(plan, plan["confirmation"])["results"][platform]["state"])
                self.code("wrong_account", native_browser.authorize_publish, key, "wrong")
                snapshot = native_browser.request(key)["plan"]["video"]
                authorization = native_browser.authorize_publish(key, identity)
                self.assertTrue(authorization["final_submission_allowed"])
                self.assertFalse(authorization["can_begin"])
                self.assertEqual(0, authorization["platform_writes"])
                self.assertEqual(snapshot, native_browser.request(key)["plan"]["video"])
                self.code("authorization_not_allowed", native_browser.authorize_publish, key, identity)
                self.code("already_started", native_browser.begin, key, identity)

    def test_submission_mode_validation(self):
        self.value["platforms"] = {"x": {"method": "native", "visibility": "account", "ai_generated": True,
                                        "submission_mode": "private"}}
        self.save()
        self.code("invalid_submission_mode", self.plan)
        self.value["platforms"] = {"douyin": {"method": "native", "visibility": "private", "ai_generated": True,
                                             "submission_mode": "draft_only"}}
        self.save()
        self.code("invalid_manifest", self.plan)

    def test_init_draft_only_is_explicit_and_scoped(self):
        output = self.root / "draft-init.json"
        args = cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(output),
                                       "--platforms", "instagram,x", "--draft-only"])
        cli.execute(args)
        plan = load_plan(output)
        self.assertTrue(all(o["submission_mode"] == "draft_only" for o in plan["platforms"].values()))
        args.out = str(self.root / "invalid-init.json")
        args.platforms = "youtube,x"
        self.code("invalid_submission_mode", cli.execute, args)
        self.assertFalse(Path(args.out).exists())

    def test_retry_requires_rejection_and_fresh_matching_absence_evidence(self):
        self.value["platforms"] = {"instagram": {"method":"native", "visibility":"account", "ai_generated":True, "expected_identity":"demo"}}
        self.save()
        key=self.native_job("instagram")
        native_browser.begin(key,"demo")
        proof=self.root/"retry.json"
        atomic_json(proof,{"identity":"demo","page_url":"https://www.instagram.com/demo/","work_absent":True,"observation":"Synthetic profile fixture; matching work absent."})
        self.code("retry_not_allowed",native_browser.retry_rejected,key,"demo",proof)
        rejected=self.root/"rejected.json"
        atomic_json(rejected,{"outcome":"uncertain","identity":"demo","observation":"Synthetic uncertain attempt."})
        native_browser.record(key,rejected)
        self.code("retry_not_allowed",native_browser.retry_rejected,key,"demo",proof)
        atomic_json(rejected,{"outcome":"rejected","identity":"demo","observation":"Synthetic explicit rejection."})
        native_browser.record(key,rejected)
        self.code("wrong_account",native_browser.retry_rejected,key,"other",proof)
        good=read_json(proof)
        atomic_json(proof,{**good,"work_absent":False})
        self.code("invalid_retry_evidence",native_browser.retry_rejected,key,"demo",proof)
        atomic_json(proof,good)
        result=native_browser.retry_rejected(key,"demo",proof)
        self.assertEqual("WAITING_FOR_BROWSER",result["state"])
        self.assertEqual(0,result["platform_writes"])
        self.assertEqual("BROWSER_IN_PROGRESS",native_browser.begin(key,"demo")["state"])
        ledger=Ledger()
        try:
            self.assertEqual("rejected",ledger.get(key)["data"]["rejected_attempts"][0]["receipt"]["outcome"])
        finally:
            ledger.close()

    def test_subset_isolation(self):
        self.value["platforms"]["douyin"]["unsupported"] = True
        self.save()
        self.assertEqual(["youtube"], list(self.plan(["youtube"])["platforms"]))
        self.code("invalid_manifest", self.plan)

    def test_visibility_and_ai_declaration_required(self):
        del self.value["platforms"]["youtube"]["visibility"]
        self.save()
        self.code("invalid_visibility", self.plan, ["youtube"])
        self.value["platforms"]["youtube"]["visibility"] = "private"
        self.value["platforms"]["youtube"]["ai_generated"] = "true"
        self.save()
        self.code("invalid_declaration", self.plan, ["youtube"])

    def test_stop_file_even_malformed_blocks_upload(self):
        (home() / "STOP.json").write_text("broken json")
        self.code("stopped", stop_check)
        plan = self.plan()
        with patch.object(adapters, "publish") as upload:
            self.code("stopped", cli.publish_plan, plan, plan["confirmation"])
            upload.assert_not_called()

    def test_external_stop_file(self):
        external = self.root / "platform-stop.json"
        external.write_text("pause")
        atomic_json(home() / "config.json", {"external_stop_file": str(external)})
        self.code("stopped", stop_check)

    def test_explicit_bilibili_stop_preserved_while_youtube_allowed(self):
        external = self.root / "bilibili-stop.json"
        atomic_json(external, {"state": "STOP_ALL_BILIBILI_READ_WRITE", "reason": "rate_limited"})
        atomic_json(home() / "config.json", {"external_stop_file": str(external)})
        self.code("stopped", stop_check, "bilibili")
        self.code("stopped", adapters.auth_status, "bilibili", "default")
        stop_check("youtube")
        self.native_job("youtube")
        self.assertTrue(external.exists())

    def test_unknown_or_local_stop_cannot_be_narrowed(self):
        external = self.root / "external-stop.json"
        atomic_json(external, {"state": "NEW_UNKNOWN_STOP"})
        atomic_json(home() / "config.json", {"external_stop_file": str(external)})
        self.code("stopped", stop_check, "youtube")
        atomic_json(external, {"state": "STOP_ALL_BILIBILI_READ_WRITE"})
        atomic_json(home() / "STOP.json", {"state": "STOP_ALL_BILIBILI_READ_WRITE"})
        self.code("stopped", stop_check, "youtube")
        atomic_json(home() / "config.json", {"external_stop_file": str(home() / "STOP.json")})
        self.code("stopped", stop_check, "youtube")

    def test_concurrent_lock_refused(self):
        with publication_lock():
            self.code("busy", lambda: publication_lock().__enter__())
        self.assertFalse((home() / "publisher.lock").exists())

    def test_atomic_claim_same_video(self):
        a, b = Ledger(), Ledger()
        try:
            self.assertTrue(a.claim("youtube", "default", "sha", {}))
            self.assertIsNone(b.claim("youtube", "default", "sha", {}))
        finally:
            a.close()
            b.close()

    def test_unknown_attempt_never_retries(self):
        self.value["platforms"]["bilibili"]["method"] = "cli"
        self.save()
        plan = self.plan(["bilibili"])
        with patch.object(adapters, "preflight", return_value={"identity": "test"}), patch.object(adapters, "publish", side_effect=TimeoutError) as upload:
            first = cli.publish_plan(plan, plan["confirmation"])
            second = cli.publish_plan(plan, plan["confirmation"])
            self.assertEqual("UNKNOWN", first["results"]["bilibili"]["state"])
            self.assertEqual("SKIPPED_EXISTING", second["results"]["bilibili"]["state"])
            self.assertEqual(1, upload.call_count)

    def test_id_survives_verification_error(self):
        plan = self.plan(["bilibili"])
        def fail(platform, plan, opts, checkpoint):
            checkpoint({"state": "SUBMITTED", "id": "BV1234567890"})
            raise TimeoutError
        with patch.object(adapters, "preflight", return_value={}), patch.object(adapters, "publish", side_effect=fail):
            r = cli.publish_plan(plan, plan["confirmation"])["results"]["bilibili"]
            self.assertEqual("BV1234567890", r["id"])
            self.assertEqual("SUBMITTED_UNVERIFIED", r["state"])

    def test_worker_receipt_survives_worker_failure(self):
        plan = self.plan(["youtube"])
        atomic_json(home() / "browser-receipts" / (plan["confirmation"] + "-youtube.json"),
                    {"state": "SUBMITTED", "id": "abcdefghijk", "method": "browser"})
        with patch.object(adapters, "publish", side_effect=TimeoutError):
            r = cli.publish_plan(plan, plan["confirmation"])["results"]["youtube"]
        self.assertEqual("abcdefghijk", r["id"])
        self.assertEqual("SUBMITTED_UNVERIFIED", r["state"])

    def test_partial_failure_preserves_other_results(self):
        plan = self.plan()
        def publish(platform, plan, opts, checkpoint):
            if platform == "youtube":
                raise TimeoutError
            return {"state": "SUBMISSION_VERIFIED", "id": platform}
        with patch.object(adapters, "preflight", return_value={}), patch.object(adapters, "publish", side_effect=publish):
            r = cli.publish_plan(plan, plan["confirmation"])
        self.assertFalse(r["all_submissions_verified"])
        self.assertEqual("SUBMISSION_VERIFIED", r["results"]["bilibili"]["state"])
        self.assertEqual("SUBMISSION_VERIFIED", r["results"]["douyin"]["state"])

    def test_risk_stops_remaining_platforms(self):
        plan = self.plan()
        with patch.object(adapters, "preflight", side_effect=VpubError("captcha_required", "test")), patch.object(adapters, "publish") as upload:
            r = cli.publish_plan(plan, plan["confirmation"])
            upload.assert_not_called()
        self.assertTrue((home() / "STOP.json").exists())
        self.assertEqual("stopped", r["results"]["youtube"]["code"])

    def test_native_login_does_not_launch_or_claim_logged_in(self):
        with patch("subprocess.Popen") as launch:
            for platform in cli.PLATFORMS:
                r = adapters.auth_login(platform, "default", method="native")
                self.assertIsNone(r["logged_in"])
                self.assertEqual("NATIVE_BROWSER_REQUIRED", r["state"])
            launch.assert_not_called()

    def test_native_job_preserves_approved_snapshot(self):
        key = self.native_job()
        self.video.write_bytes(b"later render")
        job = native_browser.request(key)
        self.assertEqual(b"synthetic test fixture, not a playable upload", Path(job["plan"]["video"]).read_bytes())

    def test_native_begin_is_single_use(self):
        key = self.native_job()
        native_browser.begin(key, "UC" + "a" * 22)
        self.code("already_started", native_browser.begin, key, "UC" + "a" * 22)

    def test_native_wrong_channel_blocked_before_upload(self):
        self.value["platforms"]["youtube"]["expected_channel_id"] = "UC" + "b" * 22
        self.save()
        key = self.native_job()
        self.code("wrong_account", native_browser.begin, key, "UC" + "a" * 22)

    def test_native_modified_job_metadata_rejected(self):
        key = self.native_job()
        path = home() / "browser-jobs" / (key + ".json")
        job = read_json(path)
        job["options"]["visibility"] = "public"
        atomic_json(path, job)
        self.code("request_changed", native_browser.begin, key, "UC" + "a" * 22)

    def test_native_evidence_requires_begin(self):
        key = self.native_job()
        self.code("not_started", native_browser.record, key, self.evidence())

    def test_native_evidence_checks_account_url_and_metadata(self):
        key = self.native_job()
        native_browser.begin(key, "UC" + "a" * 22)
        self.code("wrong_account", native_browser.record, key, self.evidence(identity="other"))
        self.code("invalid_evidence", native_browser.record, key, self.evidence(page_url="https://example.com/abcdefghijk"))
        self.code("metadata_mismatch", native_browser.record, key, self.evidence(visibility="public"))
        self.code("invalid_evidence", native_browser.record, key, self.evidence(cookie="secret"))

    def test_native_agent_evidence_distinct_from_api_verification(self):
        key = self.native_job()
        native_browser.begin(key, "UC" + "a" * 22)
        r = native_browser.record(key, self.evidence())
        self.assertEqual("AGENT_REPORTED_VERIFIED", r["state"])
        self.assertEqual("host_agent_ui_observation", r["verification_source"])
        result = adapters.verify("youtube", "default", r)
        self.assertEqual("BROWSER_VERIFICATION_REQUIRED", result["state"])

    def test_receipt_cannot_replace_prior_video_id(self):
        key = self.native_job()
        native_browser.begin(key, "UC" + "a" * 22)
        native_browser.record(key, self.evidence())
        self.code("receipt_conflict", native_browser.record, key,
                  self.evidence(id="12345678901", page_url="https://studio.youtube.com/video/12345678901/edit"))

    def test_evidence_can_be_saved_after_stop(self):
        key = self.native_job()
        native_browser.begin(key, "UC" + "a" * 22)
        atomic_json(home() / "STOP.json", {"reason": "test"})
        self.assertEqual("AGENT_REPORTED_VERIFIED", native_browser.record(key, self.evidence())["state"])

    def test_all_five_manifest_and_instagram_audience(self):
        out = self.root / "all.json"
        cli.execute(cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(out), "--platforms", ",".join(cli.PLATFORMS)]))
        value = read_json(out)
        self.assertNotIn('category_id', value['platforms']['youtube'])
        value['platforms']['youtube']['category_id'] = '1'
        atomic_json(out, value)
        p = load_plan(out)
        self.assertEqual(5, len(p["platforms"]))
        self.assertEqual("account", p["platforms"]["instagram"]["visibility"])
        self.assertEqual({'bilibili':'cli','youtube':'native','douyin':'browser','instagram':'browser','x':'browser'},
                         {k:v['method'] for k,v in p['platforms'].items()})

    def test_cli_plan_never_calls_platform(self):
        with patch.object(adapters, "publish") as publish, patch.object(adapters, "auth_status") as status:
            r = cli.execute(cli.parser().parse_args(["publish", str(self.manifest), "--dry-run"]))
            self.assertEqual(0, r["platform_writes"])
            publish.assert_not_called()
            status.assert_not_called()

    def test_tiktok_is_suspended_in_selection_and_old_manifests(self):
        self.assertNotIn("tiktok", cli.PLATFORMS)
        self.assertIn("x", cli.PLATFORMS)
        self.assertIn("tiktok", cli.capabilities()["suspended_platforms"])
        self.code("platform_suspended", native_browser.login, "tiktok")
        self.code("platform_suspended", adapters.auth_login, "tiktok", "default")
        self.value["platforms"] = {"tiktok": {"method": "native"}}
        self.save()
        self.code("platform_suspended", self.plan)
        # Previously prepared plans may not bypass suspension either.
        old = {"platforms": {"tiktok": {}}, "confirmation": "old"}
        self.code("platform_suspended", cli.publish_plan, old, "old")

    def test_suspended_tiktok_pending_job_cannot_begin(self):
        ledger = Ledger()
        key = ledger.claim("tiktok", "default", "old-video", {"options": {"method": "native"}})
        ledger.save(key, "WAITING_FOR_BROWSER", {"options": {"method": "native"}})
        ledger.close()
        self.code("platform_suspended", native_browser.begin, key, "tester")
        ledger = Ledger()
        self.assertEqual("WAITING_FOR_BROWSER", ledger.get(key)["state"])
        ledger.close()

    def x_manifest(self):
        self.value.update(title="原創短片", description="Original short", tags=["AI"], content_language="zh-Hant")
        self.value["platforms"] = {"x": {"method": "native", "visibility": "account",
                                             "ai_generated": True, "expected_identity": "tester", "metadata_mode":"bilingual"}}
        self.save()

    def test_x_caption_visibility_and_methods(self):
        self.x_manifest()
        p = self.plan()
        self.assertEqual("原創短片\n\nOriginal short\n\n#AI\n\nAI 生成內容 / AI-generated", p["platforms"]["x"]["post_text"])
        self.value["platforms"]["x"]["visibility"] = "private"
        self.save()
        self.code("invalid_visibility", self.plan)
        self.value["platforms"]["x"]["visibility"] = "account"
        self.value["tags"] = ["two words"]
        self.save()
        self.code("invalid_tags", self.plan)
        self.value["tags"] = ["AI"]
        self.value["platforms"]["x"]["method"] = "cli"
        self.save()
        self.code("invalid_method", self.plan)

    def test_language_preserves_input_and_changes_only_disclosure(self):
        self.x_manifest()
        self.value.update(title="简体中文标题", description="简介原文", content_language="zh-Hans")
        self.save()
        opts = self.plan()["platforms"]["x"]
        self.assertEqual("简体中文标题", opts["title"])
        self.assertEqual("简介原文", opts["description"])
        self.assertIn("AI 生成内容 / AI-generated", opts["post_text"])
        self.assertNotIn("內容", opts["post_text"])
        self.value["platforms"]["x"]["content_language"] = "ja"
        self.value["platforms"]["x"]["title"] = "日本語の動画"
        self.save()
        self.assertEqual("日本語の動画", self.plan()["platforms"]["x"]["title"])
        self.assertTrue(self.plan()["platforms"]["x"]["post_text"].endswith("AI-generated"))
        self.value["platforms"]["x"]["content_language"] = "../../bad"
        self.save()
        self.code("invalid_language", self.plan)

    def test_metadata_revision_preserves_video_id_and_history(self):
        key = self.native_job()
        identity = "UC" + "a" * 22
        native_browser.begin(key, identity)
        native_browser.record(key, self.evidence())
        self.value.update(title="简体标题", content_language="zh-Hans")
        self.save()
        plan = self.plan(["youtube"])
        self.code("wrong_account", native_browser.revise, key, self.manifest, plan["confirmation"], "other")
        self.code("confirmation_required", native_browser.revise, key, self.manifest, "stale", identity)
        result = native_browser.revise(key, self.manifest, plan["confirmation"], identity)
        self.assertEqual("METADATA_EDIT_IN_PROGRESS", result["state"])
        self.assertEqual("abcdefghijk", result["receipt"]["id"])
        self.code("already_started", native_browser.begin, key, identity)
        self.assertEqual("AGENT_REPORTED_VERIFIED", native_browser.record(key, self.evidence())["state"])
        ledger = Ledger()
        self.assertEqual(1, len(ledger.rows()))
        self.assertEqual(1, len(ledger.get(key)["data"]["metadata_revisions"]))
        ledger.close()

    def test_metadata_revision_rejects_replacement_media_and_privacy(self):
        key = self.native_job()
        identity = "UC" + "a" * 22
        native_browser.begin(key, identity)
        self.value["platforms"]["youtube"]["visibility"] = "public"
        self.save()
        self.code("revision_scope_changed", native_browser.revise, key, self.manifest, self.plan(["youtube"])["confirmation"], identity)
        self.value["platforms"]["youtube"]["visibility"] = "private"
        self.video.write_bytes(b"different video")
        self.save()
        self.code("revision_media_changed", native_browser.revise, key, self.manifest, self.plan(["youtube"])["confirmation"], identity)

    def test_x_receipt_binds_author_id_and_exact_caption(self):
        self.x_manifest()
        key = self.native_job("x")
        self.code("wrong_account", native_browser.begin, key, "other")
        native_browser.begin(key, "tester")
        opts = self.plan()["platforms"]["x"]
        e = {k: opts[k] for k in ("title", "description", "tags", "visibility", "ai_generated", "post_text")}
        e.update(outcome="verified", id="123456789", identity="tester",
                 page_url="https://x.com/other/status/123456789", observation="Synthetic X fixture only.")
        path = self.root / "x-evidence.json"
        atomic_json(path, e)
        self.code("invalid_evidence", native_browser.record, key, path)
        e["page_url"] = "https://x.com/tester/status/123456789"
        e["post_text"] = "missing AI disclosure"
        atomic_json(path, e)
        self.code("metadata_mismatch", native_browser.record, key, path)
        e["post_text"] = opts["post_text"]
        atomic_json(path, e)
        r = native_browser.record(key, path)
        self.assertEqual("AGENT_REPORTED_VERIFIED", r["state"])
        p = self.plan()
        self.assertEqual("SKIPPED_EXISTING", cli.publish_plan(p, p["confirmation"])["results"]["x"]["state"])

    def test_interrupted_snapshot_copy_does_not_block_the_next_publish(self):
        plan = self.plan(["youtube"])
        partial = home() / "jobs" / plan["confirmation"] / "video.mp4"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"synthetic")  # What a copy cut off by Ctrl-C or a full disk leaves behind.
        result = cli.publish_plan(plan, plan["confirmation"])["results"]["youtube"]
        self.assertEqual("WAITING_FOR_BROWSER", result["state"])
        self.assertEqual(self.video.read_bytes(), partial.read_bytes())

    def test_snapshot_failure_keeps_earlier_results_and_leaves_no_claim(self):
        plan = self.plan(["bilibili", "youtube"])

        def publish(platform, safe_plan, opts, checkpoint):
            Path(safe_plan["video"]).unlink()
            self.video.write_bytes(b"re-rendered while publishing")
            return {"state": "SUBMISSION_VERIFIED", "id": "BV1234567890"}

        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), \
                patch.object(adapters, "publish", side_effect=publish):
            results = cli.publish_plan(plan, plan["confirmation"])["results"]
        self.assertEqual("SUBMISSION_VERIFIED", results["bilibili"]["state"])
        self.assertEqual("NOT_STARTED", results["youtube"]["state"])
        self.assertEqual("source_changed", results["youtube"]["code"])
        ledger = Ledger()
        self.assertEqual(["bilibili"], [row["platform"] for row in ledger.rows()])
        ledger.close()

    def test_missing_cover_snapshot_never_uses_up_the_attempt(self):
        cover = self.root / "portrait.jpg"
        cover.write_bytes(b"cover")
        self.value["covers"] = {"douyin": {"portrait": "portrait.jpg"}}
        self.save()
        plan = self.plan(["douyin"])
        cover.write_bytes(b"changed after approval")
        with patch.object(adapters, "publish") as upload:
            first = cli.publish_plan(plan, plan["confirmation"])["results"]["douyin"]
            upload.assert_not_called()
        self.assertEqual("NOT_STARTED", first["state"])
        self.assertEqual("source_changed", first["code"])
        ledger = Ledger()
        self.assertEqual([], ledger.rows())
        ledger.close()

    def test_refused_browser_lock_releases_the_claim_so_publish_can_run_again(self):
        self.value["platforms"]["douyin"]["method"] = "browser"
        self.save()
        plan = self.plan(["douyin"])
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), \
                patch.object(adapters, "publish", side_effect=VpubError("browser_busy", "Busy", "Wait.")):
            first = cli.publish_plan(plan, plan["confirmation"])["results"]["douyin"]
        self.assertEqual("NOT_STARTED", first["state"])
        self.assertEqual("browser_busy", first["code"])
        with patch.object(adapters, "preflight", return_value={"identity": "owner"}), \
                patch.object(adapters, "publish", return_value={"state": "SUBMISSION_VERIFIED", "id": "123456789"}) as upload:
            second = cli.publish_plan(plan, plan["confirmation"])["results"]["douyin"]
        self.assertEqual("SUBMISSION_VERIFIED", second["state"])
        upload.assert_called_once()

    def run_cli(self, *arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["vpub", *arguments]), patch.object(sys, "stdout", output), self.assertRaises(SystemExit) as stopped:
            cli.main()
        return stopped.exception.code, json.loads(output.getvalue())

    def test_ordinary_file_mistakes_name_the_problem(self):
        missing = str(self.root / "missing.json")
        code, result = self.run_cli("plan", missing)
        self.assertEqual(2, code)
        self.assertEqual("file_not_found", result["error"]["code"])
        self.assertIn(missing, result["error"]["message"])
        broken = self.root / "broken.json"
        broken.write_text('{"schema_version": 1,\n oops}')
        code, result = self.run_cli("plan", str(broken))
        self.assertEqual(2, code)
        self.assertEqual("invalid_file", result["error"]["code"])
        self.assertIn("line 2", result["error"]["message"])
        code, result = self.run_cli("read-all", "status", "a" * 32)
        self.assertEqual(2, code)
        self.assertEqual("run_missing", result["error"]["code"])

    def test_limits_the_browser_routes_enforce_fail_at_plan_time(self):
        self.value["platforms"] = {"x": {"method": "browser", "visibility": "account", "ai_generated": False, "title": "x" * 71}}
        self.save()
        self.code("invalid_title", self.plan)
        self.value["platforms"]["x"].update(method="native")
        self.save()
        self.assertEqual("native", self.plan()["platforms"]["x"]["method"])
        self.value["platforms"] = {"instagram": {"method": "browser", "visibility": "account", "ai_generated": False,
                                                 "description": "a" * 1999, "title": "t" * 100, "tags": [f"tag{i}" for i in range(30)]}}
        self.save()
        self.code("invalid_description", self.plan)
        self.value["platforms"] = {"douyin": {"method": "browser", "visibility": "private", "ai_generated": False,
                                              "description": "好" * 999, "tags": ["两字"]}}
        self.save()
        self.code("invalid_description", self.plan)


if __name__ == "__main__":
    unittest.main()
