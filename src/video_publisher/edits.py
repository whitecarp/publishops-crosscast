"""Corrections to a durable existing work. Never upload or delete a video."""
from copy import deepcopy
from pathlib import Path
import re
from .core import Ledger, VpubError, atomic_json, digest, douyin_caption, file_sha, home, now, publication_lock, read_json, receipt_turn, stop_check, title_limit
from .language_policy import route
from .safety import check_public_text, public_text_flags


def validate_patch(platform, value, verifying=False, ai_generated=True):
    allowed={'expected_identity','title','description','tags','content_language','metadata_language','metadata_mode','portrait','chapters','duration_seconds','cover'}
    if not isinstance(value,dict) or set(value)-allowed or not isinstance(value.get('expected_identity'),str) or not value['expected_identity']:
        raise VpubError('invalid_edit','Provide expected_identity and supported correction fields only; privacy/media replacement/deletion are forbidden.')
    p=deepcopy(value)
    groups=sum(('description' in p or 'title' in p, 'portrait' in p, 'chapters' in p, 'cover' in p))
    if groups != 1:
        raise VpubError('invalid_edit','Correct one field group per request: caption, portrait, cover or chapters. Each has its own save/read-back receipt.')
    metadata='description' in p or 'title' in p
    if 'tags' in p and (not metadata or platform not in {'bilibili','douyin'}):
        raise VpubError('unsupported_edit','Tags can be edited with the title and description on Bilibili and Douyin.')
    if metadata:
        if not isinstance(p.get('title'),str) or not 1<=len(p['title'])<=100 or any(c in p['title'] for c in '\r\n\x00'):
            raise VpubError('invalid_title','An edit requires a one-line title of at most 100 characters.')
        if platform in {'bilibili','douyin'} and len(p['title'])>title_limit(platform):
            raise VpubError('invalid_title',f'{platform} titles allow at most {title_limit(platform)} characters.')
        policy=route(p.get('content_language','und'),platform)
        if p.get('metadata_mode')!=policy['mode'] or p.get('metadata_language')!=policy['language']:
            raise VpubError('wrong_metadata_language','Use the automatic source-plus-target language policy.')
        if not isinstance(p.get('description'),str) or not p['description'].strip() or '\x00' in p['description']:
            raise VpubError('invalid_description','Provide complete replacement text.')
        # Only new copy leaves links out. A read-back compares what the work already says, links and all.
        if not verifying and re.search(r'https?://|www\.',p['description'],re.I):
            raise VpubError('links_disabled','Correction copy excludes external URLs.')
        if platform=='youtube' and len(p['description'].encode())>5000:
            raise VpubError('description_too_long','YouTube description exceeds the local 5000-byte budget.')
        if platform in {'instagram','x'}:
            p['post_text']=p['title']+'\n\n'+p['description']+('\n\nAI-generated' if ai_generated else '')
            # None is an adopted post. No upload here declared anything, so the worker keeps whatever its caption says now.
            if ai_generated is None: p['keep_ai_line']=True
            if platform=='instagram' and len(p['post_text'])>2200:
                raise VpubError('description_too_long','Instagram caption exceeds 2200 characters; preserve both languages when shortening.')
        if 'tags' in p and (not isinstance(p['tags'],list) or not all(isinstance(t,str) and t.strip() and not any(c in t for c in ',\r\n\x00') for t in p['tags'])
                            or (platform=='bilibili' and not 1<=len(p['tags'])<=10)):
            raise VpubError('invalid_tags','Tags are a list of nonempty words without commas or new lines; Bilibili takes 1 to 10.')
        if platform=='bilibili' and len(p['description'])>2000:
            raise VpubError('description_too_long','Bilibili descriptions allow at most 2000 characters.')
        if platform=='douyin':
            p['caption']=douyin_caption(p['description'],p.get('tags',[]))
            if len(p['caption'].encode('utf-16-le'))//2>1000:
                raise VpubError('description_too_long','Douyin description plus hashtags exceeds 1000 UTF-16 units.')
    if 'portrait' in p:
        if platform!='douyin': raise VpubError('unsupported_edit','Shared Instagram cover is handed to the user; this command updates the Douyin cover.')
        f=Path(p['portrait']).resolve()
        if not f.is_file() or f.suffix.lower() not in {'.png','.jpg','.jpeg'}:
            raise VpubError('invalid_cover','Use the completed shared portrait artwork.')
        p['portrait']=str(f);p['portrait_sha256']=file_sha(f)
    if 'cover' in p:
        if platform not in {'youtube','bilibili'}: raise VpubError('unsupported_edit','This command replaces a YouTube or Bilibili cover; other platforms have their own cover steps.')
        f=Path(str(p['cover'])).resolve()
        if not f.is_file() or f.suffix.lower() not in {'.png','.jpg','.jpeg'}:
            raise VpubError('invalid_cover','Use a complete JPG or PNG thumbnail.')
        p['cover']=str(f);p['cover_sha256']=file_sha(f)
    if 'chapters' in p:
        if platform not in {'douyin','bilibili','youtube'}: raise VpubError('unsupported_edit','Chapter corrections support Douyin, Bilibili and YouTube.')
        from .chapters import validate, youtube_rules
        validate(p['chapters'],platform,'native',p.get('duration_seconds'))
        if platform=='youtube': youtube_rules(p['chapters'],p['duration_seconds'])
    if not(metadata or 'portrait' in p or 'chapters' in p or 'cover' in p):
        raise VpubError('invalid_edit','No correction fields were supplied.')
    if not verifying: check_public_text(p.get('title'),p.get('description'),p.get('tags'),p.get('chapters'))
    return p


