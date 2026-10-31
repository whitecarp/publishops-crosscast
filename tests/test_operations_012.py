from copy import deepcopy
from contextlib import redirect_stderr
import io
import os
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from video_publisher import operations_runs as runs, operations as ops, operations_api as api
from video_publisher import messages, playlists, cli, operations_batch
from video_publisher.core import home, atomic_json, read_json, digest, VpubError


class Fixture(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=t.name);env.start();self.addCleanup(env.stop)


class SchedulerTests(Fixture):
    def watch(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[
            {'platform':'youtube','work_id':'abcdefghijk','identity':'owner'},
            {'platform':'instagram','work_id':'post123','identity':'owner'}]})

    def value(self,action):return {'identity':'owner','items':[],'metrics':{},'next_cursor':None,'complete':True}

    def test_parallel_reads_overlap_but_each_platform_has_one_context(self):
        self.watch();barrier=threading.Barrier(2)
        def backend(p,a,w,**kw):
            if a=='comments':barrier.wait(timeout=4)
            return self.value(a)
        def browser(p,a,account,**kw):
            barrier.wait(timeout=4)
            return {'identity':'owner','results':[{'job':j,'ok':True,'data':self.value(j['operation'])} for j in kw['jobs']]}
        output=io.StringIO()
        with patch.object(ops,'backend',side_effect=backend),patch.object(ops,'browser_identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=browser) as b,redirect_stderr(output):
            result=runs.sync(workers=2,progress=True)
        self.assertEqual('COLLECTED',result['state']);self.assertEqual(4,result['reads_completed']);b.assert_called_once()
        self.assertIn('read_progress',output.getvalue());self.assertNotIn('items',output.getvalue())
        self.assertEqual(1,len(runs.listing(limit=1)['items']))

    def test_child_interruption_restores_finished_job_without_replay(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[{'platform':'youtube','work_id':'abcdefghijk','identity':'owner'}]})
        with patch.object(ops,'backend',side_effect=[self.value('comments'),KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):runs.sync(workers=2)
        saved=runs.listing()['items'][0]
        with patch.object(ops,'backend',return_value=self.value('stats')) as backend:r=runs.resume(saved['run_id'])
        backend.assert_called_once();self.assertEqual('stats',backend.call_args.args[1]);self.assertEqual('COLLECTED',r['state'])

    def test_invalid_worker_budget(self):
        self.watch()
        for n in (0,5,True):
            with self.assertRaises(VpubError):runs.sync(workers=n)

    def test_youtube_session_reuses_service_and_owner_checks(self):
        s=MagicMock();s.channels().list().execute.return_value={'items':[{'id':'owner'}]}
        s.videos().list().execute.return_value={'items':[{'snippet':{'channelId':'owner','title':'Test'},'statistics':{},'status':{}}]}
        s.commentThreads().list().execute.return_value={'items':[]}
        with patch.object(api.adapters,'yt_service',return_value=s) as build:
            with api.read_session():
                api.execute('youtube','comments','abcdefghijk',limit=20)
                api.execute('youtube','stats','abcdefghijk')
                api.execute('youtube','comments','abcdefghijk',limit=20,cursor='next')
            self.assertEqual(1,build.call_count)
        self.assertEqual(1,s.channels().list().execute.call_count)
        self.assertEqual(1,s.videos().list().execute.call_count)
        s.close.assert_called_once()

    def test_api_context_does_not_leak_between_runs_or_threads(self):
        seen=[];barrier=threading.Barrier(2)
        def work():
            with api.read_session():
                seen.append(id(api._READ.cache));barrier.wait(timeout=3)
            self.assertIsNone(api._READ.cache)
        ts=[threading.Thread(target=work) for _ in range(2)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(2,len(set(seen)))

    def test_message_checkpoint_recovery_cache_and_private_summary(self):
        value={'identity':'owner','items':[{'id':'mention1','text':'private text'}],'complete':True}
        def failure(p,a,account,**kw):
            atomic_json(kw['progress_path'],{'identity':'owner','request_hash':digest(kw['jobs']),
                        'results':[{'job':kw['jobs'][0],'ok':True,'data':value}]})
            raise TimeoutError()
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=failure):
            first=messages.sync('x','mentions,dm_preview')
        self.assertEqual('PARTIAL',first['state']);self.assertNotIn('private text',str(first))
        def success(p,a,account,**kw):
            self.assertEqual(['dm_preview'],[j['operation'] for j in kw['jobs']])
            return {'identity':'owner','results':[{'job':kw['jobs'][0],'ok':True,'data':{'identity':'owner','items':[]}}]}
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=success):
            second=messages.resume(first['run_id'])
        self.assertEqual('COLLECTED',second['state'])
        with patch.object(messages.direct_messages,'identity',return_value='owner'),patch.object(runs.adapters,'browser_call') as backend:
            cached=messages.sync('x','mentions,dm_preview')
        backend.assert_not_called();self.assertEqual(2,cached['cached_reads'])
        self.assertTrue(all(r['newly_observed']==0 for r in cached['sources']))

    def test_message_merge_recovery_preserves_original_baseline_counts(self):
        value={'identity':'owner','items':[{'id':'id1','text':'text'}]}
        first=messages.merge('x','mentions',value,observation_id='same')
        again=messages.merge('x','mentions',value,observation_id='same')
        self.assertEqual(first,again);self.assertTrue(first['baseline'])

    def test_unsupported_message_sources_are_explicit(self):
        r=messages.sync('youtube','mentions,dm_preview')
        self.assertEqual('PARTIAL',r['state'])
        self.assertEqual(['UNSUPPORTED','UNSUPPORTED'],[s['state'] for s in r['sources']])
        self.assertEqual(0,r['reads_completed'])

    def test_cli_new_read_controls(self):
        p=cli.parser();a=p.parse_args(['ops','sync','--workers','2','--progress'])
        self.assertEqual(2,a.workers);self.assertTrue(a.progress)
        a=p.parse_args(['messages','sync','--platforms','x','--full'])
        self.assertTrue(a.full);self.assertEqual(300,a.min_interval)
        self.assertEqual('resume',p.parse_args(['messages','resume','a'*32]).action)


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_allowlist_and_message_account_scope(self):
        for operation in ('publish','reply','delete'):
            with self.assertRaises(VpubError):operations_batch.validate({'platform':'x','expected_identity':'owner','jobs':[{'operation':operation,'work_id':'@account','limit':20}]})
        with self.assertRaises(VpubError):operations_batch.validate({'platform':'x','expected_identity':'owner','jobs':[{'operation':'mentions','work_id':'12345678','limit':20}]})

    async def test_message_challenge_stops_remaining_batch_reads(self):
        page=MagicMock()
        jobs=[{'operation':k,'work_id':'@account','limit':20} for k in ('mentions','dm_preview')]
        with patch('video_publisher.browser_runtime.signed_in_page',new=AsyncMock(return_value=(page,'owner'))),patch('video_publisher.message_sources.execute',new=AsyncMock(return_value={'sources':{'mentions':{'state':'FAILED','code':'human_verification_required'}}})) as source:
            result=await operations_batch.execute(page,'x','owner',{'platform':'x','expected_identity':'owner','jobs':jobs})
        source.assert_awaited_once();self.assertEqual('batch_stopped',result['results'][1]['error']['code'])


