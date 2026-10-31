"""Explicit, idempotent likes/saves with durable single-attempt receipts."""
from pathlib import Path
import re
import uuid
from . import adapters, operations
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, stop_check

PENDING={'ACTION_SUBMITTING','ACTION_UNVERIFIED'}


def validate(platform,work,collection=None):
    operations.work_id(platform,work);stop_check(platform)
    if collection and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',collection):
        raise VpubError('invalid_collection','Use an exact collection ID returned by engage collections.')
    if collection and platform not in {'bilibili','youtube'}:
        raise VpubError('unsupported_collection','This adapter uses the platform default Save/Bookmark only.')
    if platform=='youtube' and collection in {'WL','LL','FL'}:
        raise VpubError('unsupported_collection','Use an ordinary owned playlist; Watch Later, Likes and legacy Favorites are not supported here.')


def dispatch(platform,action,work=None,**kw):
    if platform in {'bilibili','youtube'}:
        from .engagement_api import execute
        return execute(platform,action,work,**kw)
    return adapters.browser_call(platform,'engagement','default',operation=action,work_id=work,**kw)


def collections(platform,cursor=None):
    operations.platform_check(platform);stop_check(platform)
    if cursor and (platform!='youtube' or len(cursor)>1000):raise VpubError('invalid_cursor','Collection pagination is supported on YouTube only.')
    if platform not in {'bilibili','youtube'}:
        from .collections import listing
        return listing(platform)
    return dispatch(platform,'collections',cursor=cursor)


def status(platform,work,collection=None,expected_identity=None):
    validate(platform,work,collection)
    result=dispatch(platform,'status',work,collection=collection,expected_identity=expected_identity)
    return {**result,'platform':platform,'work_id':work,'observed_at':now(),'platform_writes':0}


def submitting(path):
    saved=read_json(path);saved.update(state='ACTION_SUBMITTING',submitted_at=now())
    atomic_json(path,saved)


def finish(saved,observed):
    key='liked' if saved['kind']=='like' else 'saved'
    valid=observed.get('identity')==saved['identity'] and observed.get(key) is saved['desired']
    saved.update(state='ACTION_VERIFIED' if valid else 'ACTION_UNVERIFIED',after=observed,verified_at=now() if valid else None)
    return saved


def set_action(platform,work,kind,desired=True,collection=None,dry_run=False,test_id=None):
    validate(platform,work,collection)
    if kind not in {'like','save'} or not isinstance(desired,bool):raise VpubError('invalid_action','Choose like/save and on/off.')
    if test_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',test_id):raise VpubError('invalid_test_id','Use a short alphanumeric test label.')
    if kind=='save' and platform=='youtube' and not collection:
        raise VpubError('collection_required','YouTube saves require --collection with an owned playlist ID.')
    if kind=='like' and collection:raise VpubError('invalid_collection','Like does not use a collection.')
    with publication_lock():
        before=status(platform,work,collection)
        selected=before.get('collection') if kind=='save' else None
        current=before.get('liked' if kind=='like' else 'saved')
        if not isinstance(current,bool):raise VpubError('state_unavailable','The exact current state is unavailable; nothing changed.')
        index=home()/'engagement/targets'/(digest([platform,before['identity'],work,kind,selected])+'.json')
        if index.exists():
            prior=read_json(home()/'engagement/receipts'/(read_json(index)['key']+'.json'))
            if prior['state'] in PENDING:
                if prior['desired']==desired and current is desired:
                    final=finish(prior,before)
                    atomic_json(home()/'engagement/receipts'/(prior['key']+'.json'),final)
                    operations.record('engagement',platform,work,final)
                    return {**final,'platform_writes':0,'reused':True}
                return {**prior,'platform_writes':0,'reused':True,'hint':'Use engage verify KEY; unresolved attempts are never toggled or automatically resent.'}
        if current is desired:return {'state':'NO_CHANGE','platform_writes':0,'platform':platform,'work_id':work,'kind':kind,'desired':desired,'observed':before}
        if dry_run:return {'state':'DRY_RUN','platform_writes':0,'platform':platform,'work_id':work,'kind':kind,'desired':desired,'observed':before}
        key=uuid.uuid4().hex;path=home()/'engagement/receipts'/(key+'.json')
        saved={'key':key,'state':'ACTION_READY','platform':platform,'work_id':work,'identity':before['identity'],
               'kind':kind,'desired':desired,'collection':selected,'before':before,'at':now(),'test':bool(test_id),'test_id':test_id}
        atomic_json(path,saved);atomic_json(index,{'key':key})
        try:
            result=dispatch(platform,'set',work,kind=kind,desired=desired,collection=selected,
                            expected_identity=before['identity'],receipt_path=str(path))
            saved={**read_json(path),**result}
            # Verification is a new independent API/CLI/browser read, never a second click.
            observed=status(platform,work,selected,expected_identity=before['identity'])
            if result.get('state')=='NO_CHANGE':saved.update(state='NO_CHANGE',after=observed)
            else:saved=finish(saved,observed)
            atomic_json(path,saved)
        except Exception as exc:
            saved=read_json(path)
            saved['state']='ACTION_UNVERIFIED' if saved['state'] in PENDING else 'ACTION_FAILED'
            saved['error_code']=exc.code if isinstance(exc,VpubError) else type(exc).__name__
            saved['platform_writes']=None if saved['state']=='ACTION_UNVERIFIED' else 0
            atomic_json(path,saved)
        operations.record('engagement',platform,work,saved)
        return saved


def verify(key):
    if not re.fullmatch(r'[a-f0-9]{32}',key):raise VpubError('invalid_receipt','Use an engagement receipt key.')
    path=home()/'engagement/receipts'/(key+'.json')
    if not path.exists():raise VpubError('receipt_required','Engagement receipt not found.')
    with publication_lock():
        saved=read_json(path)
        observed=status(saved['platform'],saved['work_id'],saved['collection'],saved['identity'])
        if saved['state'] in PENDING|{'ACTION_VERIFIED'}:saved=finish(saved,observed)
        else:saved['after']=observed
        atomic_json(path,saved)
        operations.record('engagement',saved['platform'],saved['work_id'],saved)
        return {**saved,'platform_writes':0}
