"""Free YouTube playlists, saves and comment actions, driven in a real browser against a small copy of YouTube's pages.

The copy follows what Studio and the watch page did on a real channel, including how Studio delays a change while it runs
its own checks. Nothing here contacts YouTube.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from video_publisher import adapters, cli, collections as c, comment_actions, engagement, safety, youtube_playlists as free, youtube_studio as yt
from video_publisher.core import VpubError, atomic_json, digest, home, read_json

CHANNEL = "UC" + "a" * 22
VIDEO = "dQw4w9WgXcQ"
OTHER = "aBcDeFgHiJk"
FIRST = "PLaaaaaaaaaaaaaaaaaaaa"
SECOND = "PLbbbbbbbbbbbbbbbbbbbb"
WORDS = {"public": "公开", "private": "私享", "unlisted": "不公开列出"}
STYLE = "<style>ytcp-playlist-row,ytcp-video-row,ytcp-comment,ytcp-comment-thread,ytcp-icon-button,tp-yt-paper-item,ytcp-menu-service-item-renderer,ytcp-dialog{display:block;min-height:10px;min-width:10px}ytcp-button{display:inline-block}</style>"


def menu_script():
    return """
for (const box of document.querySelectorAll('[data-opens]')) box.onclick = () => { const menu = document.getElementById(box.dataset.opens); menu.hidden = !menu.hidden; };
"""


class FakeYouTube:
    def __init__(self):
        self.playlists = {FIRST: {"title": "Recipes", "description": "", "visibility": "public", "videos": [VIDEO]},
                          SECOND: {"title": "Music", "description": "", "visibility": "private", "videos": []}}
        self.saved = {FIRST: {VIDEO}, SECOND: set()}
        self.watch_later = set()
        self.liked = False
        self.comments = [{"id": "c1", "text": "Nice", "own": False, "liked": False}, {"id": "c2", "text": "Mine", "own": True, "liked": False}]
        self.writes = []
        self.confirm = True
        self.keep = True
        self.dialog_title = None

    async def html(self, route, body):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><title>t</title>" + STYLE + "<body>" + body)

    async def handle(self, route):
        url = urlsplit(route.request.url)
        path = url.path
        if route.request.method == "POST":
            write = parse_qs(url.query).get("write")
            if write:
                return await self.write(route, write[0].split("/"), json.loads(route.request.post_data or "{}"))
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        if url.hostname == "www.youtube.com" and path == "/watch":
            return await self.html(route, self.watch_page())
        if url.hostname != "studio.youtube.com":
            return await route.abort()
        if path.endswith("/content/playlists"):
            return await self.html(route, self.list_page())
        if path.endswith("/videos") and path.startswith("/playlist/"):
            return await self.html(route, self.videos_page(path.split("/")[2]))
        if path.endswith("/edit") and path.startswith("/playlist/"):
            return await self.html(route, self.edit_page(path.split("/")[2]))
        if path.endswith("/comments"):
            return await self.html(route, self.comments_page())
        await self.html(route, "<p>Studio</p>")

    async def write(self, route, parts, data):
        kind = parts[0]
        self.writes.append((kind, *parts[1:], *data.values()))
        if kind == "save":
            (self.saved[parts[1]].discard if parts[2] == VIDEO and parts[2] in self.saved[parts[1]] else self.saved[parts[1]].add)(parts[2])
        elif kind == "like":
            self.liked = not self.liked
        elif kind == "create":
            self.playlists["PLnewnewnewnewnewnewnew"] = {"title": data["title"], "description": data.get("description", ""), "visibility": data["visibility"], "videos": []}
            self.saved["PLnewnewnewnewnewnewnew"] = set()
        elif kind == "update" and self.keep:
            self.playlists[parts[1]].update(data)
        elif kind == "delete":
            del self.playlists[parts[1]]
        elif kind == "comment-like":
            next(row for row in self.comments if row["id"] == parts[1])["liked"] ^= True
        elif kind == "comment-edit":
            next(row for row in self.comments if row["id"] == parts[1])["text"] = data["text"]
        elif kind == "comment-delete":
            self.comments = [row for row in self.comments if row["id"] != parts[1]]
        await route.fulfill(status=200, content_type="application/json", body="{}")

    def list_page(self):
        rows = "".join(f"""<ytcp-playlist-row><a id="playlist-title-link" href="/playlist/{key}/edit">{row['title']}</a><span class="visibility-label"> {WORDS[row['visibility']]}</span>
            <span class="videos-count-cell">{len(row['videos'])}</span><span class="last-time-updated-cell">2026</span>
            <ytcp-icon-button id="overflow-menu-button" data-opens="menu-{key}"></ytcp-icon-button>
            <div id="menu-{key}" hidden><tp-yt-paper-item test-id="EDIT_ON_YOUTUBE">Edit</tp-yt-paper-item><tp-yt-paper-item test-id="DELETE_FOREVER" onclick="{self.delete_call(key)}">Delete</tp-yt-paper-item></div></ytcp-playlist-row>""" for key, row in self.playlists.items())
        # The window's outer element has no height on the real page, and a delete is answered once before it is sent for real.
        return f"""{rows}<ytcp-icon-button id="navigate-after" aria-disabled="true"></ytcp-icon-button>
            <ytcp-confirmation-dialog id="playlist-delete-dialog" style="display:flex;height:0"><div id="ask" hidden><p id="ask-text"></p><ytcp-button id="cancel-button"><button>Cancel</button></ytcp-button><ytcp-button id="confirm-button"><button onclick="sure()">Delete</button></ytcp-button></div></ytcp-confirmation-dialog>
            <button aria-label="Create" data-opens="create-menu">Create</button>
            <div id="create-menu" hidden><tp-yt-paper-item test-id="upload">Upload</tp-yt-paper-item><tp-yt-paper-item test-id="new-playlist" onclick="document.getElementById('make').hidden = false">New</tp-yt-paper-item></div>
            <ytcp-playlist-creation-dialog id="make" hidden><div id="textbox" contenteditable="true" class="title"></div><div id="textbox" contenteditable="true" class="notes"></div>
              <ytcp-dropdown-trigger data-opens="make-menu"><span id="shown">{WORDS['public']}</span></ytcp-dropdown-trigger>
              <div id="make-menu" hidden>{"".join(f'<tp-yt-paper-item test-id="{code}" onclick="choose(this, {chr(39)}{name}{chr(39)})">{WORDS[name]}</tp-yt-paper-item>' for name, code in yt.PRIVACY.items())}</div>
              <ytcp-button id="create-button"><button onclick="make()">Create</button></ytcp-button></ytcp-playlist-creation-dialog>
            <script>{menu_script()}
            let chosen = 'public';
            function choose(item, name) {{ chosen = name; document.getElementById('shown').textContent = item.textContent; item.parentElement.hidden = true; }}
            function make() {{ fetch('/youtubei/v1/playlist/create?write=create', {{method: 'POST', body: JSON.stringify({{title: document.querySelector('.title').innerText, description: document.querySelector('.notes').innerText, visibility: chosen}})}}).then(() => {{ document.getElementById('make').hidden = true; document.getElementById('make').style.display = 'none'; }}); }}
            let target = '';
            function ask(key, title) {{ target = key; document.getElementById('ask-text').textContent = 'Delete ' + title + '?'; document.getElementById('ask').hidden = false; }}
            function sure() {{ fetch('/youtubei/v1/playlist/delete', {{method: 'POST', body: '{{}}'}}).then(() => setTimeout(() => fetch('/youtubei/v1/playlist/delete?write=delete/' + target, {{method: 'POST', body: '{{}}'}}).then(() => {{ document.getElementById('ask').hidden = true; }}), 1500)); }}
            </script>"""

    def delete_call(self, key):
        if not self.confirm:
            return ""
        title = repr(self.dialog_title) if self.dialog_title else "this.closest('ytcp-playlist-row').querySelector('#playlist-title-link').textContent"
        return f"ask('{key}', {title})"

    def videos_page(self, key):
        rows = "".join(f'<ytcp-video-row><a id="video-title" href="/video/{video}/edit">Title {video}</a></ytcp-video-row>' for video in self.playlists[key]["videos"])
        return f'{rows}<ytcp-icon-button id="navigate-after" aria-disabled="true"></ytcp-icon-button>'

    def edit_page(self, key):
        row = self.playlists[key]
        items = "".join(f'<tp-yt-paper-item test-id="{code}" onclick="choose(this, {chr(39)}{name}{chr(39)})">{WORDS[name]}</tp-yt-paper-item>' for name, code in yt.PRIVACY.items())
        return f"""<div id="title-textarea"><div id="textbox" contenteditable="true" oninput="ready()">{row['title']}</div></div>
            <div id="description-textarea"><div id="textbox" contenteditable="true" oninput="ready()">{row['description']}</div></div>
            <div><ytcp-text-dropdown-trigger id="menu-button" data-opens="visibility-menu"><span id="shown">{WORDS[row['visibility']]}</span></ytcp-text-dropdown-trigger><ytcp-text-menu id="visibility-menu" hidden>{items}</ytcp-text-menu></div>
            <ytcp-button id="save" aria-disabled="true"><button onclick="save()">Save</button></ytcp-button>
            <script>{menu_script()}
            let chosen = '{row['visibility']}';
            function ready() {{ document.getElementById('save').removeAttribute('aria-disabled'); }}
            function choose(item, name) {{ chosen = name; document.getElementById('shown').textContent = item.textContent; item.parentElement.hidden = true; ready(); }}
            // Save greys out at once and the change goes out later, as it does in Studio.
            function save() {{ const body = JSON.stringify({{title: document.querySelector('#title-textarea #textbox').innerText, description: document.querySelector('#description-textarea #textbox').innerText, visibility: chosen}});
              document.getElementById('save').setAttribute('aria-disabled', 'true'); setTimeout(() => fetch('/youtubei/v1/browse/edit_playlist?write=update/{key}', {{method: 'POST', body}}), 1500); }}
            </script>"""

    def watch_page(self):
        rows = '<toggleable-list-item-view-model><button aria-pressed="false">Watch later<br>Private</button></toggleable-list-item-view-model>' + "".join(
            f"""<toggleable-list-item-view-model><button aria-pressed="{'true' if VIDEO in self.saved[key] else 'false'}" onclick="flip(this, '{key}')">{row['title']}<br>{WORDS[row['visibility']]}</button></toggleable-list-item-view-model>""" for key, row in self.playlists.items())
        return f"""<yt-player-quick-action-buttons><like-button-view-model style="display:block;width:0;height:0;overflow:hidden"><button aria-pressed="false" style="width:0;height:0;padding:0;border:0"></button></like-button-view-model></yt-player-quick-action-buttons>
            <ytd-watch-metadata><ytd-menu-renderer>
              <button aria-pressed="{'true' if self.liked else 'false'}" aria-label="like this video" onclick="fetch('/youtubei/v1/like/like?write=like', {{method: 'POST', body: '{{}}'}}); this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">Like</button>
              <button aria-label="Share">Share</button>
              <button aria-label="Save to playlist" onclick="document.getElementById('sheet').hidden = false">Save</button>
            </ytd-menu-renderer></ytd-watch-metadata>
            <yt-sheet-view-model id="sheet" hidden>{rows}</yt-sheet-view-model>
            <script>function flip(button, key) {{ fetch('/youtubei/v1/browse/edit_playlist?write=save/' + key + '/{VIDEO}', {{method: 'POST', body: '{{}}'}}).then(() => button.setAttribute('aria-pressed', button.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')); }}</script>"""

    def comments_page(self):
        rows = ""
        for row in self.comments:
            handle = "mychannel" if row["own"] else "viewer"
            items = (f"""<ytcp-menu-service-item-renderer>置顶</ytcp-menu-service-item-renderer><ytcp-menu-service-item-renderer onclick="editing('{row['id']}')">修改</ytcp-menu-service-item-renderer>
                        <ytcp-menu-service-item-renderer onclick="removing('{row['id']}')">删除</ytcp-menu-service-item-renderer>""" if row["own"]
                     else "<ytcp-menu-service-item-renderer>移除</ytcp-menu-service-item-renderer><ytcp-menu-service-item-renderer>举报</ytcp-menu-service-item-renderer>")
            rows += f"""<ytcp-comment-thread><ytcp-comment comment-id="{row['id']}"><a id="name" href="http://www.youtube.com/@{handle}"><span class="author-text">{handle}</span></a><span class="published-time-text">now</span>
              <div id="content-text">{row['text']}</div>
              <ytcp-comment-toggle-button id="like-button"><ytcp-icon-button aria-label="赞" onclick="fetch('/youtubei/v1/comment/perform_comment_action?write=comment-like/{row['id']}', {{method: 'POST', body: '{{}}'}}); this.parentElement.isToggled = !this.parentElement.isToggled"></ytcp-icon-button></ytcp-comment-toggle-button>
              <ytcp-icon-button id="action-menu-button" data-opens="menu-{row['id']}"></ytcp-icon-button><div id="menu-{row['id']}" hidden>{items}</div></ytcp-comment></ytcp-comment-thread>"""
        states = json.dumps({row["id"]: row["liked"] for row in self.comments})
        confirm = "document.getElementById('sure').hidden = false; document.getElementById('sure').dataset.comment = id" if self.confirm else "fetch('/youtubei/v1/comment/perform_comment_action?write=comment-delete/' + id, {method: 'POST', body: '{}'})"
        return f"""{rows}<div id="box"></div>
            <ytcp-confirmation-dialog id="sure" hidden><button id="confirm-button" onclick="fetch('/youtubei/v1/comment/perform_comment_action?write=comment-delete/' + document.getElementById('sure').dataset.comment, {{method: 'POST', body: '{{}}'}}); document.getElementById('sure').hidden = true">Delete</button></ytcp-confirmation-dialog>
            <script>{menu_script()}
            const states = {states};
            for (const button of document.querySelectorAll('#like-button')) button.isToggled = states[button.closest('ytcp-comment').getAttribute('comment-id')];
            function removing(id) {{ {confirm} }}
            function editing(id) {{ document.getElementById('box').innerHTML = '<ytcp-commentbox><textarea id="textarea"></textarea><div id="submit-button"><button>Save</button></div></ytcp-commentbox>';
              document.querySelector('#submit-button button').onclick = () => {{ fetch('/youtubei/v1/comment/update_comment?write=comment-edit/' + id, {{method: 'POST', body: JSON.stringify({{text: document.getElementById('textarea').value}})}}); document.getElementById('box').innerHTML = ''; }}; }}
            </script>"""


class Browser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "accounts/default/youtube-handle.json", {"identity": CHANNEL, "handle": "mychannel"})
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(8000)
        self.site = FakeYouTube()
        await self.page.route("**/*", self.site.handle)
        for target in (patch.object(yt, "appears", side_effect=self.fast_appears), patch.object(yt, "QUIET_TICKS", 6)):
            target.start()
            self.addCleanup(target.stop)

    async def no_wait(self, ms):
        return None

    async def fast_appears(self, page, target, seconds):
        for _ in range(30):
            if await target.count():
                return True
            await page.wait_for_timeout(100)
        return False

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    def receipt(self, action, collection=None, title=None, **extra):
        key = digest([action, collection, title, str(extra)])
        atomic_json(c.receipt_path(key), {"key": key, "platform": "youtube", "identity": CHANNEL, "action": action, "collection_id": collection, "title": title, "state": "READY", "intent": {}, **extra})
        return key

    async def run_op(self, operation, **payload):
        return await yt.playlists(self.page, CHANNEL, {"platform": "youtube", "operation": operation, **payload})


class PlaylistTests(Browser):
    async def test_the_list_shows_each_playlist_with_its_id_and_visibility(self):
        result = await self.run_op("list")
        self.assertTrue(result["complete"])
        self.assertEqual([(FIRST, "Recipes", "public", 1), (SECOND, "Music", "private", 0)], [(r["id"], r["title"], r["visibility"], r["videos"]) for r in result["items"]])
        self.assertEqual([], self.site.writes)

    async def test_a_list_with_more_pages_is_not_complete_when_the_read_stops(self):
        with patch.object(yt, "TABLE_PAGES", 1), patch.object(FakeYouTube, "list_page", lambda s: '<ytcp-playlist-row><a id="playlist-title-link" href="/playlist/PLccccccccccccc/edit">A</a></ytcp-playlist-row><ytcp-icon-button id="navigate-after" aria-disabled="false"></ytcp-icon-button>'):
            self.assertFalse((await self.run_op("list"))["complete"])

    async def test_a_playlists_videos_come_from_studio(self):
        result = await self.run_op("items", collection_id=FIRST)
        self.assertEqual(([VIDEO], True), ([r["id"] for r in result["items"]], result["complete"]))

    async def test_the_save_window_says_whether_one_video_is_in_a_playlist(self):
        inside = await self.run_op("items", collection_id=FIRST, work_id=VIDEO)
        outside = await self.run_op("items", collection_id=SECOND, work_id=VIDEO)
        self.assertEqual(([{"id": VIDEO}], True), (inside["items"], inside["complete"]))
        self.assertEqual(([], True), (outside["items"], outside["complete"]))
        self.assertEqual([], self.site.writes)

    async def test_adding_clicks_only_that_playlist_and_marks_the_receipt_first(self):
        key = self.receipt("add", SECOND, work_id=VIDEO, before_members=[])
        await self.run_op("add", collection_id=SECOND, work_id=VIDEO, before_members=[], receipt_key=key)
        self.assertEqual([("save", SECOND, VIDEO)], self.site.writes)
        self.assertEqual({VIDEO}, self.site.saved[SECOND])
        self.assertEqual({VIDEO}, self.site.saved[FIRST])
        self.assertEqual("SUBMITTING", read_json(c.receipt_path(key))["state"])

    async def test_removing_takes_it_out_of_that_playlist_only(self):
        key = self.receipt("remove", FIRST, work_id=VIDEO, before_members=[VIDEO])
        await self.run_op("remove", collection_id=FIRST, work_id=VIDEO, before_members=[VIDEO], receipt_key=key)
        self.assertEqual(set(), self.site.saved[FIRST])

    async def test_a_video_that_moved_since_the_check_is_not_touched(self):
        key = self.receipt("remove", SECOND, work_id=VIDEO, before_members=[VIDEO])
        with self.assertRaises(VpubError) as caught:
            await self.run_op("remove", collection_id=SECOND, work_id=VIDEO, before_members=[VIDEO], receipt_key=key)
        self.assertEqual("collection_changed", caught.exception.code)
        self.assertEqual([], self.site.writes)

    async def test_a_playlist_id_that_is_not_this_channels_is_never_tried(self):
        with self.assertRaises(VpubError) as caught:
            await self.run_op("items", collection_id="PLzzzzzzzzzzzzzzzzzz", work_id=VIDEO)
        self.assertEqual("collection_not_visible", caught.exception.code)

    async def test_two_playlists_with_one_title_stop_it(self):
        self.site.playlists[SECOND]["title"] = "Recipes"
        with self.assertRaises(VpubError) as caught:
            await self.run_op("items", collection_id=FIRST, work_id=VIDEO)
        self.assertEqual("playlist_ambiguous", caught.exception.code)

    async def test_a_title_is_matched_whole_and_watch_later_is_ignored(self):
        self.site.playlists[SECOND]["title"] = "Recipes old"
        self.assertEqual([{"id": VIDEO}], (await self.run_op("items", collection_id=FIRST, work_id=VIDEO))["items"])
        self.site.playlists[SECOND]["title"] = "Watch"
        self.assertEqual([], (await self.run_op("items", collection_id=SECOND, work_id=VIDEO))["items"])

    async def test_creating_sets_private_and_finds_the_new_id(self):
        key = self.receipt("create", None, "Cooking", before_ids=[FIRST, SECOND])
        result = await self.run_op("create", collection_id=None, title="Cooking", description="About food", privacy="private", receipt_key=key)
        self.assertEqual("PLnewnewnewnewnewnewnew", result["collection_id"])
        self.assertEqual(("private", "About food"), (self.site.playlists[result["collection_id"]]["visibility"], self.site.playlists[result["collection_id"]]["description"]))

    async def test_renaming_waits_for_the_change_studio_sends_later(self):
        key = self.receipt("rename", FIRST, "Later")
        await self.run_op("rename", collection_id=FIRST, title="Later", receipt_key=key)
        self.assertEqual("Later", self.site.playlists[FIRST]["title"])

    async def test_the_wait_ends_at_its_limit_on_a_page_that_never_goes_quiet(self):
        await self.page.goto("https://studio.youtube.com/channel/x")
        await self.page.evaluate("setInterval(() => fetch('/youtubei/v1/log_event', {method: 'POST', body: '{}'}), 200)")
        clicks = []

        async def act():
            clicks.append(1)

        with patch.object(yt, "MOST_TICKS", 6):
            await yt.act_and_settle(self.page, act)
        self.assertEqual([1], clicks)

    async def test_the_wait_ends_soon_on_a_quiet_page(self):
        await self.page.goto("https://studio.youtube.com/channel/x")
        started = asyncio.get_running_loop().time()

        async def act():
            return None

        await yt.act_and_settle(self.page, act)
        self.assertLess(asyncio.get_running_loop().time() - started, 12)

    async def test_renaming_saves_once_and_reads_it_back(self):
        key = self.receipt("rename", FIRST, "Cooking")
        result = await self.run_op("rename", collection_id=FIRST, title="Cooking", description="New words", privacy="unlisted", receipt_key=key)
        self.assertTrue(result["submitted"])
        self.assertEqual(1, len(self.site.writes))
        self.assertEqual(("Cooking", "New words", "unlisted"), tuple(self.site.playlists[FIRST][name] for name in ("title", "description", "visibility")))

    async def test_a_rename_that_did_not_keep_is_not_called_done(self):
        self.site.keep = False
        key = self.receipt("rename", FIRST, "Cooking")
        with self.assertRaises(VpubError) as caught:
            await self.run_op("rename", collection_id=FIRST, title="Cooking", receipt_key=key)
        self.assertEqual("playlist_unverified", caught.exception.code)
        self.assertEqual("SUBMITTING", read_json(c.receipt_path(key))["state"])

    async def test_nothing_to_save_stops_before_any_write(self):
        key = self.receipt("rename", FIRST, "Recipes")
        with self.assertRaises(VpubError) as caught, patch.object(yt, "wait_ready", return_value=False):
            await self.run_op("rename", collection_id=FIRST, title="Recipes", receipt_key=key)
        self.assertEqual("edit_not_ready", caught.exception.code)
        self.assertEqual([], self.site.writes)
        self.assertEqual("READY", read_json(c.receipt_path(key))["state"])

    async def test_deleting_stays_until_studio_has_really_sent_it(self):
        key = self.receipt("delete", FIRST)
        await self.run_op("delete", collection_id=FIRST, receipt_key=key)
        self.assertEqual([("delete", FIRST)], self.site.writes)
        self.assertEqual("SUBMITTING", read_json(c.receipt_path(key))["state"])
        self.assertEqual({SECOND}, set(self.site.playlists))

    async def test_no_confirmation_window_means_nothing_is_deleted_or_marked(self):
        self.site.confirm = False
        key = self.receipt("delete", FIRST)
        with self.assertRaises(VpubError) as caught, patch.object(self.page, "wait_for_timeout", side_effect=self.no_wait):
            await self.run_op("delete", collection_id=FIRST, receipt_key=key)
        self.assertEqual("layout_changed", caught.exception.code)
        self.assertEqual([], self.site.writes)
        self.assertEqual("READY", read_json(c.receipt_path(key))["state"])

    async def test_a_confirmation_naming_another_playlist_stops_the_delete(self):
        self.site.dialog_title = "Music"
        key = self.receipt("delete", FIRST)
        with self.assertRaises(VpubError) as caught:
            await self.run_op("delete", collection_id=FIRST, receipt_key=key)
        self.assertEqual("wrong_collection", caught.exception.code)
        self.assertEqual([], self.site.writes)
        self.assertEqual("READY", read_json(c.receipt_path(key))["state"])

    async def test_a_playlist_not_in_the_list_is_not_deleted(self):
        key = self.receipt("delete", "PLzzzzzzzzzzzzzzzzzz")
        with self.assertRaises(VpubError) as caught:
            await self.run_op("delete", collection_id="PLzzzzzzzzzzzzzzzzzz", receipt_key=key)
        self.assertEqual("collection_not_visible", caught.exception.code)
        self.assertEqual([], self.site.writes)


class WatchPageTests(Browser):
    async def test_the_visible_like_button_is_read_not_the_players_hidden_copy(self):
        self.site.liked = True
        result = await yt.engagement(self.page, CHANNEL, {"operation": "status", "work_id": VIDEO})
        self.assertIs(True, result["liked"])

    async def test_a_like_clicks_the_visible_button_once(self):
        path = home() / "receipt.json"
        atomic_json(path, {"state": "ACTION_READY"})
        result = await yt.engagement(self.page, CHANNEL, {"operation": "set", "work_id": VIDEO, "kind": "like", "desired": True, "receipt_path": str(path)})
        self.assertEqual(1, result["platform_writes"])
        self.assertEqual([("like",)], self.site.writes)
        self.assertEqual("ACTION_SUBMITTING", read_json(path)["state"])

    async def test_a_save_reads_and_changes_one_playlist(self):
        status = await yt.engagement(self.page, CHANNEL, {"operation": "status", "work_id": VIDEO, "collection": SECOND})
        self.assertEqual((False, SECOND), (status["saved"], status["collection"]))
        path = home() / "receipt.json"
        atomic_json(path, {"state": "ACTION_READY"})
        await yt.engagement(self.page, CHANNEL, {"operation": "set", "work_id": VIDEO, "kind": "save", "desired": True, "collection": SECOND, "receipt_path": str(path)})
        self.assertEqual([("save", SECOND, VIDEO)], self.site.writes)

    async def test_a_save_already_there_sends_nothing(self):
        result = await yt.engagement(self.page, CHANNEL, {"operation": "set", "work_id": VIDEO, "kind": "save", "desired": True, "collection": FIRST, "receipt_path": "unused"})
        self.assertEqual(("NO_CHANGE", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.site.writes)


class CommentTests(Browser):
    def payload(self, kind, comment="c1", desired=True, operation="set", text="Nice", receipt=None):
        return {"operation": operation, "receipt_path": receipt, "request": {"identity": CHANNEL, "work_id": VIDEO, "kind": kind, "desired": desired, "comment": {"id": comment, "text": text}}}

    def receipt_file(self):
        path = home() / "comment.json"
        atomic_json(path, {"state": "COMMENT_READY"})
        return str(path)

    async def test_status_reads_the_like_and_text(self):
        self.site.comments[0]["liked"] = True
        result = await yt.comment_action(self.page, CHANNEL, self.payload("like", operation="status"))
        self.assertEqual((True, "Nice", False), (result["liked"], result["text"], result["deleted"]))

    async def test_a_like_marks_the_receipt_then_clicks_once(self):
        path = self.receipt_file()
        result = await yt.comment_action(self.page, CHANNEL, self.payload("like", receipt=path))
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("comment-like", "c1")], self.site.writes)

    async def test_a_comment_already_liked_sends_nothing(self):
        self.site.comments[0]["liked"] = True
        result = await yt.comment_action(self.page, CHANNEL, self.payload("like", receipt=self.receipt_file()))
        self.assertEqual("NO_CHANGE", result["state"])
        self.assertEqual([], self.site.writes)

    async def test_unliking_works_the_same_way(self):
        self.site.comments[0]["liked"] = True
        await yt.comment_action(self.page, CHANNEL, self.payload("like", desired=False, receipt=self.receipt_file()))
        self.assertEqual([("comment-like", "c1")], self.site.writes)

    async def test_editing_the_channels_own_comment_types_and_saves_once(self):
        await yt.comment_action(self.page, CHANNEL, self.payload("edit", "c2", "New words", text="Mine", receipt=self.receipt_file()))
        self.assertEqual([("comment-edit", "c2", "New words")], self.site.writes)

    async def test_deleting_goes_through_studios_confirmation(self):
        await yt.comment_action(self.page, CHANNEL, self.payload("delete", "c2", text="Mine", receipt=self.receipt_file()))
        self.assertEqual([("comment-delete", "c2")], self.site.writes)

    async def test_a_delete_with_no_confirmation_was_already_marked_as_submitting(self):
        self.site.confirm = False
        path = self.receipt_file()
        with patch.object(yt, "CONFIRM_BUTTON", "#never"):
            result = await yt.comment_action(self.page, CHANNEL, self.payload("delete", "c2", text="Mine", receipt=path))
        self.assertEqual([("comment-delete", "c2")], self.site.writes)
        self.assertEqual("COMMENT_UNVERIFIED", result["state"])

    async def test_a_viewers_comment_has_no_edit_or_delete_to_click(self):
        path = self.receipt_file()
        with self.assertRaises(VpubError):
            await yt.comment_action(self.page, CHANNEL, self.payload("edit", "c1", "New", receipt=path))
        self.assertEqual([], self.site.writes)

    async def test_a_comment_that_is_gone_reads_as_deleted(self):
        result = await yt.comment_action(self.page, CHANNEL, self.payload("delete", "gone", operation="status"))
        self.assertIs(True, result["deleted"])

    async def test_another_account_stops_before_anything(self):
        with self.assertRaises(VpubError) as caught:
            await yt.comment_action(self.page, "UC" + "b" * 22, self.payload("like", receipt=self.receipt_file()))
        self.assertEqual("wrong_account", caught.exception.code)

    async def test_moderation_says_it_needs_the_add_on(self):
        with self.assertRaises(VpubError) as caught:
            await yt.comment_action(self.page, CHANNEL, self.payload("moderate", desired="published", receipt=self.receipt_file()))
        self.assertEqual("paid_feature_required", caught.exception.code)


class CommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "accounts/default/youtube-identity.json", {"identity": CHANNEL})
        self.rows = [{"id": FIRST, "title": "Recipes"}]
        self.members = []
        self.calls = []
        for target in (patch.object(c, "call", side_effect=self.fake), patch.object(adapters, "addon_installed", return_value=False)):
            target.start()
            self.addCleanup(target.stop)

    def fake(self, platform, operation, owner, **options):
        self.calls.append((operation, options))
        if operation == "list":
            return {"identity": owner, "items": [dict(row) for row in self.rows], "complete": True}
        if operation == "items":
            return {"identity": owner, "collection_id": options["collection_id"], "items": [{"id": video} for video in self.members], "complete": True}
        c.before_submit(dict(platform=platform, operation=operation, **options), owner)
        if operation == "create":
            self.rows.append({"id": SECOND, "title": options["title"]})
            return {"collection_id": SECOND}
        if operation == "rename":
            self.rows[0]["title"] = options["title"]
        if operation == "delete":
            self.rows.clear()
        if operation in {"add", "remove"}:
            self.members = [options["work_id"]] if operation == "add" else []
        return {}

    def run_cli(self, *argv):
        return cli.run_command(cli.parser().parse_args(["playlist", *argv]))

    def test_list_and_items_need_no_add_on(self):
        self.assertEqual(FIRST, self.run_cli("list")["items"][0]["id"])
        self.assertEqual([], self.run_cli("items", FIRST)["items"])

    def test_create_is_private_unless_asked(self):
        result = self.run_cli("create", "--title", "Cooking", "--request-id", "c1")
        self.assertEqual(("CONFIRMED", SECOND), (result["state"], result["collection_id"]))
        self.assertEqual("private", next(options for operation, options in self.calls if operation == "create")["privacy"])

    def test_update_with_only_new_words_keeps_the_title(self):
        result = self.run_cli("update", FIRST, "--description", "New words", "--request-id", "u1")
        self.assertEqual("CONFIRMED", result["state"])
        options = next(options for operation, options in self.calls if operation == "rename")
        self.assertEqual(("Recipes", "New words"), (options["title"], options["description"]))

    def test_update_with_the_same_title_and_nothing_else_writes_nothing(self):
        result = self.run_cli("update", FIRST, "--title", "Recipes", "--request-id", "u1")
        self.assertEqual(0, result["platform_writes"])
        self.assertNotIn("rename", [operation for operation, _ in self.calls])

    def test_update_with_nothing_asked_is_refused(self):
        with self.assertRaises(VpubError) as caught:
            self.run_cli("update", FIRST, "--request-id", "u1")
        self.assertEqual("invalid_options", caught.exception.code)

    def test_add_and_remove_are_exact_and_read_back(self):
        added = self.run_cli("add", FIRST, VIDEO, "--request-id", "a1")
        self.assertEqual("CONFIRMED", added["state"])
        self.assertEqual({"collection_id": FIRST, "work_id": VIDEO}, self.calls[0][1])
        removed = self.run_cli("remove", FIRST, VIDEO, "--request-id", "r1")
        self.assertEqual("CONFIRMED", removed["state"])

    def test_the_same_request_never_runs_twice(self):
        self.run_cli("add", FIRST, VIDEO, "--request-id", "a1")
        self.assertTrue(self.run_cli("add", FIRST, VIDEO, "--request-id", "a1")["reused"])
        self.assertEqual(1, sum(operation == "add" for operation, _ in self.calls))

    def test_reordering_stays_paid(self):
        with self.assertRaises(VpubError) as caught:
            self.run_cli("move", FIRST, "item", "--position", "1", "--request-id", "m1")
        self.assertEqual("paid_feature_required", caught.exception.code)

    def test_a_short_special_list_is_refused(self):
        for bad in ("WL", "LL", "../x", "PL space here"):
            with self.subTest(bad=bad), self.assertRaises(VpubError):
                self.run_cli("items", bad)

    def test_a_page_cursor_is_refused_rather_than_ignored(self):
        with self.assertRaises(VpubError) as caught:
            self.run_cli("list", "--cursor", "abc")
        self.assertEqual("cursor_unsupported", caught.exception.code)

    def test_the_add_on_keeps_its_api_playlists(self):
        with patch.object(adapters, "addon_installed", return_value=True), patch.object(adapters, "youtube_api") as api:
            self.assertIs(api.return_value.playlists, adapters.youtube_playlists())
        self.assertIs(free, adapters.youtube_playlists())


class RouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        addon = patch.object(adapters, "addon_installed", return_value=False)
        addon.start()
        self.addCleanup(addon.stop)

    def test_saves_and_their_playlists_use_youtubes_own_pages(self):
        with patch.object(free, "listing", return_value={"items": []}) as listing:
            engagement.collections("youtube")
        listing.assert_called_once()
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL, "saved": False, "liked": False, "collection": FIRST}) as call:
            engagement.status("youtube", VIDEO, FIRST)
        self.assertEqual(("youtube", "engagement", "default"), call.call_args.args)
        self.assertEqual(FIRST, call.call_args.kwargs["collection"])

    def test_a_save_still_needs_a_chosen_playlist_and_counts_towards_the_cap(self):
        with self.assertRaises(VpubError) as caught:
            engagement.set_action("youtube", VIDEO, "save")
        self.assertEqual("collection_required", caught.exception.code)
        safety.set_limits(likes_per_day=0)
        with patch.object(engagement, "status", return_value={"identity": CHANNEL, "liked": False, "saved": False, "collection": FIRST}), patch.object(engagement, "dispatch") as send:
            with self.assertRaises(VpubError) as caught:
                engagement.set_action("youtube", VIDEO, "save", collection=FIRST)
        self.assertEqual("daily_limit_reached", caught.exception.code)
        send.assert_not_called()

    def test_comment_actions_use_the_browser_without_the_add_on(self):
        atomic_json(home() / "operations/comments" / (digest(["youtube", VIDEO]) + ".json"), {"identity": CHANNEL, "items": [{"id": "c1", "text": "Nice", "is_owner": False}]})
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL, "liked": False, "text": "Nice", "deleted": False}) as call:
            comment_actions.status("youtube", VIDEO, "c1")
        self.assertEqual(("youtube", "comment_management", "default"), call.call_args.args)

    def test_comment_likes_use_the_browser_even_with_the_add_on(self):
        request = {"platform": "youtube", "work_id": VIDEO, "identity": CHANNEL, "comment": {"id": "c1"}, "kind": "like"}
        with patch.object(adapters, "addon_installed", return_value=True), patch.object(adapters, "browser_call", return_value={}) as call:
            comment_actions.dispatch(request)
        call.assert_called_once()
        with patch.object(adapters, "addon_installed", return_value=True), patch.object(adapters, "youtube_api") as api:
            comment_actions.dispatch({**request, "kind": "moderate"})
        api.return_value.comment_action.assert_called_once()


if __name__ == "__main__":
    unittest.main()
