"""Bilibili's 62003 means approved and held for a scheduled release; a field test saw it reported as a failure."""
import json
import subprocess
import unittest
from unittest.mock import patch

from video_publisher import adapters
from video_publisher.core import VpubError


def answer(error):
    return subprocess.CompletedProcess([], 0, json.dumps({"ok": False, "error": error}, ensure_ascii=False), "")


class NotPublicYetTests(unittest.TestCase):
    def call(self, error):
        with patch.object(adapters, "stop_check"), patch.object(adapters.subprocess, "run", return_value=answer(error)), \
                patch.object(adapters.safety, "pushed_back") as pushed:
            with self.assertRaises(VpubError) as caught:
                adapters.bili(["video", "info", "BV1test"])
        return caught.exception, pushed

    def test_approved_and_waiting_is_not_a_platform_objection(self):
        failure, pushed = self.call({"code": "api_error", "message": "62003：稿件已审核通过，等待发布中"})
        self.assertEqual("not_public_yet", failure.code)
        self.assertIn("Nothing is wrong", failure.message)
        pushed.assert_not_called()

    def test_a_length_still_read_as_zero_says_to_wait_and_keeps_its_code(self):
        # A field run set chapters right after upload and was told they were past a 0-second video; four minutes later they went through.
        failure, pushed = self.call({"code": "invalid_argument", "message": "章节 '第一章' 的时间超出视频时长 0 秒。"})
        self.assertEqual("invalid_argument", failure.code)
        self.assertIn("hasn't finished processing", failure.message)
        self.assertIn("few minutes", failure.hint)
        failure, pushed = self.call({"code": "invalid_argument", "message": "章节 '第一章' 的时间超出视频时长 60 秒。"})
        self.assertNotIn("hasn't finished processing", failure.message)

    def test_the_collection_limit_says_so_and_keeps_its_code(self):
        # A field run's account answered 20082 您创建的合集个数太多了, which read like a CrossCast fault.
        failure, pushed = self.call({"code": "api_error", "message": "20082：您创建的合集个数太多了"})
        self.assertEqual("api_error", failure.code)
        self.assertIn("as many collections as Bilibili allows", failure.message)

    def test_other_errors_and_lookalike_numbers_keep_their_code(self):
        for message in ("62002：稿件不可见", "code 620031", "请求错误"):
            with self.subTest(message=message):
                failure, pushed = self.call({"code": "api_error", "message": message})
                self.assertEqual("api_error", failure.code)
                pushed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
