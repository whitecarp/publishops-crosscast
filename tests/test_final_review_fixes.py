"""From the final review: a playlist confirmed from its title alone, an edit whose next check looked for the old words, and a scheduled video that could never read as done once it went out. Fakes only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import adapters, browser_backends, collections, edits, youtube_studio
from video_publisher.core import Ledger, VpubError, atomic_json, digest, read_json

OWNER = 'UC_SYNTHETIC_OWNER'
PLAYLIST = 'PLsynthetic123'
VIDEO = 'AbCdEfGhI12'


class Text:
    def __init__(self, value): self.value = value
    @property
    def first(self): return self
    async def wait_for(self, **kwargs): pass
    async def inner_text(self): return self.value


class StudioPage:
    def __init__(self, title, description, visibility):
        self.values = {'#title-textarea #textbox': title, '#description-textarea #textbox': description,
                       'ytcp-video-metadata-visibility #visibility-text': visibility}
    async def goto(self, url, **kwargs): self.url = url
    async def wait_for_timeout(self, value): raise AssertionError('Unexpected wait in synthetic read-back')
    def locator(self, selector): return Text(self.values[selector])


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        current = cls(2030, 1, 2, 12, tzinfo=timezone.utc)
        return current.astimezone(tz) if tz else current.replace(tzinfo=None)


def verify_video(receipt, title='Title', description='Description', visibility='Private'):
    with patch.object(browser_backends, 'risk_check', new=AsyncMock()), patch.object(browser_backends, 'datetime', FrozenDateTime):
        return asyncio.run(browser_backends.verify_youtube(StudioPage(title, description, visibility), receipt))


class Isolated(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'VPUB_HOME': str(self.root)})
        env.start()
        self.addCleanup(env.stop)


class PlaylistRegression(Isolated):
    def receipt(self, **kwargs):
        return dict(key=digest(['playlist']), platform='youtube', identity=OWNER, action='rename',
                    collection_id=PLAYLIST, title='Same title', state='UNVERIFIED',
                    privacy='private', description='New description', platform_writes=1, **kwargs)

    def observed(self, **fields):
        return {'identity': OWNER, 'complete': True,
                'items': [{'id': PLAYLIST, 'title': 'Same title', **fields}]}

    def test_f1_mismatched_requested_fields_stay_unverified(self):
        result = collections.checked_result(self.receipt(), self.observed(visibility='public', description='Old description'))
        self.assertEqual('UNVERIFIED', result['state'])

    def test_unread_requested_description_is_not_confirmation(self):
        result = collections.checked_result(self.receipt(), self.observed(visibility='private'))
        self.assertEqual('UNVERIFIED', result['state'])
        self.assertIn('description', result['readback_differs'])

    def test_unread_requested_privacy_is_not_confirmation(self):
        result = collections.checked_result(self.receipt(), self.observed(description='New description'))
        self.assertEqual('UNVERIFIED', result['state'])
        self.assertIn('privacy', result['readback_differs'])

    def test_matching_requested_fields_confirm(self):
        result = collections.checked_result(self.receipt(), self.observed(visibility='private', description='New description'))
        self.assertEqual('CONFIRMED', result['state'])

    def test_title_only_rename_still_confirms(self):
        receipt = self.receipt()
        receipt.update(privacy=None, description=None)
        result = collections.checked_result(receipt, self.observed())
        self.assertEqual('CONFIRMED', result['state'])

    def test_verify_uses_details_without_save(self):
        receipt = self.receipt()
        atomic_json(collections.receipt_path(receipt['key']), receipt)
        observed = self.observed(visibility='private', description='New description')
        with patch.object(collections, 'identity', return_value=OWNER), patch.object(collections, 'call', return_value=observed) as call:
            result = collections.verify(receipt['key'])
        call.assert_called_once_with('youtube', 'details', OWNER, collection_id=PLAYLIST)
        self.assertEqual(('CONFIRMED', 0), (result['state'], result['platform_writes']))

    def test_unreadable_details_remain_unverified_without_save(self):
        receipt = self.receipt()
        atomic_json(collections.receipt_path(receipt['key']), receipt)
        with patch.object(collections, 'identity', return_value=OWNER), patch.object(collections, 'call', side_effect=VpubError('owner_unverified', 'No readable editor')) as call:
            result = collections.verify(receipt['key'])
        call.assert_called_once_with('youtube', 'details', OWNER, collection_id=PLAYLIST)
        self.assertEqual(('UNVERIFIED', 0), (result['state'], result['platform_writes']))

    def test_details_reader_reuses_fields_and_never_submits(self):
        page = object()
        with patch.object(youtube_studio, 'playlist_fields', new=AsyncMock(return_value=(Text('Same title'), Text('New description')))) as fields, \
             patch.object(youtube_studio, 'visibility_trigger', return_value=Text('Private')), \
             patch.object(youtube_studio, 'risk_check', new=AsyncMock()), \
             patch.object(collections, 'before_submit') as submit:
            result = asyncio.run(youtube_studio.playlists(page, OWNER, {'operation':'details', 'collection_id':PLAYLIST}))
        fields.assert_awaited_once_with(page, PLAYLIST)
        submit.assert_not_called()
        self.assertEqual(('private', 'New description', 0), (result['items'][0]['visibility'], result['items'][0]['description'], result['platform_writes']))

    def test_empty_description_is_a_requested_change(self):
        observed = self.observed(visibility='private', description='')
        operations = []
        def call(platform, operation, owner, **kwargs):
            operations.append(operation)
            return observed if operation in {'list', 'details'} else {'collection_id': PLAYLIST}
        with patch.object(collections, 'identity', return_value=OWNER), patch.object(collections, 'call', side_effect=call):
            result = collections.change('youtube', 'rename', PLAYLIST, title='Same title', description='', request_id='clear-description')
        self.assertEqual(['list', 'rename', 'details'], operations)
        self.assertEqual('CONFIRMED', result['state'])


class EditedPublicationRegression(Isolated):
    def seed(self):
        plan_hash = digest(['plan'])
        receipt = {'state':'SUBMISSION_VERIFIED', 'method':'browser', 'platform':'youtube', 'id':VIDEO,
                   'identity':OWNER, 'title':'Old title', 'expected_text':'Old description', 'visibility':'private',
                   'final_submit_attempted':True, 'video_sha256':digest(['media'])}
        record = {'receipt':deepcopy(receipt), 'identity':{'identity':OWNER}, 'plan_hash':plan_hash,
                  'approved_plan':{'confirmation':plan_hash, 'description':'Old description'},
                  'options':{'method':'browser', 'ai_generated':False, 'title':'Old title', 'description':'Old description'},
                  'edits':[{'edit_id':'older-history-entry'}]}
        ledger = Ledger()
        key = ledger.claim('youtube','default',digest(['media']),record)
        ledger.save(key,'SUBMISSION_VERIFIED',record)
        ledger.close()
        durable = self.root/'browser-receipts'/(plan_hash+'-youtube.json')
        atomic_json(durable,receipt)
        request = self.root/'edit-request.json'
        atomic_json(request,{'expected_identity':OWNER, 'title':'New title', 'description':'New description',
                             'content_language':'en', 'metadata_language':'en', 'metadata_mode':'target_only'})
        return key, request, durable, receipt, record

    def apply_edit(self, key, request, state):
        with patch.object(adapters,'addon_installed',return_value=False), \
             patch.object(adapters,'browser_call',return_value={'state':state,'platform_writes':1,'id':VIDEO}):
            result = edits.run(key, request)
        ledger = Ledger()
        saved = ledger.get(key)['data']
        ledger.close()
        return result, saved

    def test_f2_verified_edit_updates_future_readback(self):
        key, request, durable, receipt, record = self.seed()
        result, saved = self.apply_edit(key, request, 'EDIT_VERIFIED')
        answer = verify_video(saved['receipt'], 'New title', 'New description')
        self.assertEqual('SUBMISSION_VERIFIED',answer['state'])
        self.assertEqual('New description',saved['receipt']['expected_text'])
        self.assertEqual(record['approved_plan'],saved['approved_plan'])
        self.assertEqual(record['edits'][0],saved['edits'][0])
        self.assertEqual(receipt,saved['edits'][-1]['previous_receipt'])
        self.assertEqual('New description',read_json(durable)['expected_text'])
        self.assertTrue(read_json(durable)['final_submit_attempted'])
        self.assertEqual(receipt['video_sha256'],read_json(durable)['video_sha256'])

    def test_uncertain_edit_does_not_advance_expectations(self):
        key, request, durable, receipt, record = self.seed()
        _, saved = self.apply_edit(key, request, 'EDIT_SUBMITTED_UNVERIFIED')
        self.assertEqual(record['options'],saved['options'])
        self.assertEqual(receipt,saved['receipt'])
        self.assertEqual(receipt,read_json(durable))

    def test_each_platform_looks_next_for_what_it_shows(self):
        self.assertEqual({'title':'T','expected_text':'D'}, edits.found_after_edit('youtube',{'title':'T','description':'D'}))
        self.assertEqual({'title':'T','expected_text':'T\n\nD'}, edits.found_after_edit('x',{'title':'T','description':'D','post_text':'T\n\nD'}))
        self.assertEqual({'title':'T','expected_text':'T','expected_caption':'C'}, edits.found_after_edit('douyin',{'title':'T','description':'D','caption':'C'}))
        self.assertEqual({'title':'T'}, edits.found_after_edit('bilibili',{'title':'T','description':'D'}))

    def test_durable_receipt_of_another_work_is_not_rewritten(self):
        key, request, durable, receipt, record = self.seed()
        other = {**receipt, 'id':'ZyXwVuTsR98'}
        atomic_json(durable,other)
        self.apply_edit(key, request, 'EDIT_VERIFIED')
        self.assertEqual(other,read_json(durable))


class ScheduledPublicationRegression(unittest.TestCase):
    def receipt(self, future=False):
        day = 3 if future else 1
        return {'state':'SUBMISSION_VERIFIED', 'method':'browser', 'platform':'youtube', 'id':VIDEO,
                'title':'Title', 'expected_text':'Description', 'visibility':'private',
                'schedule':{'at':f'2030-01-{day:02d}T12:00:00+00:00','date':[2030,1,day],'time':[12,0]}}

    def test_f3_elapsed_public_verifies_current_state_only(self):
        receipt = self.receipt()
        result = verify_video(receipt,visibility='Public')
        self.assertEqual('SUBMISSION_VERIFIED',result['state'])
        self.assertEqual(receipt['schedule'],result['schedule'])
        self.assertIs(False,result['schedule_evidence']['on_time_publication_verified'])
        self.assertIn('actual publication time and on-time release were not established',result['note'])

    def test_future_matching_schedule_is_verified(self):
        result = verify_video(self.receipt(True), visibility='Scheduled\nJan 3, 2030\n12:00 PM')
        self.assertEqual('SUBMISSION_VERIFIED',result['state'])
        self.assertIs(False,result['schedule_evidence']['scheduled_time_elapsed'])

    def test_premature_public_even_with_matching_date_is_unverified(self):
        result = verify_video(self.receipt(True), visibility='Public\nJan 3, 2030\n12:00 PM')
        self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])
        self.assertIn('visibility',result['readback_differs'])

    def test_elapsed_but_private_or_still_scheduled_is_unverified(self):
        for visibility in ('Private', 'Scheduled\nJan 1, 2030\n12:00 PM'):
            with self.subTest(visibility=visibility):
                result=verify_video(self.receipt(),visibility=visibility)
                self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])

    def test_elapsed_public_with_wrong_text_is_unverified(self):
        result=verify_video(self.receipt(),description='Different description',visibility='Public')
        self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])
        self.assertIn('description',result['readback_differs'])

    def test_unreadable_or_naive_schedule_never_proves_due(self):
        for when in ('invalid', '2030-01-01T12:00:00'):
            with self.subTest(when=when):
                receipt=self.receipt()
                receipt['schedule']['at']=when
                result=verify_video(receipt,visibility='Public')
                self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])
                self.assertIn('schedule_time',result['readback_differs'])

    def test_ordinary_unscheduled_public_readback_is_unchanged(self):
        receipt=self.receipt()
        receipt.pop('schedule')
        receipt['visibility']='public'
        result=verify_video(receipt,visibility='Public')
        self.assertEqual('SUBMISSION_VERIFIED',result['state'])
        self.assertNotIn('schedule_evidence',result)


if __name__ == '__main__':
    unittest.main()
