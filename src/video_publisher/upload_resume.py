"""Durable YouTube resumable uploads; session URLs never enter public receipts."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .core import VpubError, atomic_json, digest, file_sha, home, now, read_json, stop_check

CHUNK = 8 * 1024 * 1024


def session_url(value):
    p = urlsplit(value or '')
    if p.scheme != 'https' or p.hostname not in {'www.googleapis.com', 'youtube.googleapis.com'} or p.port not in {None, 443} or p.username or p.password or p.path != '/upload/youtube/v3/videos':
        raise VpubError('invalid_upload_session', 'Unexpected upload session destination.')
    return value


def run(request, plan, account, identity, checkpoint, *, max_chunks=None, chunk_size=CHUNK):
    if max_chunks is not None and (type(max_chunks) is not int or max_chunks < 1):
        raise VpubError('invalid_limit', 'max-chunks must be a positive integer.')
    if chunk_size <= 0 or chunk_size % (256 * 1024):
        raise VpubError('invalid_chunk', 'Upload chunks must be multiples of 256 KiB.')
    video = Path(plan['video'])
    if file_sha(video) != plan['video_sha256'] or video.stat().st_size != plan['video_bytes']:
        raise VpubError('source_changed', 'The upload snapshot changed; no bytes sent.')
    key = digest(['youtube', account, plan['video_sha256']])
    path = home() / 'upload-sessions' / (key + '.json')
    intent = digest([identity, plan['video_sha256'], plan['video_bytes'], request.body])
    saved = read_json(path) if path.exists() else None
    if saved and (saved['identity'] != identity or saved['intent'] != intent):
        raise VpubError('upload_conflict', 'Account, media or metadata differs from the original upload.')
    size = video.stat().st_size

    def persist(**values):
        saved.update(values, updated_at=now())
        atomic_json(path, saved)
        public = {k: saved[k] for k in ('state', 'offset', 'bytes', 'id') if k in saved}
        checkpoint({**public, 'method': 'api', 'upload_session_key': key})

    def request_http(uri, method, body=None, headers=None):
        stop_check('youtube')
        try:
            return request.http.request(uri, method=method, body=body, headers=headers or {})
        except Exception:
            persist(state='UPLOAD_INTERRUPTED')
            raise VpubError('upload_interrupted', 'Connection interrupted. Resume the same receipt; do not publish again.') from None

    def response(resp, content):
        code = int(resp.status)
        if code in {200, 201}:
            try:
                value = json.loads(content)
                vid = value['id']
                if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
                    raise ValueError('id')
            except (ValueError, KeyError, TypeError):
                persist(state='UPLOAD_UNVERIFIED')
                raise VpubError('upload_unverified', 'Upload response lacked a valid video ID; retain this session.') from None
            persist(state='SUBMITTED', offset=size, id=vid)
            return vid
        if code == 308:
            match = re.fullmatch(r'bytes=0-(\d+)', resp.get('range', ''))
            offset = int(match[1]) + 1 if match else 0
            if (resp.get('range') and not match) or offset > size or offset < saved.get('offset', 0):
                raise VpubError('invalid_upload_range', 'Server returned an inconsistent upload offset.')
            uri = session_url(resp.get('location', saved['uri']))
            persist(state='UPLOADING', offset=offset, uri=uri)
            if resp.get('retry-after'):
                raise VpubError('upload_retry_later', 'Server requested a delay before resuming. Retry later; the session is retained.')
            return None
        persist(state='UPLOAD_EXPIRED' if code in {404, 410} else 'UPLOAD_INTERRUPTED')
        raise VpubError('upload_expired' if code in {404, 410} else 'upload_http_error',
                        f'Upload session returned HTTP {code}; retained without creating another upload.')

    if saved:
        if saved.get('id'):
            return {'id': saved['id'], 'state': 'SUBMITTED', 'method': 'api', 'upload_session_key': key}
        if not saved.get('uri'):
            raise VpubError('upload_initialization_uncertain', 'Session initiation was interrupted. No automatic second upload will be created.')
        chunk_size = saved['chunk_size']
        # Server offset is authoritative, including a lost final response.
        resp, content = request_http(session_url(saved['uri']), 'PUT', headers={'Content-Length': '0', 'Content-Range': f'bytes */{size}'})
        vid = response(resp, content)
        if vid:
            return {'id': vid, 'state': 'SUBMITTED', 'method': 'api', 'upload_session_key': key}
    else:
        saved = {'identity': identity, 'intent': intent, 'bytes': size, 'offset': 0, 'chunk_size': chunk_size}
        persist(state='UPLOAD_INITIATING')
        headers = {**request.headers, 'X-Upload-Content-Type': request.resumable.mimetype(),
                   'X-Upload-Content-Length': str(size), 'Content-Length': str(request.body_size)}
        resp, _ = request_http(request.uri, request.method, request.body, headers)
        if resp.status != 200 or not resp.get('location'):
            raise VpubError('upload_initialization_uncertain', 'Upload session was not confirmed; no automatic retry.')
        # Retain the returned session locally even when a future host change
        # requires inspection; never lose it or send bytes to an untrusted host.
        persist(state='UPLOAD_SESSION_CHECK', uri=resp['location'])
        session_url(saved['uri'])
        persist(state='UPLOADING')
    chunks = 0
    with video.open('rb') as stream:
        while saved['offset'] < size:
            start = saved['offset']
            stream.seek(start)
            data = stream.read(chunk_size)
            resp, content = request_http(session_url(saved['uri']), 'PUT', data,
                {'Content-Length': str(len(data)), 'Content-Range': f'bytes {start}-{start+len(data)-1}/{size}'})
            vid = response(resp, content)
            if vid:
                return {'id': vid, 'state': 'SUBMITTED', 'method': 'api', 'upload_session_key': key}
            if saved['offset'] <= start:
                raise VpubError('upload_stalled', 'Server made no progress; session retained for status inspection.')
            chunks += 1
            if max_chunks is not None and chunks >= max_chunks:
                persist(state='UPLOAD_PAUSED')
                return {'state': 'UPLOAD_PAUSED', 'offset': saved['offset'], 'bytes': size, 'method': 'api', 'upload_session_key': key}
    raise VpubError('upload_unverified', 'All bytes acknowledged without a video ID. Resume to query the same session.')
