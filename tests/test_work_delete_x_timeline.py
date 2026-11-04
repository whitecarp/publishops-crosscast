"""Adversarial X pagination proof/capture tests. No browser or network calls."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch
from urllib.parse import urlencode
import unittest

import test_work_delete_x as fixtures
from video_publisher import work_delete_x as route, work_delete_state as state
from video_publisher import work_delete_x_readback as readback
from video_publisher.core import VpubError, atomic_json, home, read_json


OWNER_ID = '111111'
OTHER_ID = '222222'
POST_ID = '2111111111111111112'


def cursor(value='next', kind='Bottom'):
    return {'entryId': 'cursor-' + kind, 'content': {
        'entryType': 'TimelineTimelineCursor', 'cursorType': kind, 'value': value}}


def tweet(number=POST_ID):
    return {'entryId': 'tweet-' + number, 'content': {
        'entryType': 'TimelineTimelineItem', 'itemContent': {
            'itemType': 'TimelineTweet', 'tweet_results': {
                'result': {'__typename': 'Tweet', 'rest_id': number}}}}}


def page(entries=None, *, initial=False, end=False):
    instructions = ([{'type': 'TimelineClearCache'}] if initial else [])
    instructions.append({'type': 'TimelineAddEntries',
                         'entries': entries if entries is not None else [cursor()]})
    if end:
        instructions.append({'type': 'TimelineTerminateTimeline', 'direction': 'Bottom'})
    return {'data': {'user': {'result': {'__typename': 'User', 'rest_id': OWNER_ID,
                                      'timeline': {'timeline': {'instructions': instructions}}}}}}


def records():
    return [
        ('UserByScreenName', {'screen_name': fixtures.OWNER}, 200,
         {'data': {'user': {'result': {'__typename': 'User', 'rest_id': OWNER_ID,
                                     'core': {'screen_name': fixtures.OWNER}}}}}),
        ('UserOriginalsTimeline', {'userId': OWNER_ID}, 200,
         page([tweet(), cursor('next')], initial=True)),
        ('UserOriginalsTimeline', {'userId': OWNER_ID, 'cursor': 'next'}, 200,
         page([cursor('still-nonempty')], end=True)),
    ]


def instructions(record):
    return record[3]['data']['user']['result']['timeline']['timeline']['instructions']


class TimelineParserAdversarialTests(fixtures.OfflineCase):
    def assert_pending(self, data):
        self.assertFalse(readback.proof(data, fixtures.OWNER)[1])

    def test_control_fixture_has_linked_owner_and_explicit_bottom_with_nonempty_cursor(self):
        self.assertEqual(({POST_ID}, True), readback.proof(records(), fixtures.OWNER))

    def test_wrong_requested_or_returned_screen_name_and_multiple_owner_ids_are_refused(self):
        for mode in ('request', 'result', 'duplicate-owner', 'no-owner'):
            with self.subTest(mode=mode):
                data = records()
                if mode == 'request':
                    data[0][1]['screen_name'] = 'other_owner'
                elif mode == 'result':
                    data[0][3]['data']['user']['result']['core']['screen_name'] = 'other_owner'
                elif mode == 'duplicate-owner':
                    extra = deepcopy(data[0])
                    extra[3]['data']['user']['result']['rest_id'] = OTHER_ID
                    data.insert(1, extra)
                else:
                    data.pop(0)
                self.assert_pending(data)

    def test_wrong_request_user_cannot_borrow_terminal_from_other_profile(self):
        for index in (1, 2):
            with self.subTest(index=index):
                data = records()
                data[index][1]['userId'] = OTHER_ID
                self.assert_pending(data)

    def test_response_user_disagrees_with_bound_request_is_not_complete(self):
        for index in (1, 2):
            for conflict in ('rest_id', 'screen_name', 'typename'):
                with self.subTest(index=index, conflict=conflict):
                    data = records()
                    user = data[index][3]['data']['user']['result']
                    if conflict == 'rest_id':
                        user['rest_id'] = OTHER_ID
                    elif conflict == 'screen_name':
                        user['core'] = {'screen_name': 'other_owner'}
                    else:
                        user['__typename'] = 'UserUnavailable'
                    self.assert_pending(data)

    def test_cursor_gap_reordering_repeat_and_second_initial_are_refused(self):
        for mode in ('gap', 'reorder', 'repeat', 'second-initial', 'after-end'):
            with self.subTest(mode=mode):
                data = records()
                if mode == 'gap':
                    data[2][1]['cursor'] = 'not-the-returned-cursor'
                elif mode == 'reorder':
                    data[1], data[2] = data[2], data[1]
                elif mode == 'repeat':
                    data.insert(2, deepcopy(data[1]))
                elif mode == 'second-initial':
                    data[2][1].pop('cursor')
                else:
                    data.append(deepcopy(data[2]))
                self.assert_pending(data)

    def test_foreign_operation_path_top_and_nested_terminal_do_not_finish_chain(self):
        for mode in ('operation', 'path', 'top', 'nested', 'missing'):
            with self.subTest(mode=mode):
                data = records()
                if mode == 'operation':
                    data[2] = ('TweetDetail', *data[2][1:])
                elif mode == 'path':
                    body = data[2][3]
                    sequence = instructions(data[2])
                    body['data'] = {'threaded_conversation_with_injections_v2': {'instructions': sequence}}
                elif mode == 'top':
                    instructions(data[2])[-1]['direction'] = 'Top'
                elif mode == 'nested':
                    terminal = instructions(data[2]).pop()
                    instructions(data[2])[0]['entries'][0]['content']['unrelated'] = terminal
                else:
                    instructions(data[2]).pop()
                self.assert_pending(data)

    def test_response_error_or_failed_status_anywhere_refuses_completion(self):
        for index in range(3):
            for mode in ('errors', 'status', 'body'):
                with self.subTest(index=index, mode=mode):
                    data = records()
                    if mode == 'errors':
                        data[index][3]['errors'] = [{'code': 32}]
                    elif mode == 'status':
                        op, variables, _, body = data[index]
                        data[index] = (op, variables, 403, body)
                    else:
                        data[index] = (*data[index][:3], None)
                    self.assert_pending(data)

    def test_unknown_reordered_instruction_and_ambiguous_bottom_cursor_are_refused(self):
        for mode in ('instruction', 'cursor', 'early-terminal', 'duplicate-terminal'):
            with self.subTest(mode=mode):
                data = records()
                sequence = instructions(data[2])
                if mode == 'instruction':
                    sequence.insert(0, {'type': 'TimelineReplaceEntry'})
                elif mode == 'cursor':
                    sequence[0]['entries'].append(cursor('another'))
                elif mode == 'early-terminal':
                    sequence.reverse()
                else:
                    sequence.append(deepcopy(sequence[-1]))
                self.assert_pending(data)

    def test_unreadable_tweet_items_cannot_be_silently_counted_as_no_posts(self):
        variants = [
            {'entryType': 'TimelineTimelineItem'},
            {'entryType': 'TimelineTimelineItem', 'itemContent': {}},
            {'entryType': 'TimelineTimelineItem', 'itemContent': {'itemType': 'TimelineTweet'}},
            {'entryType': 'TimelineTimelineItem', 'itemContent': {'itemType': 'TimelineTweet',
                'tweet_results': {'result': {'__typename': 'TweetWithVisibilityResults'}}}},
            {'entryType': 'TimelineTimelineItem', 'itemContent': {'itemType': 'UnknownPostShape'}},
            {'entryType': 'TimelineTimelineModule'},
            {'entryType': 'TimelineTimelineModule', 'items': [{}]},
            {'entryType': 'TimelineTimelineModule', 'items': [{'item': {}}]},
        ]
        for content in variants:
            with self.subTest(content=content):
                data = records()
                instructions(data[1])[1]['entries'][0]['content'] = content
                self.assert_pending(data)

    def test_target_in_nested_module_quote_or_terminal_page_remains_in_union(self):
        for mode in ('module', 'quote', 'last-page'):
            with self.subTest(mode=mode):
                data = records()
                target_tweet = tweet(fixtures.WORK)
                if mode == 'module':
                    entry = {'entryId': 'module-1', 'content': {'entryType': 'TimelineTimelineModule',
                        'items': [{'item': target_tweet['content']}]}}
                    instructions(data[1])[1]['entries'].append(entry)
                elif mode == 'quote':
                    own = instructions(data[1])[1]['entries'][0]['content']['itemContent']['tweet_results']['result']
                    own['quoted_status_result'] = target_tweet['content']['itemContent']['tweet_results']
                else:
                    instructions(data[2])[0]['entries'].append(target_tweet)
                ids, complete = readback.proof(data, fixtures.OWNER)
                self.assertIn(fixtures.WORK, ids)
                self.assertTrue(complete)


class EventPage:
    def __init__(self):
        self.listeners = {}

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        if callback in self.listeners.get(event, []):
            self.listeners[event].remove(callback)

    def emit(self, event, value):
        for callback in list(self.listeners.get(event, [])):
            callback(value)


class Request:
    def __init__(self, operation, variables, *, method='GET', origin='https://x.com'):
        self.url = origin + '/i/api/graphql/observed/' + operation + '?' + urlencode({'variables': json.dumps(variables)})
        self.method = method


class Response:
    def __init__(self, request, status, body, *, release=None, failure=None, service_worker=False):
        self.request, self.status, self.body = request, status, body
        self.release, self.failure, self.from_service_worker = release, failure, service_worker
        self.started, self.cancelled = False, False

    async def json(self):
        self.started = True
        try:
            if self.release is not None:
                await self.release.wait()
            if self.failure:
                raise self.failure
            return deepcopy(self.body)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class TimelineCaptureAdversarialTests(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()
        self.page = EventPage()
        self.capture = readback.TimelineCapture(self.page, fixtures.OWNER)
        self.capture.start()
        self.addAsyncCleanup(self.capture.close)

    def enqueue(self, data=None, *, release_last=None):
        responses = []
        for index, (operation, variables, status, body) in enumerate(data or records()):
            request = Request(operation, variables)
            response = Response(request, status, body, release=release_last if index == 2 else None)
            self.page.emit('request', request)
            self.page.emit('response', response)
            responses.append(response)
        return responses

    async def settle(self):
        await asyncio.gather(*self.capture.tasks)

    async def test_response_completion_order_cannot_reorder_cursor_chain(self):
        releases = [asyncio.Event() for _ in range(3)]
        for index, (operation, variables, status, body) in enumerate(records()):
            request = Request(operation, variables)
            self.page.emit('request', request)
            self.page.emit('response', Response(request, status, body, release=releases[index]))
        for index in (2, 0):
            releases[index].set()
        await asyncio.sleep(0)
        self.assertFalse(self.capture.snapshot()[1])
        releases[1].set()
        await self.settle()
        self.assertEqual(({POST_ID}, True), self.capture.snapshot())

    async def test_response_without_captured_fresh_request_cannot_supply_owner_or_terminal(self):
        for operation, variables, status, body in records():
            self.page.emit('response', Response(Request(operation, variables), status, body))
        self.assertEqual((set(), False), self.capture.snapshot())
        self.assertEqual([], self.capture.tasks)

    async def test_unfinished_extra_request_revokes_previously_complete_snapshot(self):
        self.enqueue()
        await self.settle()
        self.assertTrue(self.capture.snapshot()[1])
        self.page.emit('request', Request('UserOriginalsTimeline', {'userId': OWNER_ID, 'cursor': 'later'}))
        self.assertFalse(self.capture.snapshot()[1])

    async def test_foreign_origin_method_or_operation_never_supply_a_terminal(self):
        self.enqueue(records()[:2])
        operation, variables, status, body = records()[2]
        for method, origin, op in [('POST', 'https://x.com', operation),
                                   ('GET', 'https://x.com.invalid', operation),
                                   ('GET', 'http://x.com', operation),
                                   ('GET', 'https://x.com', 'TweetDetail')]:
            request = Request(op, variables, method=method, origin=origin)
            self.page.emit('request', request)
            self.page.emit('response', Response(request, status, body))
        await self.settle()
        self.assertFalse(self.capture.snapshot()[1])

    async def test_service_worker_and_unreadable_response_fail_closed(self):
        self.enqueue(records()[:2])
        operation, variables, status, body = records()[2]
        request = Request(operation, variables)
        self.page.emit('request', request)
        self.page.emit('response', Response(request, status, body, service_worker=True))
        await self.settle()
        self.assertFalse(self.capture.snapshot()[1])

    async def test_json_failure_cannot_be_treated_as_empty_terminal(self):
        self.enqueue(records()[:2])
        operation, variables, status, body = records()[2]
        request = Request(operation, variables)
        self.page.emit('request', request)
        self.page.emit('response', Response(request, status, body, failure=ValueError('Synthetic unreadable JSON')))
        await self.settle()
        self.assertFalse(self.capture.snapshot()[1])

    async def test_close_detaches_listeners_and_cancels_pending_json_without_success(self):
        release = asyncio.Event()
        responses = self.enqueue(release_last=release)
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertTrue(responses[-1].started)
        await self.capture.close()
        self.assertTrue(responses[-1].cancelled)
        self.assertFalse(any(self.page.listeners.values()))
        self.assertFalse(self.capture.snapshot()[1])


class TimelineIntegrationAdversarialTests(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()
        self.ready('x', fixtures.WORK)
        self.original_receipt = self.path.read_bytes()
        self.page = fixtures.XPage(self.marked)
        self.page.present = False
        self.page.profile_message = ''
        self.page.profile_posts = [fixtures.post(wid=POST_ID)]

    def sealed_capture(self, *, after_close=None, after_snapshot=None):
        class Sealed:
            def __init__(self, page, actual):
                self.page, self.actual = page, actual
            def start(self):
                pass
            def snapshot(self):
                if after_snapshot:
                    after_snapshot(self.page)
                return {POST_ID}, True
            async def close(self):
                await asyncio.sleep(0)
                if after_close:
                    after_close(self.page)
        return Sealed

    async def observe(self, capture_type):
        with patch.object(route, 'TimelineCapture', capture_type):
            try:
                result = await route.absence(self.page, fixtures.OWNER, self.target, True, 0)
            except VpubError as exc:
                self.assertIn(exc.code, {'wrong_account', 'wrong_work', 'stopped', 'human_verification_required'})
                result = None
        self.assertEqual([], self.page.events)
        self.assertEqual(self.original_receipt, self.path.read_bytes())
        return result

    async def test_identity_drift_after_snapshot_is_rechecked(self):
        result = await self.observe(self.sealed_capture(after_snapshot=lambda page: setattr(page.tree.children[0], 'href', '/other_owner/')))
        self.assertTrue(result is None or not state.verified(result, self.target))

    async def test_identity_or_origin_drift_during_async_close_cannot_return_verified(self):
        for mode in ('identity', 'origin'):
            with self.subTest(mode=mode):
                def change(page):
                    if mode == 'identity':
                        page.tree.children[0].href = '/other_owner/'
                    else:
                        page.url = 'https://other.invalid/' + fixtures.OWNER
                result = await self.observe(self.sealed_capture(after_close=change))
                self.assertTrue(result is None or not state.verified(result, self.target))

    async def test_stop_created_during_async_close_cannot_return_verified(self):
        def stop(page):
            atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop during async capture close'})
        result = await self.observe(self.sealed_capture(after_close=stop))
        self.assertTrue(result is None or not state.verified(result, self.target))
        self.assertEqual('Synthetic stop during async capture close', read_json(home() / 'STOP.json')['reason'])

    async def test_identity_missing_on_later_iterations_revokes_prior_fresh_flag(self):
        waits = []
        original_wait = self.page.wait_for_timeout
        async def wait(milliseconds):
            await original_wait(milliseconds)
            waits.append(milliseconds)
            if len(waits) >= 2:
                self.page.tree.children = [node for node in self.page.tree.children if node.name != 'Profile']
        class EndOnlyAfterWait:
            def __init__(self, page, actual):
                pass
            def start(self):
                pass
            def snapshot(self):
                return {POST_ID}, len(waits) >= 2
            async def close(self):
                pass
        with patch.object(self.page, 'wait_for_timeout', side_effect=wait):
            result = await self.observe(EndOnlyAfterWait)
        self.assertIsNotNone(result)
        self.assertFalse(result['fresh'] or result['list_complete'] or result['list_absent'])
        self.assertLessEqual(sum(waits), 50500)


if __name__ == '__main__':
    unittest.main()
