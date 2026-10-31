from copy import deepcopy
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from video_publisher import engagement as e,engagement_api as api,cli,operations
from video_publisher.core import home,read_json,VpubError,atomic_json


class EngagementTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=t.name);env.start();self.addCleanup(env.stop)
        self.observed={'identity':'owner','liked':False,'saved':False,'collection':None}
        self.actions=[]
    def dispatch(self,p,a,w=None,**kw):
        self.actions.append(a)
        if a=='set':
            e.submitting(kw['receipt_path'])
            self.observed['liked' if kw['kind']=='like' else 'saved']=kw['desired']
            return {'state':'ACTION_UNVERIFIED','platform_writes':1}
        return deepcopy(self.observed)
    def test_set_readback_repeat_noop_and_restore_retains_history(self):
        with patch.object(e,'dispatch',side_effect=self.dispatch):
            a=e.set_action('x','123456789','like');same=e.set_action('x','123456789','like')
            b=e.set_action('x','123456789','like',False)
        self.assertEqual('ACTION_VERIFIED',a['state']);self.assertEqual('NO_CHANGE',same['state'])
        self.assertEqual(2,self.actions.count('set'));self.assertNotEqual(a['key'],b['key'])
        self.assertEqual(2,len(list((home()/'engagement/receipts').glob('*.json'))))
    def test_preview_never_dispatches_a_write(self):
        with patch.object(e,'dispatch',side_effect=self.dispatch):r=e.set_action('x','123456789','save',dry_run=True)
        self.assertEqual('DRY_RUN',r['state']);self.assertEqual(['status'],self.actions)
        self.assertFalse((home()/'engagement/receipts').exists())
    def test_timeout_blocks_repeat_and_opposite_action(self):
        def fail(p,a,w=None,**kw):
            if a=='set':e.submitting(kw['receipt_path']);raise TimeoutError()
            return deepcopy(self.observed)
        with patch.object(e,'dispatch',side_effect=fail) as send:
            a=e.set_action('x','123456789','like');b=e.set_action('x','123456789','like');c=e.set_action('x','123456789','like',False)
        self.assertEqual(1,sum(v.args[1]=='set' for v in send.call_args_list))
        self.assertEqual('ACTION_UNVERIFIED',a['state']);self.assertTrue(b['reused']);self.assertTrue(c['reused'])
    def test_late_visibility_resolves_pending_without_second_send(self):
        def fail(p,a,w=None,**kw):
            if a=='set':e.submitting(kw['receipt_path']);raise TimeoutError()
            return deepcopy(self.observed)
        with patch.object(e,'dispatch',side_effect=fail):a=e.set_action('x','123456789','like')
        self.observed['liked']=True
        with patch.object(e,'dispatch',side_effect=self.dispatch):r=e.verify(a['key'])
        self.assertEqual('ACTION_VERIFIED',r['state']);self.assertEqual(0,r['platform_writes']);self.assertNotIn('set',self.actions)
    def test_unknown_state_and_bad_targets_fail_before_write(self):
        with patch.object(e,'dispatch',return_value={**self.observed,'liked':None}) as call:
            with self.assertRaises(VpubError):e.set_action('x','123456789','like')
            self.assertEqual(['status'],[v.args[1] for v in call.call_args_list])
        for p,w,c in [('x','https://x.com/me/status/1',None),('youtube','abcdefghijk','WL'),('instagram','abcde','folder')]:
            with self.assertRaises(VpubError):e.status(p,w,c)
    def test_youtube_save_requires_playlist_before_network(self):
        with patch.object(e,'dispatch') as call,self.assertRaises(VpubError):e.set_action('youtube','abcdefghijk','save')
        call.assert_not_called()
    def test_account_change_cannot_verify_success(self):
        original={'identity':'owner','kind':'like','desired':True}
        self.assertEqual('ACTION_UNVERIFIED',e.finish(original,{'identity':'other','liked':True})['state'])
    def test_failure_before_submit_is_not_claimed_as_a_write(self):
        def fail(p,a,w=None,**kw):
            if a=='set':raise VpubError('human_verification_required','Complete verification')
            return deepcopy(self.observed)
        with patch.object(e,'dispatch',side_effect=fail):r=e.set_action('x','123456789','like')
        self.assertEqual('ACTION_FAILED',r['state']);self.assertEqual(0,r['platform_writes'])
        self.observed['liked']=True
        with patch.object(e,'dispatch',side_effect=self.dispatch):checked=e.verify(r['key'])
        self.assertEqual('ACTION_FAILED',checked['state'])
    def test_race_to_desired_state_does_not_claim_an_action(self):
        def race(p,a,w=None,**kw):
            if a=='set':
                self.observed['liked']=True
                return {'state':'NO_CHANGE','platform_writes':0}
            return deepcopy(self.observed)
        with patch.object(e,'dispatch',side_effect=race):r=e.set_action('x','123456789','like')
        self.assertEqual('NO_CHANGE',r['state']);self.assertEqual(0,r['platform_writes'])
    def test_explicit_test_label_survives_receipt(self):
        with patch.object(e,'dispatch',side_effect=self.dispatch):r=e.set_action('x','123456789','like',test_id='hello-01')
        self.assertTrue(r['test']);self.assertEqual('hello-01',r['test_id'])
    def test_parser_exposes_exact_unlike_and_unsave(self):
        for kind in ('like','save'):
            a=cli.parser().parse_args(['engage',kind,'x','123456789','--remove'])
            self.assertTrue(a.remove);self.assertFalse(a.dry_run)
    def event(self,key,state='ACTION_VERIFIED',kind='like',desired=True,test=False):
        return {'event_id':key,'at':'2026-10-30T00:00:00+00:00','platform':'x','work_id':'123456789','action':'engagement',
                'result':{'key':key,'state':state,'kind':kind,'desired':desired,'test':test}}
    def test_report_deduplicates_verified_actions_and_labels_pending(self):
        events=[self.event('a'),self.event('a'),self.event('b',kind='save',desired=False),self.event('c','ACTION_UNVERIFIED')]
        r=operations.report('2026-01-01T00:00:00Z','en',_events=events)
        self.assertEqual(1,r['platforms']['x']['engagement_verified']['like_on'])
        self.assertEqual(1,r['platforms']['x']['engagement_verified']['save_off'])
        self.assertIn('remain unverified',r['summary'])
    def test_report_excludes_test_traffic_in_both_languages(self):
        for lang in ('zh','en'):
            r=operations.report('2026-01-01T00:00:00Z',lang,_events=[self.event('a',test=True)])
            self.assertEqual({},r['platforms']);self.assertNotIn('X',r['summary'])
    def test_report_does_not_count_failed_action_as_verified(self):
        r=operations.report('2026-01-01T00:00:00Z','zh',_events=[self.event('a','ACTION_FAILED')])
        self.assertEqual(0,r['platforms']['x']['engagement_verified']['like_on'])
        self.assertEqual(1,r['platforms']['x']['engagement_failed'])
        self.assertIn('提交前停止',r['summary'])


