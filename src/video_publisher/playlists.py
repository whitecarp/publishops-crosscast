"""Owned YouTube playlists: bounded reads, durable single-attempt writes and readback."""
import re
import time
from . import adapters
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, stop_check


def client():
    service=adapters.yt_service('default')
    rows=service.channels().list(part='id',mine=True).execute().get('items',[])
    if len(rows)!=1:raise VpubError('channel_required','Expected one authorized YouTube channel.')
    return service,rows[0]['id']


def identifier(value,kind='playlist'):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',value):
        raise VpubError('invalid_id','Use an exact playlist or playlist-item ID.')
    if kind=='playlist' and not value.startswith('PL'):
        raise VpubError('unsupported_playlist','Only ordinary playlists are supported; uploads, likes and Watch Later are excluded.')
    return value


def owned(service,owner,playlist,missing=False):
    identifier(playlist)
    rows=service.playlists().list(part='snippet,status,contentDetails',id=playlist).execute().get('items',[])
    if not rows and missing:return None
    if not rows:raise VpubError('playlist_not_visible','Playlist is not visible to the current channel yet.')
    if len(rows)!=1 or rows[0]['snippet']['channelId']!=owner:
        raise VpubError('wrong_playlist','The playlist must belong to the authorized channel.')
    return rows[0]


def item_owned(service,playlist,item,missing=False):
    identifier(item,'item')
    rows=service.playlistItems().list(part='snippet,contentDetails',id=item).execute().get('items',[])
    if not rows and missing:return None
    if not rows:raise VpubError('playlist_item_not_visible','Playlist item is not visible yet.')
    if len(rows)!=1 or rows[0]['snippet']['playlistId']!=playlist:
        raise VpubError('wrong_playlist_item','Playlist item is missing or belongs to another playlist.')
    return rows[0]


def listing(playlist=None,cursor=None,limit=50):
    if type(limit) is not int or not 1<=limit<=50:raise VpubError('invalid_limit','Use limit 1–50.')
    stop_check('youtube');service,owner=client()
    args={'part':'snippet,status,contentDetails','maxResults':limit}
    if cursor:args['pageToken']=cursor
    if playlist:
        owned(service,owner,playlist)
        result=service.playlistItems().list(**args,playlistId=playlist).execute()
    else:result=service.playlists().list(**args,mine=True).execute()
    return {'identity':owner,'playlist_id':playlist,'items':result.get('items',[]),
            'next_cursor':result.get('nextPageToken'),'complete':not result.get('nextPageToken'),'platform_writes':0}


def receipt_path(key):
    if not re.fullmatch(r'[a-f0-9]{64}',key or ''):raise VpubError('invalid_key','Use the returned receipt key.')
    return home()/'operations/playlists'/(key+'.json')


def verify_value(service,r):
    action=r['action'];p=r.get('playlist_id');owner=r['identity'];desired=r['desired']
    if not p:return False
    current=owned(service,owner,p,missing=action=='delete')
    if action=='delete':return current is None
    if action in {'create','update'}:
        return all(current.get(part,{}).get(k)==v for part,fields in desired.items() for k,v in fields.items())
    item=r.get('item_id')
    if not item:return False  # Lost insert response is ambiguous, never create another item.
    current=item_owned(service,p,item,missing=action=='remove')
    if action=='remove':return current is None
    if current['snippet']['resourceId'].get('videoId')!=r['video_id']:return False
    return action=='add' or current['snippet']['position']==desired['position']


def readback(service,r):
    for delay in (0,0.5,1,2):
        if delay:time.sleep(delay)
        try:
            if verify_value(service,r):return True
        except VpubError as exc:
            if exc.code not in {'playlist_not_visible','playlist_item_not_visible'}:raise
    return False


def verify(key):
    path=receipt_path(key)
    if not path.exists():raise VpubError('receipt_missing','No playlist request with this key.')
    with publication_lock():
        r=read_json(path);service,owner=client()
        if owner!=r['identity']:raise VpubError('wrong_account','The playlist request belongs to another channel.')
        good=readback(service,r)
        r.update(state='CONFIRMED' if good else 'UNVERIFIED',verified_at=now())
        atomic_json(path,r)
        return {**r,'platform_writes':0}


