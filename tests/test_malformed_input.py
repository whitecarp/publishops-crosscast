"""Assistants write these JSON files, so a wrong value must get a clear error, never a raw crash. Offline."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import chapters, cli, covers, deletion, edits, instagram_media, metadata, native_browser, notifications, operations
from video_publisher.core import VpubError, atomic_json, load_plan

BAD = [None, 5, True, [], {}, "", "\x00", ["a"], [None], [{}], {"a": None}]
OPTIONS = ["account", "title", "description", "tags", "visibility", "ai_generated", "schedule", "content_language", "metadata_language",
           "metadata_mode", "chapters", "duration_seconds", "tid", "expected_uid", "method", "category_id", "made_for_kids",
           "expected_channel_id", "playlist_ids", "playlist_titles", "expected_identity", "submission_mode"]


class MalformedInputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        (self.root / "clip.mp4").write_bytes(b"synthetic")
        (self.root / "cover.jpg").write_bytes(b"synthetic")
        self.base = cli.template(self.root / "clip.mp4", "en")
        self.base.update(cover="cover.jpg", tags=["tag"], covers={"douyin": {"portrait": "cover.jpg"}})
        self.base["platforms"]["youtube"]["category_id"] = "22"
        for platform in ("x", "instagram"):
            self.base["platforms"][platform] = {"account": "default", "method": "browser", "visibility": "account", "ai_generated": True}
        self.manifest = self.root / "publish.json"
        self.input = self.root / "input.json"
        self.crashes = []
        atomic_json(self.manifest, self.base)
        load_plan(self.manifest)

    def tearDown(self):
        self.assertEqual([], self.crashes[:10])

    def attempt(self, label, func):
        try:
            func()
        except VpubError:
            pass
        except Exception as exc:  # Anything else reaches the user as internal_error.
            self.crashes.append(f"{label}: {type(exc).__name__}: {exc}"[:200])

    def plan_with(self, label, changed, platforms=None):
        atomic_json(self.manifest, changed)
        self.attempt(label, lambda: load_plan(self.manifest, platforms))

    def test_manifest(self):
        for value in BAD:
            self.plan_with(f"file={value!r}", value)
            for field in ("schema_version", "video", "cover", "covers", "title", "description", "tags", "platforms", "content_language"):
                self.plan_with(f"{field}={value!r}", {**self.base, field: value})
            for platform in self.base["platforms"]:
                changed = copy.deepcopy(self.base)
                changed["platforms"][platform] = value
                self.plan_with(f"{platform}={value!r}", changed, [platform])
                for field in OPTIONS:
                    changed = copy.deepcopy(self.base)
                    changed["platforms"][platform][field] = value
                    self.plan_with(f"{platform}.{field}={value!r}", changed, [platform])
            self.plan_with(f"covers.douyin={value!r}", {**self.base, "covers": {"douyin": value}})
            self.plan_with(f"covers.douyin.portrait={value!r}", {**self.base, "covers": {"douyin": {"portrait": value}}})

    def test_covers_and_instagram_preparation(self):
        for value in BAD:
            for shape in ({"douyin": value}, {"douyin": {"portrait": value}}, value):
                atomic_json(self.manifest, {**self.base, "covers": shape})
                self.attempt(f"covers.prepare {shape!r}", lambda: covers.prepare(self.manifest, self.root / "cover.jpg", self.root / "out", self.root / "covers.json", self.root / "cover.jpg"))
                self.attempt(f"instagram.prepare {shape!r}", lambda: instagram_media.prepare(self.manifest, self.root / "ig.json"))

    def test_chapters(self):
        for value in BAD:
            for platform in ("douyin", "youtube", "bilibili"):
                self.attempt(f"chapters {value!r}", lambda: chapters.validate(value, platform, "native", 100))
                self.attempt(f"chapter item {value!r}", lambda: chapters.validate([value, value], platform, "native", 100))
                self.attempt(f"duration {value!r}", lambda: chapters.validate([{"start_seconds": 0, "title": "a"}], platform, "native", value))
                self.attempt(f"start {value!r}", lambda: chapters.validate([{"start_seconds": value, "title": "a"}], platform, "native", 100))
                self.attempt(f"title {value!r}", lambda: chapters.validate([{"start_seconds": 0, "title": value}], platform, "native", 100))

    def test_edit_patch(self):
        good = {"expected_identity": "owner", "title": "T", "description": "D", "content_language": "en", "metadata_language": "en", "metadata_mode": "target_only"}
        for platform in ("youtube", "instagram", "x", "douyin"):
            for value in BAD:
                self.attempt(f"patch {value!r}", lambda: edits.validate_patch(platform, value))
                for field in (*good, "portrait", "chapters", "duration_seconds"):
                    self.attempt(f"patch.{field}={value!r}", lambda: edits.validate_patch(platform, {**good, field: value}))

    def test_metadata_request_and_apply(self):
        request, output = self.root / "request.json", self.root / "out.json"
        job = metadata.request(self.manifest)
        atomic_json(request, job)
        valid = {"request_id": job["request_id"], "platforms": {p: {"title": "T", "description": "D", "tags": ["a"], "metadata_language": t["language"], "metadata_mode": t["mode"]}
                                                                for p, t in job["targets"].items()}}

        def apply(label, response):
            atomic_json(self.input, response)
            self.attempt(label, lambda: metadata.apply(self.manifest, request, self.input, output))
            output.unlink(missing_ok=True)

        for value in BAD:
            apply(f"response={value!r}", value)
            apply(f"platforms={value!r}", {**valid, "platforms": value})
            apply(f"request_id={value!r}", {**valid, "request_id": value})
            for platform in ("youtube", "douyin"):
                changed = copy.deepcopy(valid)
                changed["platforms"][platform] = value
                apply(f"{platform}={value!r}", changed)
                for field in ("title", "description", "tags", "metadata_language", "metadata_mode", "description_parts", "category_id", "chapters"):
                    changed = copy.deepcopy(valid)
                    changed["platforms"][platform][field] = value
                    apply(f"{platform}.{field}={value!r}", changed)
        for value in BAD:
            atomic_json(self.manifest, value)
            self.attempt(f"metadata.request file={value!r}", lambda: metadata.request(self.manifest))
            atomic_json(self.manifest, {**self.base, "platforms": value})
            self.attempt(f"metadata.request platforms={value!r}", lambda: metadata.request(self.manifest))
            atomic_json(self.manifest, {**self.base, "platforms": {"youtube": value}})
            self.attempt(f"metadata.request youtube={value!r}", lambda: metadata.request(self.manifest))

    def test_browser_and_deletion_evidence(self):
        plan = load_plan(self.manifest, ["youtube"])
        key = cli.publish_plan(plan, plan["confirmation"])["results"]["youtube"]["key"]
        identity = "UC" + "a" * 22
        native_browser.begin(key, identity)
        options = plan["platforms"]["youtube"]
        good = {k: options[k] for k in ("title", "description", "tags", "visibility", "ai_generated", "made_for_kids", "category_id")}
        good.update(id="abcdefghijk", identity=identity, page_url="https://studio.youtube.com/video/abcdefghijk/edit", observation="Synthetic.")
        for value in BAD:
            atomic_json(self.input, value)
            self.attempt(f"evidence={value!r}", lambda: native_browser.record(key, self.input))
            self.attempt(f"deletion={value!r}", lambda: deletion.record(key, self.input))
            self.attempt(f"retry={value!r}", lambda: native_browser.retry_rejected(key, identity, self.input))
            for outcome in ("uncertain", "draft", "action_required", "rejected"):
                for field in ("outcome", "id", "page_url", "identity", "observation", "post_text", "media_preview", "draft_saved"):
                    atomic_json(self.input, {**good, "outcome": outcome, field: value})
                    self.attempt(f"{outcome}.{field}={value!r}", lambda: native_browser.record(key, self.input))

    def test_report_notes_and_email_inventory(self):
        for value in BAD:
            for notes in (value, [value], [{"text": value, "source": "https://example.com"}], [{"text": "a", "source": value}]):
                atomic_json(self.input, notes)
                self.attempt(f"notes={notes!r}", lambda: operations.report(None, "en", str(self.input)))
            for inventory in (value, {"tools": value}, {"plugins": value}, {"profiles": value}, {"plugins": {"gmail": value}},
                              {"profiles": {"gmail": value}}, {"profiles": {"gmail": {"content": value}}}):
                atomic_json(self.input, inventory)
                self.attempt(f"inventory={inventory!r}", lambda: notifications.inspect_mailbox("a@gmail.com", self.input))
            self.attempt(f"email={value!r}", lambda: notifications.email_address(value))


class ResumeAfterCleanupTests(unittest.TestCase):
    def test_a_removed_copy_is_explained_and_nothing_is_uploaded(self):
        with self.assertRaises(VpubError) as e:
            cli.check_upload_copy({"video": str(Path(tempfile.gettempdir()) / "no-such-vpub-copy.mp4"), "video_sha256": "x"})
        self.assertEqual("snapshot_removed", e.exception.code)
        self.assertIn("verify", e.exception.hint)


if __name__ == "__main__":
    unittest.main()
