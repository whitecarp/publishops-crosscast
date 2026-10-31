import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from video_publisher import messages, operations_inbox, operations, comment_actions as actions
from video_publisher import comment_actions_api as api, youtube_moderation, comment_collection as collection
from video_publisher.core import VpubError, home, atomic_json, read_json, digest


class InboxTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=tmp.name);env.start();self.addCleanup(env.stop)
        self.value={'identity':'owner','items':[{'conversation_id':'123-456','summary':'Contact\nHello\n2h'}]}

    def test_baseline_dedup_relative_time_and_handled_state(self):
        self.assertTrue(messages.merge('x','dm_preview',self.value)['baseline'])
        key=messages.listing()['items'][0]['key'];messages.mark(key,'handled')
        self.value['items'][0]['summary']='Contact\nHello\n3h'
        again=messages.merge('x','dm_preview',self.value)
        self.assertEqual(0,again['newly_observed']);self.assertEqual(0,again['changed'])
        self.assertEqual([],messages.listing()['items'])
        self.assertEqual('handled',messages.listing(status='all')['items'][0]['status'])

    def test_new_preview_reopens_same_stable_thread(self):
        messages.merge('x','dm_preview',self.value)
        key=messages.listing()['items'][0]['key'];messages.mark(key,'handled')
        self.value['items'][0]['summary']='Contact\nChanged message\n3h'
        self.assertEqual(1,messages.merge('x','dm_preview',self.value)['changed'])
        row=messages.listing()['items'][0]
        self.assertEqual(key,row['key']);self.assertEqual(2,row['revision']);self.assertEqual('unread',row['status'])

    def test_missing_rows_not_deleted_and_accounts_do_not_share_marks(self):
        messages.merge('x','dm_preview',self.value)
        messages.merge('x','dm_preview',{**self.value,'items':[]})
        self.assertEqual(1,len(messages.listing()['items']))
        messages.merge('x','dm_preview',{**self.value,'identity':'other'})
        rows=messages.listing()['items'];self.assertEqual(2,len(rows));self.assertNotEqual(rows[0]['key'],rows[1]['key'])

    def test_idless_previews_are_not_claimed_as_conversations(self):
        messages.merge('instagram','dm_preview',{'identity':'owner','items':[{'summary':'Contact\nHello\n2h'}]})
        row=messages.listing()['items'][0]
        self.assertFalse(row['stable_source_id']);self.assertTrue(row['source_id'].startswith('preview-'))

    def test_shared_comment_mark_is_not_duplicated(self):
        operations_inbox.merge('youtube','abc',{'identity':'owner','observed_at':'now','items':[{'id':'c','text':'Hi'}]})
        row=messages.listing()['items'][0];self.assertEqual('comments',row['kind'])
        messages.mark(row['key'],'handled')
        self.assertEqual([],operations_inbox.listing()['items']);self.assertEqual([],messages.listing()['items'])

    def test_failed_sync_does_not_advance_baseline(self):
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(messages.adapters,'browser_call',side_effect=TimeoutError):
            run=messages.sync('x','dm_preview')
        self.assertEqual('PARTIAL',run['state']);self.assertEqual('FAILED',run['sources'][0]['state'])
        self.assertFalse((home()/'operations/messages').exists())
        self.assertTrue(messages.merge('x','dm_preview',self.value)['baseline'])

    def test_wrong_account_never_persists_a_source(self):
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(messages.adapters,'browser_call',return_value={'identity':'other'}):
            self.assertEqual('FAILED',messages.sync('x','dm_preview')['sources'][0]['state'])
        self.assertFalse((home()/'operations/messages').exists())

    def test_summary_has_no_private_message_content(self):
        response={'identity':'owner','results':[{'job':{'operation':'dm_preview','work_id':'@account','limit':20},'ok':True,'data':self.value}]}
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(messages.adapters,'browser_call',return_value=response):
            run=messages.sync('x','dm_preview')
        self.assertEqual('COLLECTED',run['state']);self.assertEqual('OBSERVED',run['sources'][0]['state'])
        self.assertNotIn('Contact',str(run));self.assertNotIn('Hello',str(run));self.assertEqual(0,run['platform_writes'])

    def test_local_pagination_and_filters(self):
        self.value['items']*=2
        messages.merge('x','dm_preview',self.value)
        messages.merge('x','mentions',{'identity':'owner','items':[{'id':'one','text':'@owner hi'}]})
        first=messages.listing(limit=1);second=messages.listing(limit=1,cursor=first['next_cursor'])
        self.assertNotEqual(first['items'][0]['key'],second['items'][0]['key'])
        self.assertEqual(1,len(messages.listing(kind='mentions')['items']))


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_union_survives_virtualized_rows_and_never_claims_complete(self):
        page=MagicMock();page.wait_for_timeout=AsyncMock()
        values=[{'items':[{'id':'a','text':'One'}]},{'items':[{'id':'b','text':'Two'}]}]
        with patch('video_publisher.operations_browser.comments',new=AsyncMock(side_effect=values)),patch.object(collection,'risk_check',new=AsyncMock()),patch.object(collection,'expand',new=AsyncMock(return_value=0)),patch.object(collection,'scroll',new=AsyncMock(return_value=True)),patch.object(collection,'stop_check'):
            result=await collection.collect(page,'instagram','owner','post',50,2,0)
        self.assertEqual(['a','b'],[c['id'] for c in result['items']]);self.assertFalse(result['complete'])
        self.assertEqual('page_budget',result['stop_reason'])

    async def test_no_continuation_stops_without_repeating_scroll(self):
        page=MagicMock()
        with patch('video_publisher.operations_browser.comments',new=AsyncMock(return_value={'items':[]})),patch.object(collection,'risk_check',new=AsyncMock()),patch.object(collection,'expand',new=AsyncMock(return_value=0)),patch.object(collection,'scroll',new=AsyncMock(return_value=False)) as scroll,patch.object(collection,'stop_check'):
            result=await collection.collect(page,'douyin','owner','12345678',10,5,0)
        self.assertEqual(1,result['pages_read']);self.assertEqual('no_visible_continuation',result['stop_reason']);self.assertEqual(1,scroll.await_count)

    async def test_item_budget_prevents_extra_controls(self):
        page=MagicMock()
        with patch('video_publisher.operations_browser.comments',new=AsyncMock(return_value={'items':[{'id':'a'}]})),patch.object(collection,'risk_check',new=AsyncMock()),patch.object(collection,'expand',new=AsyncMock()) as expand,patch.object(collection,'stop_check'):
            result=await collection.collect(page,'x','owner','12345678',1,10,5)
        self.assertEqual('item_budget',result['stop_reason']);expand.assert_not_awaited()

    async def test_own_x_reply_is_rejected_before_browser_navigation(self):
        from video_publisher.comment_actions_x import execute
        page=MagicMock()
        with self.assertRaises(VpubError):await execute(page,'owner',{'request':{'identity':'owner','work_id':'12345678','comment':{'is_owner':True}}})
        page.goto.assert_not_called()


