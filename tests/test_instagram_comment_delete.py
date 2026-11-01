"""Deleting your own Instagram comment, in a real browser against a small copy of what a field check saw: a menu that appears
when the pointer is on the row, and a window with Delete and Cancel. Nothing here contacts Instagram, and this has not been tried on a real account."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import comment_actions
from video_publisher.comment_actions_browser import execute
from video_publisher.core import VpubError, atomic_json, digest, home, read_json

OWNER = "owner"
WORK = "AbC_12"


def row(number, text, menu=True, choices="Delete"):
    options = f'<div role="button" class="options" data-choices="{choices}"><svg width="20" height="20" aria-label="Comment Options"></svg></div>' if menu else ""
    return f'<div class="holder"><div data-crosscast-comment-row="{number}">{text}</div>{options}</div>'


PAGE = """<!doctype html><style>.options { display: none; } .holder { min-height: 30px; } .holder:hover .options { display: block; }</style><body><main>%s</main>
<script>
for (const menu of document.querySelectorAll('.options')) menu.onclick = () => {
  const window_ = document.createElement('div');
  window_.setAttribute('role', 'dialog');
  for (const word of [...menu.dataset.choices.split(','), 'Cancel']) {
    const button = document.createElement('button');
    button.textContent = word;
    if (word === 'Delete') button.onclick = () => { fetch('/write', {method: 'POST', body: menu.closest('.holder').innerText}); window_.remove(); menu.closest('.holder').remove(); };
    window_.append(button);
  }
  document.body.append(window_);
};
</script>"""


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.writes = []
        self.seen_at_send = None
        self.html = PAGE % (row(0, "Mine") + row(1, "Theirs"))
        self.receipt = str(Path(temp.name) / "receipt.json")
        atomic_json(self.receipt, {"state": "COMMENT_READY"})
        await self.page.route("**/*", self.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        if route.request.method == "POST":
            self.writes.append(route.request.post_data)
            self.seen_at_send = read_json(self.receipt)["state"]
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)

    def comment(self, number=0, text="Mine", mine=True):
        return {"id": str(100 + number), "text": text, "ui_index": number, "author": OWNER, "is_owner": mine}

    async def run_it(self, operation="set", comment=None, shown=None):
        comment = comment or self.comment()
        await self.page.goto(f"https://www.instagram.com/p/{WORK}/")
        request = {"comment": comment, "work_id": WORK, "identity": OWNER, "kind": "delete", "desired": True}
        rows = shown if shown is not None else [self.comment(), self.comment(1, "Theirs", False)]
        with patch("video_publisher.comment_actions_browser.open_work", new=AsyncMock()), patch("video_publisher.comment_actions_browser.comments", new=AsyncMock(return_value={"items": rows})):
            return await execute(self.page, "instagram", OWNER, {"request": request, "operation": operation, "receipt_path": self.receipt})


class DeleteTests(Base):
    async def test_asking_only_reads(self):
        self.assertEqual(False, (await self.run_it("status"))["deleted"])
        self.assertEqual(0, await self.page.get_by_role("dialog").count())
        self.assertEqual([], self.writes)

    async def test_it_records_the_attempt_then_presses_delete_once(self):
        result = await self.run_it()
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(1, len(self.writes))
        self.assertIn("Mine", self.writes[0])
        self.assertEqual("COMMENT_SUBMITTING", self.seen_at_send)
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (read_json(self.receipt)["state"], read_json(self.receipt)["platform_writes"]))

    async def test_a_viewers_comment_is_refused_before_any_menu_opens(self):
        with self.assertRaises(VpubError) as caught:
            await self.run_it(comment=self.comment(1, "Theirs", False))
        self.assertEqual("own_comment_required", caught.exception.code)
        self.assertEqual(0, await self.page.get_by_role("dialog").count())
        self.assertEqual([], self.writes)

    async def test_another_comments_menu_is_never_used(self):
        self.html = PAGE % (row(0, "Mine", menu=False) + row(1, "Theirs"))
        with self.assertRaises(VpubError) as caught:
            await self.run_it()
        self.assertEqual("layout_changed", caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_a_menu_with_no_delete_stops_it(self):
        self.html = PAGE % (row(0, "Mine", choices="Report") + row(1, "Theirs"))
        with self.assertRaises(VpubError) as caught:
            await self.run_it()
        self.assertEqual("layout_changed", caught.exception.code)
        self.assertEqual([], self.writes)
        self.assertEqual("COMMENT_READY", read_json(self.receipt)["state"])

    async def test_a_comment_that_is_gone_reads_as_deleted(self):
        result = await self.run_it("status", shown=[self.comment(1, "Theirs", False)])
        self.assertIs(True, result["deleted"])

    async def test_a_comment_whose_words_changed_stops_it(self):
        with self.assertRaises(VpubError) as caught:
            await self.run_it(shown=[{**self.comment(), "text": "Edited"}])
        self.assertEqual("comment_changed", caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_a_stop_holds_it_at_the_last_moment(self):
        atomic_json(home() / "STOP.json", {"reason": "test"})
        with self.assertRaises(VpubError) as caught:
            await self.run_it()
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.writes)


class CommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)

    def snapshot(self, mine):
        atomic_json(home() / "operations/comments" / (digest(["instagram", WORK]) + ".json"),
                    {"identity": OWNER, "items": [{"id": "100", "text": "Mine", "is_owner": mine}]})

    def test_deleting_your_own_goes_through_and_reads_back(self):
        self.snapshot(True)
        answers = [{"identity": OWNER, "text": "Mine", "deleted": False}, {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}, {"identity": OWNER, "text": None, "deleted": True}]
        with patch.object(comment_actions, "dispatch", side_effect=answers):
            result = comment_actions.mutate("instagram", WORK, "100", "delete")
        self.assertEqual("COMMENT_VERIFIED", result["state"])

    def test_a_viewers_comment_is_refused_up_front(self):
        self.snapshot(False)
        with patch.object(comment_actions, "dispatch") as sent, self.assertRaises(VpubError) as caught:
            comment_actions.mutate("instagram", WORK, "100", "delete")
        self.assertEqual("own_comment_required", caught.exception.code)
        sent.assert_not_called()

    def test_removing_a_viewers_comment_is_not_offered_yet(self):
        self.snapshot(False)
        with self.assertRaises(VpubError) as caught:
            comment_actions.mutate("instagram", WORK, "100", "remove")
        self.assertEqual("unsupported_operation", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
