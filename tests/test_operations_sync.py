from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import operations as ops, operations_sync as sync, operations_api as api, cli
from video_publisher.core import atomic_json, home, VpubError, digest, read_json


class SyncTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        e=patch.dict(os.environ,VPUB_HOME=t.name);e.start();self.addCleanup(e.stop)
        self.target={'platform':'youtube','work_id':'abcdefghijk','identity':'owner'}
        atomic_json(home()/'operations/watchlist.json',{'items':[self.target]})
        self.stats={'identity':'owner','work_id':'abcdefghijk','source':'api','state':'OBSERVED',
                    'metrics':{'views':api.metric(10,'Views'),'percent':api.metric(5,'Completion','percent')}}
        self.comments={'state':'OBSERVED','identity':'owner','items':[{'id':'q','text':'你好','is_owner':False,'owner_replied':False}],
                       'complete':True,'replies_complete':True,'next_cursor':None}

    def test_sync_only_reads_and_excludes_own_or_already_answered_comments(self):
        self.comments['items'] += [{'id':'own','is_owner':True},{'id':'answered','is_owner':False,'owner_replied':True}]
        def run(p,action,w,**kw):return deepcopy(self.comments if action=='comments' else self.stats)
        with patch.object(ops,'backend',side_effect=run) as backend:
            first=sync.sync(language='en');second=sync.sync(language='zh')
        self.assertEqual({'comments','stats'},{c.args[1] for c in backend.call_args_list})
        self.assertEqual('COLLECTED',first['state']);self.assertEqual(0,first['platform_writes'])
        self.assertEqual(['q'],[c['comment']['id'] for c in first['inbox']])
        self.assertIsNone(first['items'][0]['comments']['newly_observed_count'])
        self.assertEqual(0,second['items'][0]['comments']['newly_observed_count'])
        self.assertEqual(0,second['items'][0]['stats']['changes']['views']['delta'])

    def test_bounded_pagination_deduplicates_and_keeps_reply_incompleteness(self):
        a={**self.comments,'complete':False,'replies_complete':False,'next_cursor':'page2'}
        with patch.object(ops,'backend',side_effect=[a,self.comments]):
            result=sync.collect('youtube','abcdefghijk','owner',20,2)
        self.assertEqual(1,len(result['items']));self.assertEqual(2,result['pages_read']);self.assertFalse(result['complete'])

    def test_page_budget_retains_next_cursor(self):
        a={**self.comments,'complete':False,'next_cursor':'page2'}
        with patch.object(ops,'backend',return_value=a):result=sync.collect('youtube','abcdefghijk','owner',20,1)
        self.assertEqual('page2',result['next_cursor']);self.assertFalse(result['complete'])

    def test_browser_zero_counter_cannot_claim_complete_conversation(self):
        for platform in ('instagram','x','douyin'):
            with patch.object(ops,'backend',return_value=deepcopy(self.comments)):
                result=sync.collect(platform,'123456789','owner',20,2)
            self.assertFalse(result['complete'])

    def test_run_report_excludes_previous_successful_sends(self):
        ops.record('reply','youtube','abcdefghijk',{'reply_key':'old','state':'REPLY_VERIFIED'})
        with patch.object(ops,'backend',side_effect=[deepcopy(self.comments),deepcopy(self.stats)]):
            result=sync.sync()
        self.assertEqual(0,result['report']['platforms']['youtube']['replies_verified'])

    def test_repeated_cursor_stops_without_advancing_snapshot(self):
        a={**self.comments,'next_cursor':'same'}
        with patch.object(ops,'backend',return_value=a),self.assertRaises(VpubError):sync.collect('youtube','abcdefghijk','owner',20,3)
        self.assertFalse((home()/'operations/comments').exists())

    def test_wrong_account_fails_closed_and_preserves_snapshot(self):
        with patch.object(ops,'backend',return_value={**self.comments,'identity':'other'}) as backend:
            result=sync.sync()
        self.assertEqual('PARTIAL',result['state']);self.assertEqual('wrong_account',result['errors'][0]['code'])
        backend.assert_called_once();self.assertFalse((home()/'operations/comments').exists())

    def test_failure_does_not_prevent_other_platform_from_reading(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[self.target,{**self.target,'platform':'instagram','work_id':'abcde'}]})
        def run(p,a,w,**kw):
            if p=='youtube':raise VpubError('oauth_scope_required','Authorization required')
            return deepcopy(self.comments if a=='comments' else self.stats)
        batch={'browser_sessions':1,'results':[{'job':{'work_id':'abcde','operation':a},'ok':True,'data':run('instagram',a,'abcde')} for a in ('comments','stats')]}
        with patch.object(ops,'backend',side_effect=run),patch.object(sync,'browser_batch',return_value=batch):result=sync.sync()
        self.assertEqual('PARTIAL',result['state']);self.assertIn('stats',result['items'][1])
        self.assertEqual(1,len(result['errors']))

    def test_empty_watchlist_and_invalid_limits_do_no_network(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[]})
        with patch.object(ops,'backend') as backend:
            for kwargs in ({},{'pages':0},{'limit':101},{'platforms':'unknown'}):
                with self.assertRaises(VpubError):sync.sync(**kwargs)
        backend.assert_not_called()

    def test_stats_delta_requires_same_source_unit_and_exact_numbers(self):
        current=deepcopy(self.stats);current['metrics']['views']['value']=7;current['metrics']['percent']['value']=8
        d=sync.metric_changes(self.stats,current)
        self.assertEqual(-3,d['views']['delta']);self.assertEqual(3,d['percent']['delta']);self.assertEqual('percentage_points',d['percent']['unit'])
        for change in ({'source':'browser'},{'identity':'different'},{'work_id':'different'}):
            self.assertIsNone(sync.metric_changes(self.stats,{**current,**change})['views']['delta'])
        for field,value in [('value',None),('value',float('nan')),('approximate',True),('unit','percent')]:
            altered=deepcopy(current);altered['metrics']['views'][field]=value
            self.assertIsNone(sync.metric_changes(self.stats,altered)['views']['delta'])

    def test_watch_add_deduplicates_and_remove_does_not_read(self):
        with patch.object(ops,'observe',return_value=self.stats) as read:
            sync.watch('add','youtube','abcdefghijk');r=sync.watch('add','youtube','abcdefghijk')
            self.assertEqual(1,len(r['items']))
            read.reset_mock();sync.watch('remove','youtube','abcdefghijk');read.assert_not_called()

    def test_parser_exposes_all_platform_works_and_replies(self):
        self.assertEqual('youtube',cli.parser().parse_args(['works','youtube']).platform)
        self.assertEqual('replies',cli.parser().parse_args(['comments','replies','youtube','abcdefghijk','top']).action)
        self.assertEqual(2,cli.parser().parse_args(['ops','sync']).pages)


