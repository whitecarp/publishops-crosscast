from copy import deepcopy
import os,tempfile,unittest
from unittest.mock import patch
from video_publisher import engagement as e, cli, operations
from video_publisher.core import home, VpubError


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
