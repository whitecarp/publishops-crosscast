from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from video_publisher import operations as ops, operations_api as api, cli
from video_publisher.core import atomic_json,home,digest,VpubError,read_json
from video_publisher.operations_browser import number,douyin_metrics


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,VPUB_HOME=self.tmp.name);self.env.start()
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.env.stop)
        self.comment={'id':'comment123','text':'请问歌名','author_id':'viewer','is_owner':False,'language_hint':'zh'}
        self.snapshot={'identity':'channel','items':[self.comment],'state':'OBSERVED'}
        atomic_json(home()/'operations/comments'/(digest(['youtube','abcdefghijk'])+'.json'),self.snapshot)

    def test_first_read_is_baseline_and_second_read_only_counts_new_ids(self):
        with patch.object(ops,'backend',return_value=deepcopy(self.snapshot)):
            first=ops.observe('youtube','abcdefghijk','comments')
        second=deepcopy(self.snapshot);second['items'].append({**self.comment,'id':'another'})
        with patch.object(ops,'backend',return_value=second): later=ops.observe('youtube','abcdefghijk','comments')
        self.assertIsNone(first['newly_observed_count']);self.assertEqual(1,later['newly_observed_count'])

    def test_reply_goes_directly_once_without_approval_hash(self):
        with patch.object(ops,'backend',return_value={'state':'REPLY_VERIFIED','reply_id':'new123','platform_writes':1}) as send:
            result=ops.reply('youtube','abcdefghijk','comment123','歌名是测试曲目。')
            duplicate=ops.reply('youtube','abcdefghijk','comment123','歌名是测试曲目。')
        self.assertEqual('REPLY_VERIFIED',result['state']);send.assert_called_once()
        self.assertEqual('歌名是测试曲目。',send.call_args.kwargs['reply_request']['text'])
        self.assertTrue(duplicate['reused']);self.assertEqual(0,duplicate['platform_writes'])

    def test_dry_run_never_dispatches_or_reserves_a_send(self):
        with patch.object(ops,'backend') as send:
            result=ops.reply('youtube','abcdefghijk','comment123','谢谢。',dry_run=True)
        send.assert_not_called();self.assertEqual(0,result['platform_writes'])
        self.assertFalse((home()/'operations/replies').exists())

    def test_explicitly_prepared_repair_retains_original_attempt_history(self):
        key=digest(['youtube','channel','abcdefghijk','comment123'])
        history=[{'state':'REPLY_UNVERIFIED','at':'2026-10-29T00:00:00Z'}]
        atomic_json(home()/'operations/replies'/(key+'.json'),{'state':'REPLY_READY','recovery_history':history})
        with patch.object(ops,'backend',return_value={'state':'REPLY_VERIFIED','reply_id':'new'}):
            result=ops.reply('youtube','abcdefghijk','comment123','你好')
        self.assertEqual(history,result['recovery_history'])

    def test_timeout_after_submit_blocks_resend(self):
        def uncertain(*a,**kw):
            p=Path(kw['reply_receipt_path']);d=read_json(p);d['state']='REPLY_SUBMITTING';atomic_json(p,d)
            raise TimeoutError()
        with patch.object(ops,'backend',side_effect=uncertain) as send:
            with self.assertRaises(TimeoutError):ops.reply('youtube','abcdefghijk','comment123','谢谢。')
            result=ops.reply('youtube','abcdefghijk','comment123','另一个版本。')
        send.assert_called_once();self.assertEqual('REPLY_UNVERIFIED',result['state'])

    def test_reply_language_is_independent_of_report_setting(self):
        ops.set_language('en')
        result=ops.reply('youtube','abcdefghijk','comment123','谢谢。',dry_run=True)
        self.assertEqual('zh',result['language']);self.assertEqual('谢谢。',result['text'])
        with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment123','Thanks',language='en',dry_run=True)

    def test_latin_text_requires_host_language_identification(self):
        self.snapshot['items'][0].update(text='Quelle musique?',language_hint='und')
        atomic_json(home()/'operations/comments'/(digest(['youtube','abcdefghijk'])+'.json'),self.snapshot)
        with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment123','Mystique.',dry_run=True)
        result=ops.reply('youtube','abcdefghijk','comment123','C’est Mystique.',language='fr',dry_run=True)
        self.assertEqual('fr',result['language'])

    def test_existing_owner_reply_is_not_replied_to_again(self):
        self.snapshot['items'][0]['owner_replied']=True
        atomic_json(home()/'operations/comments'/(digest(['youtube','abcdefghijk'])+'.json'),self.snapshot)
        with patch.object(ops,'backend') as send,self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment123','谢谢。')
        send.assert_not_called()

    def test_unseen_comment_and_option_like_work_ids_are_rejected(self):
        with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','other','谢谢。')
        with self.assertRaises(VpubError):ops.work_id('bilibili','--help')
        with self.assertRaises(VpubError):ops.work_id('x','https://x.com/owner/status/123')

    def test_report_counts_unique_comments_and_latest_reply_state(self):
        for _ in range(2):ops.record('comments','youtube','abcdefghijk',deepcopy(self.snapshot))
        ops.record('reply','youtube','abcdefghijk',{'reply_key':'k','state':'REPLY_UNVERIFIED'})
        ops.record('reply_verify','youtube','abcdefghijk',{'reply_key':'k','state':'REPLY_VERIFIED'})
        zh=ops.report(language='zh');en=ops.report(language='en')
        self.assertEqual(1,zh['platforms']['youtube']['comments_read'])
        self.assertEqual(1,zh['platforms']['youtube']['replies_verified'])
        self.assertEqual(0,zh['platforms']['youtube']['replies_pending'])
        self.assertIn('回复1条并确认可见',zh['summary']);self.assertIn('verified visibility',en['summary'])
        self.assertNotIn('点赞',zh['summary']);self.assertNotIn('liked',en['summary'])

    def test_report_defaults_to_saved_user_language(self):
        ops.set_language('en');self.assertEqual('en',ops.report()['language'])
        ops.set_language('zh');self.assertEqual('zh',ops.report()['language'])
        with self.assertRaises(VpubError):ops.report(since='2026-10-30T12:00:00')

    def test_failed_read_is_not_reported_as_empty_success(self):
        with patch.object(ops,'backend',side_effect=VpubError('comments_unavailable','Denied')):
            with self.assertRaises(VpubError):ops.observe('youtube','abcdefghijk','comments')
        result=ops.report(language='zh')
        self.assertIn('读取未完成',result['summary']);self.assertNotIn('读取0条评论',result['summary'])

    def test_parser_keeps_direct_reply_and_optional_preview(self):
        args=cli.parser().parse_args(['comments','reply','youtube','abcdefghijk','comment123','--text','Thanks','--language','en'])
        self.assertFalse(args.dry_run);self.assertEqual('en',args.language)

    def test_own_comments_are_only_allowed_for_the_exact_explicit_self_test(self):
        self.snapshot['items'][0]['is_owner']=True
        atomic_json(home()/'operations/comments'/(digest(['youtube','abcdefghijk'])+'.json'),self.snapshot)
        with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment123','你好',dry_run=True)
        key='a'*64
        atomic_json(home()/'operations/tests'/(key+'.json'),{'platform':'youtube','work_id':'abcdefghijk','comment_id':'comment123'})
        result=ops.reply('youtube','abcdefghijk','comment123','你好',dry_run=True,_test_key=key)
        self.assertTrue(result['test'])
        with self.assertRaises(VpubError):ops.reply('youtube','abcdefghijk','comment123','Different',dry_run=True,_test_key=key)

    def test_uncertain_self_test_never_posts_a_second_seed(self):
        key=digest(['youtube','abcdefghijk','test-one'])
        atomic_json(home()/'operations/tests'/(key+'.json'),{'platform':'youtube','work_id':'abcdefghijk','state':'TEST_SEED_SUBMITTING'})
        with patch.object(ops,'backend',return_value={'state':'TEST_SEED_UNVERIFIED'}) as backend:
            result=ops.self_test('youtube','abcdefghijk','test-one')
        self.assertEqual('TEST_SEED_UNVERIFIED',result['state'])
        self.assertEqual('test_seed_verify',backend.call_args.args[1]);backend.assert_called_once()

    def test_reports_exclude_self_test_replies_and_owner_comments(self):
        snapshot=deepcopy(self.snapshot);snapshot['items'][0]['is_owner']=True
        ops.record('comments','youtube','abcdefghijk',snapshot)
        ops.record('reply','youtube','abcdefghijk',{'test':True,'state':'REPLY_VERIFIED','reply_key':'test'})
        result=ops.report(language='en')['platforms']['youtube']
        self.assertEqual(0,result['comments_read']);self.assertEqual(0,result['replies_verified'])


