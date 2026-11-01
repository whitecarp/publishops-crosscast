"""Bilibili 合集 through bili-cli. Its season commands do most of the work. A complete member list and rename, which it
lacks, use its own request helpers in a separate process, so the GPL library never loads in CrossCast's process."""
import re
from . import adapters
from .collections import before_submit
from .core import VpubError, now

# bili-cli's season list reads one page of this size.
PAGE = 50
HELPER = '''import sys
from bili_cli import config, guard
from bili_cli.commands.season import _api, _seasons
from bili_cli.errors import BiliError
from bili_cli.runtime import run


def sections(entry):
    return (entry.get("sections") or {}).get("sections") or []


async def mine(cred, season_id):
    for entry in await _seasons(cred):
        if (entry.get("season") or {}).get("id") == season_id:
            return entry
    raise BiliError("not_found", f"在你的合集里找不到 ID {season_id}。")


async def items(cred, season_id):
    entry = await mine(cred, season_id)
    rows = []
    for section in sections(entry):
        detail = await _api("season/section", "GET", cred).update_params(id=section["id"]).result
        rows += [{"id": e.get("bvid"), "title": e.get("title")} for e in detail.get("episodes") or []]
    return {"items": rows, "count": entry["season"].get("ep_count")}


async def rename(cred, season_id, title):
    entry = await mine(cred, season_id)
    old = entry["season"]
    # The page sends the whole season, so everything but the title goes back as it was.
    season = {"id": old["id"], "title": title, "cover": old.get("cover"), "desc": old.get("desc") or "",
              "season_price": old.get("season_price") or 0, "isEnd": old.get("isEnd") or 0}
    sorts = [{"id": x["id"], "sort": x.get("order") or number} for number, x in enumerate(sections(entry), 1)]
    guard.check("season")
    await _api("season/edit", "POST", cred, json_body=True).update_data(season=season, sorts=sorts, csrf=cred.bili_jct).result
    guard.record("season", {"id": season_id, "title": title}, "ok")
    return {"id": season_id, "title": title}


async def body():
    cred = config.require_credential()
    if sys.argv[1] == "items":
        return await items(cred, int(sys.argv[2]))
    return await rename(cred, int(sys.argv[2]), sys.argv[3])


run(body)
'''


def listing(owner):
    rows = adapters.bili(['season', 'list']).get('seasons')
    if not isinstance(rows, list):
        raise VpubError('upstream_schema_changed', 'bili-cli did not return a season list.')
    return {'platform': 'bilibili', 'identity': owner, 'complete': len(rows) < PAGE, 'scope': 'owned_work_collections',
            'items': [{'id': str(row['id']), 'title': row['title'], 'state': row.get('state'), 'videos': row.get('videos')} for row in rows],
            'coverage': 'bili_cli', 'platform_writes': 0, 'observed_at': now()}


def members(owner, collection):
    data = adapters.bili(['items', collection], program=HELPER)
    rows = data.get('items')
    if not isinstance(rows, list):
        raise VpubError('upstream_schema_changed', 'bili-cli did not return the collection members.')
    known = all(re.fullmatch(r'BV[A-Za-z0-9]{10}', str(row.get('id'))) for row in rows)
    return {'identity': owner, 'collection_id': collection, 'items': rows, 'complete': known and (data.get('count') or len(rows)) == len(rows),
            'coverage': 'bili_cli', 'platform_writes': 0, 'observed_at': now()}


def call(operation, owner, collection_id=None, **kw):
    if operation == 'list':
        return listing(owner)
    if operation == 'items':
        return members(owner, collection_id)
    before_submit({'platform': 'bilibili', 'operation': operation, 'collection_id': collection_id, **kw}, owner)
    if operation == 'create':
        made = adapters.bili(['season', 'create', '--cover', kw['cover'], '--desc', kw['description'], '--', kw['title']])
        return {'identity': owner, 'submitted': True, 'collection_id': str(made['id']) if made.get('id') else None}
    if operation == 'rename':
        adapters.bili(['rename', collection_id, kw['title']], program=HELPER)
    elif operation == 'delete':
        adapters.bili(['season', 'delete', collection_id])
    else:
        adapters.bili(['season', operation, collection_id, kw['work_id']])
    return {'identity': owner, 'submitted': True, 'collection_id': collection_id}
