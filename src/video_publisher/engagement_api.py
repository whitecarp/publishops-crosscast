"""Reuse bili-cli and the official YouTube API for explicit account actions."""
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
    s=adapters.yt_service('default')
    channels=s.channels().list(part='id',mine=True).execute().get('items',[])
    if len(channels)!=1:raise VpubError('channel_required','Expected one authorized channel.')
    identity=channels[0]['id'];check_identity(identity,kw.get('expected_identity'))
    if action=='collections':
        args={'part':'snippet,status','mine':True,'maxResults':50}
        if kw.get('cursor'):args['pageToken']=kw['cursor']
        data=s.playlists().list(**args).execute()
        return {'identity':identity,'items':[{'id':p['id'],'title':p['snippet']['title'],'visibility':p['status']['privacyStatus']} for p in data.get('items',[])],
                'next_cursor':data.get('nextPageToken'),'source':'youtube_playlists_api'}
    videos=s.videos().list(part='id',id=work).execute().get('items',[])
    if len(videos)!=1:raise VpubError('work_unavailable','Video is unavailable to this account.')
    ratings=s.videos().getRating(id=work).execute().get('items',[])
    if len(ratings)!=1 or ratings[0].get('videoId')!=work or ratings[0].get('rating') not in {'like','dislike','none'}:
        raise VpubError('state_unavailable','YouTube did not return a recognized rating for this video.')
    rating=ratings[0]['rating'];collection=kw.get('collection');members=[]
    if collection:
        playlists=s.playlists().list(part='snippet',id=collection).execute().get('items',[])
        if len(playlists)!=1 or playlists[0]['snippet']['channelId']!=identity:
            raise VpubError('wrong_collection','Choose a playlist owned by the authorized channel.')
        data=s.playlistItems().list(part='snippet',playlistId=collection,videoId=work,maxResults=50).execute()
        members=data.get('items',[])
        if data.get('nextPageToken') or len(members)>1:raise VpubError('ambiguous_membership','Duplicate playlist entries require manual inspection; nothing removed.')
        if any(m['snippet'].get('playlistId')!=collection or m['snippet'].get('resourceId',{}).get('videoId')!=work for m in members):
            raise VpubError('wrong_collection','Playlist membership response did not match the work.')
    observed={'identity':identity,'liked':rating=='like','rating':rating,'saved':bool(members) if collection else None,
              'collection':collection,'source':'youtube_data_api','save_semantics':'membership in the selected owned playlist'}
    if action=='status':return observed
    if action!='set':raise VpubError('invalid_action','Unsupported engagement action.')
    field='liked' if kw['kind']=='like' else 'saved';desired=kw['desired']
    if observed[field] is desired:return {'state':'NO_CHANGE','platform_writes':0}
    if not isinstance(observed[field],bool):raise VpubError('state_unavailable','Cannot determine the current state.')
    creds=adapters.yt_credentials('default')
    if not any(creds.has_scopes([scope]) for scope in (adapters.PLAYLIST_SCOPE,'https://www.googleapis.com/auth/youtube')):
        raise VpubError('oauth_scope_required','Authorize YouTube management permission before changing likes or playlists.')
    from .engagement import submitting
    stop_check('youtube');submitting(kw['receipt_path'])
    if kw['kind']=='like':s.videos().rate(id=work,rating='like' if desired else 'none').execute()
    elif desired:s.playlistItems().insert(part='snippet',body={'snippet':{'playlistId':collection,'resourceId':{'kind':'youtube#video','videoId':work}}}).execute()
    else:s.playlistItems().delete(id=members[0]['id']).execute()
    return {'state':'ACTION_UNVERIFIED','platform_writes':1}


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
