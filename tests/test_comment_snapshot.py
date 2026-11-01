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
        self.change("delete", "c2", True, {"identity": OWNER, "text": "Mine"}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1, "gone_from_page": True}, after)
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
