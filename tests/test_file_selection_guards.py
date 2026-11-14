"""From a review: a stop arriving after a form is ready still has to hold a file back. The real routes run against fake pages, up to the moment a file would be chosen."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends as backends, browser_forms as forms, collections_douyin
from video_publisher.core import VpubError, atomic_json, home, read_json


class SelectionReached(Exception):
    """End the fake route at its first file selection, before any later step."""


class Guard:
    """The guard on a Douyin cover slot's press, as the real page answers when nothing else is on top: the press may go, and went through."""
    async def evaluate(self, script, *args):
        return {'shown': True, 'ready': True, 'passed': ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'], 'held': None, 'in_the_way': None}

    async def dispose(self):
        pass


class Locator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def locator(self, *args, **kwargs):
        return self

    filter = get_by_text = locator

    async def all(self):
        return [] if self.page.kind == 'chooser' else [self]

    async def is_visible(self):
        return True

    async def count(self):
        return 1

    async def get_attribute(self, name):
        return 'video/' if name == 'accept' else None

    async def wait_for(self, **kwargs):
        pass

    async def evaluate_all(self, script):
        return ['https://analytics.twitter.com/user/synthetic_owner/home']

    async def evaluate(self, script, *args, **kwargs):
        return False  # Douyin's description box holds no cursor, so nothing is pressed to close its suggestions.

    async def evaluate_handle(self, script, *args, **kwargs):
        return Guard()

    async def click(self, **kwargs):
        if self.page.kind != 'chooser':
            self.page.maybe_stop()

    async def fill(self, value):
        self.page.maybe_stop()

    async def set_input_files(self, path, **kwargs):
        self.page.selections += 1
        raise SelectionReached()

    set_files = set_input_files


class Mouse:
    """A Douyin cover slot is pressed with the pointer where it rests, as a click on it would press it."""
    def __init__(self, page):
        self.page = page

    async def move(self, x, y):
        pass

    async def down(self):
        self.page.maybe_stop()

    async def up(self):
        pass


class Page:
    def __init__(self, kind, stop_late):
        self.kind, self.stop_late, self.selections = kind, stop_late, 0
        self.mouse = Mouse(self)
        self.url = ('https://creator.douyin.com/creator-micro/content/publish'
                    if kind in {'douyin', 'collection'} else 'https://studio.x.com/library/1_2/edit')

    def locator(self, *args, **kwargs):
        return Locator(self)

    get_by_text = locator
    get_by_placeholder = locator

    async def goto(self, url, **kwargs):
        self.url = url

    async def wait_for_timeout(self, ms):
        pass

    async def evaluate(self, script, *args):
        # No hover state exists on this stand-in; the real STOP reader below
        # still decides whether a selected cover may reach the file input.
        return script == forms.DOUYIN_UNHOVERED

    def maybe_stop(self):
        if self.stop_late:
            atomic_json(home() / 'STOP.json', {'source': 'operator', 'reason': 'synthetic test'})

    def expect_file_chooser(self, **kwargs):
        page = self

        class Chooser:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            @property
            def value(self):
                async def ready():
                    # Check must be after the awaited chooser result, not just
                    # before opening it or before awaiting this result.
                    page.maybe_stop()
                    return Locator(page)
                return ready()

        return Chooser()


class FileSelectionGuardTests(unittest.IsolatedAsyncioTestCase):
    observations = []

    async def run_route(self, kind, stop_late):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VPUB_HOME': folder}):
            page = Page(kind, stop_late)
            plan = {'confirmation': 'synthetic', 'video_sha256': '0' * 64,
                    'cover': str(Path(folder) / 'not-read.png'),
                    'options': {'title': 'Synthetic', 'post_text': 'Synthetic',
                                'description': 'Synthetic', 'tags': []}}
            tx = backends.Transaction('douyin' if kind in {'douyin', 'collection'} else 'x', 'synthetic_owner', plan)
            tx.save(media_selected=True, upload_filename='synthetic.mp4', before_ids=[],
                    media_url='https://studio.x.com/library/1_2/edit' if kind == 'x' else None)
            outcome = 'returned'
            # The DOM warning detector is clear; the production guard and STOP
            # reader are not mocked. No browser, transport or media bytes exist.
            with patch.object(backends, 'risk_check', AsyncMock(return_value=None)), patch.object(forms, 'risk_check', AsyncMock(return_value=None)):
                try:
                    if kind == 'x':
                        await forms.publish_x(page, tx)
                    elif kind == 'douyin':
                        await forms.finish_douyin_form(page, tx, [])
                    elif kind == 'collection':
                        await collections_douyin.execute(page, 'synthetic_owner',
                            {'operation': 'create', 'title': 'Synthetic', 'cover': plan['cover']})
                    else:
                        await backends.upload_file(page, 'not-read.mp4',
                                                   trigger=Locator(page) if kind == 'chooser' else None)
                except SelectionReached:
                    outcome = 'selected'
                except VpubError as exc:
                    outcome = exc.code
            saved = read_json(tx.path)
            result = {'route': kind, 'stop_late': stop_late, 'outcome': outcome,
                      'selections': page.selections, 'stop_retained': (home() / 'STOP.json').exists(),
                      'media_intent_retained': saved['media_selected'],
                      'final_submit_attempted': saved['final_submit_attempted']}
            self.observations.append(result)
            self.assertTrue(result['media_intent_retained'])
            self.assertFalse(result['final_submit_attempted'])
            return result

    async def assert_stopped(self, kind):
        result = await self.run_route(kind, True)
        self.assertEqual('stopped', result['outcome'])
        self.assertEqual(0, result['selections'])
        self.assertTrue(result['stop_retained'])

    async def test_x_cover_stops_after_dialog_opens(self):
        await self.assert_stopped('x')

    async def test_douyin_cover_routes_stop_after_preparation(self):
        for kind in ('douyin', 'collection'):
            with self.subTest(route=kind):
                await self.assert_stopped(kind)

    async def test_chooser_stops_after_awaited_result(self):
        await self.assert_stopped('chooser')

    async def test_normal_selection_still_reaches_each_boundary_once(self):
        for kind in ('x', 'douyin', 'chooser', 'direct', 'collection'):
            with self.subTest(route=kind):
                result = await self.run_route(kind, False)
                self.assertEqual('selected', result['outcome'])
                self.assertEqual(1, result['selections'])
                self.assertFalse(result['stop_retained'])

    def test_every_file_reaches_a_page_through_the_one_checked_door(self):
        """The review found the video was checked and the covers were not, so no route gets its own way in."""
        for path in Path(backends.__file__).parent.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            count = source.count(".set_input_files(") + source.count(".set_files(")
            self.assertEqual(2 if path.name == "browser_backends.py" else 0, count, path.name)


if __name__ == '__main__':
    unittest.main()
