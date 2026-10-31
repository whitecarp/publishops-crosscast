"""Corrections to a durable existing work. Never upload or delete a video."""
from copy import deepcopy
from pathlib import Path
import re
from .core import Ledger, VpubError, atomic_json, digest, file_sha, home, now, publication_lock, read_json, stop_check
from .language_policy import route


def validate_patch(platform, value):
    allowed={'expected_identity','title','description','content_language','metadata_language','metadata_mode','portrait','chapters','duration_seconds'}
    if not isinstance(value,dict) or set(value)-allowed or not isinstance(value.get('expected_identity'),str) or not value['expected_identity']:
        raise VpubError('invalid_edit','Provide expected_identity and supported correction fields only; privacy/media replacement/deletion are forbidden.')
    p=deepcopy(value)
    groups=sum(('description' in p or 'title' in p, 'portrait' in p, 'chapters' in p))
    if groups != 1:
        raise VpubError('invalid_edit','Correct one field group per request: caption, portrait, or chapters. Each has its own save/read-back receipt.')
    metadata='description' in p or 'title' in p
    if metadata:
        if platform not in {'youtube','instagram','x'}:
            raise VpubError('unsupported_edit','This release edits captions on YouTube/Instagram/X and covers/chapters on Douyin.')
        if not isinstance(p.get('title'),str) or not 1<=len(p['title'])<=100 or '\n' in p['title']:
            raise VpubError('invalid_title','An edit requires a one-line title of at most 100 characters.')
        policy=route(p.get('content_language','und'),platform)
        if p.get('metadata_mode')!=policy['mode'] or p.get('metadata_language')!=policy['language']:
            raise VpubError('wrong_metadata_language','Use the automatic source-plus-target language policy.')
        if not isinstance(p.get('description'),str) or not p['description'].strip() or '\x00' in p['description']:
            raise VpubError('invalid_description','Provide complete replacement text.')
        if re.search(r'https?://|www\.',p['description'],re.I):
            raise VpubError('links_disabled','Correction copy excludes external URLs.')
        if platform=='youtube' and len(p['description'].encode())>5000:
            raise VpubError('description_too_long','YouTube description exceeds the local 5000-byte budget.')
        if platform in {'instagram','x'}:
            p['post_text']=p['title']+'\n\n'+p['description']+('\n\nAI-generated' if p.get('ai_generated', True) else '')
            if platform=='instagram' and len(p['post_text'])>2200:
                raise VpubError('description_too_long','Instagram caption exceeds 2200 characters; preserve both languages when shortening.')
    if 'portrait' in p:
        if platform!='douyin': raise VpubError('unsupported_edit','Shared Instagram cover is handed to the user; this command updates the Douyin cover.')
        f=Path(p['portrait']).resolve()
        if not f.is_file() or f.suffix.lower() not in {'.png','.jpg','.jpeg'}:
            raise VpubError('invalid_cover','Use the completed shared portrait artwork.')
        p['portrait']=str(f);p['portrait_sha256']=file_sha(f)
    if 'chapters' in p:
        if platform!='douyin': raise VpubError('unsupported_edit','Chapter corrections currently support Douyin.')
        from .chapters import validate
        validate(p['chapters'],platform,'native',p.get('duration_seconds'))
    if not(metadata or 'portrait' in p or 'chapters' in p):
        raise VpubError('invalid_edit','No correction fields were supplied.')
    return p


