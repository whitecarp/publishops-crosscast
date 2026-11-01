"""Replies on Instagram and Douyin, driven in an invisible browser against small copies of their pages, and a reply caught between two attempts. Each copy follows what the code reads; nothing is contacted."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import operations as ops, operations_browser, safety, youtube_studio
from video_publisher.core import VpubError, atomic_json, digest, home, read_json, reply_pressing

OWNER = "owner"
WORK = "abcde12345"
DOUYIN_WORK = "7300000000000000001"
COMMENT = "111"
QUESTION = "这首歌叫什么？"
ANSWER = "测试曲目。"


class Pages(SharedBrowser):
    def prepare(self):
        self.sent = []
        self.receipt = self.root / "reply.json"
        atomic_json(self.receipt, {"state": "REPLY_READY"})

    async def refused(self, code, call=None):
        with self.assertRaises(VpubError) as caught:
            await (call or self.run_it)()
        self.assertEqual(code, caught.exception.code)
        await self.page.evaluate("1")
        self.assertEqual([], self.sent)


class InstagramTests(Pages):
    def prepare(self):
        super().prepare()
        self.comment_text = QUESTION
        self.extra = ""
        self.mention = "@viewer"
        self.identity = OWNER

    async def handle(self, route):
        if route.request.method == "POST":
            self.sent.append((json.loads(route.request.post_data)["text"], read_json(self.receipt)["state"]))
            return await self.json_at(route, {})
        if urlsplit(route.request.url).path.rstrip("/") not in {f"/p/{WORK}", f"/reel/{WORK}"}:
            return await self.page_at(route, "<main>Elsewhere</main>")
        await self.page_at(route, f"""<main><svg aria-label="More options" width="10" height="10"></svg>
          <div><a href="/viewer/">viewer</a><a href="/p/{WORK}/c/{COMMENT}/"><time datetime="2026-10-30T00:00:00Z">1h</time></a>
            <span dir="auto">{self.comment_text}</span><div role="button" onclick="document.querySelector('textarea').value = '{self.mention}'"><span>Reply</span></div></div>
          {self.extra}
          <textarea aria-label="Add a comment…"></textarea><div role="button" id="post"><span>Post</span></div></main>
          <script>document.getElementById('post').onclick = () => fetch('/send', {{method: 'POST', body: JSON.stringify({{text: document.querySelector('textarea').value}})}});</script>""")

    def payload(self, comment_text=QUESTION, **changes):
        return {"work_id": WORK, "operation": "reply", "reply_receipt_path": str(self.receipt),
                "reply_request": {"comment": {"id": COMMENT, "text": comment_text, "author": "viewer"}, "text": ANSWER, "expected_identity": OWNER, **changes}}

    async def run_it(self):
        return await operations_browser.execute(self.page, "instagram", self.identity, self.payload())

    async def test_it_marks_the_receipt_then_sends_once_under_the_exact_comment(self):
        result = await self.run_it()
        await self.page.evaluate("1")
        self.assertEqual(("REPLY_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([(f"@viewer {ANSWER}", "REPLY_SUBMITTING")], self.sent)
        saved = read_json(self.receipt)
        self.assertEqual(("REPLY_UNVERIFIED", f"@viewer {ANSWER}", [COMMENT]), (saved["state"], saved["submitted_text"], saved["before_reply_ids"]))

    async def test_a_viewer_writing_the_send_buttons_word_stops_it_rather_than_guessing(self):
        self.extra = '<div><a href="/other/">other</a><a href="/p/x/c/222/"><time datetime="2026-10-30T00:00:00Z">1h</time></a><span dir="auto">Post</span></div>'
        await self.refused("layout_changed")

    async def test_a_viewer_writing_the_reply_buttons_word_in_this_comment_stops_it(self):
        self.comment_text = "Reply"
        await self.refused("layout_changed", lambda: operations_browser.execute(self.page, "instagram", OWNER, self.payload("Reply")))

    async def test_a_comment_whose_words_changed_is_not_answered(self):
        self.comment_text = "Different words now"
        await self.refused("comment_changed")

    async def test_another_account_stops_before_anything(self):
        self.identity = "someone_else"
        await self.refused("wrong_account")

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped")
        self.assertEqual("REPLY_READY", read_json(self.receipt)["state"])

    async def test_the_platform_asking_for_a_check_stops_it_and_everything_after_it(self):
        self.extra = '<div role="dialog"><h2>Confirm it\'s you</h2></div>'
        await self.refused("human_verification_required")
        self.assertEqual("instagram", read_json(home() / "STOP.json")["platform"])

    async def test_checking_a_reply_reads_only(self):
        await self.run_it()
        await self.page.evaluate("1")
        saved = {**read_json(self.receipt), "at": "2026-01-01T00:00:00+00:00"}
        self.sent.clear()
        result = await operations_browser.execute(self.page, "instagram", OWNER, {**self.payload(), "operation": "verify_reply", "existing_reply": saved})
        self.assertEqual("REPLY_UNVERIFIED", result["state"])
        self.assertEqual([], self.sent)

    async def test_an_attempt_that_has_already_moved_on_is_not_sent_again(self):
        for state in ("REPLY_SUBMITTING", "REPLY_UNVERIFIED", "REPLY_VERIFIED", "REPLY_REJECTED"):
            with self.subTest(state=state):
                atomic_json(self.receipt, {"state": state})
                await self.refused("attempt_exists")
                self.assertEqual(state, read_json(self.receipt)["state"])


class DouyinCreatorTests(Pages):
    def prepare(self):
        super().prepare()
        self.rows = [self.row(COMMENT, QUESTION, "Viewer")]
        self.answer = {"status_code": 0, "comment": {"cid": "999", "aweme_id": DOUYIN_WORK, "text": ANSWER, "reply_id": COMMENT, "user": {"unique_id": OWNER}}}
        self.extra = ""

    def row(self, cid, text, nickname, **fields):
        return {"cid": cid, "aweme_id": DOUYIN_WORK, "text": text, "user": {"nickname": nickname, "unique_id": "viewer"}, "reply_id": "0", "reply_comment": [], **fields}

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.path.endswith("/comment/list/select/"):
            return await self.json_at(route, {"status_code": 0, "comments": self.rows})
        if route.request.method == "POST":
            self.sent.append((json.loads(route.request.post_data)["text"], read_json(self.receipt)["state"]))
            return await self.json_at(route, self.answer)
        rows = "".join(f"""<div class="comment-item-a"><div class="comment-main-a"><span class="username-a">{row['user']['nickname']}</span><div class="text-a">{row['text']}</div></div>
          <button onclick="this.parentElement.querySelector('.composer').hidden = false">回复</button>
          <div class="composer" hidden><div contenteditable="true"></div><button onclick="fetch('/aweme/v1/creator/comment/reply/', {{method: 'POST', body: JSON.stringify({{text: this.previousElementSibling.innerText}})}})">发送</button></div></div>""" for row in self.rows)
        await self.page_at(route, f"""<div onclick="fetch('/aweme/v1/creator/comment/list/select/')">评论管理</div>{rows}{self.extra}""")

    async def run_it(self):
        return await operations_browser.execute(self.page, "douyin", OWNER, {"work_id": DOUYIN_WORK, "operation": "reply", "reply_receipt_path": str(self.receipt),
                                                                             "reply_request": {"comment": {"id": COMMENT, "text": QUESTION}, "text": ANSWER, "expected_identity": OWNER}})

    async def test_it_marks_the_receipt_then_sends_once_through_creator_center(self):
        result = await self.run_it()
        self.assertEqual(("REPLY_UNVERIFIED", True, "999", 1), (result["state"], result["send_acknowledged"], result["reply_id"], result["platform_writes"]))
        self.assertEqual([(ANSWER, "REPLY_SUBMITTING")], self.sent)
        self.assertEqual(("REPLY_UNVERIFIED", "creator_center"), (read_json(self.receipt)["state"], read_json(self.receipt)["send_surface"]))

    async def test_a_refusal_from_douyin_is_kept_as_a_refusal_and_not_called_sent(self):
        self.answer = {"status_code": 8, "status_msg": "No."}
        result = await self.run_it()
        self.assertEqual(("REPLY_REJECTED", False), (result["state"], result["send_acknowledged"]))
        self.assertEqual("REPLY_REJECTED", read_json(self.receipt)["state"])

    async def test_an_answer_about_another_comment_does_not_count_as_this_reply(self):
        self.answer["comment"]["reply_id"] = "222"
        result = await self.run_it()
        self.assertEqual(("REPLY_UNVERIFIED", False), (result["state"], result["send_acknowledged"]))

    async def test_an_owner_reply_already_in_the_thread_stops_it(self):
        self.rows[0]["reply_comment"] = [{"user": {"unique_id": OWNER}}]
        await self.refused("already_replied")

    async def test_a_reply_to_a_reply_is_refused(self):
        self.rows[0]["reply_id"] = "555"
        await self.refused("reply_level_unsupported")

    async def test_two_people_with_one_name_and_one_comment_stop_it(self):
        self.rows.append(self.row("222", QUESTION, "Viewer"))
        await self.refused("comment_ambiguous")

    async def test_a_comment_whose_words_changed_is_not_answered(self):
        self.rows[0]["text"] = "Different words now"
        await self.refused("comment_changed")

    async def test_a_comment_on_another_work_is_not_answered(self):
        self.rows[0]["aweme_id"] = "7300000000000000002"
        await self.refused("comment_changed")

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped")
        self.assertEqual("REPLY_READY", read_json(self.receipt)["state"])

    async def test_the_platform_asking_for_a_check_stops_it(self):
        self.extra = "<h1>安全验证</h1>"
        await self.refused("human_verification_required")
        self.assertEqual("douyin", read_json(home() / "STOP.json")["platform"])

    async def test_an_attempt_that_has_already_moved_on_is_not_sent_again(self):
        atomic_json(self.receipt, {"state": "REPLY_UNVERIFIED"})
        await self.refused("attempt_exists")


class YouTubeTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_attempt_that_has_already_moved_on_is_not_sent_again(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "reply.json"
            atomic_json(receipt, {"state": "REPLY_UNVERIFIED"})
            box, send = AsyncMock(input_value=AsyncMock(return_value=ANSWER)), AsyncMock(is_enabled=AsyncMock(return_value=True))
            read = AsyncMock(return_value={"items": [{"id": COMMENT, "text": QUESTION, "parent_id": None}]})
            with patch.object(youtube_studio, "read_comments", read), patch.object(youtube_studio, "comment_box", AsyncMock(return_value=(box, send))), patch.object(youtube_studio, "risk_check", AsyncMock()), \
                    patch.object(youtube_studio, "stop_check"), self.assertRaises(VpubError) as caught:
                await youtube_studio.operations(MagicMock(), OWNER, {"operation": "reply", "work_id": "abcdefghijk", "reply_receipt_path": str(receipt),
                                                                     "reply_request": {"comment": {"id": COMMENT, "text": QUESTION}, "text": ANSWER, "expected_identity": OWNER}})
            self.assertEqual("attempt_exists", caught.exception.code)
            send.click.assert_not_called()


class TwoAttemptsTests(unittest.TestCase):
    """A command killed part way can go on running in the shared background browser, and a second attempt queues behind it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "operations/comments" / (digest(["x", "1234567890"]) + ".json"), {"identity": OWNER, "items": [
            {"id": COMMENT, "text": "Question", "author": "V", "author_id": "viewer", "is_owner": False, "owner_replied": False, "language_hint": "en"}]})
        self.sent = []

    def press(self, path):
        """The last steps a real worker takes for a reply, whichever platform it is on."""
        saved = reply_pressing(path)
        self.sent.append("reply")
        saved["state"] = "REPLY_UNVERIFIED"
        atomic_json(path, saved)
        return {"state": "REPLY_UNVERIFIED", "platform_writes": 1}

    def test_the_second_attempt_sends_nothing_when_the_first_got_there_while_it_queued(self):
        def killed(platform, action, work, reply_receipt_path, **options):
            raise KeyboardInterrupt  # The first command is killed here, and its work carries on in the session.

        with patch.object(ops, "backend", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        path = next((home() / "operations/replies").glob("*.json"))
        self.assertEqual("REPLY_READY", read_json(path)["state"])

        def second(platform, action, work, reply_receipt_path, **options):
            self.press(reply_receipt_path)  # The session finishes the first command.
            return self.press(reply_receipt_path)  # Then the second command's turn comes.

        with patch.object(ops, "backend", side_effect=second), self.assertRaises(VpubError) as caught:
            ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        self.assertEqual("attempt_exists", caught.exception.code)
        self.assertEqual(["reply"], self.sent)
        self.assertEqual("REPLY_UNVERIFIED", read_json(path)["state"])
        self.assertEqual(1, safety.status()["used_today"]["x"]["replies_per_day"])

    def killed_attempt(self):
        def killed(platform, action, work, reply_receipt_path, **options):
            raise KeyboardInterrupt
        with patch.object(ops, "backend", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        return next((home() / "operations/replies").glob("*.json"))

    def test_the_first_attempt_finishing_during_the_second_ones_look_still_goes_once(self):
        """From a review: the old command finished after the new one had read its receipt and before it wrote a fresh one over it, so the record of that press was lost and a second reply went."""
        path = self.killed_attempt()
        old = threading.Thread(target=lambda: self.press(path))
        reclaim = safety.reclaim

        def during_the_look(saved_path, saved):
            old.start()
            time.sleep(0.5)  # It is now at this receipt's door.
            return reclaim(saved_path, saved)

        def second(platform, action, work, reply_receipt_path, **options):
            old.join(30)  # The session finishes the first command before this one's turn comes.
            return self.press(reply_receipt_path)
        with patch.object(safety, "reclaim", side_effect=during_the_look), patch.object(ops, "backend", side_effect=second), self.assertRaises(VpubError) as caught:
            ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        self.assertEqual("attempt_exists", caught.exception.code)
        self.assertEqual(["reply"], self.sent)
        self.assertEqual("REPLY_UNVERIFIED", read_json(path)["state"])

    def test_a_first_attempt_already_pressed_is_kept_not_written_over(self):
        path = self.killed_attempt()
        self.press(path)
        with patch.object(ops, "backend") as send:
            result = ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        send.assert_not_called()
        self.assertTrue(result["reused"])
        self.assertEqual(["reply"], self.sent)

    def test_only_a_reply_still_waiting_can_be_pressed(self):
        path = self.killed_attempt()
        for state in ("REPLY_SUBMITTING", "REPLY_UNVERIFIED", "REPLY_VERIFIED", "REPLY_REJECTED", "REPLY_FAILED", None):
            with self.subTest(state=state):
                atomic_json(path, {"state": state})
                with self.assertRaises(VpubError) as caught:
                    self.press(path)
                self.assertEqual("attempt_exists", caught.exception.code)
        self.assertEqual([], self.sent)

    def test_a_failed_attempt_never_writes_over_a_press_that_got_in(self):
        """The command is told it failed in the same moment its still-running work presses. The press must not be lost."""
        path = self.killed_attempt()
        failing, started = [], threading.Event()
        old = threading.Thread(target=lambda: (started.wait(30), self.press(path)))

        def failed(platform, action, work, reply_receipt_path, **options):
            failing.append(True)
            raise TimeoutError
        real = ops.read_json

        def during_the_failure(where):
            saved = real(where)
            if failing and not started.is_set():
                started.set()
                time.sleep(0.5)  # It is now at this receipt's door.
            return saved
        old.start()
        with patch.object(ops, "backend", side_effect=failed), patch.object(ops, "read_json", side_effect=during_the_failure), self.assertRaises(TimeoutError):
            ops.reply("x", "1234567890", COMMENT, "Answer", "en")
        old.join(30)
        self.assertEqual(["reply"], self.sent)
        self.assertEqual("REPLY_UNVERIFIED", read_json(path)["state"])


class ManyAtOnceTests(unittest.TestCase):
    """Real processes all reaching the same receipt at the same moment: one gets through."""
    SCRIPT = (
        "import sys, time\n"
        "from video_publisher.core import VpubError, reply_pressing\n"
        "while time.time() < float(sys.argv[2]):\n    time.sleep(0.001)\n"
        "try:\n    reply_pressing(sys.argv[1]); print('pressed')\n"
        "except VpubError as caught:\n    print(caught.code)\n"
    )

    def test_one_of_many_processes_gets_to_press(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reply.json"
            atomic_json(path, {"state": "REPLY_READY"})
            start = str(time.time() + 3)
            env = {**os.environ, "VPUB_HOME": temp, "PYTHONPATH": os.pathsep.join(sys.path)}
            runs = [subprocess.Popen([sys.executable, "-c", self.SCRIPT, str(path), start], env=env, stdout=subprocess.PIPE, text=True) for _ in range(6)]
            answers = sorted(run.communicate(timeout=60)[0].strip() for run in runs)
        self.assertEqual(["attempt_exists"] * 5 + ["pressed"], answers)


if __name__ == "__main__":
    unittest.main()