def send(platform, account, actual, receipt, key, patch, dry_run, verify_only, person_approved=False):
    """One correction, once. An uncertain save keeps its receipt, which blocks a repeat until the work is inspected."""
    work_id=receipt['id']
    if actual!=patch['expected_identity']: raise VpubError('wrong_account','Correction identity differs from the publication receipt.')
    stop_check(platform)
    edit_id=digest([key,patch]);path=home()/'edits'/(edit_id+'.json')
    flags=public_text_flags(patch.get('title'),patch.get('description'),patch.get('tags'),patch.get('chapters'))
    flagged={'text_flags':flags} if flags and not verify_only else {}
    if dry_run: return {'key':key,'id':work_id,'patch':patch,'platform_writes':0,'edit_id':edit_id,**flagged}
    tried=False
    if path.exists():
        saved=read_json(path)
        # A prior success is history, not current platform state. The backend
        # reads the existing work and skips saving only when it still matches.
        tried=saved.get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'}
        if tried and not verify_only and not person_approved:
            raise VpubError('inspect_existing_edit','A save was already attempted; inspect the existing work before any retry.',
                            'If a person has looked at the work and the change is not there, add --person-approved to try once more.')
    def checkpoint(data): atomic_json(path,{'key':key,'edit_id':edit_id,'at':now(),**data})
    # The record keeps that the flag was given, for a person to read later. It is someone's word, not proof that a person looked.
    approved={'person_approved':True} if person_approved and not verify_only else {}
    if not verify_only: checkpoint({'state':'EDIT_PREPARING','id':work_id,**approved})
    from . import adapters
    if platform=='bilibili':
        from .edits_bilibili import run as edit_bilibili
        result=edit_bilibili(account,actual,receipt,patch,checkpoint,verify_only)
    elif platform=='youtube' and adapters.addon_installed():
        if 'chapters' in patch: raise VpubError('unsupported_edit','With the add-on, put the chapter lines in the description and edit that.')
        result=adapters.youtube_api().update_video(account,work_id,patch,checkpoint,verify_only)
    else:
        result=adapters.browser_call(platform,'edit',account,expected_identity=actual,
            receipt=receipt,patch=patch,edit_receipt_path=str(path),verify_only=verify_only)
    # A check changes nothing, so it must not leave what reads as a save that was tried and block the real one.
    if verify_only and not tried and result.get('state')=='EDIT_SUBMITTED_UNVERIFIED': result={**result,'state':'EDIT_MISMATCH'}
    # A check can confirm an unclear save, and nothing less clears it: a page can show the old words for a while, and a cover cannot be compared at all.
    if verify_only and tried and result.get('state')!='EDIT_VERIFIED': result={**result,'state':'EDIT_SUBMITTED_UNVERIFIED'}
    result={**result,**approved,**flagged}
    checkpoint(result)
    return result