def youtube_update(account, work_id, patch, checkpoint, verify_only=False):
    from . import adapters
    service=adapters.yt_service(account)
    owned=service.channels().list(part='id',mine=True).execute().get('items',[])
    if [i['id'] for i in owned]!=[patch['expected_identity']]:
        raise VpubError('wrong_account','Authorized YouTube channel differs from the correction target.')
    current=service.videos().list(part='snippet,status',id=work_id).execute().get('items',[])
    if len(current)!=1 or current[0]['snippet']['channelId']!=patch['expected_identity']:
        raise VpubError('wrong_work','Existing video does not belong to the expected channel.')
    before=current[0];snippet=before['snippet']
    fields=('title','description','tags','categoryId','defaultLanguage','defaultAudioLanguage')
    body={k:deepcopy(snippet[k]) for k in fields if k in snippet}
    body.update(title=patch['title'],description=patch['description'])
    if all(snippet.get(k)==body.get(k) for k in ('title','description')):
        return {'state':'EDIT_VERIFIED','id':work_id,'unchanged':True,'platform_writes':0}
    if verify_only:
        return {'state':'EDIT_SUBMITTED_UNVERIFIED','id':work_id,'platform_writes':0}
    creds=adapters.yt_credentials(account)
    if not any(creds.has_scopes([s]) for s in (adapters.PLAYLIST_SCOPE,'https://www.googleapis.com/auth/youtube','https://www.googleapis.com/auth/youtubepartner')):
        raise VpubError('oauth_scope_required','Existing-video editing needs the initial full YouTube permissions.', 'Run vpub auth login youtube --method api --client-json PATH and approve the requested permissions.')
    checkpoint({'state':'EDIT_SUBMITTING','id':work_id,'before':before})
    service.videos().update(part='snippet',body={'id':work_id,'snippet':body}).execute()
    after=service.videos().list(part='snippet,status',id=work_id).execute()['items'][0]
    if (any(after['snippet'].get(k)!=body.get(k) for k in ('title','description','categoryId','tags'))
            or after['status'].get('privacyStatus')!=before['status'].get('privacyStatus')):
        raise VpubError('edit_unverified','Saved metadata or unchanged privacy did not match read-back; inspect the existing video.')
    return {'state':'EDIT_VERIFIED','id':work_id,'url':'https://www.youtube.com/watch?v='+work_id,
            'title':body['title'],'description':body['description'],'privacy':after['status']['privacyStatus'],'platform_writes':1}


def run(key, patch_path, dry_run=False, verify_only=False):
    with publication_lock():
        ledger=Ledger()
        try:
            row=ledger.get(key)
            if not row: raise VpubError('receipt_required','Use the key of an existing recorded publication.')
            record=row['data'];receipt=record.get('receipt',{});work_id=receipt.get('id')
            if not work_id: raise VpubError('work_id_required','Verify the existing work ID first; never infer an ID from a title or upload again.')
            patch=validate_patch(row['platform'],read_json(patch_path))
            actual=record.get('identity',{}).get('identity') or receipt.get('identity')
            if actual!=patch['expected_identity']: raise VpubError('wrong_account','Correction identity differs from the publication receipt.')
            stop_check(row['platform'])
            edit_id=digest([key,patch]);path=home()/'edits'/(edit_id+'.json')
            if dry_run: return {'key':key,'id':work_id,'patch':patch,'platform_writes':0,'edit_id':edit_id}
            if path.exists():
                saved=read_json(path)
                # A prior success is history, not current platform state. The backend
                # reads the existing work and skips saving only when it still matches.
                if saved.get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'} and not verify_only:
                    raise VpubError('inspect_existing_edit','A save was already attempted; inspect the existing work before any retry.')
            def checkpoint(data): atomic_json(path,{'key':key,'edit_id':edit_id,'at':now(),**data})
            if not verify_only: checkpoint({'state':'EDIT_PREPARING','id':work_id})
            from . import adapters
            if row['platform']=='youtube': result=youtube_update(row['account'],work_id,patch,checkpoint,verify_only)
            else:
                result=adapters.browser_call(row['platform'],'edit',row['account'],expected_identity=actual,
                    receipt=receipt,patch=patch,edit_receipt_path=str(path),verify_only=verify_only)
            checkpoint(result)
            record.setdefault('edits',[]).append({'edit_id':edit_id,'patch':patch,'result':result,'at':now()})
            if result.get('state')=='EDIT_VERIFIED' and 'description' in patch:
                record['options'].update({k:v for k,v in patch.items() if k!='expected_identity'})
                if 'post_text' in patch: record['receipt']['expected_text']=patch['post_text']
                record['receipt']['title']=patch['title']
            ledger.save(key,row['state'],record)
            return {'key':key,**result}
        finally: ledger.close()
