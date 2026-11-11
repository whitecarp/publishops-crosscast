"""Corrections to a durable existing work. Never upload or delete a video."""
from copy import deepcopy
from pathlib import Path
import re
from .core import DOUYIN_TOO_LONG, Ledger, VpubError, X_POST_LIMIT, atomic_json, digest, douyin_caption, douyin_caption_units, file_sha, home, now, publication_lock, read_json, receipt_turn, stop_check, title_limit, x_post_text, x_weighted_length
from .language_policy import route
from .safety import check_public_text, public_text_flags


X_EDIT_UNVERIFIED_MESSAGE = ('An attempted X edit may have succeeded. Reading old text at the original post address does not prove failure.')
X_EDIT_INSPECTION_HINT = ('Do not retry or submit another edit based on this read-back. Open the post on X and inspect its current text and any available edit history. '
                          'If a correction is needed, make it manually on X; CrossCast will not retry this uncertain edit, even with person approval. '
                          'Edit-version behavior has not been verified on a live account.')


def x_edit_receipt_matches(saved, identity, work_id):
    return (saved.get('platform')=='x' and saved.get('identity')==identity
            and str(saved.get('id'))==str(work_id))


def x_edit_was_attempted(saved, identity, work_id):
    return x_edit_receipt_matches(saved,identity,work_id) and (
        saved.get('final_submit_attempted') is True or saved.get('platform_writes')==1
        or saved.get('state')=='EDIT_SUBMITTING'
        or saved.get('state')=='EDIT_SUBMITTED_UNVERIFIED' and 'platform_writes' not in saved)


def x_edit_readback_note(attempted):
    if attempted:
        return {'message':X_EDIT_UNVERIFIED_MESSAGE,'hint':X_EDIT_INSPECTION_HINT}
    return {'message':'This call made no edit. The requested post text or identity could not be verified.',
            'hint':'Open the post on X and inspect its current text. This check does not establish that an edit was attempted or failed.'}