class APITests(unittest.TestCase):
    def setUp(self):
        self.s=MagicMock();self.s.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'owner'}]}
        self.s.videos.return_value.list.return_value.execute.return_value={'items':[{'id':'abcdefghijk'}]}
        self.s.videos.return_value.getRating.return_value.execute.return_value={'items':[{'videoId':'abcdefghijk','rating':'none'}]}
        self.s.playlists.return_value.list.return_value.execute.return_value={'items':[{'id':'PLtest','snippet':{'channelId':'owner'}}]}
        self.member={'id':'item','snippet':{'playlistId':'PLtest','resourceId':{'videoId':'abcdefghijk'}}}
        self.s.playlistItems.return_value.list.return_value.execute.return_value={'items':[self.member]}
    def test_youtube_playlist_owner_must_match(self):
        self.s.playlists.return_value.list.return_value.execute.return_value['items'][0]['snippet']['channelId']='other'
        with patch.object(api.adapters,'yt_service',return_value=self.s),self.assertRaises(VpubError):api.execute('youtube','status','abcdefghijk',collection='PLtest')
        self.s.playlistItems.assert_not_called()
    def test_youtube_duplicate_membership_is_not_blindly_removed(self):
        self.s.playlistItems.return_value.list.return_value.execute.return_value={'items':[self.member,self.member]}
        with patch.object(api.adapters,'yt_service',return_value=self.s),self.assertRaises(VpubError):api.execute('youtube','set','abcdefghijk',collection='PLtest',kind='save',desired=False)
        self.s.playlistItems.return_value.delete.assert_not_called()
    def test_youtube_noop_does_not_rate(self):
        with patch.object(api.adapters,'yt_service',return_value=self.s):r=api.execute('youtube','set','abcdefghijk',kind='like',desired=False)
        self.assertEqual('NO_CHANGE',r['state']);self.s.videos.return_value.rate.assert_not_called()
    def test_youtube_removes_exact_playlist_item_after_durable_submit(self):
        with tempfile.TemporaryDirectory() as t,patch.object(api.adapters,'yt_service',return_value=self.s),patch.object(api.adapters,'yt_credentials') as credentials:
            path=Path(t)/'receipt.json';atomic_json(path,{'state':'ACTION_READY'})
            credentials.return_value.has_scopes.return_value=True
            r=api.execute('youtube','set','abcdefghijk',kind='save',desired=False,collection='PLtest',receipt_path=str(path))
            self.assertEqual('ACTION_SUBMITTING',read_json(path)['state'])
        self.s.playlistItems.return_value.delete.assert_called_once_with(id='item');self.assertEqual(1,r['platform_writes'])
    def test_bilibili_remove_always_passes_exact_folder(self):
        with tempfile.TemporaryDirectory() as t,patch.object(api.adapters,'auth_status',return_value={'identity':'1'}),patch.object(api,'bili_folders',return_value=[{'id':9,'has_video':True,'default':True}]),patch.object(api.adapters,'bili',return_value={'liked':False}) as call:
            path=Path(t)/'receipt.json';atomic_json(path,{'state':'ACTION_READY'})
            api.bilibili('set','BV1234567890',kind='save',desired=False,receipt_path=str(path))
        self.assertEqual(['video','fav','BV1234567890','--folder','9','--remove'],call.call_args.args[0])
    def test_bilibili_missing_default_keeps_like_readable_but_stops_save(self):
        with patch.object(api.adapters,'auth_status',return_value={'identity':'1'}),patch.object(api,'bili_folders',return_value=[]),patch.object(api.adapters,'bili',return_value={'liked':True}):
            r=api.bilibili('status','BV1234567890')
            self.assertTrue(r['liked']);self.assertIsNone(r['saved'])
            with self.assertRaises(VpubError):api.bilibili('set','BV1234567890',kind='save',desired=True)


class DouyinDetailTests(unittest.TestCase):
    def test_exact_work_account_state_not_tooltip_or_count(self):
        from video_publisher.engagement_browser import douyin_detail_state
        self.assertEqual({'liked':True,'saved':False},douyin_detail_state({'status_code':0,'aweme_detail':{
            'aweme_id':'123','user_digged':1,'collect_stat':0,'statistics':{'digg_count':999}}},'123'))
    def test_other_work_and_missing_state_are_not_false(self):
        from video_publisher.engagement_browser import douyin_detail_state
        for data in ({'status_code':0,'aweme_detail':{'aweme_id':'other','user_digged':1,'collect_stat':0}},
                     {'status_code':0,'aweme_detail':{'aweme_id':'123','user_digged':0}},
                     {'status_code':1,'aweme_detail':{'aweme_id':'123','user_digged':0,'collect_stat':0}},
                     {'status_code':0,'aweme_detail':{'aweme_id':'123','user_digged':'0','collect_stat':0}}):
            with self.assertRaises(VpubError):douyin_detail_state(data,'123')
