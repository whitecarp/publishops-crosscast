"""Account-bound browser collections; uncertain writes are verified, never replayed."""
import re
from pathlib import Path
from . import adapters
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, stop_check
from .direct_messages import identity
from .browser_session import reuse

PLATFORMS = ('instagram', 'x', 'douyin')


def validate(platform, collection=None):
    if platform not in PLATFORMS:
        raise VpubError('unsupported_platform', 'Use instagram, x or douyin; YouTube uses playlist commands.')
    if collection is not None and (not isinstance(collection,str) or not re.fullmatch(r'[0-9]{1,40}', collection)):
        raise VpubError('invalid_collection', 'Use the exact numeric ID returned by collection list.')
    stop_check(platform)


def receipt_path(key):
    if not re.fullmatch(r'[a-f0-9]{64}', key or ''):
        raise VpubError('invalid_key', 'Use the returned collection receipt key.')
    return home() / 'operations/collections' / (key + '.json')


def call(platform, operation, owner, **kw):
    return adapters.browser_call(platform, 'collections', 'default', operation=operation, expected_identity=owner, **kw)


def listing(platform, collection=None):
    validate(platform, collection)
    return call(platform, 'items' if collection else 'list', identity(platform), collection_id=collection)


def checked_result(r, observed):
    if observed.get('identity') != r['identity']:
        raise VpubError('wrong_account', 'Collection readback belongs to another account.')
    rows=observed['items']; action=r['action']; target=r.get('collection_id')
    if action in {'add','remove'}:
        if observed.get('collection_id')!=target:raise VpubError('wrong_collection','Membership readback belongs to another collection.')
        ids={x['id'] for x in rows}; expected=set(r['before_members'])
        if action=='add':expected.add(r['work_id'])
        else:expected.discard(r['work_id'])
        good=observed.get('complete') is True and ids==expected
        r.update(state='CONFIRMED' if good else 'UNVERIFIED',checked_at=now(),observed=observed)
        return r
    matches=[x for x in rows if x['id']==target]
    if action=='delete':good=not matches and observed.get('complete') is True
    else:good=len(matches)==1 and matches[0]['title']==r['title']
    r.update(state='CONFIRMED' if good else 'UNVERIFIED', checked_at=now(), observed=observed)
    return r


