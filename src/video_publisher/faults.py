"""Opt-in fail-stop test hooks. Never perform a platform operation."""
import os
import uuid
from .core import VpubError, config, home, now
from . import audit_journal

POINTS={'before_press','after_press','delete_readback_lost','external'}
_fallback=uuid.uuid4().hex

def requested():
    value=os.environ.get('VPUB_FAULT')
    if not value or config().get('fault_injection') is not True: return None
    if value not in POINTS: raise VpubError('fault_injection_refused','Unknown test fault; nothing was contacted.')
    return value

def gate(platform,options,point=None):
    point=requested() if point is None else point
    if point is None: return None
    if (point not in POINTS or platform not in {'youtube','douyin'} or options.get('method')!='browser'
        or options.get('visibility') not in {'private','unlisted'} or options.get('schedule')):
        raise VpubError('fault_injection_refused','Test faults require an immediate private or unlisted YouTube/Douyin browser plan. Nothing was contacted.')
    return point

def label(point,key,platform,*,fired=False,record=True):
    value={'point':point,'at':now()}
    if record: audit_journal.append('fault',lambda ref:dict(point=point,ref=ref(key),platform=platform,fired=fired))
    return value

def fire(point,wanted,key,platform,save):
    if point!=wanted: return
    # A single command may have two browser workers. An exclusive marker in
    # its private data folder limits the entire process family to one firing.
    token=audit_journal.RUN or _fallback
    path=home()/'audit'/'faults'/(token+'.once')
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(b'1');stream.flush();os.fsync(stream.fileno())
    except FileExistsError: return
    save(label(point,key,platform,record=False))
    audit_journal.append('fault',lambda ref:dict(point=point,ref=ref(key),platform=platform,fired=True))
    raise VpubError('injected_fault','The explicitly armed test fault stopped this command. Inspect the retained receipt; do not repeat the write.')
