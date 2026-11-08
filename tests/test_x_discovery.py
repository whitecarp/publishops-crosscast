"""Offline X readback regressions: real transactions, fake DOM evaluations, no browser.

The row fixtures start at POST_FIELDS' output boundary; they do not prove the
JavaScript selectors against a live site. Existing form tests cover DOM nesting.
The custom-cover fixture uses the Episode 7 receipt's field shape with synthetic
values. Discovery correlates native prepress context with an own post; it cannot
independently verify posted bytes or exclude an identical external concurrent post.
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
        self.paused = []  # Each pause asked for, in milliseconds, and how many reads of the posts had happened by then.
        self.reads = 0

    async def goto(self, url, **kwargs):
        self.url = url

    async def wait_for_timeout(self, milliseconds):
        self.paused.append((milliseconds, self.reads))

    def locator(self, selector):
        if selector == 'main article':
            self.reads += 1
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
        self.tx.save(before_ids=[], expected_text=WORDS, media_url=MEDIA_URL, media_selected=True,
                     upload_filename=self.tx.media_name(), file_video_seen=True, metadata_readback=True)
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

    async def test_a_verify_waits_for_a_video_player_x_puts_in_late(self):
        """From a field run: the first vpub verify on a cold browser read the post before X had put its video player in, so it read as having no video and stayed unconfirmed until a second run."""
        late = row(video=False)
        self.page.rows = [late]
        reads = {'n': 0}
        plain = self.page.locator

        def locator(selector):
            if selector == 'main article':
                reads['n'] += 1
                late['video'] = reads['n'] > 2
            return plain(selector)
        self.page.locator = locator
        await self.submit()
        result = await self.resume()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(late['href'].split('/')[-1], result['id'])
        self.assertGreater(reads['n'], 2)

    async def test_a_post_whose_player_never_comes_is_still_not_the_upload(self):
        self.page.rows = [row(video=False)]
        await self.submit()
        result = await self.resume()
        self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
        self.assertNotIn('id', result)
        # From a review: each new look comes half a second after the last, for X_PLAYER_SECONDS in all, so a cold browser has that long.
        waits = [reads for milliseconds, reads in self.page.paused if milliseconds == 500]
        self.assertEqual(backends.X_PLAYER_SECONDS * 2, len(waits))
        self.assertEqual(list(range(waits[0], waits[0] + len(waits))), waits)

    async def test_publishing_itself_does_not_wait_for_a_player(self):
        """finish() reads again on its own, five seconds apart, so only a one-shot read waits."""
        self.page.rows = [row(video=False)]
        await self.submit()
        await forms.finish(self.page, self.tx, self.tx.receipt['before_ids'])
        self.assertNotIn(500, [milliseconds for milliseconds, _ in self.page.paused])

    async def test_same_text_different_video_is_not_the_upload(self):
        self.page.rows = [row(media='88')]
        await self.submit()
        await self.assert_uncertain()

    async def test_legacy_receipt_without_preclick_video_context_stays_uncertain(self):
        self.page.rows = [row(media=None)]
        await self.submit()
        self.tx.receipt.pop('file_video_seen')
        self.tx.save()
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
        await self.submit()
        for first, second in ((MEDIA, None), (MEDIA, '88'), (None, '88'), (None, None)):
            with self.subTest(media=(first, second)):
                self.page.rows = [row(post=post_id(sequence=1), media=first), row(post=post_id(sequence=2), media=second)]
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

    async def test_foreign_repost_ad_and_quote_only_rows_do_not_steal_the_unique_match(self):
        own = row(media=None)
        self.page.rows = [own, row(post=post_id(sequence=1), author='reposted_author'),
                          row(post=post_id(sequence=2), lines=['Promoted', WORDS]),
                          row(post=post_id(sequence=3), video=False)]
        await self.submit()
        result = await self.resume()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(own['href'].split('/')[-1], result['id'])

    async def test_missing_receipt_evidence_does_not_fall_back_to_title_or_video(self):
        await self.submit()
        saved = dict(self.tx.receipt)
        required = ('method', 'platform', 'identity', 'video_sha256', 'upload_filename', 'media_url', 'media_selected',
                    'file_video_seen', 'metadata_readback', 'expected_text', 'before_ids', 'started_at',
                    'final_submit_attempted', 'final_presses')
        for key in required:
            with self.subTest(key=key):
                receipt = {k: v for k, v in saved.items() if k != key}
                for media in (MEDIA, None):
                    self.page.rows = [row(media=media)]
                    result = await backends.verify(self.page, 'x', OWNER, receipt)
                    self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
                    self.assertNotIn('id', result)
        self.button.click.assert_awaited_once()

    async def test_invalid_context_or_truthy_marker_substitutes_are_not_binding(self):
        await self.submit()
        self.page.rows = [row(media=None)]
        cases = (('method', 'native'), ('platform', 'instagram'), ('identity', 'someone_else'), ('identity', ''),
                 ('video_sha256', 'f' * 64), ('video_sha256', 'short'), ('upload_filename', 'unbound.mp4'),
                 ('media_url', 'https://example.invalid/library/13_77/edit'), ('expected_text', ' '),
                 ('media_selected', 1), ('file_video_seen', False), ('metadata_readback', False),
                 ('final_submit_attempted', 1), ('final_presses', 0), ('final_presses', 2), ('final_presses', True),
                 ('before_ids', None), ('before_ids', [123]), ('before_ids', ['not-an-id']),
                 ('schedule', {'at': datetime.now(timezone.utc).isoformat()}))
        for key, value in cases:
            with self.subTest(field=key, value=value):
                self.assertEqual([], await backends.x_new_posts(self.page, OWNER, {**self.tx.receipt, key: value}))
        self.button.click.assert_awaited_once()

    async def test_wrong_receipt_account_is_rejected_by_native_verify(self):
        await self.submit()
        with self.assertRaises(VpubError) as caught:
            await backends.verify(self.page, 'x', OWNER, {**self.tx.receipt, 'identity': 'someone_else'})
        self.assertEqual('wrong_account', caught.exception.code)
        self.button.click.assert_awaited_once()

    async def test_start_time_must_be_aware_valid_and_not_future(self):
        await self.submit()
        invalid = (None, '', 'not-a-time', datetime.now().isoformat(),
                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), '0001-01-01T00:00:00+00:00')
        for stamp in invalid:
            with self.subTest(stamp=stamp):
                result = await backends.verify(self.page, 'x', OWNER, {**self.tx.receipt, 'started_at': stamp})
                self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
                self.assertNotIn('id', result)
        # Do not accidentally apply clock grace twice to the receipt's start.
        self.page.rows = [row(post=post_id(seconds_ago=-240), media=None)]
        future = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
        self.assertEqual([], await backends.x_new_posts(self.page, OWNER, {**self.tx.receipt, 'started_at': future}))

    async def test_utc_z_or_offset_timestamp_preserves_the_same_instant(self):
        await self.submit()
        started = datetime.fromisoformat(self.tx.receipt['started_at'])
        for stamp in (started.isoformat().replace('+00:00', 'Z'), started.astimezone(timezone(timedelta(hours=5))).isoformat()):
            with self.subTest(stamp=stamp):
                result = await backends.verify(self.page, 'x', OWNER, {**self.tx.receipt, 'started_at': stamp})
                self.assertEqual('SUBMISSION_VERIFIED', result['state'])

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

    async def test_short_copy_custom_cover_discovers_from_episode7_receipt_shape(self):
        # Same receipt fields as the observed run; no private values or account calls.
        self.page.rows = [row(poster='https://pbs.twimg.com/media/synthetic-custom-cover.jpg')]
        self.tx.save(cover={'source': 'production_artwork', 'state': 'UI_ACCEPTED', 'remote_pixels_verified': False})
        await self.submit()
        self.assertNotIn('id', self.tx.receipt)
        original = {key: self.tx.receipt[key] for key in ('video_sha256', 'identity', 'media_url', 'upload_filename', 'before_ids', 'expected_text')}
        result = await forms.finish(self.page, self.tx, [])
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(self.page.rows[0]['href'].split('/')[-1], result['id'])
        self.assertEqual(original, {key: result[key] for key in original})
        self.assertIn('correlation, not independent verification of posted video bytes', result['note'])
        self.assertIn('external concurrent post cannot be excluded', result['note'])
        again = await self.resume()
        self.assertEqual(result['discovery_source'], read_json(self.tx.path)['discovery_source'])
        self.assertEqual(result['note'], again['note'])
        self.button.click.assert_awaited_once()

    async def test_existing_explicit_id_custom_cover_verification_is_unchanged(self):
        self.page.rows = [row(media=None)]
        await self.submit()
        self.tx.save(id=self.page.rows[0]['href'].split('/')[-1])
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])
        self.button.click.assert_awaited_once()

    async def test_permalink_with_a_newly_exposed_conflicting_id_stays_unverified(self):
        self.page.rows = [row(media=None)]
        await self.submit()
        goto = self.page.goto

        async def reveal_conflict(url, **kwargs):
            await goto(url, **kwargs)
            if '/status/' in url:
                self.page.rows[0]['poster'] = 'https://pbs.twimg.com/amplify_video_thumb/88/img/a.jpg'

        self.page.goto = reveal_conflict
        result = await self.resume()
        self.assertEqual('SUBMITTED_UNVERIFIED', result['state'])
        self.button.click.assert_awaited_once()

    async def test_existing_x_new_works_callers_keep_their_time_filter_contract(self):
        fresh = {'id': post_id()}
        old = {'id': post_id(seconds_ago=86400)}
        self.assertEqual([fresh], backends.x_new_works('x', self.tx.receipt, [old, fresh]))
        self.assertEqual([old, fresh], backends.x_new_works('instagram', self.tx.receipt, [old, fresh]))
        self.assertEqual([old, fresh], backends.x_new_works('x', {}, [old, fresh]))

    async def test_late_bound_readback_can_settle_without_a_second_click(self):
        self.page.rows = []
        await self.submit()
        await self.assert_uncertain()
        self.page.rows = [row(media=None)]
        self.assertEqual('SUBMISSION_VERIFIED', (await self.resume())['state'])
        resumed = backends.Transaction('x', OWNER, self.plan, resume=True)
        with self.assertRaises(VpubError) as error:
            await resumed.submit(self.page, self.button)
        self.assertEqual('already_submitted', error.exception.code)
        self.button.click.assert_awaited_once()

    async def test_timed_out_native_final_attempt_can_read_custom_cover_without_resending(self):
        self.page.rows = [row(media=None)]
        self.button.click.side_effect = TimeoutError('synthetic click timeout')
        with self.assertRaises(TimeoutError):
            await self.tx.submit(self.page, self.button, still_holds=AsyncMock())
        self.assertEqual('UNKNOWN', read_json(self.tx.path)['state'])
        result = await self.resume()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(1, result['final_presses'])  # An attempt marker, not a click-completion counter.
        self.button.click.assert_awaited_once()

    async def test_failed_prepress_binding_never_records_or_clicks_a_final_attempt(self):
        self.page.rows = [row(media=None)]
        holds = AsyncMock(side_effect=VpubError('wrong_work', 'Synthetic composer binding mismatch'))
        with self.assertRaises(VpubError):
            await self.tx.submit(self.page, self.button, still_holds=holds)
        self.assertIs(False, read_json(self.tx.path)['final_submit_attempted'])
        self.assertEqual([], await backends.x_new_posts(self.page, OWNER, self.tx.receipt))
        self.button.click.assert_not_awaited()

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
