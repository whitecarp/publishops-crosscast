"""Offline X readback regressions: real transactions, fake DOM evaluations, no browser.

The row fixtures start at POST_FIELDS' output boundary; they do not prove the
JavaScript selectors against a live site. Existing form tests cover DOM nesting.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, Mock, patch

from video_publisher import browser_backends as backends, browser_forms as forms, x_pages
from video_publisher.core import VpubError, file_sha, read_json


OWNER = 'fixture_owner'
WORDS = '第七集：再来一次 / One more try!'
MEDIA = '77'
MEDIA_URL = f'https://studio.x.com/library/13_{MEDIA}/edit'


def post_id(seconds_ago=0, sequence=0):
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return str(((int(moment.timestamp() * 1000) - 1288834974657) << 22) + sequence)


def row(post=None, text=WORDS, author=OWNER, video=True, media=MEDIA, **extra):
    return {'href': f'/{author}/status/{post or post_id()}', 'text': text,
            'time': datetime.now(timezone.utc).isoformat(), 'video': video,
            'poster': f'https://pbs.twimg.com/amplify_video_thumb/{media}/img/a.jpg' if media else None,
            'lines': [text or ''], **extra}


class Page:
    """Only read operations exist. An unexpected selector fails the fixture."""
    def __init__(self, rows):
        self.rows = rows
        self.url = 'https://studio.x.com/library/13_77/edit'
        self.waited_ids = []

    async def goto(self, url, **kwargs):
        self.url = url

    async def wait_for_timeout(self, milliseconds):
        pass

    def locator(self, selector):
        if selector == 'main article':
            return Mock(all=AsyncMock(return_value=[Mock(evaluate=AsyncMock(return_value=item)) for item in self.rows]))
        if selector == 'a[href]':
            return Mock(evaluate_all=AsyncMock(return_value=[{'url': 'https://x.com' + item['href'], 'text': item['text'] or ''} for item in self.rows]))
        if selector == 'body':
            return Mock(inner_text=AsyncMock(return_value='\n'.join(item['text'] or '' for item in self.rows)))
        if selector.startswith('article:has('):
            post = re.search(r'in_reply_to=(\d+)', selector)[1]
            self.waited_ids.append(post)
            item = next(item for item in self.rows if item['href'].endswith('/status/' + post))
            found = Mock(evaluate=AsyncMock(return_value=item), wait_for=AsyncMock())
            found.first = found
            return found
        raise AssertionError(f'Unexpected selector: {selector}')


async def look_once(page, predicate, *args):
    return await predicate()


class XDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, VPUB_HOME=str(self.root / 'vpub'), BILI_CLI_HOME=str(self.root / 'bili'))
        env.start()
        self.addCleanup(env.stop)
        # Only the driver's exception type is used. No driver installation or browser is needed.
        driver = ModuleType('patchright.async_api')
        driver.Error = type('BrowserError', (Exception,), {})
        imported = patch.dict(sys.modules, {'patchright.async_api': driver})
        imported.start()
        self.addCleanup(imported.stop)
        for target, replacement in (('video_publisher.browser_backends.risk_check', AsyncMock()),
                                    ('video_publisher.browser_backends.look_until', look_once),
                                    ('video_publisher.browser_forms.asyncio.sleep', AsyncMock())):
            mocked = patch(target, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        video = self.root / 'fixture.mp4'
        video.write_bytes(b'harmless synthetic video bytes')
        self.plan = {'confirmation': 'a' * 64, 'video': str(video), 'video_sha256': file_sha(video),
                     'options': {'title': 'The full original media title', 'post_text': WORDS, 'submission_mode': 'publish', 'visibility': 'account'}}
        self.tx = backends.Transaction('x', OWNER, self.plan)
        self.tx.save(before_ids=[], expected_text=WORDS, media_url=MEDIA_URL, media_selected=True, file_video_seen=True)
        self.page = Page([row()])
        self.button = AsyncMock()

    async def submit(self):
        async def clicked(**kwargs):
            saved = read_json(self.tx.path)
            self.assertEqual(('UNKNOWN', True, 1), (saved['state'], saved['final_submit_attempted'], saved['final_presses']))
        self.button.click.side_effect = clicked
        await self.tx.submit(self.page, self.button, still_holds=AsyncMock())

    async def resume(self):
        with patch.object(forms, 'publish_x', AsyncMock(side_effect=AssertionError('No reupload or repost on resume'))):
            return await backends.execute(self.page, 'x', OWNER, {'action': 'resume', 'plan': self.plan})

    async def assert_uncertain(self):
        result = await forms.finish(self.page, self.tx, self.tx.receipt['before_ids'])
        self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
        self.assertNotIn('id', result)
        result = await self.resume()
        self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
        self.assertNotIn('id', result)
        self.assertEqual((True, 1), (result['final_submit_attempted'], result['final_presses']))
        self.assertEqual(1, self.button.click.await_count)
        self.assertEqual(self.plan['video_sha256'], result['video_sha256'])

    async def test_shortened_bilingual_text_discovers_exact_bound_post_after_one_click(self):
        self.assertNotIn(self.plan['options']['title'], WORDS)
        await self.submit()
        result = await forms.finish(self.page, self.tx, [])
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(self.page.rows[0]['href'].split('/')[-1], result['id'])
        self.assertEqual(1, self.button.click.await_count)
        self.assertEqual(1, result['final_presses'])
        self.assertEqual(result['id'], read_json(self.tx.path)['id'])
        self.assertEqual({result['id']}, set(self.page.waited_ids))

    async def test_resume_discovers_shortened_text_without_uploading_or_clicking_again(self):
        await self.submit()
        result = await self.resume()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(self.page.rows[0]['href'].split('/')[-1], result['id'])
        self.button.click.assert_awaited_once()

    async def test_same_text_different_video_is_not_the_upload(self):
        self.page.rows = [row(media='88')]
        await self.submit()
        await self.assert_uncertain()

    async def test_missing_poster_number_cannot_be_replaced_by_preclick_binding(self):
        self.page.rows = [row(media=None)]
        await self.submit()
        await self.assert_uncertain()

    async def test_title_or_expected_text_substrings_never_discover_a_post(self):
        await self.submit()
        for text in (self.plan['options']['title'], WORDS + ' extra', 'Prefix ' + WORDS, WORDS.replace(' ', '')):
            with self.subTest(text=text):
                self.page.rows = [row(text=text)]
                await self.assert_uncertain()

    async def test_missing_post_text_never_discovers_a_post(self):
        await self.submit()
        for text in (None, '', '   '):
            with self.subTest(text=text):
                self.page.rows = [row(text=text)]
                await self.assert_uncertain()

    async def test_duplicate_exact_posts_are_ambiguous_even_with_the_same_video(self):
        self.page.rows = [row(post=post_id(sequence=1)), row(post=post_id(sequence=2))]
        await self.submit()
        await self.assert_uncertain()

    async def test_an_unbound_second_exact_post_also_keeps_discovery_uncertain(self):
        self.page.rows = [row(post=post_id(sequence=1)), row(post=post_id(sequence=2), media=None)]
        await self.submit()
        await self.assert_uncertain()

    async def test_before_ids_and_id_time_both_exclude_old_posts(self):
        await self.submit()
        known = post_id()
        self.tx.save(before_ids=[known])
        self.page.rows = [row(post=known)]
        await self.assert_uncertain()
        # The displayed time looks current; the durable ID still says yesterday.
        self.page.rows = [row(post=post_id(seconds_ago=86400))]
        await self.assert_uncertain()

    async def test_future_id_outside_clock_grace_is_not_evidence(self):
        self.page.rows = [row(post=post_id(seconds_ago=-86400))]
        await self.submit()
        await self.assert_uncertain()

    async def test_existing_clock_grace_accepts_a_recent_bound_post(self):
        self.page.rows = [row(post=post_id(seconds_ago=120))]
        await self.submit()
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])

    async def test_other_authors_ads_and_parsed_quote_only_video_do_not_count(self):
        await self.submit()
        for item in (row(author='other_owner'), row(lines=['Promoted', WORDS]), row(video=False)):
            with self.subTest(row=item):
                self.page.rows = [item]
                await self.assert_uncertain()

    async def test_missing_receipt_evidence_does_not_fall_back_to_title_or_video(self):
        await self.submit()
        saved = dict(self.tx.receipt)
        for key in ('expected_text', 'media_url', 'before_ids', 'started_at', 'final_submit_attempted'):
            with self.subTest(key=key):
                receipt = {k: v for k, v in saved.items() if k != key}
                self.assertEqual([], await backends.x_new_posts(self.page, OWNER, receipt))

    async def test_known_id_still_rejects_wrong_text_or_a_contradictory_media_id(self):
        await self.submit()
        post = self.page.rows[0]['href'].split('/')[-1]
        self.tx.save(id=post)
        for item in (row(post=post, media='88'), row(post=post, text=WORDS + ' extra')):
            with self.subTest(row=item):
                self.page.rows = [item]
                self.assertEqual('SUBMITTED_UNVERIFIED', (await self.resume())['state'])
        self.page.rows = [row(post=post)]
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])
        self.button.click.assert_awaited_once()

    async def test_short_copy_custom_cover_needs_an_identified_post_when_media_id_is_hidden(self):
        # The field run used short copy and a custom cover. Prepress binding alone
        # cannot distinguish another new post with the same words and another video.
        self.page.rows = [row(poster='https://pbs.twimg.com/media/synthetic-custom-cover.jpg')]
        await self.submit()
        await self.assert_uncertain()
        # Preserve the existing readback contract once an independent check has
        # identified the post; discovery itself must not manufacture that ID.
        self.tx.save(id=self.page.rows[0]['href'].split('/')[-1])
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])
        self.button.click.assert_awaited_once()

    async def test_existing_x_new_works_callers_keep_their_time_filter_contract(self):
        fresh = {'id': post_id()}
        old = {'id': post_id(seconds_ago=86400)}
        self.assertEqual([fresh], backends.x_new_works('x', self.tx.receipt, [old, fresh]))
        self.assertEqual([old, fresh], backends.x_new_works('instagram', self.tx.receipt, [old, fresh]))
        self.assertEqual([old, fresh], backends.x_new_works('x', {}, [old, fresh]))

    async def test_late_bound_readback_can_settle_without_a_second_click(self):
        self.page.rows = [row(media=None)]
        await self.submit()
        await self.assert_uncertain()
        self.page.rows[0]['poster'] = f'https://pbs.twimg.com/ext_tw_video_thumb/{MEDIA}/pu/img/a.jpg'
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])
        resumed = backends.Transaction('x', OWNER, self.plan, resume=True)
        with self.assertRaises(VpubError) as error:
            await resumed.submit(self.page, self.button)
        self.assertEqual('already_submitted', error.exception.code)
        self.button.click.assert_awaited_once()

    async def test_only_trusted_video_poster_paths_supply_a_media_id(self):
        for poster in (f'https://example.invalid/amplify_video_thumb/{MEDIA}/img/a.jpg',
                       f'https://pbs.twimg.com.example.invalid/amplify_video_thumb/{MEDIA}/img/a.jpg',
                       f'https://example.invalid/?next=https://pbs.twimg.com/amplify_video_thumb/{MEDIA}/img/a.jpg',
                       f'https://pbs.twimg.com/media/{MEDIA}.jpg', None):
            with self.subTest(poster=poster):
                fields = await x_pages.post_fields(Mock(evaluate=AsyncMock(return_value=row(poster=poster))))
                self.assertIsNone(fields['media_id'])
        for kind in ('amplify_video_thumb', 'ext_tw_video_thumb'):
            fields = await x_pages.post_fields(Mock(evaluate=AsyncMock(return_value=row(poster=f'https://pbs.twimg.com/{kind}/{MEDIA}/img/a.jpg'))))
            self.assertEqual(MEDIA, fields['media_id'])


if __name__ == '__main__':
    unittest.main()
