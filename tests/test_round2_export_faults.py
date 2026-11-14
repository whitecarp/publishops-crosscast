"""Offline stores and fake pages only. No sign-in, network, or browser process."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import audit_export as export, audit_journal as journal, core, cli, faults, adapters
from video_publisher import browser_backends as browser, work_delete as wd, work_delete_state as ds, edits
import test_work_delete as deletion
import test_publish_workers as workers

class ExportTests(unittest.TestCase):
    setUp=deletion.WorkDeletionTests.setUp
    seed=deletion.WorkDeletionTests.seed

    def planted(self):
        secret='PRIVATE_CANARY_https://name.example/secret'
        key,_=self.seed('youtube',plan_hash='c'*64,approved_plan={'video_bytes':12345678},
            receipt={'state':'UNKNOWN','title':secret,'id':secret,'identity':secret,'error':{'code':secret},'final_presses':1},
            options={'method':'browser','visibility':'private','title':secret},title=secret,notes=secret)
        journal.RUN='d'*32
        journal.command_start(SimpleNamespace(command='verify',key=key))
        journal.transition('browser',key,'youtube',{'state':'UNKNOWN','phase':'submitting','final_presses':1})
        journal.command_end(SimpleNamespace(key=key),journal.STARTED,4,None,{})
        journal.command_start(SimpleNamespace(command='verify',key=key))
        journal.transition('browser',key,'youtube',{'state':'SUBMISSION_VERIFIED','final_presses':1})
        journal.command_end(SimpleNamespace(key=key),journal.STARTED,0,None,{})
        self.addCleanup(setattr,journal,'RUN',None)
        return key,secret

    def test_allowlist_canary_determinism_private_sources_never_opened(self):
        key,secret=self.planted()
        forbidden={'accounts','browser-jobs','browser-debug','media'}
        original=Path.open
        def checked(p,*a,**k):
            self.assertFalse(forbidden & set(p.parts),p)
            return original(p,*a,**k)
        with patch.object(Path,'open',checked),patch.object(adapters,'browser_call',side_effect=AssertionError('network')):
            a=export.export_data();b=export.export_data()
        self.assertEqual(a,b)
        text=json.dumps(a)
        for private in (key,secret,'c'*64,str(core.home()),'2026-11-03T00:00:00'):
            self.assertNotIn(private,text)
        pub=a['publications'][0]
        self.assertEqual(pub['error_code'],'other');self.assertIsNone(a['window']['origin'])
        self.assertEqual(pub['size_mib'],11.8)
        self.assertEqual(a['counts']['in_doubt_settled_by_verify'],1)
        self.assertEqual(a['counts']['presses_total'],1)

    def test_pseudonyms_change_with_salt_but_repeated_exports_do_not(self):
        self.planted();a=export.export_data()
        self.assertEqual(a['publications'][0]['ref'],export.export_data()['publications'][0]['ref'])
        (core.home()/'audit/salt').write_bytes(bytes(32))
        self.assertNotEqual(a['publications'][0]['ref'],export.export_data()['publications'][0]['ref'])

    def test_every_string_leaf_is_closed_or_typed(self):
        self.planted();data=export.export_data()
        words=export.STATES|export.CODES|export.PHASES|export.COMMANDS|export.FAULTS|export.SEEN|export.FIELDS|set(core.PLATFORMS)|{
            'crosscast-audit/1','relative','claim','save','ledger','browser','private','other','transition','command_start','command_end',
            'vcs','archive','local','unknown','win32','Windows','Linux','Darwin','linux','darwin','nt','posix','submitting'}
        def check(v):
            if isinstance(v,dict):
                for value in v.values(): check(value)
            elif isinstance(v,list):
                for value in v: check(value)
            elif isinstance(v,str):
                self.assertTrue(v in words or re.fullmatch(r'[a-f0-9]{8}|[a-f0-9]{16}|[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64}|\d{4}-\d{2}-\d{2}|[0-9][0-9A-Za-z.+-]{0,39}',v),repr(v))
        check(data)
        forged={'type':'transition','state':'secret_name','phase':'/private/path','error_code':'other_secret','ref':'raw_id','at':'bad'}
        clean=export.project_event(forged,lambda _:None)
        self.assertEqual(clean['state'],'other');self.assertIsNone(clean['ref'])
        self.assertNotIn('secret',json.dumps(clean))
        self.assertIsNone(export.project_event({'type':'span','slot':True},lambda _:None)['slot'])
        with self.assertRaises(core.VpubError): export.export_data(times='PRIVATE_CANARY')

    def test_window_exact_times_and_output_never_overwrites_input(self):
        self.planted();a=export.export_data(times='exact')
        at=a['events'][-1]['at']
        b=export.export_data(since=at,until=at,times='exact')
        self.assertTrue(b['events']);self.assertTrue(all(e['at']==at for e in b['events']))
        self.assertEqual(export.export_data(since='2100-01-01T00:00:00Z')['events'],[])
        with self.assertRaises(core.VpubError): export.export_data(since='2020-01-01')
        with self.assertRaises(core.VpubError): export.export_file(core.home()/'config.json')

    def test_window_excludes_untouched_earlier_publications_in_existing_ledger(self):
        # The study reuses its one data folder, which already has publications.
        # A historical receipt must not enter a phase export without an event
        # referring to it inside that phase. All stores here are temporary.
        with patch.object(journal, 'utc', return_value='2026-11-10T00:00:00.000Z'):
            old_key, _ = self.seed('douyin')
        with patch.object(journal, 'utc', return_value='2026-11-19T00:00:00.000Z'):
            current_key, _ = self.seed('youtube')
            journal.command_start(SimpleNamespace(command='verify', key=current_key))
            journal.command_end(SimpleNamespace(key=current_key), journal.STARTED, 0, None, {})
        self.addCleanup(setattr, journal, 'RUN', None)
        before = {p: p.read_bytes() for p in core.home().rglob('*') if p.is_file()}
        with patch.object(adapters, 'browser_call', side_effect=AssertionError('network')):
            all_rows = export.export_data(times='exact')
            out = Path(self.temp.name) / 'phase-export.json'
            args = cli.parser().parse_args(['audit', 'export', '--since',
                '2026-11-19T00:00:00Z', '--until', '2026-11-20T00:00:00Z', '--out', str(out)])
            cli.run_command(args)
            phase = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual({p['platform'] for p in all_rows['publications']}, {'douyin', 'youtube'})
        self.assertEqual([p['platform'] for p in phase['publications']], ['youtube'])
        old_ref = next(p['ref'] for p in all_rows['publications'] if p['platform'] == 'douyin')
        self.assertNotIn(old_ref, json.dumps(phase))
        self.assertNotIn(old_key, json.dumps(phase))
        self.assertEqual({p: p.read_bytes() for p in core.home().rglob('*') if p.is_file()}, before)

    def test_observation_only_changes_journal_and_failure_is_not_success(self):
        key,_=self.planted();db=core.home()/'publications.sqlite3';before=db.read_bytes()
        self.assertTrue(export.observe(key,'once')['recorded']);self.assertEqual(db.read_bytes(),before)
        with patch.object(journal,'append',return_value=False),self.assertRaises(core.VpubError) as e: export.observe(key,'absent')
        self.assertEqual(e.exception.code,'audit_recording_failed')

    def test_loss_counts_and_sequence_gaps_are_command_specific(self):
        self.planted();journal.RUN='b'*32
        journal.append('command_start',lambda _:dict(command='doctor',flags={}))
        with patch.object(journal,'home',side_effect=PermissionError()): journal.append('stage',lambda _: {})
        # A process-local loss at a different home cannot be attributed to this run.
        with journal._loss_lock: journal._losses[core.home()/'audit']=2
        journal.append('stage',lambda _:dict(stage='reading back'))
        a=export.export_data();r=next(x for x in a['runs'] if x['run']=='b'*32)
        self.assertEqual(r['dropped_events'],2);self.assertFalse(r['loss_complete'])
        self.assertFalse(any(p['history_complete'] for p in a['publications']))
        file=core.home()/'audit/journal.jsonl';lines=file.read_bytes().splitlines(keepends=True);file.write_bytes(b''.join(lines[:-2]+lines[-1:]))
        a=export.export_data();self.assertFalse(a['integrity']['chain_ok'])
        self.assertTrue(next(x for x in a['runs'] if x['run']=='b'*32)['sequence_gap'])

    def test_known_span_arithmetic(self):
        rows=[dict(slot=0,t_start=0,t_end=10,state='SUBMISSION_VERIFIED'),dict(slot=1,t_start=1,t_end=4,state='SUBMISSION_VERIFIED'),dict(slot=1,t_start=5,t_end=8,state='UNKNOWN')]
        self.assertEqual(export.concurrency(rows,2,12),dict(workers=2,wall=12,sum=16,overlap=6,idle_by_slot=[0,4],all_verified=False))

    def test_successful_command_transient_unknown_is_not_counted_as_uncertain(self):
        key,_=self.seed('youtube');journal.command_start(SimpleNamespace(command='publish'))
        journal.transition('browser',key,'youtube',{'state':'UNKNOWN','final_presses':1})
        journal.transition('browser',key,'youtube',{'state':'SUBMISSION_VERIFIED','final_presses':1})
        journal.command_end(SimpleNamespace(),journal.STARTED,0,None,{})
        counts=export.export_data()['counts'];self.assertEqual(counts['presses_total'],1);self.assertEqual(counts['in_doubt_total'],0)

    def test_an_additional_press_is_not_a_clean_verify_settlement(self):
        key,_=self.seed('youtube');journal.command_start(SimpleNamespace(command='publish'))
        journal.transition('browser',key,'youtube',{'state':'UNKNOWN','final_presses':1})
        journal.command_end(SimpleNamespace(),journal.STARTED,4,None,{})
        journal.command_start(SimpleNamespace(command='resume'))
        journal.transition('browser',key,'youtube',{'state':'SUBMISSION_VERIFIED','final_presses':2})
        journal.command_end(SimpleNamespace(),journal.STARTED,0,None,{})
        counts=export.export_data()['counts'];self.assertEqual(counts['in_doubt_open'],1);self.assertEqual(counts['in_doubt_settled_by_verify'],0)

    def test_export_cli_is_byte_stable_without_self_journaling(self):
        self.planted();out=Path(self.temp.name)/'public.json';before=(core.home()/'audit/journal.jsonl').read_bytes()
        args=cli.parser().parse_args(['audit','export','--out',str(out)])
        cli.run_command(args);a=out.read_bytes();cli.run_command(args)
        self.assertEqual(a,out.read_bytes());self.assertEqual(before,(core.home()/'audit/journal.jsonl').read_bytes())

class FaultTests(unittest.TestCase):
    setUp=deletion.WorkDeletionTests.setUp
    seed=deletion.WorkDeletionTests.seed
    code=deletion.WorkDeletionTests.code
    owned=deletion.WorkDeletionTests.owned
    deleted=deletion.WorkDeletionTests.deleted

    def arm(self,point):
        core.atomic_json(core.home()/'config.json',{'fault_injection':True})
        env=patch.dict(os.environ,VPUB_FAULT=point);env.start();self.addCleanup(env.stop)
        journal.RUN=os.urandom(16).hex();self.addCleanup(setattr,journal,'RUN',None)

    def tx(self,point=None):
        return browser.Transaction('youtube','owner',{'confirmation':'a'*64,'video_sha256':'b'*64,'options':{'account':'default','method':'browser','visibility':'private','title':'test'},'_fault':point})

    def test_disabled_config_has_no_effect(self):
        with patch.dict(os.environ,VPUB_FAULT='after_press'):
            self.assertIsNone(faults.gate('bilibili',{'visibility':'public'}))

    def test_all_faults_reject_public_account_bilibili_before_any_adapter(self):
        for point in faults.POINTS:
            self.arm(point)
            for p,v in [('youtube','public'),('youtube','account'),('douyin','public'),('bilibili','private'),('x','private')]:
                plan={'confirmation':'a','platforms':{p:{'method':'browser','visibility':v,'account':'default'}}}
                with patch.object(adapters,'preflight',side_effect=AssertionError('contact')) as pre, self.assertRaises(core.VpubError) as e:
                    cli.publish_plan(plan,'a',reviewed=True)
                self.assertEqual(e.exception.code,'fault_injection_refused');pre.assert_not_called()

    def test_fault_positions_save_uncertainty_then_never_press_twice(self):
        for point,writes in [('before_press',0),('after_press',1)]:
            with patch.dict(os.environ,VPUB_HOME=str(Path(self.temp.name)/point)):
                self.arm(point);tx=self.tx(point);button=SimpleNamespace(click=AsyncMock())
                with patch.object(browser,'risk_check',new=AsyncMock()):
                    with self.assertRaises(core.VpubError) as e: asyncio.run(tx.submit(SimpleNamespace(url='https://example.invalid'),button))
                    self.assertEqual(e.exception.code,'injected_fault')
                    self.assertEqual(button.click.await_count,writes)
                    saved=core.read_json(tx.path);self.assertEqual(saved['state'],'UNKNOWN');self.assertTrue(saved['final_submit_attempted']);self.assertEqual(saved['fault']['point'],point)
                    with self.assertRaises(core.VpubError): asyncio.run(tx.submit(SimpleNamespace(url=''),button))
                    self.assertEqual(button.click.await_count,writes)
                # Same command cannot fire a second hook.
                faults.fire(point,point,'f'*64,'youtube',lambda _:self.fail('second firing'))

    def test_external_changes_only_label_and_event(self):
        self.arm('external');tx=self.tx('external');button=SimpleNamespace(click=AsyncMock())
        with patch.object(browser,'risk_check',new=AsyncMock()): asyncio.run(tx.submit(SimpleNamespace(url='https://example.invalid'),button))
        self.assertEqual(button.click.await_count,1);self.assertEqual(core.read_json(tx.path)['fault']['point'],'external')
        self.assertFalse(any(e.get('fired') for e in export.export_data()['events'] if e['type']=='fault'))

    def test_delete_lost_readback_one_press_repeated_delete_refused_verify_recovers(self):
        self.arm('delete_readback_lost');key,_=self.seed('youtube',options={'method':'browser','visibility':'private','account':'default'})
        confirm=self.code(key);clicks=[]
        def fake(target,operation,path):
            if operation=='inspect': return self.owned(target)
            if operation=='delete': ds.begin_press(path,target);clicks.append(1);return self.deleted(target,platform_writes=1)
            self.assertEqual(operation,'verify');return self.deleted(target)
        with patch.object(wd,'backend',side_effect=fake):
            result=wd.run(key,confirm=confirm,person_approved=True)
            self.assertEqual(result['state'],'DELETE_PENDING');self.assertEqual(result['error']['code'],'injected_fault')
            with self.assertRaises(core.VpubError) as e: wd.run(key,confirm=confirm,person_approved=True)
            self.assertEqual(e.exception.code,'deletion_already_attempted')
            self.assertEqual(wd.run(key,verify_only=True)['state'],'DELETED_VERIFIED')
        self.assertEqual(clicks,[1]);self.assertEqual(core.read_json(ds.receipt_path(key))['fault']['point'],'delete_readback_lost')

    def test_external_resume_edit_delete_public_gate_precedes_contact(self):
        self.arm('external');key,_=self.seed('youtube',options={'method':'browser','visibility':'public'})
        for call in [lambda:cli.run_command(cli.parser().parse_args(['resume',key])),lambda:edits.run(key,'MUST_NOT_READ'),lambda:wd.run(key,verify_only=True)]:
            with patch.object(adapters,'browser_call',side_effect=AssertionError('contact')),self.assertRaises(core.VpubError) as e: call()
            self.assertEqual(e.exception.code,'fault_injection_refused')

    def test_external_delete_has_same_one_press_and_verified_outcome(self):
        self.arm('external');key,_=self.seed('youtube',options={'method':'browser','visibility':'private','account':'default'})
        confirm=self.code(key);calls=[]
        def fake(target,operation,path):
            calls.append(operation)
            if operation=='inspect':return self.owned(target)
            ds.begin_press(path,target);return self.deleted(target,platform_writes=1)
        with patch.object(wd,'backend',side_effect=fake): answer=wd.run(key,confirm=confirm,person_approved=True)
        self.assertEqual(answer['state'],'DELETED_VERIFIED');self.assertEqual(calls,['inspect','delete'])
        self.assertEqual(core.read_json(ds.receipt_path(key))['fault']['point'],'external')

    def test_external_resume_arms_the_worker_from_current_gate(self):
        self.arm('external');key,_=self.seed('youtube',options={'method':'browser','visibility':'private','account':'default'})
        def fake(platform,action,account,**payload):
            self.assertEqual((platform,action),('youtube','resume'));self.assertEqual(payload['plan']['_fault'],'external')
            return {'state':'SUBMISSION_VERIFIED','fault':{'point':'external','at':core.now()}}
        with patch.object(cli,'check_upload_copy'),patch.object(adapters,'browser_call',side_effect=fake):
            answer=cli.run_command(cli.parser().parse_args(['resume',key]))
        self.assertEqual(answer['state'],'SUBMISSION_VERIFIED')

    def test_fire_saves_label_before_event_and_has_no_platform_calls(self):
        self.arm('before_press');order=[]
        with patch.object(journal,'append',side_effect=lambda *a:order.append('event')),self.assertRaises(core.VpubError):
            faults.fire('before_press','before_press','a'*64,'youtube',lambda _:order.append('save'))
        self.assertEqual(order,['save','event'])
        self.assertNotRegex(inspect.getsource(faults),r'\.(?:click|fill|type|goto|request|post|get)\(' .replace('|get',''))

class SpanTests(unittest.TestCase):
    setUp=workers.PublishWorkersTests.setUp
    patch=workers.PublishWorkersTests.patch
    identity=staticmethod(workers.PublishWorkersTests.identity)
    verified=staticmethod(workers.PublishWorkersTests.verified)
    plan=workers.PublishWorkersTests.plan
    run_plan=workers.PublishWorkersTests.run_plan
    rows=workers.PublishWorkersTests.rows
    await_fake=workers.PublishWorkersTests.await_fake

    def test_faulted_publish_repeat_and_resume_keep_single_press(self):
        for point,expected in [('after_press',1),('before_press',0),('external',1)]:
            with patch.dict(os.environ,VPUB_HOME=str(self.root/point),VPUB_FAULT=point):
                core.atomic_json(core.home()/'config.json',{'fault_injection':True});journal.RUN=os.urandom(16).hex()
                button=SimpleNamespace(click=AsyncMock());plan=self.plan(('youtube',),visibility='private');plan['confirmation']='c'*64
                def publish(p,prepared,opts,checkpoint):
                    self.assertEqual(prepared['_fault'],point)
                    tx=browser.Transaction(p,'youtube-owner',{**prepared,'options':opts})
                    self.await_fake(tx.submit(SimpleNamespace(url='https://example.invalid'),button))
                    return tx.save(state='SUBMITTED_UNVERIFIED')
                self.publish.side_effect=publish
                with patch.object(browser,'risk_check',new=AsyncMock()),patch('video_publisher.rehearsal.require_local_prerequisites'):
                    answer=self.run_plan(plan)['results']['youtube']
                    self.assertEqual(answer['state'],'UNKNOWN' if point!='external' else 'SUBMITTED_UNVERIFIED')
                    self.assertEqual(self.run_plan(plan)['results']['youtube']['state'],'SKIPPED_EXISTING')
                    self.assertEqual(button.click.await_count,expected)
                    row=self.rows()['youtube']
                    def resume(p,action,account,**payload):
                        self.assertEqual(action,'resume');self.assertIsNone(payload['plan']['_fault'])
                        tx=browser.Transaction(p,'youtube-owner',payload['plan'],True)
                        self.assertTrue(tx.receipt['final_submit_attempted'])
                        return tx.save(state='SUBMISSION_VERIFIED',id='verified-existing')
                    # Disabling the switch must override the old armed upload plan.
                    with patch.dict(os.environ,VPUB_FAULT=''),patch.object(adapters,'browser_call',side_effect=resume):
                        cli.run_command(cli.parser().parse_args(['resume',row['key']]))
                    self.assertEqual(button.click.await_count,expected)
        journal.RUN=None

    def test_external_edit_is_labelled_and_keeps_plain_result(self):
        key='a'*64;result={'state':'EDIT_VERIFIED','platform_writes':1}
        with patch.object(adapters,'addon_installed',return_value=False),patch.object(adapters,'browser_call',return_value=result):
            answer=edits.send('youtube','default','owner',{'id':'abc'},key,{'expected_identity':'owner','title':'test'},False,False,fault='external')
        self.assertEqual(answer['state'],'EDIT_VERIFIED');self.assertEqual(answer['platform_writes'],1)
        saved=list((core.home()/'edits').glob('*.json'));self.assertEqual(len(saved),1)
        self.assertEqual(core.read_json(saved[0])['fault']['point'],'external')

    def test_real_dispatch_emits_serial_and_parallel_slot_spans(self):
        import time
        for n in (1,2):
            with patch.dict(os.environ,VPUB_HOME=str(self.root/str(n))):
                journal.command_start(SimpleNamespace(command='publish',workers=n))
                barrier=threading.Barrier(n)
                def fake(p,plan,o,checkpoint):
                    barrier.wait(3);time.sleep(.01);return self.verified(p,plan,o,checkpoint)
                self.publish.side_effect=fake
                with patch('video_publisher.rehearsal.require_local_prerequisites'):
                    self.run_plan(self.plan(),workers=n)
                rows=[e for e in export.export_data()['events'] if e['type']=='span']
                self.assertEqual(len(rows),2)
                self.assertEqual(set(e['slot'] for e in rows),{0} if n==1 else {0,1})
                rows.sort(key=lambda e:e['t_start'])
                self.assertEqual(rows[0]['t_end']<=rows[1]['t_start'],n==1)
        journal.RUN=None
