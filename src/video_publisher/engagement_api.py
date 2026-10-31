"""Reuse bili-cli for explicit account actions."""
import json
import os
import subprocess
import sys
from . import adapters
from .core import VpubError, stop_check


def bili_folders(work):
    # Pinned bili-agent exposes membership in its read-only helper, but its
    # public `me favs` command omits video context. Do not read/copy credentials.
    code='''import sys
from bili_cli.commands.video import _own_video, _folders
from bili_cli.runtime import run
async def body():
    video, _ = _own_video(sys.argv[1])
    return {"folders": await _folders(video.credential, video)}
run(body)
'''
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    for k in ('NO_PROXY','no_proxy'):
        if k in env:env[k]=','.join(x for x in env[k].split(',') if ':' not in x)
    cp=subprocess.run([sys.executable,'-c',code,work],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    try:data=json.loads(cp.stdout)
    except ValueError:raise VpubError('upstream_schema_changed','bili-cli folder membership could not be read.') from None
    if not data.get('ok'):raise VpubError(data.get('error',{}).get('code','upstream_error'),'bili-cli folder read failed.')
    return data['data']['folders']


def check_identity(actual,expected):
    if expected and actual!=expected:raise VpubError('wrong_account','Signed-in account changed; no action performed.')


def execute(platform,action,work=None,**kw):
    if platform=='bilibili':return bilibili(action,work,**kw)
    return adapters.youtube_api().engagement(action,work,**kw)


def bilibili(action,work,**kw):
    identity=adapters.auth_status('bilibili','default','cli')['identity'];check_identity(identity,kw.get('expected_identity'))
    if action=='collections':return {'identity':identity,'items':adapters.bili(['me','favs'])['folders'],'source':'bili_cli'}
    relation=adapters.bili(['video','relation',work]);folders=bili_folders(work)
    collection=kw.get('collection')
    selected=[f for f in folders if str(f['id'])==collection] if collection else [f for f in folders if f.get('default')]
    if collection and len(selected)!=1:raise VpubError('collection_required','Choose an exact favorite folder ID using --collection.')
    folder=selected[0] if len(selected)==1 else None
    observed={'identity':identity,'liked':relation.get('liked'),'saved':folder.get('has_video') if folder else None,'collection':str(folder['id']) if folder else None,
              'source':'bili_cli_and_pinned_membership_helper','save_semantics':'membership in the selected favorite folder'}
    if action=='status':return observed
    if action!='set':raise VpubError('invalid_action','Unsupported engagement action.')
    field='liked' if kw['kind']=='like' else 'saved'
    if observed[field] is kw['desired']:return {'state':'NO_CHANGE','platform_writes':0}
    if not isinstance(observed[field],bool):raise VpubError('state_unavailable','Cannot determine the current state.')
    args=['video','like',work] if kw['kind']=='like' else ['video','fav',work,'--folder',str(folder['id'])]
    if not kw['desired']:args+=['--undo' if kw['kind']=='like' else '--remove']
    from .engagement import submitting
    stop_check('bilibili');submitting(kw['receipt_path'])
    adapters.bili(args)
    return {'state':'ACTION_UNVERIFIED','platform_writes':1}
