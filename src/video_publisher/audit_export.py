"""Allow-list export. No platform, sign-in file, browser, or media is opened."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import sqlite3

from .core import PLATFORMS, VpubError, atomic_json, home
from . import audit_journal as journal

HEX = re.compile(r"^[a-f0-9]{64}$")
REF = re.compile(r"^[a-f0-9]{16}$")
STATES = set("PREPARING IN_PROGRESS UNKNOWN PREPARATION_INCOMPLETE PREVIEW_READY WAITING_FOR_USER SUBMITTED_UNVERIFIED SUBMISSION_VERIFIED AGENT_REPORTED_VERIFIED SKIPPED_EXISTING NOT_STARTED DELETE_READY DELETE_PENDING DELETED_VERIFIED DELETE_PREVIEW EDIT_PREPARING EDIT_SUBMITTING EDIT_SUBMITTED_UNVERIFIED EDIT_VERIFIED EDIT_MISMATCH PENDING_WORK_ID PENDING SUBMITTED VERIFIED FAILED NOT_SUPPORTED forgotten".split())
CODES = set("stopped interrupted confirmation_required deletion_already_attempted deletion_not_allowed deletion_target_unverified deletion_target_changed unsupported_delete attempt_exists already_submitted press_blocked cover_blocked wrong_account wrong_work owner_unverified needs_person human_verification_required injected_fault fault_injection_refused backend_timeout browser_timeout readback_failed schedule_unverified identity_mismatch busy file_not_found invalid_file internal_error submission_unverified preparation_incomplete inspect_existing_edit receipt_missing stopped_before_press draft_only".split())
PHASES = set("initial selecting_media uploading submitting reading_back complete blocked metadata cover schedule draft pending_chapters".split())
COMMANDS = set("publish go resume verify edit edit-work delete works stats status doctor receipts plan prepare snapshot init metadata covers browser auth safety stop cleanup preferences capabilities audit comments messages engage dm collection playlist widgets ops adopt read-all report notify instagram setup onboard".split())
FAULTS = {'before_press', 'after_press', 'delete_readback_lost', 'external'}
SEEN = {'once', 'absent', 'draft', 'duplicate', 'unclear'}
FIELDS = {'title','description','tags','visibility','schedule','cover','portrait','chapters','widgets','caption','crop','language','playlist'}

def choice(v, values):
    return v if isinstance(v, str) and v in values else 'other' if v is not None else None

def hexval(v, pattern=HEX):
    return v if isinstance(v,str) and pattern.fullmatch(v) else None

def obj(v):
    return v if isinstance(v,dict) else {}

def integer(v):
    return v if type(v) is int and v>=0 else None

def number(v):
    return round(v,6) if type(v) in (int,float) and 0<=v<1e15 else None

def boolean(v):
    return v if type(v) is bool else None

def stamp(v):
    if not isinstance(v,dict): return None
    def ver(x):
        return x if isinstance(x,str) and re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,39}',x) else None
    return dict(version=ver(v.get('version')), source_digest=hexval(v.get('source_digest')),
                install=choice(v.get('install'),{'vcs','archive','local','unknown'}), commit=hexval(v.get('commit'),re.compile(r'^[a-f0-9]{40}$')),
                python=ver(v.get('python')),os=choice(v.get('os'),{'win32','Windows','Linux','Darwin','windows','linux','darwin','nt','posix'}))

def timestamp(v):
    try:
        if not isinstance(v,str): return None
        dt=datetime.fromisoformat(v.replace('Z','+00:00'))
        if dt.tzinfo is None: return None
        return dt.astimezone(timezone.utc)
    except ValueError: return None

def utc(dt):
    return dt.isoformat(timespec='milliseconds').replace('+00:00','Z') if dt else None

def safe_path(root,path):
    path=Path(path)
    try: path.relative_to(root)
    except ValueError: raise VpubError('audit_export_invalid','Export source is outside the data folder.')
    for p in [path,*path.parents]:
        if p==root.parent: break
        if p.exists() or p.is_symlink():
            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:
                raise VpubError('audit_export_invalid','Export does not follow linked source files.')
    return path

def read_object(root,path):
    path=safe_path(root,path)
    return obj(json.loads(path.read_bytes())) if path.is_file() else {}

def events_from(root):
    file=safe_path(root,root/'audit/journal.jsonl')
    if not file.exists(): return [],dict(ok=True,records=0,head=journal.ZERO,not_ended=[])
    events=[]
    for line in file.read_bytes().splitlines():
        try:
            v=json.loads(line)
            if isinstance(v,dict): events.append(v)
        except ValueError: continue  # Integrity below still reports the damaged source.
    safe_path(root,root/'audit/head.json')
    return events,journal.verify_chain(root/'audit')

def proof(v):
    return {k:boolean(obj(v).get(k)) for k in ('present','owned','page_gone','list_absent','list_complete','fresh')}

def snap(v):
    if not isinstance(v,dict): return None
    return dict(ref=hexval(v.get('ref'),REF),platform=choice(v.get('platform'),PLATFORMS),state=choice(v.get('state'),STATES),
                final_submit_attempted=boolean(v.get('final_submit_attempted')),final_presses=integer(v.get('final_presses')),
                deletion_state=choice(v.get('deletion_state'),STATES),delete_attempted=boolean(v.get('delete_attempted')))

def project_event(v,when):
    """Closed projection even for a forged, rehashed journal."""
    kind=v.get('type')
    if kind not in {'command_start','command_end','transition','call','stage','span','fault','observation'}: return None
    r=dict(seq=integer(v.get('seq')),prev=hexval(v.get('prev')),at=when(v.get('at')),day=utc(timestamp(v.get('at')))[:10] if timestamp(v.get('at')) else None,
           type=kind,run=hexval(v.get('run'),re.compile(r'^[a-f0-9]{32}$')),dropped_before=integer(v.get('dropped_before')) or 0)
    if kind=='command_start':
        f=obj(v.get('flags'))
        r['awl_run_id']=hexval(v.get('awl_run_id'),re.compile(r'^[a-f0-9]{32}$'))
        r.update(command=choice(v.get('command'),COMMANDS),action=choice(v.get('action'),journal.ACTIONS|{'export','observe'}),
                 flags={k:boolean(f.get(k)) for k in ('dry_run','verify_only','person_approved','confirm_given','no_cover','cover_given','person_checked')},
                 code=stamp(v.get('code')),before=snap(v.get('before')),fault_armed=choice(v.get('fault_armed'),FAULTS))
        r['flags']['workers']=f.get('workers') if type(f.get('workers')) is int and f['workers'] in (1,2) else None
        # Labels are optional declared IDs, not arbitrary paths or text.
        r['session']=v.get('session') if isinstance(v.get('session'),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}',v['session']) else None
        r['purpose']=choice(v.get('purpose'),{'organic','test','timing','fault'})
    elif kind=='command_end':
        r.update(exit_code=v.get('exit_code') if type(v.get('exit_code')) is int and 0<=v['exit_code']<=255 else None,
                 ok=boolean(v.get('ok')),seconds=number(v.get('seconds')),error_code=choice(v.get('error_code'),CODES),after=snap(v.get('after')),platforms={})
        for p,d in obj(v.get('platforms')).items():
            if p in PLATFORMS:
                d=obj(d);r['platforms'][p]=dict(ref=hexval(d.get('ref'),REF),state=choice(d.get('state'),STATES),code=choice(d.get('code'),CODES),
                  previous_state=choice(d.get('previous_state'),STATES),final_presses=integer(d.get('final_presses')),final_submit_attempted=boolean(d.get('final_submit_attempted')),id_present=boolean(d.get('id_present')))
    elif kind=='transition':
        r.update(kind=choice(v.get('kind'),{'ledger','browser','deletion','edit'}),ref=hexval(v.get('ref'),REF),platform=choice(v.get('platform'),PLATFORMS),
          state=choice(v.get('state'),STATES),receipt_state=choice(v.get('receipt_state'),STATES),phase=choice(v.get('phase'),PHASES),
          final_presses=integer(v.get('final_presses')),final_submit_attempted=boolean(v.get('final_submit_attempted')),media_selected=boolean(v.get('media_selected')),
          still_a_draft=boolean(v.get('still_a_draft')),id_present=boolean(v.get('id_present')),delete_attempted=boolean(v.get('delete_attempted')),
          proof=proof(v.get('proof')),platform_writes=integer(v.get('platform_writes')),error_code=choice(v.get('error_code'),CODES),
          operation=choice(v.get('operation'),{'claim','save','forget','create','press','readback','cancel'}),field_group=choice(v.get('field_group'),{'caption','cover','portrait','chapters'}))
    elif kind=='call':
        r.update(platform=choice(v.get('platform'),PLATFORMS),route=choice(v.get('route'),{'browser','bili','cli','api','native'}),action=choice(v.get('action'),journal.ACTIONS),
                 operation=choice(v.get('operation'),journal.OPERATIONS),ok=boolean(v.get('ok')),error_code=choice(v.get('error_code'),CODES),seconds=number(v.get('seconds')))
    elif kind=='stage':
        from .progress import STAGES
        r.update(platform=choice(v.get('platform'),PLATFORMS),action=choice(v.get('action'),journal.ACTIONS),stage=choice(v.get('stage'),STAGES))
    elif kind=='span':
        r.update(platform=choice(v.get('platform'),PLATFORMS),slot=v.get('slot') if type(v.get('slot')) is int and v['slot'] in (0,1) else None,t_start=number(v.get('t_start')),t_end=number(v.get('t_end')),state=choice(v.get('state'),STATES),code=choice(v.get('code'),CODES))
    elif kind=='fault': r.update(point=choice(v.get('point'),FAULTS),ref=hexval(v.get('ref'),REF),platform=choice(v.get('platform'),PLATFORMS),fired=boolean(v.get('fired')))
    else: r.update(ref=hexval(v.get('ref'),REF),platform=choice(v.get('platform'),PLATFORMS),seen=choice(v.get('seen'),SEEN),by='person')
    return r

def count_events(events):
    counts=dict(commands_by_exit_code={},attempts_by_state={},presses_total=0,attempts_with_more_than_one_press=0,in_doubt_total=0,
       in_doubt_settled_by_verify=0,in_doubt_settled_by_person=0,in_doubt_open=0,refusals_by_guard={},uploads_started_total=0,replayed_uploads=0,
       deletes_by_state={},delete_presses=0,deletes_confirmed_by_own_readback=0,edits_by_state={},stops_by_code={},faults_fired_by_point={},
       cli_calls=0,platform_calls=0,platform_reads=None,platform_writes=None)
    presses=defaultdict(int);uploads=Counter();states={};deleted={};edits={};pending=set();ever=set();settled={};del_pressed=set();starts={}
    by_run=defaultdict(dict);last={};ended=set();pending_presses={}
    def outcome(identity,state):
        deleting,k=identity
        attempted=k in del_pressed if deleting else presses[k]>0
        if state in {'UNKNOWN','SUBMITTED_UNVERIFIED','DELETE_PENDING'} and attempted:
            if identity not in pending: pending_presses[identity]=presses[k]
            pending.add(identity);ever.add(identity);settled.pop(identity,None)
        if state in {'SUBMISSION_VERIFIED','DELETED_VERIFIED'} and identity in pending and (deleting or presses[k]==pending_presses[identity]):
            pending.remove(identity);settled[identity]='verify'
    for e in events:
        k=e.get('ref');t=e['type']
        if t=='command_start': counts['cli_calls']+=1
        if t=='command_start': starts[e.get('run')]=e
        if t=='command_end':
            ended.add(e.get('run'))
            for identity,state in by_run[e.get('run')].items(): outcome(identity,state)
            code=str(e['exit_code']) if e['exit_code'] is not None else 'other';counts['commands_by_exit_code'][code]=counts['commands_by_exit_code'].get(code,0)+1
            if e.get('error_code') in {'stopped','interrupted','human_verification_required'}:
                counts['stops_by_code'][e['error_code']]=counts['stops_by_code'].get(e['error_code'],0)+1
            before,after=starts.get(e.get('run'),{}).get('before'),e.get('after')
            unchanged=before and after and all(before.get(f)==after.get(f) for f in ('final_presses','delete_attempted'))
            if e['exit_code']==2 and (unchanged or not before and not after):
                guard=e.get('error_code') or 'other';counts['refusals_by_guard'][guard]=counts['refusals_by_guard'].get(guard,0)+1
        if t=='call': counts['platform_calls']+=1
        if t=='transition' and k:
            if e.get('kind') in {'browser','ledger'}:
                states[k]=e.get('state') or 'other';n=e.get('final_presses') or 0
                if n>presses[k]: counts['presses_total']+=n-presses[k];presses[k]=n
                if e.get('kind')=='browser' and e.get('phase')=='selecting_media': uploads[k]+=1
            if e.get('kind')=='deletion':
                deleted[k]=e.get('state') or 'other'
                if e.get('delete_attempted') and k not in del_pressed: del_pressed.add(k);counts['delete_presses']+=1
            if e.get('kind')=='edit': edits[k]=e.get('state') or 'other'
            identity=(e.get('kind')=='deletion',k)
            if e.get('kind') in {'ledger','browser','deletion'}:
                by_run[e.get('run')][identity]=e.get('state');last[identity]=(e.get('run'),e.get('state'))
        if t=='observation' and e.get('seen') in {'once','absent','draft','duplicate'}:
            for identity in list(pending):
                if identity[1]==k: pending.remove(identity);settled[identity]='person'
        if t=='fault' and e.get('fired'):
            p=e.get('point') or 'other';counts['faults_fired_by_point'][p]=counts['faults_fired_by_point'].get(p,0)+1
    # A transient pre-click UNKNOWN followed by verification in the same
    # command is ordinary progress, not an uncertain command outcome. For
    # commands lacking an end, use only the latest retained state per attempt.
    for identity,(run,state) in last.items():
        if run not in ended: outcome(identity,state)
    counts.update(attempts_by_state=dict(Counter(states.values())),attempts_with_more_than_one_press=sum(n>1 for n in presses.values()),
      uploads_started_total=sum(uploads.values()),replayed_uploads=sum(max(0,n-1) for n in uploads.values()),deletes_by_state=dict(Counter(deleted.values())),
      deletes_confirmed_by_own_readback=sum(s=='DELETED_VERIFIED' for s in deleted.values()),edits_by_state=dict(Counter(edits.values())),
      in_doubt_total=len(ever),in_doubt_open=len(pending),in_doubt_settled_by_verify=sum(s=='verify' for s in settled.values()),in_doubt_settled_by_person=sum(s=='person' for s in settled.values()))
    return counts

def concurrency(spans,workers,wall):
    usable=[s for s in spans if s.get('slot') in (0,1) and s.get('t_start') is not None and s.get('t_end') is not None and s['t_end']>=s['t_start']]
    points=sorted({s[k] for s in usable for k in ('t_start','t_end')})
    union=overlap=0;busy=[0,0]
    for a,b in zip(points,points[1:]):
        active={s['slot'] for s in usable if s['t_start']<=a and s['t_end']>=b}
        if active: union+=b-a
        if len(active)>1: overlap+=b-a
        for slot in active: busy[slot]+=b-a
    return dict(workers=workers,wall=wall,sum=round(sum(s['t_end']-s['t_start'] for s in usable),6),overlap=round(overlap,6),
       idle_by_slot=[round(union-busy[i],6) for i in range(workers or 1)],all_verified=bool(usable) and all(s.get('state')=='SUBMISSION_VERIFIED' for s in usable))

def export_data(root=None,*,since=None,until=None,times='relative',exact_sizes=False):
    if times not in {'relative','exact'}: raise VpubError('audit_window_invalid','Choose relative or exact times.')
    root=Path(root or home()).resolve()
    start,stop=timestamp(since),timestamp(until)
    if since and not start or until and not stop or start and stop and stop<start: raise VpubError('audit_window_invalid','Use ordered ISO times with an explicit time zone.')
    raw,chain=events_from(root)
    chosen=[e for e in raw if timestamp(e.get('at')) and (not start or timestamp(e['at'])>=start) and (not stop or timestamp(e['at'])<=stop)]
    origin=min((timestamp(e['at']) for e in chosen),default=None)
    def when(value):
        dt=timestamp(value)
        return utc(dt) if times=='exact' else round((dt-origin).total_seconds(),6) if dt and origin else None
    events=[p for e in chosen if (p:=project_event(e,when)) is not None]
    gap_runs=set();previous_seq=0
    for e in raw:
        seq=integer(e.get('seq'))
        if seq!=previous_seq+1: gap_runs.add(e.get('run'))
        previous_seq=seq or previous_seq
    saltfile=safe_path(root,root/'audit/salt')
    salt=saltfile.read_bytes() if saltfile.exists() else None
    if salt is not None and len(salt)!=32: raise VpubError('audit_export_invalid','Invalid audit salt.')
    def ref(v): return hmac.new(salt,v.encode(),'sha256').hexdigest()[:16] if salt and hexval(v) else None
    publications=[];deletions=[];builds=[]
    def build(v):
        s=stamp(v)
        if s and s not in builds: builds.append(s)
        return s
    rows=[];db=safe_path(root,root/'publications.sqlite3')
    if db.is_file():
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as con:
            con.row_factory=sqlite3.Row
            rows=[dict(r) for r in con.execute('SELECT key,platform,media_sha,state,data,updated_at FROM publications ORDER BY key')]
    selected_refs={e.get('ref') for e in events}|{v.get('ref') for e in events for v in obj(e.get('platforms')).values()}
    for row in rows:
        if not hexval(row['key']) or row['platform'] not in PLATFORMS:
            raise VpubError('audit_export_invalid','A ledger record has an invalid local identifier.')
        key=row['key'];data=obj(json.loads(row['data']));options=obj(data.get('options'));plan=obj(data.get('approved_plan'));receipt=obj(data.get('receipt'))
        ph=data.get('plan_hash')
        if hexval(ph) and row['platform'] in PLATFORMS:
            receipt={**receipt,**read_object(root,root/'browser-receipts'/(ph+'-'+row['platform']+'.json'))}
        if (start or stop) and ref(key) not in selected_refs: continue
        rb=obj(receipt.get('readback'));size=integer(plan.get('video_bytes'))
        edits=[]
        for edit in data.get('edits',[]) if isinstance(data.get('edits'),list) else []:
            ed=obj(edit);saved=obj(ed.get('result'))
            if hexval(ed.get('edit_id')): saved={**saved,**read_object(root,root/'edits'/(ed['edit_id']+'.json'))}
            edits.append(dict(group=journal.edit_group(obj(ed.get('patch'))),state=choice(saved.get('state'),STATES),platform_writes=integer(saved.get('platform_writes')),person_approved=boolean(saved.get('person_approved')),t=when(ed.get('at'))))
        publications.append(dict(ref=ref(key),media_ref=ref(row.get('media_sha')),platform=choice(row['platform'],PLATFORMS),route=choice(options.get('method'),{'browser','cli','api','native'}),
            submission_mode=choice(options.get('submission_mode'),{'submit','draft_only'}),visibility=choice(options.get('visibility','account'),{'public','private','unlisted','account'}),scheduled=bool(options.get('schedule')),
            size_mib=(size/1048576 if exact_sizes else round(size/1048576,1)) if size is not None else None,
            code=build(data.get('code')),code_last=build(data.get('code_last')),state=choice(row['state'],STATES),receipt_state=choice(receipt.get('state'),STATES),phase=choice(receipt.get('phase'),PHASES),
            blocked_phase=choice(receipt.get('blocked_phase'),PHASES),error_code=choice(obj(receipt.get('error') or data.get('error')).get('code'),CODES),
            final_submit_attempted=boolean(receipt.get('final_submit_attempted')),final_presses=integer(receipt.get('final_presses')),
            uploads_started=sum(e['type']=='transition' and e.get('kind')=='browser' and e.get('phase')=='selecting_media' and e.get('ref')==ref(key) for e in events),
            readback=dict(source=choice(rb.get('source'),{'page','list','platform','api','browser','creator','studio'}),verified=boolean(rb.get('verified')),differs=[f for f in rb.get('differs',[]) if f in FIELDS] if isinstance(rb.get('differs'),list) else [],still_a_draft=boolean(receipt.get('still_a_draft')),id_present=bool(receipt.get('id')),under_review=boolean(receipt.get('under_review'))),
            parts={k:choice(receipt.get(k),STATES) for k in ('chapters_state','widgets_state')},edits=edits,dismissed=boolean(data.get('dismissed')),fault=choice(obj(receipt.get('fault')).get('point'),FAULTS),
            observations=[dict(seen=e['seen'],t=e['at']) for e in events if e['type']=='observation' and e.get('ref')==ref(key)],
            t_started=when(data.get('started_at')),t_updated=when(row.get('updated_at')),t_verified=when(receipt.get('verified_at')),day=utc(timestamp(data.get('started_at')))[:10] if timestamp(data.get('started_at')) else None,
            history_complete=bool(chain['ok'] and not chain.get('not_ended') and not start and not stop and not any(e['dropped_before'] for e in events)
                and any(e.get('ref')==ref(key) and e.get('operation')=='claim' for e in events))))
        d=read_object(root,root/'work-deletions'/(key+'.json')) if hexval(key) else {}
        if d:
            deletions.append(dict(ref=ref(key),platform=choice(row['platform'],PLATFORMS),state=choice(d.get('state'),STATES),delete_attempted=boolean(d.get('delete_attempted')),
              approval={k:boolean(obj(d.get('approval')).get(k)) for k in ('confirm_matched','person_approved')},proof=proof(d.get('evidence')),platform_writes=integer(obj(d.get('evidence')).get('platform_writes')),
              error_code=choice(obj(d.get('error')).get('code'),CODES),code=build(d.get('code')),press_code=build(d.get('press_code')),readback_code=build(d.get('readback_code')),fault=choice(obj(d.get('fault')).get('point'),FAULTS),
              t_created=when(d.get('created_at')),t_attempted=when(d.get('attempted_at')),t_updated=when(d.get('updated_at')),day=utc(timestamp(d.get('created_at')))[:10] if timestamp(d.get('created_at')) else None))
    runs=[]
    for rid in sorted({e['run'] for e in events if e['run']}):
        es=[e for e in events if e['run']==rid];s=next((e for e in es if e['type']=='command_start'),{});end=next((e for e in reversed(es) if e['type']=='command_end'),{})
        code=build(s.get('code'));spans=[e for e in es if e['type']=='span']
        runs.append(dict(run=rid,awl_run_id=s.get('awl_run_id'),command=s.get('command'),action=s.get('action'),flags=s.get('flags',{}),build=builds.index(code) if code else None,session=s.get('session'),purpose=s.get('purpose'),
          exit_code=end.get('exit_code'),ok=end.get('ok'),seconds=end.get('seconds'),error_code=end.get('error_code'),ended=bool(end),t_start=s.get('at'),t_end=end.get('at'),
          platforms=end.get('platforms',{}),stages=[[e['platform'],e['action'],e['stage'],e['at']] for e in es if e['type']=='stage'],
          spans=[[e['platform'],e['slot'],e['t_start'],e['t_end'],e['state']] for e in spans],concurrency=concurrency(spans,obj(s.get('flags')).get('workers'),end.get('seconds')),
          dropped_events=sum(e['dropped_before'] for e in es),sequence_gap=rid in gap_runs,chain_ok=chain['ok'],loss_complete=False))
    return dict(schema='crosscast-audit/1',site=hmac.new(salt,b'crosscast-audit-site','sha256').hexdigest()[:8] if salt else None,
      window=dict(since=utc(start),until=utc(stop),days=sorted({e['day'] for e in events if e['day']}),origin=utc(origin) if times=='exact' else None),times=times,
      builds=builds,publications=publications,deletions=deletions,runs=runs,events=events,counts=count_events(events),
      integrity=dict(first_seq=events[0]['seq'] if events else None,last_seq=events[-1]['seq'] if events else None,head=chain.get('head'),chain_ok=chain['ok'],first_bad_seq=chain.get('first_bad_seq'),
        events_outside_a_command=sum(e['run'] is None for e in events),dropped_events=sum(e['dropped_before'] for e in events),dropped_count_is_lower_bound=True))

def export_file(out,**kwargs):
    result=export_data(**kwargs)
    target=Path(out).resolve();root=home().resolve()
    # An export must never overwrite input evidence or private control files.
    if target==root or root in target.parents: raise VpubError('audit_output_invalid','Choose an export destination outside the data folder.')
    atomic_json(target,result)
    return dict(schema=result['schema'],records=len(result['events']),chain_ok=result['integrity']['chain_ok'])

def observe(key,seen):
    if not hexval(key) or seen not in SEEN: raise VpubError('audit_observation_invalid','Use a recorded receipt key and an allowed observation.')
    db=home()/'publications.sqlite3'
    if not db.exists(): raise VpubError('not_found','Receipt not found.')
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as con: row=con.execute('SELECT platform FROM publications WHERE key=?',(key,)).fetchone()
    if not row: raise VpubError('not_found','Receipt not found.')
    if not journal.append('observation',lambda ref:dict(ref=ref(key),platform=choice(row[0],PLATFORMS),seen=seen,by='person')):
        raise VpubError('audit_recording_failed','The observation could not be confirmed in the journal. Inspect local evidence before recording it again.')
    return dict(recorded=True,seen=seen,receipt_changed=False)
