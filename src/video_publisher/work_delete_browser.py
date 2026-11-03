"""Free, one-shot browser deletion; each route must bind its exact owned target."""
from .core import VpubError


async def execute(page, platform, actual, payload):
    target = payload['target']
    if platform != target['platform'] or actual != target['identity']:
        raise VpubError('wrong_account', 'The current account differs from this publication receipt.')
    operation = payload.get('operation')
    if operation not in {'inspect', 'delete', 'verify'}:
        raise VpubError('unsupported_operation', 'Use inspect, delete or verify for a work deletion.')
    from . import work_delete_youtube, work_delete_x, work_delete_douyin, work_delete_instagram
    route = {'youtube': work_delete_youtube, 'x': work_delete_x,
             'douyin': work_delete_douyin, 'instagram': work_delete_instagram}.get(platform)
    if route is None:
        raise VpubError('unsupported_operation', 'No free work-deletion route for this platform.')
    return await route.execute(page, actual, target, operation, payload['deletion_receipt_path'])