def validate_patch(platform, value, verifying=False, ai_generated=True):
    allowed={'expected_identity','title','description','tags','content_language','metadata_language','metadata_mode','portrait','chapters','duration_seconds','cover'}
    if not isinstance(value,dict) or set(value)-allowed or not isinstance(value.get('expected_identity'),str) or not value['expected_identity']:
        raise VpubError('invalid_edit','Provide expected_identity and supported correction fields only; privacy/media replacement/deletion are forbidden.')
    p=deepcopy(value)
    groups=sum(('description' in p or 'title' in p, 'portrait' in p, 'chapters' in p, 'cover' in p))
    if groups != 1:
        raise VpubError('invalid_edit','Correct one field group per request: caption, portrait, cover or chapters. Each has its own save/read-back receipt.')
    metadata='description' in p or 'title' in p
    if 'tags' in p and (not metadata or platform not in {'bilibili','douyin','x'}):
        raise VpubError('unsupported_edit','Tags can be edited with the title and description on Bilibili, Douyin and X.')
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
        if 'tags' in p and (not isinstance(p['tags'],list) or not all(isinstance(t,str) and t.strip() and not any(c in t for c in ',\r\n\x00') for t in p['tags'])
                            or (platform=='bilibili' and not 1<=len(p['tags'])<=10)):
            raise VpubError('invalid_tags','Tags are a list of nonempty words without commas or new lines; Bilibili takes 1 to 10.')
        if platform=='x' and any(not re.fullmatch(r'#?\w+',t) for t in p.get('tags',[])):
            raise VpubError('invalid_tags','Caption hashtags must contain only letters, digits or underscores, without spaces.')
        if platform in {'instagram','x'}:
            p['post_text']=(x_post_text(p['title'],p['description'],p.get('tags',[]),ai_generated,p['metadata_language'])
                            if platform=='x' else p['title']+'\n\n'+p['description']+('\n\nAI-generated' if ai_generated else ''))
            # None is an adopted post. No upload here declared anything, so the worker keeps whatever its caption says now.
            if ai_generated is None: p['keep_ai_line']=True
            if platform=='instagram' and len(p['post_text'])>2200:
                raise VpubError('description_too_long','Instagram caption exceeds 2200 characters; preserve both languages when shortening.')
            if platform=='x' and not verifying and (count := x_weighted_length(p['post_text']))>X_POST_LIMIT:
                raise VpubError('x_text_too_long',f"CrossCast's preflight estimate comes to {count}, over X's {X_POST_LIMIT}. Unrecognized emoji or domain names can be overestimated. Shorten it; nothing was saved.")
        if platform=='bilibili' and len(p['description'])>2000:
            raise VpubError('description_too_long','Bilibili descriptions allow at most 2000 characters.')
        if platform=='douyin':
            p['caption']=douyin_caption(p['description'],p.get('tags',[]))
            if douyin_caption_units(p['caption'])>1000:
                raise VpubError('description_too_long',DOUYIN_TOO_LONG)
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
    """One correction, once. An uncertain X save cannot be retried, even with person approval; verification stays read-only."""
    from .work_delete_state import refuse_other_action
    refuse_other_action(key)
    work_id=receipt['id']
    if actual!=patch['expected_identity']: raise VpubError('wrong_account','Correction identity differs from the publication receipt.')
    stop_check(platform)
    edit_id=digest([key,patch]);path=home()/'edits'/(edit_id+'.json')
    flags=public_text_flags(patch.get('title'),patch.get('description'),patch.get('tags'),patch.get('chapters'))
    flagged={'text_flags':flags} if flags and not verify_only else {}
    if dry_run: return {'key':key,'id':work_id,'patch':patch,'platform_writes':0,'edit_id':edit_id,**flagged}
    if platform=='x' and not verify_only:
        # A builder update (or a different patch) changes edit_id. It must not
        # bypass an unclear earlier press on this same work. Person approval
        # cannot prove that old text at the original X address means failure.
        for prior_path in (home()/'edits').glob('*.json'):
            prior=read_json(prior_path)
            same_work=prior.get('key')==key or (prior.get('platform')==platform and
                prior.get('identity')==actual and str(prior.get('id'))==str(work_id))
            if same_work and prior.get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'}:
                raise VpubError('inspect_existing_edit',X_EDIT_UNVERIFIED_MESSAGE,
                                f'{X_EDIT_INSPECTION_HINT} Receipt: {prior_path}')
    tried=False
    saved={}
    if path.exists():
        saved=read_json(path)
        # A prior success is history, not current platform state. The backend
        # reads the existing work and skips saving only when it still matches.
        tried=saved.get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'}
        if tried and not verify_only and (platform=='x' or not person_approved):
            if platform=='x':
                raise VpubError('inspect_existing_edit',X_EDIT_UNVERIFIED_MESSAGE,
                                f'{X_EDIT_INSPECTION_HINT} Receipt: {path}')
            raise VpubError('inspect_existing_edit','A save was already attempted; inspect the existing work before any retry.',
                            'If a person has looked at the work and the change is not there, add --person-approved to try once more.')
    preserved = ({'preserved_description_sha256': saved['preserved_description_sha256']}
                 if platform == 'bilibili' and saved.get('preserved_description_sha256') else {})
    from .audit import stamped
    def checkpoint(data):
        previous = read_json(path) if path.exists() else None
        entry = stamped({'key': key, 'edit_id': edit_id, 'at': now(), **preserved, **data}, previous)
        atomic_json(path, entry)
        from .audit_journal import transition, edit_group
        transition('edit', key, platform, entry, group=edit_group(patch))
        return entry
    # The record keeps that the flag was given, for a person to read later. It is someone's word, not proof that a person looked.
    approved={'person_approved':True} if person_approved and not verify_only else {}
    if not verify_only: checkpoint({'state':'EDIT_PREPARING','id':work_id,**approved})
    from . import adapters
    if platform=='bilibili':
        from .edits_bilibili import run as edit_bilibili
        result=edit_bilibili(account,actual,receipt,patch,checkpoint,verify_only,previous=saved)
    elif platform=='youtube' and adapters.addon_installed():
        if 'chapters' in patch: raise VpubError('unsupported_edit','With the add-on, put the chapter lines in the description and edit that.')
        result=adapters.youtube_api().update_video(account,work_id,patch,checkpoint,verify_only)
    else:
        try:
            result=adapters.browser_call(platform,'edit',account,expected_identity=actual,
                receipt=receipt,patch=patch,edit_receipt_path=str(path),verify_only=verify_only,audit_key=key)
        except VpubError as exc:
            if platform=='x' and path.exists() and x_edit_was_attempted(read_json(path),actual,work_id):
                raise VpubError(exc.code,f'{exc.message} {X_EDIT_UNVERIFIED_MESSAGE}',
                                f'{exc.hint} {X_EDIT_INSPECTION_HINT} Receipt: {path}'.strip()) from exc
            raise
    # A check changes nothing, so it must not leave what reads as a save that was tried and block the real one.
    if verify_only and not tried and result.get('state')=='EDIT_SUBMITTED_UNVERIFIED': result={**result,'state':'EDIT_MISMATCH'}
    # A check can confirm an unclear save, and nothing less clears it: a page can show the old words for a while, and a cover cannot be compared at all.
    if verify_only and tried and result.get('state')!='EDIT_VERIFIED': result={**result,'state':'EDIT_SUBMITTED_UNVERIFIED'}
    x_attempted = platform=='x' and (x_edit_was_attempted(saved,actual,work_id) or x_edit_was_attempted(result,actual,work_id))
    if platform=='x' and verify_only and x_edit_receipt_matches(saved,actual,work_id):
        result={**saved,**result}  # Keep frozen attempted text when a read cannot find the owned version.
        if result.get('state')=='EDIT_VERIFIED':
            for field in ('message','hint','reason'): result.pop(field,None)
    if platform=='x' and result.get('state') in {'EDIT_MISMATCH','EDIT_SUBMITTED_UNVERIFIED'}:
        result={**result,**x_edit_readback_note(x_attempted)}
    if x_attempted: result['final_submit_attempted']=True
    result={**result,**approved,**flagged}
    entry = checkpoint(result)
    return {k: v for k, v in entry.items() if k in result or k in ("code", "code_last")}


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