def run_adopted(platform, work, patch_path, dry_run=False, verify_only=False, person_approved=False):
    """The same correction for a work CrossCast adopted rather than published, using the owner that adoption verified."""
    from .work_catalog import binding
    with publication_lock():
        adopted=binding(platform,work)
        if not adopted: raise VpubError('work_identity_required','Adopt this work first, so its owner is checked; nothing changed.',f'Run vpub ops adopt {platform} {work}.')
        patch=validate_patch(platform,read_json(patch_path),verify_only,None)
        if platform=='douyin' and 'caption' not in patch and not adopted.get('title'):
            raise VpubError('unsupported_edit','Douyin covers and chapters find the work by its title, and this adoption recorded none; nothing changed.')
        receipt={'id':work,'identity':adopted['identity'],'title':adopted.get('title')}
        key=digest(['adopted',platform,work])
        result=send(platform,'default',adopted['identity'],receipt,key,patch,dry_run,verify_only,person_approved)
        if result.get('state')=='EDIT_VERIFIED' and 'title' in patch:
            atomic_json(home()/'operations/adopted'/(digest([platform,work])+'.json'),{**adopted,'title':patch['title']})
        return {'platform':platform,'work_id':work,**result}


def found_after_edit(platform, patch):
    """What the next check of this work should find, now an edit is confirmed. A review found YouTube's next check still looked for the old description. What it said before stays in the edit's history."""
    return {'title':patch['title'],
            **({'expected_text':patch['post_text']} if 'post_text' in patch else {}),
            **({'expected_text':patch['description']} if platform=='youtube' else {}),
            **({'expected_text':patch['title'],'expected_caption':patch['caption']} if platform=='douyin' else {})}


def keep_resume_in_step(record, platform, work, owner, changes):
    """A browser attempt keeps its own copy of its receipt, which resume reads, so it must not go back to the old words. What it says about the upload and the press stays as it is."""
    plan_hash=record.get('plan_hash') or record.get('approved_plan',{}).get('confirmation')
    durable=home()/'browser-receipts'/f'{plan_hash}-{platform}.json'
    if plan_hash and record['receipt'].get('method')=='browser' and durable.exists():
        with receipt_turn(durable):
            saved=read_json(durable)
            if saved.get('id')==work and saved.get('identity')==owner:
                atomic_json(durable,{**saved,**changes})


def run(key, patch_path, dry_run=False, verify_only=False, person_approved=False):
    with publication_lock():
        ledger=Ledger()
        try:
            row=ledger.get(key)
            if not row: raise VpubError('receipt_required','Use the key of an existing recorded publication.')
            record=row['data'];receipt=record.get('receipt',{});work_id=receipt.get('id')
            if not work_id: raise VpubError('work_id_required','Verify the existing work ID first; never infer an ID from a title or upload again.')
            patch=validate_patch(row['platform'],read_json(patch_path),verify_only,record.get('options',{}).get('ai_generated',True))
            actual=record.get('identity',{}).get('identity') or receipt.get('identity')
            result=send(row['platform'],row['account'],actual,receipt,key,patch,dry_run,verify_only,person_approved)
            if dry_run: return result
            edit_id=digest([key,patch])
            record.setdefault('edits',[]).append({'edit_id':edit_id,'patch':patch,'result':result,'at':now(),'previous_receipt':deepcopy(receipt)})
            confirmed=result.get('state')=='EDIT_VERIFIED' and 'description' in patch
            changes=found_after_edit(row['platform'],patch) if confirmed else {}
            if confirmed:
                record['options'].update({k:v for k,v in patch.items() if k!='expected_identity'})
                record['receipt'].update(changes)
            ledger.save(key,row['state'],record)
            if confirmed: keep_resume_in_step(record,row['platform'],work_id,actual,changes)
            return {'key':key,**result}
        finally: ledger.close()
