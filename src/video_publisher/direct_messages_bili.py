"""Delegate to the included bili-cli; its new-contact checks stay in force."""
from pathlib import Path
import tempfile
from . import adapters
from .core import VpubError, read_json, atomic_json


def execute(operation, expected, **kw):
    if adapters.auth_status('bilibili', 'default', 'cli')['identity'] != expected:
        raise VpubError('wrong_account', 'Direct-message account changed.')
    who = kw.get('recipient'); limit = kw.get('limit', 20)
    if operation == 'list':
        return {'identity': expected, 'platform_writes': 0, **adapters.bili(['dm','list','--limit',str(limit)])}
    if operation in {'read','verify'}:
        result = adapters.bili(['dm','read',who,'--limit',str(limit)])
        rows = result.get('messages', [])
        value = {'identity': expected, 'recipient': who, 'items': rows, 'can_send': who != expected,
                 'platform_writes': 0, 'scope': 'Bounded upstream conversation page; no mark-read request.'}
        if operation == 'verify':
            receipt = kw['receipt']; old = {str(x.get('seqno')) for x in receipt['before'].get('items', [])}
            # An auto_reply is Bilibili answering in the account's name, not the message that was sent.
            value['matching_outgoing_message'] = any(x.get('from') == 'me' and not x.get('auto_reply') and x.get('text') == receipt['request']['text']
                and x.get('seqno') is not None and str(x['seqno']) not in old for x in rows)
        return value
    if operation != 'send': raise VpubError('invalid_operation','Unknown DM action.')
    from .direct_messages import submitting
    # File input keeps text out of command arguments. No --allow-new: upstream
    # refuses new contacts unless its own explicit contact authorization is used.
    with tempfile.TemporaryDirectory(prefix='vpub-dm-') as folder:
        path = Path(folder)/'message.txt'; path.write_text(kw['text'], encoding='utf-8')
        submitting(kw['receipt_path'])
        result = adapters.bili(['dm','send',who,'--file',str(path)])
    saved=read_json(kw['receipt_path']); saved.update(state='DM_UNVERIFIED', platform_writes=1,
        send_acknowledged=result.get('sent') is True, message_id=(result.get('msg_keys') or [None])[0])
    atomic_json(kw['receipt_path'], saved)
    return {'state':'DM_UNVERIFIED','platform_writes':1,'send_acknowledged':result.get('sent') is True}
