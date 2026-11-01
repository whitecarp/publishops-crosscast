"""Collection changes on Douyin and Instagram, driven in an invisible browser against small copies of their pages. X, YouTube and Bilibili have theirs elsewhere. Nothing is contacted."""
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import collections as c, collections_douyin, collections_instagram
from video_publisher.core import VpubError, atomic_json, digest, file_sha, read_json

OWNER = "owner"
FIRST = "7300000000000000001"
SECOND = "7300000000000000002"


class Pages(SharedBrowser):
    platform = None

    def prepare(self):
        self.writes = []
        self.key = digest([self.platform, OWNER, "request-1"])

    def receipt(self, action, collection, title=None, work=None, members=None, **intent):
        atomic_json(c.receipt_path(self.key), {"key": self.key, "platform": self.platform, "identity": OWNER, "action": action, "collection_id": collection, "title": title, "work_id": work,
                                               "before_members": members, "before_ids": [], "state": "READY", "platform_writes": 0, "intent": intent})

    def saved(self):
        return read_json(c.receipt_path(self.key))

    def note(self, route):
        self.writes.append((urlsplit(route.request.url).path, self.saved()["state"], json.loads(route.request.post_data or "{}")))

    async def refused(self, code, call):
        with self.assertRaises(VpubError) as caught:
            await call()
        self.assertEqual(code, caught.exception.code)
        await self.page.evaluate("1")
        self.assertEqual([], self.writes)
        return self.saved()