class PlaylistTests(Fixture):
    def setUp(self):
        super().setUp();self.service=MagicMock();self.owner='owner'
        self.playlist={'id':'PLtest123','snippet':{'channelId':'owner','title':'Old','description':'Keep me','defaultLanguage':'en'},'status':{'privacyStatus':'private'},'contentDetails':{'itemCount':2}}
        self.item={'id':'item123','snippet':{'playlistId':'PLtest123','position':1,'resourceId':{'kind':'youtube#video','videoId':'abcdefghijk'}}}
        self.service.playlists().list().execute.return_value={'items':[self.playlist]}
        self.service.playlistItems().list().execute.return_value={'items':[self.item]}
        p=patch.object(playlists,'client',return_value=(self.service,self.owner));p.start();self.addCleanup(p.stop)
        p=patch.object(playlists.adapters,'yt_credentials');c=p.start();c.return_value.has_scopes.return_value=True;self.addCleanup(p.stop)

    def test_rename_preserves_description_language_and_privacy(self):
        def update(**kw):self.playlist['snippet']['title']='New';return self.playlist
        self.service.playlists().update().execute.side_effect=update
        r=playlists.change('update',playlist='PLtest123',title='New',request_id='rename')
        call=self.service.playlists().update.call_args
        self.assertEqual('snippet',call.kwargs['part']);self.assertEqual('Keep me',call.kwargs['body']['snippet']['description'])
        self.assertEqual('en',call.kwargs['body']['snippet']['defaultLanguage']);self.assertNotIn('status',call.kwargs['body'])
        self.assertEqual('CONFIRMED',r['state'])

    def test_repeat_request_never_resends_after_lost_response(self):
        self.service.playlists().insert().execute.side_effect=TimeoutError()
        first=playlists.change('create',title='Test',request_id='new')
        second=playlists.change('create',title='Test',request_id='new')
        self.assertEqual('UNVERIFIED',first['state']);self.assertTrue(second['reused'])
        self.assertEqual(1,self.service.playlists().insert().execute.call_count)
        self.assertEqual(0,second['platform_writes'])
        self.assertFalse(playlists.verify_value(self.service,first))

    def test_request_id_conflict_blocks_write(self):
        self.service.playlists().insert().execute.side_effect=TimeoutError()
        playlists.change('create',title='One',request_id='new')
        with self.assertRaises(VpubError):playlists.change('create',title='Two',request_id='new')
        self.assertEqual(1,self.service.playlists().insert().execute.call_count)

    def test_create_defaults_private_and_receipt_precedes_write(self):
        def insert(**kw):
            r=read_json(next((home()/'operations/playlists').glob('*.json')))
            self.assertEqual('SUBMITTING',r['state']);self.assertEqual(1,r['platform_writes'])
            self.playlist['snippet']['title']='Test';return {'id':'PLtest123'}
        self.service.playlists().insert().execute.side_effect=insert
        r=playlists.change('create',title='Test',request_id='new')
        self.assertEqual('private',self.service.playlists().insert.call_args.kwargs['body']['status']['privacyStatus'])
        self.assertEqual('CONFIRMED',r['state'])

    def test_readback_retries_only_reads_for_eventual_visibility(self):
        self.service.playlists().list().execute.side_effect=[{'items':[]},{'items':[self.playlist]}]
        r={'action':'create','playlist_id':'PLtest123','identity':'owner','desired':{'snippet':{'title':'Old'}}}
        with patch.object(playlists.time,'sleep') as sleep:self.assertTrue(playlists.readback(self.service,r))
        sleep.assert_called_once();self.service.playlists().insert.assert_not_called()

    def test_foreign_playlist_blocks_mutation(self):
        self.playlist['snippet']['channelId']='other'
        with self.assertRaises(VpubError):playlists.change('delete',playlist='PLtest123',request_id='delete')
        self.service.playlists().delete.assert_not_called()

    def test_foreign_item_blocks_move(self):
        self.item['snippet']['playlistId']='PLother'
        with self.assertRaises(VpubError):playlists.change('move',playlist='PLtest123',item='item123',position=0,request_id='move')
        self.service.playlistItems().update.assert_not_called()

    def test_existing_membership_does_not_insert(self):
        r=playlists.change('add',playlist='PLtest123',video='abcdefghijk',request_id='add')
        self.assertEqual('CONFIRMED',r['state']);self.assertEqual(0,r['platform_writes'])
        self.service.playlistItems().insert().execute.assert_not_called()

    def test_duplicate_membership_is_not_guessed(self):
        self.service.playlistItems().list().execute.return_value={'items':[self.item,self.item]}
        with self.assertRaises(VpubError):playlists.change('add',playlist='PLtest123',video='abcdefghijk',request_id='add')
        self.service.playlistItems().insert.assert_not_called()

    def test_move_preserves_resource_and_excludes_content_details(self):
        def update(**kw):self.item['snippet']['position']=0;return self.item
        self.service.playlistItems().update().execute.side_effect=update
        r=playlists.change('move',playlist='PLtest123',item='item123',position=0,request_id='move')
        args=self.service.playlistItems().update.call_args.kwargs
        self.assertEqual('snippet',args['part']);self.assertEqual('abcdefghijk',args['body']['snippet']['resourceId']['videoId'])
        self.assertEqual('CONFIRMED',r['state'])

    def test_out_of_range_or_special_playlist_rejected(self):
        for position in (-1,2,True):
            with self.assertRaises(VpubError):playlists.change('move',playlist='PLtest123',item='item123',position=position,request_id='move')
        for pid in ('WL','LL','UUuploads','../other'):
            with self.assertRaises(VpubError):playlists.change('delete',playlist=pid,request_id='delete')
        self.service.playlistItems().update.assert_not_called()

    def test_manual_order_requirement_stays_unverified_without_retry(self):
        self.service.playlistItems().update().execute.side_effect=RuntimeError('manualSortRequired')
        r=playlists.change('move',playlist='PLtest123',item='item123',position=0,request_id='move')
        self.assertEqual('UNVERIFIED',r['state']);self.assertIn('Manual',r['hint'])
        self.assertEqual(1,self.service.playlistItems().update().execute.call_count)

    def test_delete_readback_and_verify_do_not_delete_twice(self):
        def delete(**kw):self.service.playlists().list().execute.return_value={'items':[]}
        self.service.playlists().delete().execute.side_effect=delete
        r=playlists.change('delete',playlist='PLtest123',request_id='delete')
        self.assertEqual('CONFIRMED',r['state']);self.assertEqual('CONFIRMED',playlists.verify(r['key'])['state'])
        self.assertEqual(1,self.service.playlists().delete().execute.call_count)

    def test_playlist_cli(self):
        p=cli.parser();a=p.parse_args(['playlist','create','--title','Example','--request-id','new'])
        self.assertEqual('create',a.action);self.assertIsNone(a.privacy)
        a=p.parse_args(['playlist','move','PLtest123','item123','--position','0','--request-id','move'])
        self.assertEqual(0,a.position)


if __name__=='__main__':unittest.main()