def found_after_edit(platform, patch, result=None):
    """What the next check of this work should find, now an edit is confirmed. A review found YouTube's next check still looked for the old description. What it said before stays in the edit's history."""
    return {'title':patch['title'],
            **({'expected_text':(result or {}).get('expected_text',patch['post_text'])} if 'post_text' in patch else {}),
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
                from .audit import stamped
                atomic_json(durable, stamped({**saved, **changes}, saved))


def run(key, patch_path, dry_run=False, verify_only=False, person_approved=False):
    with publication_lock():
        ledger=Ledger()
        try:
            row=ledger.get(key)
            if not row: raise VpubError('receipt_required','Use the key of an existing recorded publication.')
            record=row['data'];receipt=record.get('receipt',{});work_id=receipt.get('id')
            if not work_id: raise VpubError('work_id_required','Verify the existing work ID first; never infer an ID from a title or upload again.',
                                            'Run vpub verify on this receipt key: a full read-back records the ID. For a work you adopted with vpub ops adopt, use vpub edit-work PLATFORM WORK_ID instead.')
            patch=validate_patch(row['platform'],read_json(patch_path),verify_only,record.get('options',{}).get('ai_generated',True))
            actual=record.get('identity',{}).get('identity') or receipt.get('identity')
            result=send(row['platform'],row['account'],actual,receipt,key,patch,dry_run,verify_only,person_approved)
            if dry_run: return result
            edit_id=digest([key,patch])
            record.setdefault('edits',[]).append({'edit_id':edit_id,'patch':patch,'result':result,'at':now(),
                'previous_receipt':deepcopy(receipt), **{k: result[k] for k in ('code','code_last') if k in result}})
            confirmed=result.get('state')=='EDIT_VERIFIED' and 'description' in patch
            changes=found_after_edit(row['platform'],patch,result) if confirmed else {}
            if confirmed:
                record['options'].update({k:v for k,v in patch.items() if k!='expected_identity'})
                if row['platform']=='x': record['options']['post_text']=changes['expected_text']
                if receipt.get('method') == 'browser':
                    from .audit import stamped
                    record['receipt'] = stamped({**receipt, **changes}, receipt)
                else:
                    receipt.update(changes)
            ledger.save(key,row['state'],record)
            if confirmed: keep_resume_in_step(record,row['platform'],work_id,actual,changes)
            return {'key':key,**result}
        finally: ledger.close()
