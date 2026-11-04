"""The observed originals timeline terminal is distinct from a quiet DOM."""
import copy
import unittest

from video_publisher.work_delete_x_readback import proof


OWNER = 'test_owner'
USER = '12345678901'
POST = '2111111111111111111'


def cursor(value, kind='Bottom'):
    return {'entryId': 'cursor-' + kind, 'content': {'entryType': 'TimelineTimelineCursor',
            '__typename': 'TimelineTimelineCursor', 'cursorType': kind, 'value': value}}


def tweet(work=POST):
    return {'entryId': 'tweet-' + work, 'content': {'entryType': 'TimelineTimelineItem',
            'itemContent': {'__typename': 'TimelineTweet', 'tweet_results': {
                'result': {'__typename': 'Tweet', 'rest_id': work}}}}}


def page(entries, *, terminal=False, initial=False):
    instructions = ([{'type': 'TimelineClearCache'}] if initial else [])
    instructions += [{'type': 'TimelineAddEntries', 'entries': entries}]
    if terminal:
        instructions.append({'type': 'TimelineTerminateTimeline', 'direction': 'Bottom'})
    return {'data': {'user': {'result': {'timeline': {'timeline': {'instructions': instructions}}}}}}


def records():
    return [
        ('UserByScreenName', {'screen_name': OWNER}, 200, {'data': {'user': {'result': {
            '__typename': 'User', 'rest_id': USER, 'core': {'screen_name': OWNER}}}}}),
        ('UserOriginalsTimeline', {'userId': USER}, 200,
         page([tweet(), cursor('next')], initial=True)),
        ('UserOriginalsTimeline', {'userId': USER, 'cursor': 'next'}, 200,
         page([cursor('last')], terminal=True)),
    ]


class TimelineContractTests(unittest.TestCase):
    def test_initial_and_cursor_response_with_explicit_bottom_are_complete(self):
        self.assertEqual(({POST}, True), proof(records(), OWNER))

    def test_nonempty_cursor_even_repeated_after_explicit_end_is_not_an_error(self):
        values = records()
        values[-1][3]['data']['user']['result']['timeline']['timeline']['instructions'][0]['entries'] = [cursor('next')]
        self.assertEqual(({POST}, True), proof(values, OWNER))

    def test_quiet_page_or_empty_entries_without_termination_never_proves_end(self):
        for entries in ([cursor('last')], []):
            with self.subTest(entries=entries):
                values = records()[:2]
                values.append(('UserOriginalsTimeline', {'userId': USER, 'cursor': 'next'}, 200, page(entries)))
                self.assertFalse(proof(values, OWNER)[1])

    def test_other_user_and_missing_user_binding_are_pending(self):
        for variant in ('no-user', 'request', 'response', 'timeline'):
            values = records()
            if variant == 'no-user':
                values = values[1:]
            elif variant == 'request':
                values[0][1]['screen_name'] = 'elsewhere'
            elif variant == 'response':
                values[0][3]['data']['user']['result']['core']['screen_name'] = 'elsewhere'
            else:
                values[1][1]['userId'] = '999999999'
            with self.subTest(variant=variant):
                self.assertFalse(proof(values, OWNER)[1])

    def test_missing_initial_gap_repeated_request_and_post_end_page_are_pending(self):
        for variant in ('no-initial', 'gap', 'repeat', 'after-end'):
            values = records()
            if variant == 'no-initial':
                del values[1]
            elif variant == 'gap':
                values[2][1]['cursor'] = 'another-cursor'
            elif variant == 'repeat':
                values.insert(2, copy.deepcopy(values[1]))
            else:
                values.append(copy.deepcopy(values[-1]))
            with self.subTest(variant=variant):
                self.assertFalse(proof(values, OWNER)[1])

    def test_conversation_top_or_unknown_terminal_cannot_finish_profile(self):
        for kind in ('Top', 'Side', None):
            values = records()
            values[-1][3]['data']['user']['result']['timeline']['timeline']['instructions'][-1]['direction'] = kind
            self.assertFalse(proof(values, OWNER)[1])
        values = records()
        values[-1] = ('TweetDetail', values[-1][1], 200, values[-1][3])
        self.assertFalse(proof(values, OWNER)[1])

    def test_errors_non200_and_unavailable_tweets_are_not_complete(self):
        values = records()
        values[1][3]['errors'] = [{'code': 88}]
        self.assertFalse(proof(values, OWNER)[1])
        values = records()
        values[-1] = (*values[-1][:2], 503, values[-1][3])
        self.assertFalse(proof(values, OWNER)[1])
        values = records()
        result = values[1][3]['data']['user']['result']['timeline']['timeline']['instructions'][1]['entries'][0]['content']['itemContent']['tweet_results']
        result['result'] = {'__typename': 'TweetUnavailable'}
        self.assertFalse(proof(values, OWNER)[1])

    def test_module_tweets_accumulate_across_pages_and_quoted_ids_are_conservative(self):
        values = records()
        module = {'entryId': 'module', 'content': {'entryType': 'TimelineTimelineModule', 'items': [
            {'item': tweet('2111111111111111112')['content']}]}}
        values[-1][3]['data']['user']['result']['timeline']['timeline']['instructions'][0]['entries'].insert(0, module)
        self.assertEqual(({POST, '2111111111111111112'}, True), proof(values, OWNER))

    def test_unknown_replacement_or_missing_cursor_remains_pending(self):
        for kind in ('TimelineReplaceEntry', 'TimelinePinEntry', 'TimelineAddToModule'):
            values = records()
            values[-1][3]['data']['user']['result']['timeline']['timeline']['instructions'].insert(0, {'type': kind})
            self.assertFalse(proof(values, OWNER)[1])
        values = records()
        values[-1][3]['data']['user']['result']['timeline']['timeline']['instructions'][0]['entries'] = []
        self.assertFalse(proof(values, OWNER)[1])


if __name__ == '__main__':
    unittest.main()