class MetricsTests(unittest.TestCase):
    def test_missing_is_not_zero_and_units_remain_distinct(self):
        self.assertIsNone(number('-'));self.assertIsNone(number('loading'));self.assertEqual(0,number('0'))
        self.assertEqual(12000,number('1.2万'));self.assertEqual(64.29,number('64.29%'))
        d=douyin_metrics('播放量\n15\n点赞量\n-\n完播率\n0.00\n%\n2s跳出率\n64.29\n%')
        self.assertEqual(15,d['views']['value']);self.assertIsNone(d['likes']['value'])
        self.assertEqual(0,d['completion_percent']['value']);self.assertEqual('percent',d['bounce_2s_percent']['unit'])

    def test_ambiguous_repeated_metric_is_not_guessed(self):
        d=douyin_metrics('播放量\n15\n播放量\n99')
        self.assertIsNone(d['views']['value'])


class YouTubeOperationsTests(unittest.TestCase):
    def setUp(self):
        self.service=MagicMock()
        self.service.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'owner'}]}
        self.service.videos.return_value.list.return_value.execute.return_value={'items':[{'id':'abcdefghijk',
            'snippet':{'channelId':'owner','title':'Video'},'statistics':{'viewCount':'1','likeCount':'0'}}]}








class BilibiliOperationsTests(unittest.TestCase):
    def test_page_offset_does_not_skip_comments_when_limit_is_smaller_than_page(self):
        items=[{'rpid':i,'content':str(i),'user':{'mid':2}} for i in range(1,4)]
        with patch.object(api.adapters,'auth_status',return_value={'identity':'1'}),patch.object(api.adapters,'bili') as call:
            call.side_effect=[{'owner':{'mid':1}},{'items':items}]
            result=api.bilibili('comments','BV1234567890',limit=1,cursor=None)
            self.assertEqual('1:1',result['next_cursor'])
            call.side_effect=[{'owner':{'mid':1}},{'items':items}]
            next_page=api.bilibili('comments','BV1234567890',limit=1,cursor=result['next_cursor'])
            self.assertEqual('2',next_page['items'][0]['id'])

    def test_identical_reply_to_another_comment_is_not_verification(self):
        root={'rpid':1,'content':'Question','user':{'mid':2}}
        wrong={'rpid':3,'parent':999,'content':'Answer','user':{'mid':1}}
        with patch.object(api.adapters,'auth_status',return_value={'identity':'1'}),patch.object(api.adapters,'bili',side_effect=[{'owner':{'mid':1}},{'root':root,'items':[wrong]}]):
            result=api.bilibili('verify_reply','BV1234567890',reply_request={'expected_identity':'1','comment':{'id':'1','text':'Question'},'text':'Answer'})
        self.assertEqual('REPLY_UNVERIFIED',result['state'])
