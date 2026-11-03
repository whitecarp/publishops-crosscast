"""Local refusal: pinned bilibili-api-python 17.4.2 has no evidenced own-video
 deletion endpoint or explicit gone-response contract. Archive listing is GET,
 not deletion. Unsupported is not a built-but-untried write route.
"""
import re

from .core import VpubError


def execute(target: dict, operation: str, receipt_path: str):
    """Refuse locally, before account access or a destructive-attempt record."""
    if not isinstance(operation, str) or operation not in {'inspect', 'delete', 'verify'}:
        raise VpubError('invalid_operation', 'Use inspect, delete or verify.')
    if (not isinstance(target, dict) or target.get('platform') != 'bilibili'
            or not isinstance(target.get('id'), str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', target['id'])
            or not isinstance(target.get('identity'), str) or not re.fullmatch(r'[1-9][0-9]*', target['identity'])
            or not isinstance(target.get('key'), str) or not re.fullmatch(r'[a-f0-9]{64}', target['key'])
            or any(not isinstance(target.get(key), str) or not target[key].strip() for key in ('account', 'generation'))
            or not isinstance(target.get('title'), str) or not isinstance(receipt_path, str) or not receipt_path.strip()):
        raise VpubError('invalid_deletion_target', 'Deletion requires an exact publication target and receipt path.')
    raise VpubError('unsupported_delete',
                    'Bilibili work deletion is unsupported: the pinned library supplies no evidenced delete operation or exact gone-response contract. No account was read and nothing was deleted.',
                    'Inspect and manage this same video manually on Bilibili.')