class ModerationTests(unittest.TestCase):
    def thread(self,children=0):
        return {'snippet':{'videoId':'abcdefghijk','totalReplyCount':children,'topLevelComment':{'id':'c','snippet':{'textDisplay':'Hi'}}}}

    def test_held_child_is_not_mistaken_for_held_root(self):
        s=MagicMock();s.commentThreads().list().execute.side_effect=[{'items':[]},{'items':[self.thread(1)]}]
        with patch.object(youtube_moderation,'stop_check'):self.assertIsNone(youtube_moderation.state(s,'abcdefghijk','c'))

    def test_exact_childless_queue_membership_confirms_state(self):
        s=MagicMock();s.commentThreads().list().execute.side_effect=[{'items':[]},{'items':[self.thread()]}]
        with patch.object(youtube_moderation,'stop_check'):self.assertEqual('heldForReview',youtube_moderation.state(s,'abcdefghijk','c'))

    def test_partial_queue_absence_is_unknown_not_published(self):
        s=MagicMock();s.commentThreads().list().execute.return_value={'items':[]}
        with patch.object(youtube_moderation,'stop_check'):self.assertIsNone(youtube_moderation.state(s,'abcdefghijk','c'))

    def test_conflicting_membership_is_unknown(self):
        s=MagicMock();s.commentThreads().list().execute.return_value={'items':[self.thread()]}
        with patch.object(youtube_moderation,'stop_check'):self.assertIsNone(youtube_moderation.state(s,'abcdefghijk','c'))

    def test_rejected_state_and_unknown_hide_cannot_match(self):
        self.assertFalse(actions.matches({'identity':'owner','kind':'hide','desired':False},{'identity':'owner','hidden':None}))
        self.assertTrue(actions.matches({'identity':'owner','kind':'moderate','desired':'published'},{'identity':'owner','moderation_status':'published'}))
        with patch.object(actions,'target') as target:
            with self.assertRaises(VpubError):actions.mutate('youtube','abcdefghijk','c','moderate','rejected')
            target.assert_not_called()

    def test_own_comment_moderation_stops_without_network(self):
        with patch.object(actions,'target',return_value={'comment':{'is_owner':True}}),patch.object(actions,'dispatch') as network:
            with self.assertRaises(VpubError):actions.mutate('youtube','abcdefghijk','c','moderate','heldForReview')
            network.assert_not_called()

    def test_viewer_moderation_checks_membership_and_never_bans_author(self):
        service=MagicMock();service.channels().list().execute.return_value={'items':[{'id':'owner'}]}
        service.videos().list().execute.return_value={'items':[{'snippet':{'channelId':'owner'}}]}
        service.comments().list().execute.return_value={'items':[{'id':'c','snippet':{'authorChannelId':{'value':'viewer'},'textDisplay':'Hi','moderationStatus':'published'}}]}
        service.commentThreads().list().execute.return_value={'items':[self.thread()]}
        request={'platform':'youtube','work_id':'abcdefghijk','identity':'owner','comment':{'id':'c','text':'Hi'},'kind':'moderate','desired':'heldForReview'}
        with tempfile.TemporaryDirectory() as tmp:
            path=__import__('pathlib').Path(tmp)/'receipt.json';atomic_json(path,{'state':'COMMENT_READY'})
            with patch.object(api.adapters,'yt_service',return_value=service),patch.object(api.adapters,'yt_credentials') as credentials,patch.object(api,'stop_check'):
                credentials.return_value.has_scopes.return_value=True
                result=api.execute(request,'set',path)
                self.assertEqual('COMMENT_UNVERIFIED',result['state'])
                service.comments().setModerationStatus.assert_called_once_with(id='c',moderationStatus='heldForReview')
                service.comments().delete.assert_not_called()

    def test_queue_cursor_loop_stops(self):
        s=MagicMock();s.commentThreads().list().execute.return_value={'items':[],'nextPageToken':'loop'}
        with patch.object(youtube_moderation,'stop_check'),self.assertRaises(VpubError):youtube_moderation.state(s,'abcdefghijk','c')


class ParserTests(unittest.TestCase):
    def test_new_commands_keep_mutations_explicit(self):
        from video_publisher.cli import parser
        p=parser()
        self.assertEqual('mentions,dm_preview',p.parse_args(['messages','sync','--platforms','x','--sources','mentions,dm_preview']).sources)
        self.assertEqual('heldForReview',p.parse_args(['comments','moderate','youtube','abcdefghijk','c','--status','heldForReview']).status)
        self.assertEqual(3,p.parse_args(['comments','collect','instagram','abcde']).pages)

    def test_read_batch_does_not_admit_writes_or_unbounded_budget(self):
        from video_publisher.operations_batch import validate
        payload={'platform':'x','expected_identity':'owner','jobs':[{'operation':'comments','work_id':'12345678','limit':20,'pages':3,'reply_pages':1}]}
        self.assertEqual(1,len(validate(payload)))
        payload['jobs'][0]['pages']=100
        with self.assertRaises(VpubError):validate(payload)
        payload['jobs'][0]['pages']=3;payload['jobs'][0]['operation']='reply'
        with self.assertRaises(VpubError):validate(payload)