def verify(key):
    path=receipt_path(key)
    if not path.exists():raise VpubError('receipt_missing', 'Collection request does not exist.')
    with publication_lock():
        r=read_json(path);validate(r['platform'])
        if identity(r['platform'])!=r['identity']:raise VpubError('wrong_account', 'Reconnect the original account.')
        if r['state'] in {'READY','FAILED_BEFORE_SUBMIT'}:
            return {**r, 'platform_writes':0, 'hint':'No submit was recorded. Verify the platform before creating a new request.'}
        try:r=checked_result(r,call(r['platform'],'items',r['identity'],collection_id=r['collection_id']) if r['action'] in {'add','remove'} else call(r['platform'],'list',r['identity']))
        except Exception as exc:r.update(state='UNVERIFIED',error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
        atomic_json(path,r)
        return {**r,'platform_writes':0}


def change(platform, action, collection=None, title=None, request_id=None, cover=None, description=None):
    validate(platform,collection)
    if action not in {'create','rename','delete'}:raise VpubError('invalid_action','Use create, rename or delete.')
    if action!='create' and collection is None:raise VpubError('collection_required','An exact collection ID is required.')
    if action=='create' and collection is not None:raise VpubError('invalid_collection','Create does not take an existing ID.')
    if not request_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):
        raise VpubError('request_id_required','Use --request-id once per intended change; reuse after interruption.')
    if action in {'create','rename'} and (not isinstance(title,str) or not title.strip() or title!=title.strip() or len(title)>({'x':25,'douyin':20,'instagram':100}[platform]) or any(ord(c)<32 for c in title)):
        raise VpubError('invalid_title','Use a nonempty title without surrounding whitespace/control characters; maximum X 25, Douyin 20, Instagram 100.')
    intent=dict(action=action,collection=collection,title=title)
    extra={}
    if platform=='douyin' and action=='create':
        from .core import file_sha
        path=Path(cover or '')
        if not cover or not path.is_file() or path.suffix.lower() not in {'.jpg','.jpeg','.png'} or path.stat().st_size>5*1024*1024:
            raise VpubError('cover_required','Douyin requires --cover, a local JPG/PNG up to 5 MB. A square production cover is recommended.')
        from PIL import Image
        try:
            with Image.open(path) as image:
                if image.format not in {'JPEG','PNG'}:raise ValueError('image format')
                image.verify()
        except Exception:raise VpubError('invalid_cover','The collection cover is not a readable JPEG or PNG.')
        if description is not None and (not isinstance(description,str) or len(description)>200):raise VpubError('invalid_description','Douyin description maximum is 200 characters.')
        extra={'cover':str(path.resolve()),'description':description or ''}
        intent.update(cover_sha256=file_sha(path),description=description or '')
    elif cover is not None or description is not None:
        raise VpubError('invalid_options','Cover and description are only accepted when creating a Douyin collection.')
    with publication_lock(), reuse(platform):
        owner=identity(platform);key=digest([platform,owner,request_id]);path=receipt_path(key)
        if path.exists():
            r=read_json(path)
            if r['intent']!=intent:raise VpubError('request_conflict','This request ID already describes a different change.')
            return {**r,'reused':True,'platform_writes':0}
        before=call(platform,'list',owner)
        if before.get('identity')!=owner:raise VpubError('wrong_account','Unexpected collection owner.')
        matches=[x for x in before['items'] if x['id']==collection]
        if action!='create' and len(matches)!=1:raise VpubError('collection_not_visible','Exact collection ID is not visible; no changes made.')
        if action in {'create','rename'} and any(x['title']==title and x['id']!=collection for x in before['items']):
            raise VpubError('duplicate_title','A collection with this title already exists on the visible page.')
        r=dict(key=key,request_id=request_id,platform=platform,identity=owner,intent=intent,action=action,
               collection_id=collection,title=title,before_ids=[x['id'] for x in before['items']],
               state='READY',started_at=now(),platform_writes=0,next_command='vpub collection verify '+key,**extra)
        atomic_json(path,r)
        try:
            if action=='rename' and matches[0]['title']==title:
                r=checked_result(r,before)
            else:
                result=call(platform,action,owner,collection_id=collection,title=title,receipt_key=key,**extra)
                r=read_json(path)
                if action=='create' and result.get('collection_id'):
                    validate(platform,result['collection_id'])
                    if result['collection_id'] in r['before_ids']:raise VpubError('wrong_collection','Creation returned a pre-existing collection.')
                    r['collection_id']=result['collection_id'];atomic_json(path,r)
                r=checked_result(r,call(platform,'list',owner))
        except Exception as exc:
            r=read_json(path)
            r.update(state='UNVERIFIED' if r.get('platform_writes') else 'FAILED_BEFORE_SUBMIT',
                     error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
        atomic_json(path,r)
        return r


def before_submit(payload, owner):
    """Called in the worker immediately before the irreversible click."""
    path=receipt_path(payload['receipt_key']);r=read_json(path)
    expected=(payload['platform'],owner,payload['operation'],payload.get('collection_id'),payload.get('title'))
    actual=(r['platform'],r['identity'],r['action'],r.get('collection_id'),r.get('title'))
    if expected!=actual or r['state']!='READY':raise VpubError('receipt_conflict','Receipt does not permit this action.')
    if r['action'] in {'add','remove'} and (payload.get('work_id')!=r.get('work_id') or payload.get('before_members')!=r.get('before_members')):
        raise VpubError('receipt_conflict','Wrong collection member or baseline.')
    if r['platform']=='douyin' and r['action']=='create':
        from .core import file_sha
        if payload.get('description')!=r['intent']['description'] or file_sha(Path(payload.get('cover','')))!=r['intent']['cover_sha256']:
            raise VpubError('receipt_conflict','The collection cover or description changed after preparation.')
    stop_check(r['platform']);r.update(state='SUBMITTING',submitted_at=now(),platform_writes=1)
    atomic_json(path,r)


def membership(platform, action, collection, work, request_id):
    from .operations import work_id
    validate(platform,collection);work_id(platform,work)
    if action not in {'add','remove'}:raise VpubError('invalid_action','Use add or remove.')
    if platform not in {'douyin','instagram'}:raise VpubError('unverified_feature','Named-folder membership is not yet accepted on this platform.')
    if not request_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):raise VpubError('request_id_required','Use a stable --request-id.')
    intent={'action':action,'collection':collection,'work':work}
    with publication_lock(), reuse(platform):
        owner=identity(platform);key=digest([platform,owner,request_id]);path=receipt_path(key)
        if path.exists():
            r=read_json(path)
            if r['intent']!=intent:raise VpubError('request_conflict','Request ID already describes another change.')
            return {**r,'reused':True,'platform_writes':0}
        before=call(platform,'items',owner,collection_id=collection)
        if before.get('identity')!=owner or before.get('collection_id')!=collection:raise VpubError('wrong_collection','Unexpected collection identity.')
        if before.get('complete') is not True:raise VpubError('read_incomplete','Complete membership is required before editing; no write attempted.')
        ids=[x['id'] for x in before['items']]
        r={'key':key,'request_id':request_id,'platform':platform,'identity':owner,'intent':intent,'action':action,'collection_id':collection,
           'work_id':work,'title':None,'before_members':ids,'state':'READY','platform_writes':0,'started_at':now(),'next_command':'vpub collection verify '+key}
        atomic_json(path,r)
        try:
            if (work in ids)==(action=='add'):r=checked_result(r,before)
            else:
                call(platform,action,owner,collection_id=collection,work_id=work,receipt_key=key,before_members=ids)
                r=read_json(path);r=checked_result(r,call(platform,'items',owner,collection_id=collection))
        except Exception as exc:
            r=read_json(path);r.update(state='UNVERIFIED' if r.get('platform_writes') else 'FAILED_BEFORE_SUBMIT',error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
        atomic_json(path,r);return r