def change(action,playlist=None,title=None,description=None,privacy=None,video=None,item=None,position=None,request_id=None):
    if action not in {'create','update','delete','add','remove','move'}:raise VpubError('invalid_action','Unknown playlist action.')
    if not request_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):
        raise VpubError('request_id_required','Provide --request-id once per intended change; reuse it after an interruption.')
    if action!='create':identifier(playlist)
    if title is not None and (not title.strip() or len(title)>150):raise VpubError('invalid_title','Use a nonempty title of at most 150 characters.')
    if description is not None and len(description)>5000:raise VpubError('invalid_description','Description exceeds 5000 characters.')
    if privacy is not None and privacy not in {'private','unlisted','public'}:raise VpubError('invalid_privacy','Use private, unlisted or public.')
    if action=='create' and not title:raise VpubError('title_required','A new playlist needs a title.')
    if action=='update' and all(x is None for x in (title,description,privacy)):raise VpubError('update_required','Specify title, description or privacy.')
    if action=='add':
        from .operations import work_id
        work_id('youtube',video)
    if action in {'remove','move'}:identifier(item,'item')
    if action=='move' and (type(position) is not int or position<0):raise VpubError('invalid_position','Position is zero-based and nonnegative.')
    intent=dict(action=action,playlist=playlist,title=title,description=description,privacy=privacy,video=video,item=item,position=position)
    stop_check('youtube')
    with publication_lock():
        service,owner=client()
        key=digest(['youtube',owner,request_id]);path=receipt_path(key)
        if path.exists():
            prior=read_json(path)
            if prior['intent']!=intent:raise VpubError('request_conflict','This request ID already describes a different change.')
            return {**prior,'reused':True,'platform_writes':0,'next_command':'vpub playlist verify '+key}
        if not adapters.yt_credentials('default').has_scopes([adapters.PLAYLIST_SCOPE]):
            raise VpubError('oauth_scope_required','Authorize youtube.force-ssl using auth login youtube.')
        current=owned(service,owner,playlist) if playlist else None
        desired={};item_id=item;video_id=video;noop=False
        if action in {'create','update'}:
            snippet={k:v for k,v in (current or {}).get('snippet',{}).items() if k in {'title','description','defaultLanguage'}}
            if title is not None:snippet['title']=title
            if description is not None:snippet['description']=description
            body={}
            if action=='create' or title is not None or description is not None:
                body['snippet']=snippet;desired['snippet']=snippet
            if action=='create' or privacy is not None:
                status={k:v for k,v in (current or {}).get('status',{}).items() if k in {'privacyStatus','podcastStatus'}}
                status['privacyStatus']=privacy or 'private';body['status']=status;desired['status']=status
            part=','.join(body)
            if action=='update':body['id']=playlist
            request=getattr(service.playlists(),'insert' if action=='create' else 'update')(part=part,body=body)
        elif action=='delete':request=service.playlists().delete(id=playlist)
        elif action=='add':
            existing=service.playlistItems().list(part='snippet',playlistId=playlist,videoId=video,maxResults=50).execute()
            matches=existing.get('items',[])
            if len(matches)>1 or existing.get('nextPageToken'):raise VpubError('ambiguous_membership','Multiple entries for this video; use an exact item ID.')
            if matches:item_id=matches[0]['id'];noop=True
            request=service.playlistItems().insert(part='snippet',body={'snippet':{'playlistId':playlist,'resourceId':{'kind':'youtube#video','videoId':video}}})
        else:
            original=item_owned(service,playlist,item)
            video_id=original['snippet']['resourceId'].get('videoId')
            if action=='remove':request=service.playlistItems().delete(id=item)
            else:
                count=current.get('contentDetails',{}).get('itemCount')
                if not isinstance(count,int) or position>=count:raise VpubError('invalid_position','Position must be inside the playlist.')
                desired={'position':position};noop=original['snippet']['position']==position
                request=service.playlistItems().update(part='snippet',body={'id':item,'snippet':{'playlistId':playlist,'resourceId':original['snippet']['resourceId'],'position':position}})
        r={'key':key,'request_id':request_id,'identity':owner,'intent':intent,'action':action,
           'playlist_id':playlist,'item_id':item_id,'video_id':video_id,'desired':desired,
           'state':'SUBMITTING','started_at':now(),'platform_writes':0}
        atomic_json(path,r)
        try:
            if not noop:
                stop_check('youtube');r['platform_writes']=1;atomic_json(path,r)
                result=request.execute(num_retries=0)
                if action=='create':r['playlist_id']=result['id']
                if action=='add':r['item_id']=result['id']
                r['state']='SUBMITTED';atomic_json(path,r)
            good=readback(service,r)
            r.update(state='CONFIRMED' if good else 'UNVERIFIED',verified_at=now())
        except Exception as exc:
            # No mutation retry: a lost response may already have changed the playlist.
            r.update(state='UNVERIFIED',error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
            if 'manualSortRequired' in str(exc):r['hint']='Set playlist Ordering to Manual in YouTube, then verify before making a new request.'
        r['next_command']='vpub playlist verify '+key
        atomic_json(path,r)
        return r
