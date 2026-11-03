"""Linked mentions must survive title/tag edits; never flatten unknown rich text."""
import json
from copy import deepcopy
from test_free_features import OWNER, TEXT, WORK, Temporary
from video_publisher import edits, edits_bilibili
from video_publisher.core import VpubError, digest, home, read_json


MENTION = [{"type": 1, "raw_text": "Thanks ", "biz_id": ""},
           {"type": 2, "raw_text": "Example", "biz_id": "123"}]


class MentionEdits(Temporary):
    def setUp(self):
        super().setUp()
        self.bili.archive.update(desc="Thanks @Example ", desc_v2=deepcopy(MENTION))

    def run_edit(self, *, description=None, verify_only=False, tags=None):
        value = {**TEXT, "description": description if description is not None else "Thanks @Example "}
        if tags is not None:
            value["tags"] = tags
        self.marks = []
        return edits_bilibili.change("default", OWNER, {"id": WORK},
                                    edits.validate_patch("bilibili", value), self.marks.append, verify_only)

    def test_title_change_never_replaces_linked_description(self):
        # Model bili-cli build_edit: passing --desc drops desc_v2 unless separately supplied.
        original_apply = self.bili.apply
        def apply(args):
            original_apply(args)
            if "--desc" in args:
                self.bili.archive["desc_v2"] = None
        self.bili.apply = apply
        result = self.run_edit()
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual(MENTION, self.bili.archive["desc_v2"])
        self.assertNotIn("--desc", self.bili.writes()[0])
        self.assertNotIn("--desc-at", self.bili.writes()[0])
        self.assertEqual(1, len(self.bili.writes()))

    def test_tags_can_change_without_touching_mentions(self):
        self.bili.archive["title"] = TEXT["title"]
        result = self.run_edit(tags=["updated"])
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual(["updated"], self.bili.writes()[0][-1:])
        self.assertNotIn("--desc", self.bili.writes()[0])

    def test_replacing_linked_description_refuses_before_submit(self):
        with self.assertRaises(VpubError) as caught:
            self.run_edit(description="New description")
        self.assertEqual("description_mentions_require_browser", caught.exception.code)
        self.assertEqual([], self.bili.writes())
        self.assertEqual([], self.marks)

    def test_json_encoded_mentions_also_refuse(self):
        self.bili.archive["desc_v2"] = json.dumps(MENTION)
        with self.assertRaises(VpubError):
            self.run_edit(description="New description")
        self.assertEqual([], self.bili.writes())

    def test_unknown_or_missing_rich_text_never_flattens_possible_at(self):
        for value in (None, [], "", "bad JSON", {}, [{"type": 99}], [{"type": True}], [{"type": 1, "biz_id": "123"}],
                      [{"type": 1}], [{"type": 1, "raw_text": {}, "biz_id": []}],
                      [{"type": 1, "raw_text": "Partial description", "biz_id": ""}]):
            with self.subTest(value=value):
                self.bili.archive["desc_v2"] = value
                with self.assertRaises(VpubError):
                    self.run_edit(description="New description")
                self.assertEqual([], self.bili.writes())

    def test_plain_text_segments_can_be_replaced(self):
        self.bili.archive.update(desc="Plain text", desc_v2=[{"type": 1, "raw_text": "Plain text", "biz_id": ""}])
        self.assertEqual("EDIT_VERIFIED", self.run_edit(description="New text")["state"])
        self.assertIn("--desc", self.bili.writes()[0])

    def test_plain_description_without_structure_still_works(self):
        self.bili.archive.update(desc="Plain text", desc_v2=None)
        self.assertEqual("EDIT_VERIFIED", self.run_edit(description="New text")["state"])

    def test_dropped_mention_is_not_verified_even_if_words_match(self):
        original_apply = self.bili.apply
        def apply(args):
            original_apply(args)
            self.bili.archive["desc_v2"] = None
        self.bili.apply = apply
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", self.run_edit()["state"])
        self.assertEqual(1, len(self.bili.writes()))

    def test_verify_only_and_unchanged_never_submit(self):
        self.assertEqual("EDIT_MISMATCH", self.run_edit(description="Different", verify_only=True)["state"])
        self.bili.archive["title"] = TEXT["title"]
        result = self.run_edit()
        self.assertTrue(result["unchanged"])
        self.assertEqual([], self.bili.writes())

    def test_representation_change_does_not_hide_retained_mentions(self):
        original_apply = self.bili.apply
        def apply(args):
            original_apply(args)
            self.bili.archive["desc_v2"] = json.dumps(MENTION)
        self.bili.apply = apply
        self.assertEqual("EDIT_VERIFIED", self.run_edit()["state"])

    def test_later_verify_keeps_structure_evidence_without_another_save(self):
        value = edits.validate_patch("bilibili", {**TEXT, "description": "Thanks @Example "})
        result = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, False)
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.bili.archive["desc_v2"] = None
        checked = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, True)
        self.assertEqual("EDIT_MISMATCH", checked["state"])
        self.assertEqual(digest(MENTION), checked["preserved_description_sha256"])
        repeated = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, False)
        self.assertEqual("EDIT_MISMATCH", repeated["state"])
        self.assertEqual(digest(MENTION), repeated["preserved_description_sha256"])
        self.bili.archive["desc_v2"] = json.dumps(MENTION)
        restored = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, True)
        self.assertEqual("EDIT_VERIFIED", restored["state"])
        self.assertEqual(1, len(self.bili.writes()))

    def test_ambiguous_save_keeps_digest_and_verify_does_not_clear_hold(self):
        original_apply = self.bili.apply
        def apply(args):
            original_apply(args)
            self.bili.archive["desc_v2"] = None
        self.bili.apply = apply
        self.bili.fail_after = "backend_timeout"
        value = edits.validate_patch("bilibili", {**TEXT, "description": "Thanks @Example "})
        with self.assertRaises(VpubError):
            edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, False)
        record = read_json(home() / "edits" / (digest(["publication-key", value]) + ".json"))
        self.assertEqual("EDIT_SUBMITTING", record["state"])
        self.assertEqual(digest(MENTION), record["preserved_description_sha256"])
        self.assertNotIn("desc_v2", record)
        checked = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, True)
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", checked["state"])
        repeated = edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, False, person_approved=True)
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", repeated["state"])
        self.assertEqual(digest(MENTION), repeated["preserved_description_sha256"])
        with self.assertRaises(VpubError) as caught:
            edits.send("bilibili", "default", OWNER, {"id": WORK}, "publication-key", value, False, False)
        self.assertEqual("inspect_existing_edit", caught.exception.code)
        self.assertEqual(1, len(self.bili.writes()))
