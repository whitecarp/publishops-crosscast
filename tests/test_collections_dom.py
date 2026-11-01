"""X's bookmark folders, driven in a real browser against small copies of its two sites.

The rebuilt site's copy follows the page code X publishes: the folder window at /i/bookmarks/add/<post>, the address-opened
create and edit windows, and the answers they get. A signed-in account on 2026-10-31 still got the earlier site, where that
address does not exist. Nothing here contacts X, and the rebuilt-site paths have not been tried on a real account yet.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit
from video_publisher import browser_runtime as rt, collections as c, x_pages
from video_publisher.collections_browser import execute, root
from video_publisher.core import VpubError, atomic_json, digest, read_json

OWNER = 'owner'
POST = '1234567890'
PICKER = '''<div role="dialog"><h2>Add to Folder</h2><ul role="list">
<li><button type="button" id="all">All Bookmarks</button></li>%s
<li><button type="button" id="new">Create a new Bookmark Folder</button></li></ul></div>
<script>
for (const row of document.querySelectorAll('button[data-folder]')) row.onclick = () => {
  const change = row.getAttribute('aria-pressed') === 'true' ? 'RemoveMutation' : 'PutMutation';
  fetch('/i/api/graphql/abc/bookmarksAddToFolder' + change + '?folder=' + row.dataset.folder, {method: 'POST'});
  document.querySelector('[role=dialog]').remove();
};
</script>'''
EDIT = '''<div role="dialog" id="edit"><h2>Edit Folder</h2><label>Name<input id="name" value="%s" oninput="save.disabled = this.value === '%s'"></label>
<button type="button" id="ask">Delete Folder</button><button type="button" id="save" disabled>Save</button></div>
<script>
const save = document.getElementById('save');
save.onclick = () => { fetch('/i/api/graphql/abc/bookmarksFolderModalsEditMutation?name=' + encodeURIComponent(document.getElementById('name').value), {method: 'POST'}); document.getElementById('edit').remove(); };
document.getElementById('ask').onclick = () => {
  const sure = document.createElement('div');
  sure.setAttribute('role', 'dialog');
  sure.innerHTML = '<h2>Delete Folder?</h2><button type="button" id="yes">Delete</button><button type="button">Cancel</button>';
  document.body.append(sure);
  document.getElementById('yes').onclick = () => { fetch('/i/api/graphql/abc/bookmarksFolderModalsDeleteMutation', {method: 'POST'}); document.body.innerHTML = ''; };
};
</script>'''
CREATE = '''<div role="dialog" id="make"><h2>Create a folder</h2><label>Name<input id="name" oninput="go.disabled = !this.value.trim()"></label><button type="button" id="go" disabled>Create</button></div>
<script>
const go = document.getElementById('go');
go.onclick = () => { fetch('/i/api/graphql/abc/bookmarksCreateFolderModalMutation?name=' + encodeURIComponent(document.getElementById('name').value), {method: 'POST'}); document.getElementById('make').remove(); };
</script>'''


class FakeX:
    def __init__(self, page):
        self.page = page
        self.folders = {'111': 'Recipes', '222': 'Music'}
        self.inside = {'111': set(), '222': {POST}}
        self.premium = True
        self.writes = []
        self.answer = 'Done'

    async def page_at(self, route, html):
        await route.fulfill(status=200, content_type='text/html', body='<!doctype html><title>X</title><body>' + html)

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != 'x.com':
            return await route.abort()
        query = parse_qs(url.query)
        if url.path.startswith('/i/api/graphql/'):
            return await self.graphql(route, url.path.rsplit('/', 1)[1], query)
        if url.path == '/i/history':
            if not self.premium:
                return await self.page_at(route, '<p>History</p>')
            if 'create_folder' in query:
                return await self.page_at(route, CREATE)
            return await self.page_at(route, "<script>fetch('/i/api/graphql/abc/HistoryFoldersQuery')</script>")
        if url.path.startswith('/i/history/bookmarks/'):
            name = self.folders.get(url.path.rsplit('/', 1)[1], '')
            return await self.page_at(route, EDIT % (name, name))
        if url.path.startswith('/i/bookmarks/add/'):
            if not self.premium:
                return await self.page_at(route, "<script>location.replace('/i/premium_sign_up')</script>")
            post = url.path.rsplit('/', 1)[1]
            rows = ''.join(f'<li><button type="button" data-folder="{key}" aria-pressed="{"true" if post in self.inside.get(key, ()) else "false"}"><span>{name}</span></button></li>'
                           for key, name in self.folders.items())
            return await self.page_at(route, PICKER % rows)
        await self.page_at(route, '<p>Premium</p>')

    async def graphql(self, route, operation, query):
        data = {}
        if operation == 'HistoryFoldersQuery':
            items = [{'id': key, 'name': name} for key, name in self.folders.items()]
            data = {'viewer_v2': {'user_results': {'result': {'__typename': 'User', 'bookmark_collections_slice': {'items': items}}}}}
        elif operation.startswith('bookmarksAddToFolder'):
            self.writes.append((operation, query['folder'][0]))
            if self.answer == 'Done':
                (self.inside[query['folder'][0]].discard if 'Remove' in operation else self.inside[query['folder'][0]].add)(POST)
            data = {'done': self.answer}
        elif operation == 'bookmarksFolderModalsEditMutation':
            self.writes.append((operation, query['name'][0]))
            data = {'bookmark_collection_update': {'id': '111', 'name': query['name'][0]}}
        elif operation == 'bookmarksFolderModalsDeleteMutation':
            self.writes.append((operation, None))
            data = {'bookmark_collection_delete': 'Done'}
        elif operation == 'bookmarksCreateFolderModalMutation':
            self.writes.append((operation, query['name'][0]))
            data = {'bookmark_collection_create': {'id': '333', 'name': query['name'][0]}}
        await route.fulfill(status=200, content_type='application/json', body=json.dumps({'data': data}))


class FolderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'VPUB_HOME': str(Path(temp.name) / 'runtime')})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(3000)
        self.x = FakeX(self.page)
        await self.page.route('**/*', self.x.handle)
        site = patch.object(x_pages, 'rebuilt', new=AsyncMock(return_value=True))
        site.start()
        self.addCleanup(site.stop)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    def receipt(self, action, collection='111', title=None, **extra):
        key = digest([action, collection, title])
        atomic_json(c.receipt_path(key), {'key': key, 'platform': 'x', 'identity': OWNER, 'action': action, 'collection_id': collection,
                                          'title': title, 'state': 'READY', 'intent': {}, **extra})
        return key

    async def run_op(self, operation, **payload):
        return await execute(self.page, 'x', OWNER, {'platform': 'x', 'operation': operation, **payload})

    async def member(self, operation, collection='111', baseline=()):
        key = self.receipt(operation, collection, work_id=POST, before_members=list(baseline))
        return await self.run_op(operation, collection_id=collection, work_id=POST, before_members=list(baseline), receipt_key=key), key

    async def test_the_folder_list_comes_from_the_pages_own_answer(self):
        result = await self.run_op('list')
        self.assertEqual([{'id': '111', 'title': 'Recipes'}, {'id': '222', 'title': 'Music'}], result['items'])
        self.assertTrue(result['complete'])
        self.assertEqual('rebuilt', result['site'])
        self.assertEqual([], self.x.writes)

    async def test_the_window_says_whether_the_post_is_in_a_folder(self):
        outside = await self.run_op('items', collection_id='111', work_id=POST)
        inside = await self.run_op('items', collection_id='222', work_id=POST)
        self.assertEqual(([], True), (outside['items'], outside['complete']))
        self.assertEqual(([{'id': POST}], True), (inside['items'], inside['complete']))
        self.assertEqual([], self.x.writes)

    async def test_adding_clicks_only_the_chosen_folder_and_marks_the_receipt_first(self):
        result, key = await self.member('add')
        self.assertTrue(result['submitted'])
        self.assertEqual([('bookmarksAddToFolderPutMutation', '111')], self.x.writes)
        self.assertEqual({POST}, self.x.inside['111'])
        self.assertEqual({POST}, self.x.inside['222'])
        self.assertEqual('SUBMITTING', read_json(c.receipt_path(key))['state'])

    async def test_removing_takes_the_post_out_of_that_folder_only(self):
        await self.member('remove', '222', [POST])
        self.assertEqual([('bookmarksAddToFolderRemoveMutation', '222')], self.x.writes)
        self.assertEqual(set(), self.x.inside['222'])

    async def test_a_post_that_moved_since_the_check_is_not_touched(self):
        with self.assertRaises(VpubError) as caught:
            await self.member('remove', '111', [POST])
        self.assertEqual('collection_changed', caught.exception.code)
        self.assertEqual([], self.x.writes)

    async def test_a_refusal_from_x_is_not_called_a_success(self):
        self.x.answer = 'Failed'
        with self.assertRaises(VpubError) as caught:
            await self.member('add')
        self.assertEqual('platform_rejected', caught.exception.code)

    async def test_two_folders_with_one_name_stop_it_before_any_click(self):
        self.x.folders['222'] = 'Recipes'
        with self.assertRaises(VpubError) as caught:
            await self.member('add')
        self.assertEqual('layout_changed', caught.exception.code)
        self.assertEqual([], self.x.writes)

    async def test_a_folder_name_is_matched_exactly_and_never_as_a_pattern(self):
        self.x.folders['222'] = 'Recipes old'
        self.x.folders['111'] = 'Recipes'
        await self.member('add')
        self.assertEqual([('bookmarksAddToFolderPutMutation', '111')], self.x.writes)

    async def test_a_folder_that_does_not_exist_is_never_tried(self):
        with self.assertRaises(VpubError) as caught:
            await self.member('add', '999')
        self.assertEqual('collection_not_visible', caught.exception.code)
        self.assertEqual([], self.x.writes)

    async def test_without_a_folder_list_the_account_is_told_why(self):
        self.x.premium = False
        with patch('video_publisher.collections_browser.LIST_WAIT', 0.5), self.assertRaises(VpubError) as caught:
            await self.run_op('list')
        self.assertEqual('collection_list_unavailable', caught.exception.code)
        self.assertIn('Premium', caught.exception.hint)

    async def test_the_folder_window_sending_an_account_to_premium_changes_nothing(self):
        self.x.premium = False
        with patch('video_publisher.collections_browser.read_list', return_value={'items': [{'id': '111', 'title': 'Recipes'}]}):
            with self.assertRaises(VpubError) as caught:
                await self.member('add')
        self.assertEqual('premium_required', caught.exception.code)
        self.assertEqual([], self.x.writes)

    async def test_renaming_types_the_name_and_saves_once(self):
        key = self.receipt('rename', title='Cooking')
        result = await self.run_op('rename', collection_id='111', title='Cooking', receipt_key=key)
        self.assertTrue(result['submitted'])
        self.assertEqual([('bookmarksFolderModalsEditMutation', 'Cooking')], self.x.writes)

    async def test_deleting_goes_through_the_confirmation(self):
        key = self.receipt('delete')
        result = await self.run_op('delete', collection_id='111', receipt_key=key)
        self.assertTrue(result['submitted'])
        self.assertEqual([('bookmarksFolderModalsDeleteMutation', None)], self.x.writes)

    async def test_creating_reads_the_new_id_from_the_answer(self):
        key = self.receipt('create', None, title='Cooking', before_ids=['111', '222'])
        result = await self.run_op('create', collection_id=None, title='Cooking', receipt_key=key)
        self.assertEqual('333', result['collection_id'])
        self.assertEqual('333', read_json(c.receipt_path(key))['collection_id'])
        self.assertEqual([('bookmarksCreateFolderModalMutation', 'Cooking')], self.x.writes)


EARLIER_HISTORY = '''<a data-testid="AppTabBar_Profile_Link" href="/owner">Profile</a>
<button role="tab" id="tab">Bookmarks</button><script>
document.getElementById('tab').onclick = () => {
  fetch('/i/api/graphql/abc/BookmarkFoldersSlice', {method: 'POST', body: '{}'});
  const make = document.createElement('button');
  make.setAttribute('aria-label', 'Create Folder');
  make.onclick = () => {
    const form = document.createElement('div');
    form.setAttribute('role', 'dialog');
    form.innerHTML = '<input aria-label="Folder Name" id="name"><button id="go">Create</button>';
    document.body.append(form);
    document.getElementById('go').onclick = () => { fetch('/i/api/graphql/abc/createBookmarkFolder?name=' + encodeURIComponent(document.getElementById('name').value), {method: 'POST', body: '{}'}); form.remove(); };
  };
  document.body.append(make);
};
</script>'''
EARLIER_FOLDER = '''<a data-testid="AppTabBar_Profile_Link" href="/owner">Profile</a>
<button role="tab" id="tab">%s</button><script>
document.getElementById('tab').onclick = () => {
  const menu = document.createElement('div');
  menu.setAttribute('role', 'menu');
  menu.innerHTML = '<button aria-label="More" id="more">More</button>';
  document.body.append(menu);
  document.getElementById('more').onclick = () => {
    const items = document.createElement('div');
    items.innerHTML = '<div role="menuitem" id="edit">Edit</div><div role="menuitem" id="del">Delete</div>';
    document.body.append(items);
    document.getElementById('edit').onclick = () => {
      const form = document.createElement('div');
      form.innerHTML = '<input name="Folder Name" id="name"><button id="save">Save</button>';
      document.body.append(form);
      document.getElementById('save').onclick = () => { fetch('/i/api/graphql/abc/EditBookmarkFolder?name=' + encodeURIComponent(document.getElementById('name').value), {method: 'POST', body: '{}'}); form.remove(); };
    };
    document.getElementById('del').onclick = () => {
      const sure = document.createElement('div');
      sure.innerHTML = '<p>Delete Folder?</p><button data-testid="confirmationSheetConfirm" id="yes">Delete</button>';
      document.body.append(sure);
      document.getElementById('yes').onclick = () => { fetch('/i/api/graphql/abc/DeleteBookmarkFolder', {method: 'POST', body: '{}'}); sure.remove(); };
    };
  };
};
</script>'''


class EarlierSiteTests(unittest.IsolatedAsyncioTestCase):
    """The site a signed-in account still got on 2026-10-31. Its flows are the ones earlier live tests used."""

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'VPUB_HOME': str(Path(temp.name) / 'runtime')})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.folders = {'111': 'Recipes'}
        self.writes = []
        await self.page.route('**/*', self.handle)
        await self.page.goto('https://x.com/home')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != 'x.com':
            return await route.abort()
        name = url.path.rsplit('/', 1)[1]
        if route.request.method == 'POST':
            query = parse_qs(url.query)
            data = {}
            if name == 'BookmarkFoldersSlice':
                items = [{'id': key, 'name': title} for key, title in self.folders.items()]
                data = {'viewer': {'user_results': {'result': {'bookmark_collections_slice': {'items': items, 'slice_info': {}}}}}}
            else:
                self.writes.append((name, query.get('name', [None])[0]))
                if name == 'createBookmarkFolder':
                    data = {'bookmark_collection_create': {'id': '333', 'name': query['name'][0]}}
            return await route.fulfill(status=200, content_type='application/json', body=json.dumps({'data': data}))
        if url.path == '/i/history':
            return await route.fulfill(status=200, content_type='text/html', body='<!doctype html><body>' + EARLIER_HISTORY)
        if url.path.startswith('/i/history/bookmarks/'):
            return await route.fulfill(status=200, content_type='text/html', body='<!doctype html><body>' + EARLIER_FOLDER % self.folders.get(name, ''))
        await route.fulfill(status=200, content_type='text/html', body='<!doctype html><body><a data-testid="AppTabBar_Profile_Link" href="/owner">Profile</a>')

    def receipt(self, action, collection='111', title=None, **extra):
        key = digest([action, collection, title])
        atomic_json(c.receipt_path(key), {'key': key, 'platform': 'x', 'identity': OWNER, 'action': action, 'collection_id': collection,
                                          'title': title, 'state': 'READY', 'intent': {}, **extra})
        return key

    async def run_op(self, operation, **payload):
        return await execute(self.page, 'x', OWNER, {'platform': 'x', 'operation': operation, **payload})

    async def test_the_site_is_told_apart_by_the_older_label(self):
        self.assertFalse(await x_pages.rebuilt(self.page))
        await self.page.set_content('<nav><a href="/owner">Profile</a></nav>')
        self.assertTrue(await x_pages.rebuilt(self.page))

    async def test_the_list_opens_the_bookmarks_tab_and_reads_its_answer(self):
        result = await self.run_op('list')
        self.assertEqual(([{'id': '111', 'title': 'Recipes'}], True), (result['items'], result['complete']))
        self.assertEqual('earlier', result['site'])
        self.assertEqual([], self.writes)

    async def test_create_uses_the_create_folder_button(self):
        key = self.receipt('create', None, 'Cooking', before_ids=['111'])
        result = await self.run_op('create', collection_id=None, title='Cooking', receipt_key=key)
        self.assertEqual('333', result['collection_id'])
        self.assertEqual([('createBookmarkFolder', 'Cooking')], self.writes)

    async def test_rename_goes_through_the_folder_menu(self):
        key = self.receipt('rename', title='Cooking')
        await self.run_op('rename', collection_id='111', title='Cooking', receipt_key=key)
        self.assertEqual([('EditBookmarkFolder', 'Cooking')], self.writes)

    async def test_delete_goes_through_the_confirmation_sheet(self):
        key = self.receipt('delete')
        await self.run_op('delete', collection_id='111', receipt_key=key)
        self.assertEqual([('DeleteBookmarkFolder', None)], self.writes)

    async def test_adding_a_post_says_it_is_not_available_here_and_changes_nothing(self):
        key = self.receipt('add', work_id=POST, before_members=[])
        with self.assertRaises(VpubError) as caught:
            await self.run_op('add', collection_id='111', work_id=POST, before_members=[], receipt_key=key)
        self.assertEqual('unverified_feature', caught.exception.code)
        self.assertEqual('READY', read_json(c.receipt_path(key))['state'])
        with self.assertRaises(VpubError):
            await self.run_op('items', collection_id='111', work_id=POST)
        self.assertEqual([], self.writes)

    async def test_an_unhydrated_tab_retries_only_opening_the_menu(self):
        await self.page.route('**/*', lambda route: route.abort())
        await self.page.set_content('''<button role="tab" id="tab">Bookmarks</button><script>
        document.getElementById('tab').onclick=()=>{
            window.t=(window.t||0)+1;
            document.body.dataset.clicks=String(window.t);
            if(window.t===2){const b=document.createElement('button');b.setAttribute('aria-label','Create Folder');
                b.textContent='Create';b.onclick=()=>{document.body.dataset.writes='1'};document.body.append(b);}
        };</script>''')
        with patch.object(self.page, 'goto', new=AsyncMock()):
            await root(self.page, 'x', OWNER)
        self.assertEqual('2', await self.page.locator('body').get_attribute('data-clicks'))
        self.assertIsNone(await self.page.locator('body').get_attribute('data-writes'))

    async def test_an_open_menu_is_not_toggled_closed(self):
        await self.page.route('**/*', lambda route: route.abort())
        await self.page.set_content('''<button role="tab" onclick="document.body.dataset.clicks='1'">Bookmarks</button><button aria-label="Create Folder" onclick="document.body.dataset.writes='1'">Create</button>''')
        with patch.object(self.page, 'goto', new=AsyncMock()):
            await root(self.page, 'x', OWNER)
        self.assertIsNone(await self.page.locator('body').get_attribute('data-clicks'))
        self.assertIsNone(await self.page.locator('body').get_attribute('data-writes'))


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        self.page = await self.browser.new_page()
        await self.page.route('**/*', lambda route: route.abort())

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_the_account_is_read_from_the_main_menu_of_the_rebuilt_site(self):
        await self.page.set_content('<nav aria-label="Primary"><a href="/home">Home</a><a href="/someone">Profile</a></nav>')
        self.assertEqual('someone', await rt.identity(self.page, 'x'))

    async def test_the_older_label_still_works(self):
        await self.page.set_content('<a data-testid="AppTabBar_Profile_Link" href="/someone"><span>Profile</span></a>')
        self.assertEqual('someone', await rt.identity(self.page, 'x'))

    async def test_a_profile_link_outside_the_main_menu_proves_nothing(self):
        await self.page.set_content('<main><a href="/stranger">Profile</a></main>')
        self.assertIsNone(await rt.identity(self.page, 'x'))
