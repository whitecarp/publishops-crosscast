"""Bilibili 合集 through vpub collection. A fake bili-cli answers, so nothing reaches Bilibili."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from video_publisher import adapters, cli, collections as c, collections_bilibili as bili
from video_publisher.core import VpubError, read_json

OWNER = '12345'
WORK = 'BV1234567890'
OTHER = 'BV0987654321'


class FakeBili:
    def __init__(self):
        self.seasons = {7: {'id': 7, 'title': 'Old', 'state': '正常', 'videos': 1, 'episodes': [WORK]}}
        self.writes = []

    def __call__(self, args, timeout=210, program=adapters.BILI_MAIN):
        if program == bili.HELPER:
            if args[0] == 'items':
                season = self.seasons[int(args[1])]
                return {'items': [{'id': bvid, 'title': bvid} for bvid in season['episodes']], 'count': len(season['episodes'])}
            self.writes.append(tuple(args))
            self.seasons[int(args[1])]['title'] = args[2]
            return {'id': int(args[1]), 'title': args[2]}
        if args[:2] == ['season', 'list']:
            return {'seasons': list(self.seasons.values())}
        self.writes.append(tuple(args))
        if args[1] == 'create':
            self.seasons[8] = {'id': 8, 'title': args[-1], 'state': '审核中', 'videos': 0, 'episodes': []}
            return {'id': 8, 'title': args[-1]}
        if args[1] == 'delete':
            del self.seasons[int(args[2])]
            return {'id': int(args[2]), 'deleted': True}
        season = self.seasons[int(args[2])]
        season['episodes'].append(args[3]) if args[1] == 'add' else season['episodes'].remove(args[3])
        return {}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'VPUB_HOME': str(self.root / 'runtime')})
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeBili()
        for target in (patch.object(adapters, 'bili', side_effect=self.fake), patch.object(c, 'identity', return_value=OWNER)):
            target.start()
            self.addCleanup(target.stop)


class SeasonTests(Base):
    def test_the_list_shows_every_season_with_its_id_as_text(self):
        result = c.listing('bilibili')
        self.assertEqual([{'id': '7', 'title': 'Old', 'state': '正常', 'videos': 1}], result['items'])
        self.assertTrue(result['complete'])
        self.assertEqual([], self.fake.writes)

    def test_a_full_page_might_not_be_all_of_them(self):
        self.fake.seasons = {n: {'id': n, 'title': str(n)} for n in range(bili.PAGE)}
        self.assertFalse(c.listing('bilibili')['complete'])

    def test_rename_reads_the_new_title_back(self):
        result = c.change('bilibili', 'rename', '7', title='New', request_id='r1')
        self.assertEqual('CONFIRMED', result['state'])
        self.assertEqual([('rename', '7', 'New')], self.fake.writes)
        self.assertEqual(1, read_json(c.receipt_path(result['key']))['platform_writes'])

    def test_the_same_request_never_renames_twice(self):
        c.change('bilibili', 'rename', '7', title='New', request_id='r1')
        again = c.change('bilibili', 'rename', '7', title='New', request_id='r1')
        self.assertTrue(again['reused'])
        self.assertEqual(1, len(self.fake.writes))

    def test_a_rename_that_does_not_show_stays_unverified(self):
        def refused(args, timeout=210, program=adapters.BILI_MAIN):
            if program == bili.HELPER:
                raise VpubError('api_error', 'Refused.')
            return self.fake(args)
        with patch.object(adapters, 'bili', side_effect=refused):
            result = c.change('bilibili', 'rename', '7', title='New', request_id='r1')
        self.assertEqual(('UNVERIFIED', 'api_error'), (result['state'], result['error']))
        self.assertEqual('Old', self.fake.seasons[7]['title'])

    def test_a_title_that_starts_with_a_dash_cannot_become_an_option(self):
        cover = self.root / 'cover.png'
        Image.new('RGB', (100, 100), 'red').save(cover)
        result = c.change('bilibili', 'create', title='--all', request_id='c1', cover=str(cover), description='About')
        self.assertEqual(('season', 'create', '--cover', str(cover.resolve()), '--desc', 'About', '--', '--all'), self.fake.writes[0])
        self.assertEqual(('CONFIRMED', '8'), (result['state'], result['collection_id']))

    def test_create_needs_a_cover_and_rejects_a_changed_one(self):
        with self.assertRaises(VpubError) as caught:
            c.change('bilibili', 'create', title='New', request_id='c1')
        self.assertEqual('cover_required', caught.exception.code)
        self.assertEqual([], self.fake.writes)

    def test_delete_is_confirmed_by_its_absence_from_a_complete_list(self):
        result = c.change('bilibili', 'delete', '7', request_id='d1')
        self.assertEqual('CONFIRMED', result['state'])
        self.assertEqual([('season', 'delete', '7')], self.fake.writes)

    def test_an_unknown_season_is_never_tried(self):
        with self.assertRaises(VpubError) as caught:
            c.change('bilibili', 'rename', '9', title='New', request_id='r1')
        self.assertEqual('collection_not_visible', caught.exception.code)
        self.assertEqual([], self.fake.writes)


class MemberTests(Base):
    def test_adding_and_removing_keep_the_other_videos(self):
        added = c.membership('bilibili', 'add', '7', OTHER, 'a1')
        self.assertEqual('CONFIRMED', added['state'])
        self.assertEqual([WORK, OTHER], self.fake.seasons[7]['episodes'])
        removed = c.membership('bilibili', 'remove', '7', WORK, 'r1')
        self.assertEqual('CONFIRMED', removed['state'])
        self.assertEqual([OTHER], self.fake.seasons[7]['episodes'])

    def test_a_video_already_there_is_not_sent_again(self):
        result = c.membership('bilibili', 'add', '7', WORK, 'a1')
        self.assertEqual(0, result['platform_writes'])
        self.assertEqual([], self.fake.writes)

    def test_a_part_list_blocks_the_change(self):
        with patch.object(adapters, 'bili', return_value={'items': [{'id': WORK}], 'count': 5}):
            with self.assertRaises(VpubError) as caught:
                c.membership('bilibili', 'add', '7', OTHER, 'a1')
        self.assertEqual('read_incomplete', caught.exception.code)

    def test_a_video_id_that_cannot_be_read_is_not_counted_as_complete(self):
        with patch.object(adapters, 'bili', return_value={'items': [{'id': None}], 'count': 1}):
            self.assertFalse(bili.members(OWNER, '7')['complete'])

    def test_verify_only_reads(self):
        result = c.membership('bilibili', 'add', '7', OTHER, 'a1')
        self.fake.writes.clear()
        self.assertEqual('CONFIRMED', c.verify(result['key'])['state'])
        self.assertEqual([], self.fake.writes)

    def test_the_commands_parse(self):
        for argv in (['collection', 'list', 'bilibili'], ['collection', 'items', 'bilibili', '7'],
                     ['collection', 'rename', 'bilibili', '7', '--title', 'New', '--request-id', 'r1'],
                     ['collection', 'add', 'bilibili', '7', WORK, '--request-id', 'a1']):
            with self.subTest(argv=argv):
                cli.parser().parse_args(argv)


class HelperTests(unittest.TestCase):
    """The rename request itself, in a separate process, with bili-cli's request maker replaced by a recorder."""
    RECORDER = '''import json
import bili_cli.commands.season as season
from bili_cli import config, guard

SEASONS = [{"season": {"id": 7, "title": "Old", "cover": "http://i0/cover.jpg", "desc": "About", "isEnd": 1, "season_price": 0, "ep_count": 2},
            "sections": {"sections": [{"id": 71, "order": 1}, {"id": 72, "order": 2}]}}]
SENT = []


class Call:
    def __init__(self, path, method): self.path, self.method, self.sent = path, method, {}
    def update_params(self, **values): self.sent.update(values); return self
    def update_data(self, **values): self.sent.update(values); return self
    @property
    def result(self):
        async def answer():
            SENT.append([self.path, self.method, {k: v for k, v in self.sent.items() if k != "csrf"}])
            print("SENT " + json.dumps(SENT[-1], ensure_ascii=False), file=__import__("sys").stderr)
            if self.path == "season/section":
                return {"episodes": [{"bvid": "BV" + str(self.sent["id"]) + "00000000", "title": "T"}]}
            return 0
        return answer()


async def seasons(cred): return SEASONS
season._api = lambda path, method, cred, json_body=False: Call(path, method)
season._seasons = seasons
config.require_credential = lambda: type("Cred", (), {"bili_jct": "x"})()
guard.check = lambda action: None
guard.record = lambda *args: None
'''

    def run_helper(self, *args):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, BILI_CLI_HOME=folder, PYTHONIOENCODING='utf-8')
            done = subprocess.run([sys.executable, '-c', self.RECORDER + bili.HELPER, *args], capture_output=True, text=True, encoding='utf-8', timeout=120, env=env)
        sent = [json.loads(line[5:]) for line in done.stderr.splitlines() if line.startswith('SENT ')]
        return json.loads(done.stdout), sent

    def test_rename_changes_the_title_and_sends_everything_else_back_unchanged(self):
        answer, sent = self.run_helper('rename', '7', 'New title')
        self.assertTrue(answer['ok'], answer)
        self.assertEqual([['season/edit', 'POST', {
            'season': {'id': 7, 'title': 'New title', 'cover': 'http://i0/cover.jpg', 'desc': 'About', 'season_price': 0, 'isEnd': 1},
            'sorts': [{'id': 71, 'sort': 1}, {'id': 72, 'sort': 2}]}]], sent)

    def test_items_reads_every_section(self):
        answer, sent = self.run_helper('items', '7')
        self.assertEqual(['BV7100000000', 'BV7200000000'], [row['id'] for row in answer['data']['items']])
        self.assertEqual(2, answer['data']['count'])
        self.assertEqual(['season/section', 'season/section'], [row[0] for row in sent])

    def test_a_season_that_is_not_yours_sends_nothing(self):
        answer, sent = self.run_helper('rename', '9', 'New')
        self.assertEqual('not_found', answer['error']['code'])
        self.assertEqual([], sent)

    def test_without_a_sign_in_the_helper_says_so(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'BILI_CLI_HOME': folder, 'VPUB_HOME': folder}):
            with self.assertRaises(VpubError) as caught:
                adapters.bili(['rename', '7', 'New'], program=bili.HELPER)
        self.assertEqual('not_logged_in', caught.exception.code)

    def test_crosscast_itself_still_never_loads_the_gpl_library(self):
        code = ("import sys\nfrom video_publisher import collections, collections_bilibili\n"
                "print([name for name in sys.modules if name.split('.')[0] == 'bilibili_api'])\n")
        done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
        self.assertEqual('[]', done.stdout.strip(), done.stderr)


if __name__ == '__main__':
    unittest.main()
