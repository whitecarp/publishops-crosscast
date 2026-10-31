"""Discover and adopt existing works without fabricating publication receipts."""
from .core import VpubError, atomic_json, digest, home, now, platform_check, publication_lock, read_json, stop_check


def binding(platform, work):
    path = home() / 'operations/adopted' / (digest([platform, work]) + '.json')
    return read_json(path) if path.exists() else None


def discover(platform, limit=50, pages=3):
    from . import adapters
    from .operations_sync import works
    platform_check(platform)
    stop_check(platform)
    if type(limit) is not int or not 1 <= limit <= 200 or type(pages) is not int or not 1 <= pages <= 10:
        raise VpubError('invalid_limit', 'Use limit 1–200 and pages 1–10.')
    if platform in {'douyin', 'instagram', 'x'}:
        result = adapters.browser_call(platform, 'discover_works', 'default', limit=limit, pages=pages)
    else:
        cursor = None; rows = {}; identity = None; seen = set(); omitted=0
        for index in range(pages):
            value = works(platform, min(50, limit-len(rows)), cursor)
            if identity and value['identity'] != identity:
                raise VpubError('wrong_account', 'Account changed during work discovery.')
            identity = value['identity']
            omitted+=value.get('omitted_count',0)
            rows.update({v['id']: v for v in value['items']})
            cursor = value.get('next_cursor')
            if not cursor or len(rows) >= limit: break
            if cursor in seen: raise VpubError('cursor_loop', 'Work discovery repeated a cursor.')
            seen.add(cursor)
        result = {**value, 'items': list(rows.values()), 'pages_read': index+1,
                  'next_cursor': cursor, 'complete': not cursor and not omitted,'omitted_count':omitted}
    result.update(platform=platform, observed_at=now(), platform_writes=0,
                  scope='Bounded discovery only; use ops adopt to verify ownership and track one work.')
    atomic_json(home()/'operations/catalogs'/(digest([platform,result['identity']])+'.json'), result)
    return result


def adopt(platform, work, pages=3):
    from . import adapters, operations as ops
    from .operations_sync import watch
    ops.work_id(platform, work)
    stop_check(platform)
    if type(pages) is not int or not 1 <= pages <= 10:
        raise VpubError('invalid_limit', 'Use pages 1–10.')
    if platform in {'bilibili','youtube'}:
        value = ops.backend(platform, 'stats', work)
        evidence = 'fresh_api_owner_check'
    else:
        # A fresh authenticated check is mandatory; cached discovery alone is not ownership.
        value = adapters.browser_call(platform,'adopt_work','default',work_id=work,pages=pages)
        if value.get('owner_verified') is not True or value.get('work_id') != work:
            raise VpubError('owner_unverified','The official page did not verify this work owner.')
        evidence = value['ownership_evidence']
    receipt = {'schema_version':1,'platform':platform,'work_id':work,'identity':value['identity'],
               'title':value.get('title'),'url':value.get('url'),'verified_at':now(),
               'ownership_evidence':evidence,'kind':'adopted_existing_work','platform_writes':0}
    with publication_lock():
        old = binding(platform,work)
        if old and old['identity'] != receipt['identity']:
            raise VpubError('wrong_account','An existing adoption belongs to another account.')
        if platform in {'douyin','instagram','x'}:
            # Also detect a conflict with a historical upload receipt.
            try: prior = ops.browser_identity(platform,work)
            except VpubError as exc:
                if exc.code != 'work_identity_required': raise
            else:
                if prior != receipt['identity']: raise VpubError('wrong_account','Upload owner conflicts with adoption.')
        path = home()/'operations/watchlist.json'; data = watch('list')
        matches = [t for t in data['items'] if (t['platform'],t['work_id']) == (platform,work)]
        if matches and any(t['identity'] != receipt['identity'] for t in matches):
            raise VpubError('wrong_account','Watch owner conflicts with adoption.')
        atomic_json(home()/'operations/adopted'/(digest([platform,work])+'.json'),receipt)
        if not matches:
            data['items'].append({k:receipt[k] for k in ('platform','work_id','identity','title','url')} | {'added_at':now()})
        atomic_json(path,data)
    return {**receipt,'state':'ADOPTED','watching':True,'reused':bool(old)}