class DouyinTests(Pages):
    platform = "douyin"

    def prepare(self):
        super().prepare()
        self.collections = [self.collection("111", "Trips")]
        self.members = [{"aweme_id": FIRST, "desc": "First video"}, {"aweme_id": SECOND, "desc": "Second video"}]
        self.picker = [{"aweme_id": "7300000000000000003", "desc": "Third video"}]
        self.answer = {"status_code": 0}
        self.check = ""

    def collection(self, cid, title, owner=OWNER):
        return {"mix_id": cid, "mix_name": title, "author": {"unique_id": owner}}

    async def handle(self, route):
        url = urlsplit(route.request.url)
        press = "fetch('ROUTE', {method: 'POST', body: JSON.stringify({title: (document.querySelector('[placeholder*=标题]') || {}).value})})"
        if url.path == "/web/api/mix/list/":
            return await self.json_at(route, {"status_code": 0, "mix_list": self.collections, "has_more": False})
        if url.path == "/web/api/mix/detail/":
            return await self.json_at(route, {"status_code": 0, "mix_info": self.collections[0]})
        if url.path == "/web/api/mix/item/list/":
            return await self.json_at(route, {"status_code": 0, "aweme_list": self.members, "has_more": False})
        if url.path == "/aweme/v1/personal/search/item/":
            return await self.json_at(route, {"status_code": 0, "aweme_list": self.picker})
        if route.request.method == "POST":
            self.note(route)
            return await self.json_at(route, self.answer)
        if url.path.endswith("/content/manage"):
            rows = "".join(f"""<div class="new-layout-title-row-a"><span>{row['mix_name']}</span><button onclick="document.getElementById('sure').hidden = false">删除合集</button></div>""" for row in self.collections)
            return await self.page_at(route, f"""{self.check}<script>fetch('/web/api/mix/list/')</script><button>创建合集</button>{rows}
              <div role="modal" id="sure" hidden>确定要删除此合集吗 删除合集时不会删除合集里的视频<button onclick="{press.replace('ROUTE', '/web/api/mix/delete')}; this.parentElement.hidden = true">删除</button></div>""")
        cards = "".join(f"""<div class="video-card-a"><span>{row['desc']}</span><div class="video-card-operation-a"><button onclick="document.getElementById('sure').hidden = false">从合集中移除</button></div></div>""" for row in self.members)
        picker = "".join(f"""<div class="drawer-item-a"><span>{row['desc']}</span><div class="plus-area-a" onclick="this.closest('[role=sidesheet]').dataset.chosen = 'yes'">+</div></div>""" for row in self.picker)
        await self.page_at(route, f"""{self.check}<script>fetch('/web/api/mix/detail/');fetch('/web/api/mix/item/list/')</script><input placeholder="请输入合集的标题" value="Trips">{cards}
          <span id="add" onclick="fetch('/aweme/v1/personal/search/item/'); document.querySelector('[role=sidesheet]').hidden = false">添加作品</span>
          <div role="sidesheet" hidden><div class="title-a"><span class="icon-a" onclick="this.closest('[role=sidesheet]').hidden = true">x</span></div>{picker}</div>
          <div id="sure" hidden><p>确定将视频移出合集吗？</p><p>移出合集后将不会删除原视频</p><button onclick="document.getElementById('sure').remove()">移除</button></div>
          <button onclick="{press.replace('ROUTE', '/web/api/mix/update')}">保存</button>""")

    async def run_it(self, operation, **payload):
        return await collections_douyin.execute(self.page, OWNER, {"platform": "douyin", "operation": operation, "collection_id": "111", "receipt_key": self.key, **payload})

    async def test_a_rename_marks_the_receipt_then_saves_once(self):
        self.receipt("rename", "111", "New name")
        result = await self.run_it("rename", title="New name")
        self.assertEqual([("/web/api/mix/update", "SUBMITTING", {"title": "New name"})], self.writes)
        self.assertEqual((True, 1), (result["submitted"], self.saved()["platform_writes"]))

    async def test_adding_keeps_the_others_and_saves_once(self):
        ids = [FIRST, SECOND]
        self.receipt("add", "111", work="7300000000000000003", members=ids)
        result = await self.run_it("add", title=None, work_id="7300000000000000003", before_members=ids)
        self.assertEqual(1, len(self.writes))
        self.assertTrue(result["submitted"])

    async def test_removing_saves_once(self):
        ids = [FIRST, SECOND]
        self.receipt("remove", "111", work=SECOND, members=ids)
        await self.run_it("remove", title=None, work_id=SECOND, before_members=ids)
        self.assertEqual(1, len(self.writes))

    async def test_members_that_moved_since_the_check_stop_it(self):
        self.receipt("remove", "111", work=SECOND, members=[SECOND])
        saved = await self.refused("collection_changed", lambda: self.run_it("remove", title=None, work_id=SECOND, before_members=[SECOND]))
        self.assertEqual(("READY", 0), (saved["state"], saved["platform_writes"]))

    async def test_two_videos_with_one_title_stop_it(self):
        self.members[1]["desc"] = "First video"
        ids = [FIRST, SECOND]
        self.receipt("remove", "111", work=SECOND, members=ids)
        await self.refused("ambiguous_work", lambda: self.run_it("remove", title=None, work_id=SECOND, before_members=ids))

    async def test_a_work_not_in_the_picker_is_never_added(self):
        ids = [FIRST, SECOND]
        self.receipt("add", "111", work="7300000000000000009", members=ids)
        await self.refused("work_not_visible", lambda: self.run_it("add", title=None, work_id="7300000000000000009", before_members=ids))

    async def test_a_delete_reads_the_confirmation_first_and_presses_once(self):
        self.receipt("delete", "111")
        result = await self.run_it("delete", title=None)
        await self.page.evaluate("1")
        self.assertEqual("/web/api/mix/delete", self.writes[0][0])
        self.assertEqual(("SUBMITTING", 1), (self.writes[0][1], len(self.writes)))
        self.assertEqual("111", result["collection_id"])

    async def test_two_collections_with_one_title_stop_a_delete(self):
        self.collections.append(self.collection("222", "Trips"))
        self.receipt("delete", "111")
        await self.refused("ambiguous_collection", lambda: self.run_it("delete", title=None))

    async def test_another_accounts_collection_stops_it(self):
        self.collections[0] = self.collection("111", "Trips", "someone_else")
        self.receipt("delete", "111")
        await self.refused("wrong_account", lambda: self.run_it("delete", title=None))

    async def test_a_refusal_from_douyin_is_not_called_done_and_the_receipt_says_it_was_sent(self):
        self.answer = {"status_code": 9}
        self.receipt("rename", "111", "New name")
        with self.assertRaises(VpubError) as caught:
            await self.run_it("rename", title="New name")
        self.assertEqual("platform_rejected", caught.exception.code)
        self.assertEqual(("SUBMITTING", 1), (self.saved()["state"], self.saved()["platform_writes"]))

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        self.receipt("rename", "111", "New name")
        saved = await self.refused("stopped", lambda: self.run_it("rename", title="New name"))
        self.assertEqual(("READY", 0), (saved["state"], saved["platform_writes"]))

    async def test_the_platform_asking_for_a_check_stops_it(self):
        self.check = "<h1>安全验证</h1>"
        self.receipt("rename", "111", "New name")
        await self.refused("human_verification_required", lambda: self.run_it("rename", title="New name"))

    async def test_a_receipt_for_something_else_refuses_the_press(self):
        self.receipt("rename", "111", "Another name")
        await self.refused("receipt_conflict", lambda: self.run_it("rename", title="New name"))


