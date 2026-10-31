from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import operations as ops, operations_runs as runs, work_catalog as catalog, cli
from video_publisher.core import VpubError, atomic_json, read_json, home, digest


class SyncFixture(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=temp.name);env.start();self.addCleanup(env.stop)
        self.target={'platform':'youtube','work_id':'abcdefghijk','identity':'owner'}
        atomic_json(home()/'operations/watchlist.json',{'items':[self.target]})
        self.comments={'identity':'owner','items':[{'id':'q','text':'Hello','is_owner':False,'owner_replied':False}],
                       'complete':True,'replies_complete':True,'next_cursor':None,'source':'api'}
        self.stats={'identity':'owner','metrics':{},'source':'api'}

    def backend(self,p,a,w,**kw):return deepcopy(self.comments if a in {'comments','replies'} else self.stats)


class DurableSyncTests(SyncFixture):

    def test_cache_skips_network_and_retains_actual_read_time(self):
        with patch.object(ops,'backend',side_effect=self.backend) as backend:
            first=runs.sync();backend.reset_mock();second=runs.sync()
            backend.assert_not_called()
        self.assertEqual(2,second['cached_reads']);self.assertEqual(0,second['reads_completed'])
        self.assertEqual([],second['changes'])
        self.assertTrue(second['inbox'][0]['cached'])
        self.assertEqual(first['jobs'][0]['checked_at'],second['jobs'][0]['checked_at'])

    def test_full_and_zero_interval_bypass_cache(self):
        with patch.object(ops,'backend',side_effect=self.backend) as backend:
            runs.sync();backend.reset_mock();runs.sync(full=True);runs.sync(min_interval=0)
        self.assertEqual(4,backend.call_count)

    def test_expired_cache_and_changed_scope_are_read_again(self):
        with patch.object(ops,'backend',side_effect=self.backend) as backend:
            first=runs.sync()
            for path in (home()/'operations/sync-cache').glob('*.json'):
                value=read_json(path);value['checked_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat();atomic_json(path,value)
            backend.reset_mock();runs.sync();self.assertEqual(2,backend.call_count)
            backend.reset_mock();runs.sync(pages=3);self.assertEqual(2,backend.call_count)

    def test_api_second_page_interrupt_resumes_token_without_repeating_first(self):
        page1={**deepcopy(self.comments),'next_cursor':'next','complete':False}
        with patch.object(ops,'backend',side_effect=[page1,KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):runs.sync()
        run=read_json(next((home()/'operations/sync-runs').glob('*.json')))
        self.assertEqual('INTERRUPTED',run['state'])
        with patch.object(ops,'backend',side_effect=self.backend) as backend:
            result=runs.resume(run['run_id'])
        self.assertEqual('next',backend.call_args_list[0].kwargs['cursor'])
        self.assertEqual(2,backend.call_count);self.assertEqual('COLLECTED',result['state'])
        self.assertEqual(1,len(result['jobs'][0]['result']['items']))

    def test_finished_comment_job_not_replayed_after_stats_failure(self):
        with patch.object(ops,'backend',side_effect=[deepcopy(self.comments),VpubError('offline','offline')]):first=runs.sync()
        self.assertEqual('PARTIAL',first['state'])
        with patch.object(ops,'backend',return_value=deepcopy(self.stats)) as backend:second=runs.resume(first['run_id'])
        backend.assert_called_once();self.assertEqual('stats',backend.call_args.args[1])
        self.assertEqual('COLLECTED',second['state']);self.assertEqual(1,len(second['changes']))

    def test_completed_resume_has_no_network(self):
        with patch.object(ops,'backend',side_effect=self.backend):first=runs.sync()
        with patch.object(ops,'backend') as backend:r=runs.resume(first['run_id'])
        backend.assert_not_called();self.assertTrue(r['reused'])

    def test_account_change_does_not_advance_failed_page(self):
        with patch.object(ops,'backend',return_value={**self.comments,'identity':'other'}):r=runs.sync()
        self.assertEqual('PARTIAL',r['state']);self.assertEqual([],r['jobs'][0]['checkpoint']['root_pages'])
        self.assertFalse((home()/'operations/comments').exists())

    def test_changed_watchlist_blocks_resume(self):
        with patch.object(ops,'backend',side_effect=RuntimeError()):first=runs.sync()
        atomic_json(home()/'operations/watchlist.json',{'items':[{**self.target,'identity':'other'}]})
        with patch.object(ops,'backend') as backend,self.assertRaises(VpubError):runs.resume(first['run_id'])
        backend.assert_not_called()

    def test_changed_comment_emitted_once_and_missing_rows_retained(self):
        with patch.object(ops,'backend',side_effect=self.backend):
            runs.sync();self.comments['items'][0]['text']='Edited'
            r=runs.sync(full=True);self.assertEqual('changed',r['changes'][0]['change'])
            r=runs.sync(full=True);self.assertEqual([],r['changes'])
            self.comments['items']=[];runs.sync(full=True)
        from video_publisher.operations_inbox import find_item
        self.assertEqual('Edited',find_item('youtube','owner','abcdefghijk','q')['comment']['text'])

    def test_reply_page_checkpoint_preserves_parent_and_progress(self):
        root={**self.comments['items'][0],'reply_count':3}
        first={**self.comments,'items':[root]}
        child={'id':'r1','text':'reply','parent_id':'q','is_owner':False}
        page={'identity':'owner','items':[child],'next_cursor':'rnext'}
        with patch.object(ops,'backend',side_effect=[first,page,KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):runs.sync(reply_pages=2)
        saved=read_json(next((home()/'operations/sync-runs').glob('*.json')))
        with patch.object(ops,'backend',side_effect=[{**page,'next_cursor':None},self.stats]) as backend:r=runs.resume(saved['run_id'])
        self.assertEqual('replies',backend.call_args_list[0].args[1]);self.assertEqual('rnext',backend.call_args_list[0].kwargs['cursor'])
        self.assertEqual('COLLECTED',r['state'])

    def test_browser_checkpoint_survives_lost_process_response(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[{**self.target,'platform':'instagram','work_id':'abcde'}]})
        def failed(p,a,account,**kw):
            atomic_json(kw['progress_path'],{'identity':'owner','request_hash':digest(kw['jobs']),
                         'results':[{'job':kw['jobs'][0],'ok':True,'data':deepcopy(self.comments)}]})
            raise VpubError('browser_timeout','lost response')
        with patch.object(ops,'browser_identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=failed):first=runs.sync()
        self.assertEqual('DONE',first['jobs'][0]['state']);self.assertEqual('FAILED',first['jobs'][1]['state'])
        def success(p,a,account,**kw):
            self.assertEqual(['stats'],[j['operation'] for j in kw['jobs']])
            return {'identity':'owner','results':[{'job':kw['jobs'][0],'ok':True,'data':deepcopy(self.stats)}]}
        with patch.object(ops,'browser_identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=success):r=runs.resume(first['run_id'])
        self.assertEqual('COLLECTED',r['state'])

    def test_kernel_lock_rejects_second_runner_and_releases(self):
        with runs.run_lock():
            with self.assertRaises(VpubError):
                with runs.run_lock():pass
        with runs.run_lock():pass

    def test_invalid_run_path_and_limits(self):
        for key in ('../other','abc',None):
            with self.assertRaises(VpubError):runs.status(key)
        with patch.object(ops,'backend') as backend:
            for kwargs in ({'pages':0},{'min_interval':-1},{'min_interval':999999},{'reply_threads':21}):
                with self.assertRaises(VpubError):runs.sync(**kwargs)
        backend.assert_not_called()

    def test_runs_finds_hard_interruption_without_stdout_run_id(self):
        run=runs.make_run('youtube',20,2,'en',1,5,300,False)
        item=runs.listing()['items'][0]
        self.assertEqual(run['run_id'],item['run_id']);self.assertEqual(0,item['finished'])
        self.assertIn(run['run_id'],item['resume_command'])

    def test_cursor_loop_retains_only_last_validated_page(self):
        page={**self.comments,'next_cursor':'again'}
        with patch.object(ops,'backend',return_value=page):r=runs.sync(pages=3)
        self.assertEqual('cursor_loop',r['jobs'][0]['error'])
        self.assertEqual(1,len(r['jobs'][0]['checkpoint']['root_pages']))

    def test_account_scoped_cache_not_used_after_owner_change(self):
        with patch.object(ops,'backend',side_effect=self.backend):runs.sync()
        atomic_json(home()/'operations/watchlist.json',{'items':[{**self.target,'identity':'other'}]})
        with patch.object(ops,'backend',return_value={**self.comments,'identity':'other'}) as backend:r=runs.sync()
        self.assertEqual(0,r['cached_reads']);self.assertEqual(2,backend.call_count)

    def test_unrelated_platform_continues_after_account_failure(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[self.target,{**self.target,'platform':'instagram','work_id':'abcde'}]})
        def good(p,a,account,**kw):
            return {'identity':'owner','results':[{'job':j,'ok':True,'data':deepcopy(self.comments if j['operation']=='comments' else self.stats)} for j in kw['jobs']]}
        with patch.object(ops,'backend',side_effect=VpubError('auth_expired','auth')), \
             patch.object(ops,'browser_identity',return_value='owner'),patch.object(runs.adapters,'browser_call',side_effect=good):r=runs.sync()
        self.assertEqual('PARTIAL',r['state']);self.assertEqual(['DONE','DONE'],[j['state'] for j in r['jobs'] if j['platform']=='instagram'])


class AdoptionTests(SyncFixture):
    def test_adoption_is_separate_from_publish_ledger_and_idempotent(self):
        value={'identity':'owner','work_id':'abcde','owner_verified':True,'ownership_evidence':'owner_edit_control','url':'https://www.instagram.com/p/abcde/'}
        with patch.object(runs.adapters,'browser_call',return_value=value):
            a=catalog.adopt('instagram','abcde');b=catalog.adopt('instagram','abcde')
        self.assertTrue(b['reused']);self.assertEqual('owner',ops.browser_identity('instagram','abcde'))
        self.assertEqual(0,a['platform_writes']);self.assertEqual('adopted_existing_work',a['kind'])
        from video_publisher.core import Ledger
        ledger=Ledger()
        try:self.assertEqual([],list(ledger.rows()))
        finally:ledger.close()

    def test_unverified_adoption_does_not_write(self):
        with patch.object(runs.adapters,'browser_call',return_value={'owner_verified':False}),self.assertRaises(VpubError):catalog.adopt('instagram','abcde')
        self.assertFalse((home()/'operations/adopted').exists())

    def test_discovery_not_automatically_adopted(self):
        value={'identity':'owner','items':[{'id':'abcde'}],'complete':False}
        with patch.object(runs.adapters,'browser_call',return_value=value):catalog.discover('instagram')
        with self.assertRaises(VpubError):ops.browser_identity('instagram','abcde')

    def test_conflicting_watch_owner_does_not_create_adoption(self):
        atomic_json(home()/'operations/watchlist.json',{'items':[{'platform':'instagram','work_id':'abcde','identity':'other'}]})
        value={'identity':'owner','work_id':'abcde','owner_verified':True,'ownership_evidence':'owner_edit_control'}
        with patch.object(runs.adapters,'browser_call',return_value=value),self.assertRaises(VpubError):catalog.adopt('instagram','abcde')
        self.assertIsNone(catalog.binding('instagram','abcde'))

    def test_adoption_must_match_requested_work(self):
        value={'identity':'owner','work_id':'other','owner_verified':True,'ownership_evidence':'owner_edit_control'}
        with patch.object(runs.adapters,'browser_call',return_value=value),self.assertRaises(VpubError):catalog.adopt('instagram','abcde')
        self.assertIsNone(catalog.binding('instagram','abcde'))

    def test_cli_defaults_and_commands(self):
        p=cli.parser()
        self.assertEqual(300,p.parse_args(['ops','sync']).min_interval)
        self.assertTrue(p.parse_args(['ops','sync','--full']).full)
        self.assertEqual('adopt',p.parse_args(['ops','adopt','instagram','abcde']).action)
        self.assertEqual('resume',p.parse_args(['ops','resume','a'*32]).action)
