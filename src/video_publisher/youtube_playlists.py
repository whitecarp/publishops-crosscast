"""YouTube playlists on YouTube's own pages, free. Reordering stays in the paid add-on."""
from . import collections
from .core import NOT_ON_SALE, VpubError


def listing(playlist=None, cursor=None, limit=50):
    if cursor:
        raise VpubError('cursor_unsupported', 'Studio lists have no page cursor; this read already goes through every page.')
    return collections.listing('youtube', playlist)


def verify(key):
    return collections.verify(key)


def current_title(playlist):
    found = [row for row in collections.listing('youtube')['items'] if row['id'] == playlist]
    if len(found) != 1:
        raise VpubError('collection_not_visible', 'This channel has no playlist with that ID in Studio; nothing changed.')
    return found[0]['title']


def change(action, playlist=None, title=None, description=None, privacy=None, video=None, item=None, position=None, request_id=None):
    if action == 'move':
        raise VpubError('paid_feature_required', 'Reordering a playlist is part of the paid CrossCast add-on.', f'{NOT_ON_SALE} Everything else keeps working without it.')
    if action == 'add':
        return collections.membership('youtube', 'add', playlist, video, request_id)
    if action == 'remove':
        return collections.membership('youtube', 'remove', playlist, item, request_id)
    if action == 'delete':
        return collections.change('youtube', 'delete', playlist, request_id=request_id)
    if action == 'create':
        return collections.change('youtube', 'create', title=title, request_id=request_id, description=description, privacy=privacy or 'private')
    if title is None and description is None and privacy is None:
        raise VpubError('invalid_options', 'Choose at least one of --title, --description or --privacy.')
    collections.validate('youtube', playlist)
    return collections.change('youtube', 'rename', playlist, title=title or current_title(playlist), request_id=request_id, description=description, privacy=privacy)
