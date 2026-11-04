"""Bounded, read-only proof from X's own profile pagination responses.

Only requests made by this page after capture starts are eligible. An absent
DOM row, a repeated cursor or a quiet scroll is never an end-of-list signal.
"""
import asyncio
import json
import re
from urllib.parse import parse_qs, urlsplit


OPERATIONS = {'UserByScreenName', 'UserOriginalsTimeline'}
ID = re.compile(r'[0-9]{1,25}')


def request_parts(request):
    address = urlsplit(request.url)
    match = re.fullmatch(r'/i/api/graphql/[^/]+/(UserByScreenName|UserOriginalsTimeline)', address.path)
    if address.scheme != 'https' or address.netloc != 'x.com' or not match or request.method != 'GET':
        return None
    try:
        query = parse_qs(address.query)
        if len(query.get('variables', [])) != 1:
            return None
        variables = json.loads(query['variables'][0])
        if not isinstance(variables, dict):
            return None
        return match[1], variables
    except (TypeError, ValueError):
        return None


def tweet_ids(node):
    """Include quoted/foreign IDs too: extra IDs can only prevent acceptance."""
    found = set()
    if isinstance(node, dict):
        kind = node.get('__typename', '')
        if kind == 'TweetWithVisibilityResults':
            if not isinstance(node.get('tweet'), dict) or node['tweet'].get('__typename') != 'Tweet':
                raise ValueError('Missing visible post result')
        if (kind == 'TimelineTweet' or node.get('itemType') == 'TimelineTweet') and 'tweet_results' not in node:
            raise ValueError('Missing timeline post result')
        if kind == 'Tweet':
            value = node.get('rest_id')
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise ValueError('Unreadable post ID')
            found.add(value)
        elif kind.startswith('Tweet') and kind != 'TweetWithVisibilityResults':
            raise ValueError('Unreadable post result')
        if 'tweet_results' in node:
            result = node['tweet_results']
            if not isinstance(result, dict) or not isinstance(result.get('result'), dict):
                raise ValueError('Missing post result')
            if result['result'].get('__typename') not in {'Tweet', 'TweetWithVisibilityResults'}:
                raise ValueError('Unrecognized post result')
        for value in node.values():
            found.update(tweet_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.update(tweet_ids(value))
    return found


def timeline_page(body, initial):
    instructions = body['data']['user']['result']['timeline']['timeline']['instructions']
    if not isinstance(instructions, list) or not instructions:
        raise ValueError('Missing timeline instructions')
    ids, bottom, terminated, entries_seen = set(), [], False, False
    for instruction in instructions:
        kind = instruction['type']
        if kind == 'TimelineClearCache' and initial and not entries_seen:
            continue
        if kind == 'TimelineTerminateTimeline':
            if instruction.get('direction') != 'Bottom' or terminated:
                raise ValueError('Unexpected termination')
            terminated = True
        elif kind == 'TimelineAddEntries' and not entries_seen and not terminated:
            entries_seen = True
            entries = instruction['entries']
            if not isinstance(entries, list):
                raise ValueError('Missing timeline entries')
            for entry in entries:
                content = entry['content']
                entry_type = content.get('entryType')
                if entry_type == 'TimelineTimelineCursor':
                    if content.get('cursorType') == 'Bottom':
                        bottom.append(content.get('value'))
                    elif content.get('cursorType') != 'Top':
                        raise ValueError('Unknown cursor kind')
                elif entry_type not in {'TimelineTimelineItem', 'TimelineTimelineModule'}:
                    raise ValueError('Unknown entry kind')
                elif entry_type == 'TimelineTimelineItem' and not isinstance(content.get('itemContent'), dict):
                    raise ValueError('Missing timeline item')
                elif entry_type == 'TimelineTimelineModule' and not isinstance(content.get('items'), list):
                    raise ValueError('Missing timeline module')
                items = ([content] if entry_type == 'TimelineTimelineItem' else
                         [item['item'] for item in content['items']] if entry_type == 'TimelineTimelineModule' else [])
                for item in items:
                    item_content = item['itemContent']
                    if item_content.get('itemType', item_content.get('__typename')) not in {'TimelineTweet', 'TimelineUser'}:
                        raise ValueError('Unknown timeline item')
                ids.update(tweet_ids(content))
        else:
            raise ValueError('Unknown or reordered timeline instruction')
    if not entries_seen or len(bottom) != 1 or not isinstance(bottom[0], str) or not bottom[0]:
        raise ValueError('Missing unique continuation cursor')
    return ids, bottom[0], terminated


def proof(records, actual):
    """Return accumulated IDs and explicit completion, or conservatively pending."""
    ids = set()
    try:
        owner_ids, pages = set(), []
        for operation, variables, status, body in records:
            if status != 200 or not isinstance(body, dict) or body.get('errors'):
                return ids, False
            if operation == 'UserByScreenName':
                if str(variables.get('screen_name', '')).casefold() != actual.casefold():
                    continue
                user = body['data']['user']['result']
                owner = user['rest_id']
                if (user.get('__typename') != 'User' or not isinstance(owner, str) or not ID.fullmatch(owner)
                        or user['core']['screen_name'].casefold() != actual.casefold()):
                    return ids, False
                owner_ids.add(owner)
            elif operation == 'UserOriginalsTimeline':
                pages.append((variables, body))
            else:
                return ids, False
        if len(owner_ids) != 1 or not pages:
            return ids, False
        owner = next(iter(owner_ids))
        cursor, used, ended = None, set(), False
        for index, (variables, body) in enumerate(pages):
            requested = variables.get('cursor')
            if variables.get('userId') != owner or requested != cursor or ended or requested in used:
                return ids, False
            used.add(requested)
            user = body['data']['user']['result']
            if (user.get('rest_id', owner) != owner or user.get('__typename', 'User') != 'User'
                    or ('core' in user and user['core']['screen_name'].casefold() != actual.casefold())):
                return ids, False
            found, cursor, ended = timeline_page(body, initial=index == 0)
            ids.update(found)
        return ids, ended
    except (KeyError, TypeError, ValueError, AttributeError):
        return ids, False


class TimelineCapture:
    """Observe the browser; never issue, replay or change a platform request."""
    def __init__(self, page, actual):
        self.page, self.actual = page, actual
        self.requests, self.records, self.tasks = {}, {}, []
        self.failed = False

    def start(self):
        self.page.on('request', self.request)
        self.page.on('response', self.response)

    def request(self, request):
        parts = request_parts(request)
        if parts is None:
            return
        if len(self.requests) >= 24:
            self.failed = True
            return
        self.requests[request] = (len(self.requests), parts)

    def response(self, response):
        if response.request in self.requests:
            self.tasks.append(asyncio.create_task(self.read(response)))

    async def read(self, response):
        index, (operation, variables) = self.requests[response.request]
        try:
            if response.from_service_worker:
                self.failed = True
                return
            body = await asyncio.wait_for(response.json(), timeout=8)
            self.records[index] = (operation, variables, response.status, body)
        except Exception:
            self.failed = True

    def snapshot(self):
        ids = set()
        for operation, _, _, body in self.records.values():
            if operation == 'UserOriginalsTimeline':
                try:
                    ids.update(tweet_ids(body))
                except (TypeError, ValueError, AttributeError):
                    self.failed = True
        if self.failed or len(self.records) != len(self.requests):
            return ids, False
        found, complete = proof([self.records[index] for index in sorted(self.records)], self.actual)
        return ids | found, complete

    async def close(self):
        self.page.remove_listener('request', self.request)
        self.page.remove_listener('response', self.response)
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
