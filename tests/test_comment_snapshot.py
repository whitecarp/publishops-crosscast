"""After a verified change, the next command can name that comment without listing them all again. It still checks the live page first."""
import os
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import comment_actions, operations
from video_publisher.core import VpubError, atomic_json, digest, home, read_json

OWNER = "UC" + "a" * 22
WORK = "dQw4w9WgXcQ"


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.path = home() / "operations/comments" / (digest(["youtube", WORK]) + ".json")
        atomic_json(self.path, {"identity": OWNER, "items": [{"id": "c1", "text": "Nice", "is_owner": False, "language_hint": "en"}, {"id": "c2", "text": "Mine", "is_owner": True}]})

    def rows(self):
        return {row["id"]: row for row in read_json(self.path)["items"]}


class ChangeTests(Base):
    def change(self, kind, comment, desired, before, press, after):
        with patch.object(comment_actions, "dispatch", side_effect=[before, press, after]):
            return comment_actions.mutate("youtube", WORK, comment, kind, desired)

    def test_an_edit_that_read_back_updates_the_words_so_it_can_be_restored_straight_away(self):
        result = self.change("edit", "c2", "New", {"identity": OWNER, "text": "Mine"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {"identity": OWNER, "text": "New"})
        self.assertEqual("COMMENT_VERIFIED", result["state"])
        self.assertEqual("New", self.rows()["c2"]["text"])
        result = self.change("edit", "c2", "Mine", {"identity": OWNER, "text": "New"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {"identity": OWNER, "text": "Mine"})
        self.assertEqual("COMMENT_VERIFIED", result["state"])

    def test_an_edit_that_did_not_read_back_changes_nothing_here(self):
        self.change("edit", "c2", "New", {"identity": OWNER, "text": "Mine"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {"identity": OWNER, "text": "Mine"})
        self.assertEqual("Mine", self.rows()["c2"]["text"])

    def test_a_delete_that_read_back_drops_the_row(self):
        after = {"identity": OWNER, "text": None, "deleted": None, "absent_from_loaded_list": True}
        self.change("delete", "c2", True, {"identity": OWNER, "text": "Mine"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1, "gone_from_page": True, "acknowledged": True}, after)
        self.assertNotIn("c2", self.rows())
        self.assertIn("c1", self.rows())

    def test_the_next_command_still_checks_the_live_page(self):
        self.change("edit", "c2", "New", {"identity": OWNER, "text": "Mine"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {"identity": OWNER, "text": "New"})
        with patch.object(comment_actions, "dispatch", return_value={"identity": OWNER, "text": "Changed again"}), self.assertRaises(VpubError) as caught:
            comment_actions.mutate("youtube", WORK, "c2", "edit", "Mine")
        self.assertEqual("comment_changed", caught.exception.code)

    def test_a_comment_it_has_not_seen_says_what_to_do(self):
        with self.assertRaises(VpubError) as caught:
            comment_actions.mutate("youtube", WORK, "unknown", "delete")
        self.assertEqual("comment_not_observed", caught.exception.code)
        self.assertIn("comments list", caught.exception.hint)


class TryingAgainTests(Base):
    UNPINNED = {"identity": OWNER, "text": "Mine", "pinned": False}
    PINNED = {"identity": OWNER, "text": "Mine", "pinned": True}

    def stuck(self, kind="pin", before=None, desired=True):
        before = before or self.UNPINNED
        with patch.object(comment_actions, "dispatch", side_effect=[before, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, before]):
            result = comment_actions.mutate("youtube", WORK, "c2", kind, desired)
        self.assertEqual("COMMENT_UNVERIFIED", result["state"])
        return result["key"]

    def test_a_pin_that_did_not_go_through_blocks_a_repeat_and_says_how_to_try_again(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch") as sent:
            result = comment_actions.mutate("youtube", WORK, "c2", "pin")
        sent.assert_not_called()
        self.assertEqual((key, True), (result["key"], result["reused"]))
        self.assertIn("resume-action", result["hint"])

    def test_trying_again_reads_first_then_presses_once(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch", side_effect=[self.UNPINNED, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, self.PINNED]) as sent:
            result = comment_actions.resume(key)
        self.assertEqual("COMMENT_VERIFIED", result["state"])
        self.assertEqual(3, sent.call_count)
        self.assertEqual("set", sent.call_args_list[1].args[1])

    def test_if_it_went_through_after_all_nothing_is_pressed(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch", return_value=self.PINNED) as sent:
            result = comment_actions.resume(key)
        self.assertEqual(("COMMENT_VERIFIED", 1), (result["state"], sent.call_count))

    def test_a_comment_that_changed_is_left_alone(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch", return_value={**self.UNPINNED, "text": "Edited"}) as sent, self.assertRaises(VpubError) as caught:
            comment_actions.resume(key)
        self.assertEqual("state_unavailable", caught.exception.code)
        self.assertEqual(1, sent.call_count)
        self.assertEqual("Stopped because the comment text differs.", caught.exception.hint)

    def test_a_pin_state_it_cannot_read_is_left_alone(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch", return_value={**self.UNPINNED, "pinned": None}) as sent, self.assertRaises(VpubError) as caught:
            comment_actions.resume(key)
        self.assertEqual("state_unavailable", caught.exception.code)
        self.assertEqual(1, sent.call_count)
        self.assertEqual("Stopped because its state could not be read.", caught.exception.hint)

    def test_the_reason_a_try_stopped_never_holds_words_or_ids(self):
        key = self.stuck()
        with patch.object(comment_actions, "dispatch", return_value={"identity": "someone-else", "text": "Private words", "pinned": True}), \
                patch.object(comment_actions, "matches", return_value=False), self.assertRaises(VpubError) as caught:
            comment_actions.resume(key)
        self.assertEqual("Stopped because the account differs, and the comment text differs, and its state differs from before.", caught.exception.hint)

    def test_a_like_can_be_tried_again_too(self):
        before = {"identity": OWNER, "text": "Mine", "liked": False}
        key = self.stuck("like", before)
        with patch.object(comment_actions, "dispatch", side_effect=[before, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {**before, "liked": True}]):
            self.assertEqual("COMMENT_VERIFIED", comment_actions.resume(key)["state"])

    def test_deleting_and_editing_are_looked_at_on_the_site_instead(self):
        for kind, desired in (("delete", True), ("edit", "New")):
            with self.subTest(kind=kind):
                before = {"identity": OWNER, "text": "Mine", "deleted": False}
                key = self.stuck(kind, before, desired)
                with patch.object(comment_actions, "dispatch") as sent, self.assertRaises(VpubError) as caught:
                    comment_actions.resume(key)
                self.assertEqual("resume_unavailable", caught.exception.code)
                sent.assert_not_called()
                (home() / "operations/comment-targets" / (digest(["youtube", OWNER, WORK, "c2"]) + ".json")).unlink()


class ReplyTests(Base):
    def reply(self, answer):
        with patch.object(operations, "backend", return_value=answer):
            return operations.reply("youtube", WORK, "c1", "Thanks", "en", written_by="person")

    def test_a_verified_reply_can_be_named_straight_away(self):
        self.reply({"state": "REPLY_VERIFIED", "platform_writes": 1, "reply_id": "r1"})
        row = self.rows()["r1"]
        self.assertEqual(("Thanks", True, "c1"), (row["text"], row["is_owner"], row["parent_id"]))
        target = comment_actions.target("youtube", WORK, "r1")
        self.assertEqual("r1", target["comment"]["id"])

    def test_an_unverified_reply_is_not_added(self):
        self.reply({"state": "REPLY_UNVERIFIED", "platform_writes": 1, "reply_id": None})
        self.assertNotIn("r1", self.rows())

    def test_verifying_later_adds_it_once(self):
        result = self.reply({"state": "REPLY_UNVERIFIED", "platform_writes": 1, "reply_id": None})
        for _ in range(2):
            with patch.object(operations, "backend", return_value={"state": "REPLY_VERIFIED", "platform_writes": 0, "reply_id": "r1"}):
                operations.verify_reply(result["reply_key"])
        self.assertEqual(1, [row["id"] for row in read_json(self.path)["items"]].count("r1"))


if __name__ == "__main__":
    unittest.main()