class InstagramTests(Pages):
    platform = "instagram"
    CID = "1790000000000000001"

    def prepare(self):
        super().prepare()
        self.client_there = True
        self.answered_id = self.CID
        self.answered_name = None
        self.members = [{"code": "Cabcde11111", "pk": "11"}, {"code": "Cabcde22222", "pk": "22"}]
        self.saved_posts = [{"media": {"code": "Cabcde33333", "pk": "33"}}]
        self.has_next = False

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.path == "/api/graphql":
            if self.on_folder:
                edges = [{"node": row} for row in self.members]
                return await self.json_at(route, {"data": {"fetch__MediaCollection": {"id": self.CID, "media": {"edges": edges, "page_info": {"has_next_page": self.has_next, "end_cursor": None}}}}})
            return await self.json_at(route, {"data": {"viewer": {"collections_unified_with_auto_collections": {"edges": [{"node": {"__typename": "MediaCollection", "collection_id": self.CID, "collection_name": "Trips"}}], "page_info": {"has_next_page": False}}}}})
        if url.path == "/api/v1/feed/saved/posts/":
            return await self.json_at(route, {"status": "ok", "items": self.saved_posts})
        if route.request.method == "POST":
            self.note(route)
            return await self.json_at(route, {})
        self.on_folder = url.path.endswith(f"/{self.CID}/")
        client = "" if not self.client_there else f"""<script>window.require = () => ({{editCollection: async args => {{ await fetch('/edit', {{method: 'POST', body: JSON.stringify(args)}}); return {{collection_id: '{self.answered_id}', collection_name: {json.dumps(self.answered_name)} || args.collectionName}}; }}}});</script>"""
        if self.on_folder:
            return await self.page_at(route, f"""{client}<script>fetch('/api/graphql', {{method: 'POST'}})</script><svg aria-label="Edit options" width="10" height="10" onclick="document.getElementById('more').hidden = false"></svg>
              <button id="more" hidden onclick="fetch('/api/v1/feed/saved/posts/')">Add from saved</button>""")
        await self.page_at(route, f"""<script>fetch('/api/graphql', {{method: 'POST'}})</script><button>+ New Collection</button><a href="/{OWNER}/saved/trips/{self.CID}/">Trips</a>""")

    async def run_it(self, operation, **payload):
        return await collections_instagram.execute(self.page, OWNER, {"platform": "instagram", "operation": operation, "collection_id": self.CID, "receipt_key": self.key, "title": None, **payload})

    async def test_a_rename_marks_the_receipt_then_asks_the_pages_own_client_once(self):
        self.receipt("rename", self.CID, "New name")
        await self.run_it("rename", title="New name")
        self.assertEqual([("/edit", "SUBMITTING", {"collectionId": self.CID, "collectionName": "New name"})], self.writes)

    async def test_removing_sends_the_number_of_the_exact_post(self):
        ids = ["Cabcde11111", "Cabcde22222"]
        self.receipt("remove", self.CID, work="Cabcde22222", members=ids)
        await self.run_it("remove", work_id="Cabcde22222", before_members=ids)
        self.assertEqual([("/edit", "SUBMITTING", {"collectionId": self.CID, "postIdsToRemove": ["22"]})], self.writes)

    async def test_adding_needs_the_post_in_the_saved_list_and_sends_its_number(self):
        ids = ["Cabcde11111", "Cabcde22222"]
        self.receipt("add", self.CID, work="Cabcde33333", members=ids)
        await self.run_it("add", work_id="Cabcde33333", before_members=ids)
        self.assertEqual([("/edit", "SUBMITTING", {"collectionId": self.CID, "postIdsToAdd": ["33"]})], self.writes)

    async def test_a_post_not_saved_yet_is_never_added(self):
        ids = ["Cabcde11111", "Cabcde22222"]
        self.receipt("add", self.CID, work="Cabcde44444", members=ids)
        await self.refused("work_not_saved", lambda: self.run_it("add", work_id="Cabcde44444", before_members=ids))

    async def test_members_that_moved_since_the_check_stop_it(self):
        self.receipt("remove", self.CID, work="Cabcde22222", members=["Cabcde22222"])
        await self.refused("collection_changed", lambda: self.run_it("remove", work_id="Cabcde22222", before_members=["Cabcde22222"]))

    async def test_a_part_list_stops_it(self):
        self.has_next = True
        ids = ["Cabcde11111", "Cabcde22222"]
        self.receipt("remove", self.CID, work="Cabcde22222", members=ids)
        await self.refused("collection_changed", lambda: self.run_it("remove", work_id="Cabcde22222", before_members=ids))

    async def test_an_answer_naming_another_collection_is_not_called_done(self):
        self.answered_id = "1790000000000000009"
        self.receipt("rename", self.CID, "New name")
        with self.assertRaises(VpubError) as caught:
            await self.run_it("rename", title="New name")
        self.assertEqual("wrong_collection", caught.exception.code)
        self.assertEqual(("SUBMITTING", 1), (self.saved()["state"], self.saved()["platform_writes"]))

    async def test_a_new_name_the_page_did_not_keep_is_not_called_done(self):
        self.answered_name = "Old name"
        self.receipt("rename", self.CID, "New name")
        with self.assertRaises(VpubError) as caught:
            await self.run_it("rename", title="New name")
        self.assertEqual("platform_rejected", caught.exception.code)

    async def test_with_the_pages_client_gone_it_stops_rather_than_guess(self):
        self.client_there = False
        self.receipt("rename", self.CID, "New name")
        await self.refused("layout_changed", lambda: self.run_it("rename", title="New name"))

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        self.receipt("rename", self.CID, "New name")
        await self.refused("stopped", lambda: self.run_it("rename", title="New name"))


del Pages


if __name__ == "__main__":
    unittest.main()
