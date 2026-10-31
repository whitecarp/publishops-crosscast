import asyncio
from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import operations as ops, operations_inbox as inbox, operations_batch as batch
from video_publisher import operations_sync as sync, operations_runs as runs, comment_actions as actions, cli, messages
from video_publisher.core import VpubError, atomic_json, home


class LocalTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        e=patch.dict(os.environ,VPUB_HOME=t.name);e.start();self.addCleanup(e.stop)
        self.observation={'identity':'owner','state':'OBSERVED','items':[{'id':'comment','text':'你好','author_id':'viewer','is_owner':False}]}

    def observe(self,data=None):return ops.save_observation('youtube','abcdefghijk','comments',deepcopy(data or self.observation))

    def test_history_retained_when_next_page_is_empty(self):
        self.observe();self.observe({**self.observation,'items':[]})
        self.assertEqual('comment',messages.listing()['items'][0]['comment']['id'])

    def test_handled_persists_and_text_edit_reopens(self):
        self.observe();key=messages.listing()['items'][0]['key'];inbox.mark(key,'handled')
        self.observe();self.assertEqual([],messages.listing()['items'])
        changed=deepcopy(self.observation);changed['items'][0]['text']='新问题';self.observe(changed)
        item=messages.listing()['items'][0];self.assertEqual('unread',item['status']);self.assertEqual(2,item['revision'])

    def test_identity_and_work_keys_do_not_collide(self):
        self.observe();self.observe({**self.observation,'identity':'other'})
        self.assertEqual(2,messages.listing()['matching_total'])
        self.assertEqual(1,messages.listing(identity='owner')['matching_total'])

    def test_local_paging_and_marks_do_not_call_network(self):
        self.observe();self.observe({**self.observation,'identity':'other'})
        first=messages.listing(limit=1);second=messages.listing(limit=1,cursor=first['next_cursor'])
        self.assertNotEqual(first['items'][0]['key'],second['items'][0]['key'])
        with patch.object(ops,'backend') as network:
            for state in inbox.STATES:inbox.mark(first['items'][0]['key'],state)
        network.assert_not_called()

    def test_own_and_owner_answered_excluded_from_pending(self):
        self.observe({**self.observation,'items':[{'id':'own','is_owner':True}, {'id':'done','owner_replied':True}]})
        self.assertEqual([],messages.listing()['items'])
        self.assertEqual(2,len(messages.listing(status='all',include_own=True)['items']))

    def test_historical_reply_target_is_retained_but_backend_rechecks(self):
        self.observe();self.observe({**self.observation,'items':[]})
        with patch.object(ops,'backend',side_effect=VpubError('comment_changed','Gone')) as call:
            with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment','你好')
        self.assertEqual('comment',call.call_args.kwargs['reply_request']['comment']['id'])

    def test_batch_validation_rejects_writes_and_unknown_fields(self):
        base={'platform':'x','expected_identity':'owner','jobs':[{'operation':'stats','work_id':'123456789','limit':20}]}
        batch.validate(base)
        for change in ({'operation':'reply'},{'receipt_path':'x'},{'work_id':'../'},{'limit':0}):
            with self.assertRaises(VpubError):batch.validate({**base,'jobs':[{**base['jobs'][0],**change}]})

    def test_batch_preserves_first_result_and_stops_after_failure(self):
        jobs=[{'operation':a,'work_id':'123456789','limit':20} for a in ('comments','stats','comments')]
        with patch('video_publisher.operations_browser.execute',new=AsyncMock(side_effect=[{'items':[]},VpubError('human_required','Verify')])) as call:
            result=asyncio.run(batch.execute(None,'x','owner',{'platform':'x','expected_identity':'owner','jobs':jobs}))
        self.assertEqual(2,call.await_count);self.assertTrue(result['results'][0]['ok'])
        self.assertEqual('batch_stopped',result['results'][2]['error']['code'])
        self.assertEqual(0,result['platform_writes'])

    def test_sync_groups_two_works_one_browser_session(self):
        targets=[{'platform':'x','identity':'owner','work_id':w} for w in ('123456789','234567890')]
        atomic_json(home()/'operations/watchlist.json',{'items':targets})
        def run(p,action,account,**kw):
            self.assertEqual({(2,1)},{(j['pages'],j['reply_pages']) for j in kw['jobs'] if j['operation']=='comments'})
            return {'identity':'owner','results':[{'job':j,'ok':True,
                'data':deepcopy(self.observation if j['operation']=='comments' else {'identity':'owner','metrics':{},'source':'dom'})} for j in kw['jobs']]}
        with patch.object(ops,'browser_identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=run) as called:
            r=runs.sync(platforms='x')
        called.assert_called_once();self.assertEqual(1,r['browser_sessions']);self.assertEqual(2,len(r['items']))

    def test_reply_expansion_keeps_owner_reply_and_continuation(self):
        top={'id':'root','reply_count':4,'text':'Question','owner_replied':None}
        first={**self.observation,'items':[top],'complete':False,'next_cursor':None}
        page={**self.observation,'items':[top,{'id':'r','parent_id':'root','text':'Answer','is_owner':True}],'next_cursor':'more'}
        with patch.object(ops,'backend',side_effect=[first,page]):r=sync.collect('youtube','abcdefghijk','owner',20,1,1,1)
        self.assertTrue(r['items'][0]['owner_replied']);self.assertEqual('more',r['reply_continuations'][0]['next_cursor'])
        self.assertFalse(r['complete'])

    def test_comment_action_uncertainty_blocks_second_send(self):
        self.observe({**self.observation,'items':[{**self.observation['items'][0],'is_owner':True}]})
        def dispatch(req,operation='status',receipt_path=None):
            if operation=='status':return {'identity':'owner','text':'你好','deleted':False}
            actions.submitting(receipt_path);raise TimeoutError()
        with patch.object(actions,'dispatch',side_effect=dispatch) as call:
            first=actions.mutate('youtube','abcdefghijk','comment','edit','你好！',test_id='test')
            second=actions.mutate('youtube','abcdefghijk','comment','delete')
        self.assertEqual('COMMENT_UNVERIFIED',first['state']);self.assertTrue(second['reused']);self.assertEqual(2,call.call_count)

    def test_comment_actions_reject_unsupported_platform_and_audience_delete(self):
        self.observe()
        with patch.object(actions,'dispatch') as call:
            with self.assertRaises(VpubError):actions.mutate('youtube','abcdefghijk','comment','delete')
            with self.assertRaises(VpubError):actions.mutate('douyin','123456789','comment','like')
        call.assert_not_called()

    def test_comment_like_noop_and_dryrun_never_mutate(self):
        ops.save_observation('x','123456789','comments',deepcopy(self.observation))
        with patch.object(actions,'dispatch',return_value={'identity':'owner','text':'你好','liked':True}) as call:
            self.assertEqual('NO_CHANGE',actions.mutate('x','123456789','comment','like')['state'])
            self.assertEqual('DRY_RUN',actions.mutate('x','123456789','comment','like',False,True)['state'])
        self.assertTrue(all(len(c.args)==1 for c in call.call_args_list))

    def test_concurrent_matching_state_is_not_reported_as_our_write(self):
        ops.save_observation('x','123456789','comments',deepcopy(self.observation))
        with patch.object(actions,'dispatch',side_effect=[{'identity':'owner','text':'你好','liked':False},
                {'state':'NO_CHANGE','platform_writes':0},{'identity':'owner','text':'你好','liked':True}]):
            r=actions.mutate('x','123456789','comment','like')
        self.assertEqual('NO_CHANGE',r['state']);self.assertEqual(0,r['platform_writes'])

    def test_cli_exposes_local_status_and_bounded_replies(self):
        p=cli.parser()
        self.assertEqual('deferred',p.parse_args(['messages','mark','a'*64,'deferred']).status)
        self.assertEqual(1,p.parse_args(['ops','sync']).reply_pages)
        self.assertEqual('delete',p.parse_args(['comments','delete','youtube','abcdefghijk','id']).action)

    def test_report_deduplicates_management_and_excludes_test_writes(self):
        events=[{'action':'comment_management','platform':'youtube','work_id':'abcdefghijk','at':'2026-10-30T00:00:00Z','event_id':str(i),
                 'result':{'key':key,'state':state,'test':test}} for i,(key,state,test) in enumerate([
                     ('a','COMMENT_UNVERIFIED',False),('a','COMMENT_VERIFIED',False),('b','COMMENT_VERIFIED',True),('c','COMMENT_UNVERIFIED',False)])]
        for language in ('zh','en'):
            r=ops.report('2026-10-29T00:00:00Z',language,_events=events)['platforms']['youtube']
            self.assertEqual(1,r['comment_management_verified']);self.assertEqual(1,r['comment_management_pending'])

    def test_sync_does_not_resurface_local_handled_comment(self):
        self.observe();key=messages.listing()['items'][0]['key'];inbox.mark(key,'handled')
        atomic_json(home()/'operations/watchlist.json',{'items':[{'platform':'youtube','work_id':'abcdefghijk','identity':'owner'}]})
        with patch.object(ops,'backend',side_effect=[deepcopy(self.observation),{'identity':'owner','metrics':{},'source':'api'}]):
            r=runs.sync()
        self.assertEqual([],r['inbox'])