class WorksAndRepliesTests(unittest.TestCase):
    def setUp(self):
        self.s=MagicMock()
        self.s.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'owner','contentDetails':{'relatedPlaylists':{'uploads':'uploads'}}}]}
        self.s.videos.return_value.list.return_value.execute.return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':'owner','title':'Title'}}]}

    def test_youtube_uses_uploads_not_search_and_preserves_page_token(self):
        self.s.playlistItems.return_value.list.return_value.execute.return_value={'items':[{'contentDetails':{'videoId':'abcdefghijk'}}],'nextPageToken':'next'}
        with patch.object(sync.adapters,'yt_service',return_value=self.s):r=sync.works('youtube',2,'previous')
        self.assertEqual('next',r['next_cursor']);self.assertFalse(r['complete'])
        self.s.search.assert_not_called();self.assertEqual('previous',self.s.playlistItems.return_value.list.call_args.kwargs['pageToken'])

    def test_bilibili_works_exclude_scheduled_and_keep_pagination(self):
        with patch.object(sync.adapters,'auth_status',return_value={'identity':'owner'}),patch.object(sync.adapters,'bili',return_value={
            'counts':{'published':3},'items':[{'bvid':'BV1234567890','title':'Title','published':True},{'bvid':'scheduled','title':'Later','published':False}]}):
            r=sync.works('bilibili',2)
        self.assertEqual(['BV1234567890'],[v['id'] for v in r['items']]);self.assertEqual('2',r['next_cursor'])

    def test_browser_works_reject_cursor_instead_of_ignoring_it(self):
        with patch.object(sync.adapters,'browser_call') as call,self.assertRaises(VpubError):sync.works('instagram',20,'2')
        call.assert_not_called()

    def test_youtube_replies_check_work_and_parent(self):
        root={'id':'top','snippet':{'textOriginal':'Question','authorChannelId':{'value':'viewer'}}}
        self.s.commentThreads.return_value.list.return_value.execute.return_value={'items':[{'id':'top','snippet':{'videoId':'abcdefghijk','topLevelComment':root}}]}
        self.s.comments.return_value.list.return_value.execute.return_value={'items':[{'id':'reply','snippet':{'parentId':'top','textOriginal':'Hello','authorChannelId':{'value':'owner'}}}],'nextPageToken':'next'}
        with patch.object(api.adapters,'yt_service',return_value=self.s):
            r=api.execute('youtube','replies','abcdefghijk',comment_id='top',limit=2,cursor=None)
            self.assertEqual('next',r['next_cursor']);self.assertTrue(r['items'][0]['owner_replied'])
            self.s.commentThreads.return_value.list.return_value.execute.return_value['items'][0]['snippet']['videoId']='another'
            self.s.comments.reset_mock()
            with self.assertRaises(VpubError):api.execute('youtube','replies','abcdefghijk',comment_id='top',limit=2)
            self.s.comments.assert_not_called()

    def test_bilibili_reply_page_caps_size_and_keeps_root(self):
        with patch.object(api.adapters,'auth_status',return_value={'identity':'1'}),patch.object(api.adapters,'bili') as call:
            call.side_effect=[{'owner':{'mid':1}},{'root':{'rpid':1,'content':'Hi','user':{'mid':2}},'items':[], 'total':50}]
            r=api.bilibili('replies','BV1234567890',comment_id='1',limit=100,cursor='2')
        self.assertEqual('3',r['next_cursor']);self.assertFalse(r['complete']);self.assertIsNone(r['items'][0]['owner_replied'])
        self.assertEqual('20',call.call_args.args[0][-1])
