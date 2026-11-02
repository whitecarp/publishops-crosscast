"""Offline regressions for retained X assets, exact Instagram IDs and cover-only edits.

Run from the repository: python -B -m unittest discover -s tests -p test_codex_update70_guards.py -v
Reads actual source definitions through AST; uses synthetic UI doubles, no browser,
source-package imports, accounts, credentials, baseline snapshot or network.
"""
import ast
from datetime import datetime
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import types
import unittest
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src/video_publisher"

def forbidden(*args, **kwargs):
    raise AssertionError("Offline test forbids network and child-process access")

def extract(filename, names, env):
    """Compile named, unmodified definitions; never execute module top-level imports."""
    path = PKG / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    chosen = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in names:
                chosen.append(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
                chosen.append(node)
    assert len(chosen) == len(names), (filename, names)
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(path), "exec"), env)
    return env

async def noop(*args, **kwargs):
    return None

async def yes(*args, **kwargs):
    return True

async def same(node, *args, **kwargs):
    return node

async def once(page, predicate, *args, **kwargs):
    return await predicate()

class BrowserTimeout(Exception):
    pass

# These stubs only satisfy two imports inside extracted functions. They cannot launch anything.
pkg = types.ModuleType("video_publisher")
pkg.__path__ = []
form_import = types.ModuleType("video_publisher.browser_forms")
form_import.yt_button_ready = yes
patchright = types.ModuleType("patchright")
patchright.__path__ = []
browser_api = types.ModuleType("patchright.async_api")
browser_api.Error = Exception
browser_api.TimeoutError = BrowserTimeout
STUB_MODULES = {"video_publisher": pkg, "video_publisher.browser_forms": form_import,
                "patchright": patchright, "patchright.async_api": browser_api}

COMMON = {"__package__": "video_publisher", "Path": Path, "re": re, "urlsplit": urlsplit,
          "now": lambda: "synthetic-offline", "stop_check": lambda *a: None,
          "stage": lambda *a: None, "risk_check": noop, "visible": same,
          "unique_visible": same, "look_until": once, "wait_until": once}
extract("core.py", {"VpubError"}, COMMON)
extract("browser_backends.py", {"normalize", "work_url", "instagram_address_is", "links", "verify"}, COMMON)
VpubError = COMMON["VpubError"]
EVIDENCE = {}

class Node:
    def __init__(self, name="node", value="", count=1, accept="image/png"):
        self.name, self.value, self.number, self.accept = name, value, count, accept
        self.clicks, self.reads = 0, 0
        self.on_wait = None
        self.on_click = None
        self.drop_fill = False
        self.href = ""
        self.selected = []
        self.waited_handles = []

    @property
    def first(self): return self
    def filter(self, **kwargs): return self
    def locator(self, selector): return self
    def get_by_role(self, *args, **kwargs): return self
    def get_by_text(self, *args, **kwargs): return self
    async def count(self): return self.number
    async def is_enabled(self): return True
    async def is_visible(self): return True
    async def wait_for(self, **kwargs):
        if self.on_wait: self.on_wait()
    async def wait_for_element_state(self, state, **kwargs):
        self.waited_handles.append(self.name)
        if getattr(self, "stays_visible", False): raise BrowserTimeout()
    async def element_handle(self, **kwargs): return self
    async def click(self, **kwargs):
        self.clicks += 1
        if self.on_click: self.on_click()
    async def press(self, *args): pass
    async def fill(self, value): self.value = "dropped" if self.drop_fill else value
    async def input_value(self):
        self.reads += 1
        return self.value
    async def inner_text(self): return self.value
    async def get_attribute(self, name): return self.accept if name == "accept" else self.href
    async def evaluate_all(self, expression): return getattr(self, "rows", [])

class XForm(Node):
    def __init__(self, page):
        super().__init__("retained-form")
        self.page = page
        self.change, self.link, self.compose = Node("change"), Node("download"), Node("compose")
        self.title, self.description = Node("title"), Node("description")
        self.source = "https://video.twimg.com/amplify_video/77/vid/a.mp4"
        self.length = 176.4
        self.on_length = None
        self.compose.on_click = lambda: setattr(page, "url", page.url.replace("/edit", "/compose"))
    def locator(self, selector):
        if selector.startswith("a["): return self.link
        if selector == "button:visible": return self.change
        return self.compose
    def get_by_placeholder(self, name, **kwargs):
        return self.title if name == "Title" else self.description
    async def evaluate(self, expression, *args):
        if expression == XENV["X_VIDEO_LENGTH"]:
            if self.on_length: self.on_length()
            return self.length
        return [self.source]

class XPage:
    def __init__(self):
        self.url = "about:blank"
        self.form = XForm(self)
        self.owner = Node("owner")
        self.owner.rows = ["https://analytics.twitter.com/user/owner/home"]
        self.caption, self.post = Node("caption"), Node("final")
    async def goto(self, url, **kwargs): self.url = url
    async def wait_for_timeout(self, *args): pass
    def locator(self, selector):
        return self.owner if "analytics.twitter.com" in selector else self.post
    def get_by_placeholder(self, *args, **kwargs): return self.caption

class Tx:
    def __init__(self):
        self.identity = "owner"
        self.options = {"title": "Approved title", "description": "Approved description",
                        "post_text": "Approved caption", "existing_media_url": "https://studio.x.com/library/13_77/edit"}
        self.receipt = {"before_ids": [], "media_selected": False, "final_submit_attempted": False}
        self.finals = []
        self.before_final = None
    def save(self, **changes): self.receipt.update(changes); return dict(self.receipt)
    def media_name(self): return "crosscast-approved.mp4"
    def media(self): raise AssertionError("No video upload permitted in this fixture")
    def cover(self): return None
    async def submit(self, page, button, still_holds=None):
        if self.before_final: self.before_final(page)
        if still_holds: await still_holds()
        self.finals.append({"url": page.url, "source": page.form.source, "caption": page.caption.value})
        self.save(final_submit_attempted=True)

async def x_finish(page, tx, before): return tx.save(state="SYNTHETIC_FINAL_REACHED")
XENV = dict(COMMON, X_FORM_SECONDS=1, X_VIDEO_SECONDS=1,
            X_DONE_SECONDS=1, x_media_form=lambda page, filename: page.form, finish=x_finish,
            enabled=yes)
extract("browser_forms.py", {"x_studio_path", "x_media_address", "x_file_link", "x_link_names_another_video",
        "publish_x", "x_video_is_whole", "x_composer_is_this_files", "X_FILE_VIDEOS", "X_VIDEO_LENGTH",
        "X_COMPOSER_VIDEOS", "X_MEDIA_PAGE", "X_CHANGE_THUMBNAIL"}, XENV)

async def synthetic_schedule(*args): return {"at": "2030-01-01T12:00:00+00:00"}
async def synthetic_schedule_verify(page, identity, receipt):
    return {**receipt, "state": "SYNTHETIC_SCHEDULE_REACHED"}
XENV.update(datetime=datetime, x_schedule=synthetic_schedule,
            x_chosen_time_says=lambda *args: True, verify_scheduled_x=synthetic_schedule_verify)
extract("browser_backends.py", {"X_CHOSEN_TIME"}, XENV)
extract("browser_forms.py", {"x_send_scheduled"}, XENV)

class SchedulePage(XPage):
    def __init__(self):
        super().__init__()
        self.calendar = Node("calendar", count=0)
        self.calendar_save = Node("calendar-save")
        self.chosen = Node("chosen-time", "synthetic chosen time")
        self.row = Node("schedule-row")
        self.row.locator = lambda s: Node("row-buttons", count=2 if s == "button:visible" else 1)
        self.post.locator = lambda s: self.row
    def locator(self, selector):
        if selector.startswith("fieldset.CalendarNavigation"): return self.calendar
        if selector == 'button[type="button"]:visible': return self.chosen
        if selector == 'button[type="button"]': return self.calendar_save
        return super().locator(selector)

class Update70Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crosscast-u70-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        guards = [patch.dict(os.environ, {"VPUB_HOME": str(self.root / "isolated-home")}),
                  patch.dict(sys.modules, STUB_MODULES)]
        for target, name in ((socket, "create_connection"), (socket.socket, "connect"),
                             (socket.socket, "connect_ex"), (socket.socket, "sendto"),
                             (socket, "getaddrinfo"), (subprocess, "Popen"), (os, "system")):
            guards.append(patch.object(target, name, forbidden))
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

    def _callTestMethod(self, method):
        # All doubles complete immediately. Drive coroutines without an event loop:
        # Windows asyncio would otherwise create a loopback socketpair, also forbidden here.
        coroutine = method()
        try:
            try:
                coroutine.send(None)
            except StopIteration:
                return
            raise AssertionError("Synthetic fixture unexpectedly suspended for I/O")
        finally:
            coroutine.close()

    async def test_GUARD_X_late_redirect_preserves_original_receipt_and_blocks_final(self):
        page, tx = XPage(), Tx()
        def redirect():
            page.url = "https://studio.x.com/library/13_88/edit"
            page.form.link.href = "https://video.twimg.com/amplify_video/88/vid/a.mp4"
            page.form.source = page.form.link.href
        page.form.change.on_wait = redirect
        with self.assertRaises(VpubError) as caught:
            await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertEqual(tx.options["existing_media_url"], "https://studio.x.com/library/13_77/edit")
        self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])
        self.assertEqual(page.form.title.value, "")
        self.assertFalse(tx.finals)
        EVIDENCE["X_LATE_REDIRECT"] = {"expected": tx.options["existing_media_url"],
                                       "recorded": tx.receipt["media_url"], "finals": tx.finals}

    async def test_CONTROL_X_matching_asset_and_metadata_reach_one_final(self):
        page, tx = XPage(), Tx()
        page.form.link.href = "https://video.twimg.com/amplify_video/77/vid/a.mp4"
        tx.options["duration_seconds"] = 176.4
        await XENV["publish_x"](page, tx)
        self.assertEqual(len(tx.finals), 1)
        self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])

    async def test_CONTROL_X_known_wrong_download_stops(self):
        page, tx = XPage(), Tx()
        page.form.link.href = "https://video.twimg.com/amplify_video/88/vid/a.mp4"
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertFalse(tx.finals)

    async def test_CONTROL_X_immediate_redirect_stops_before_metadata(self):
        page, tx = XPage(), Tx()
        async def redirect_at_goto(url, **kwargs):
            page.url = "https://studio.x.com/library/13_88/edit" if url == tx.options["existing_media_url"] else url
        page.goto = redirect_at_goto
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertFalse(tx.finals)
        self.assertEqual(page.form.title.value, "")

    async def test_CONTROL_X_unknown_download_shape_is_not_positive_mismatch(self):
        page = XPage()
        page.form.link.href = "https://video.twimg.com/tweet_video/opaque.mp4"
        self.assertFalse(await XENV["x_link_names_another_video"](
            page.form, "crosscast-approved.mp4", "https://studio.x.com/library/13_77/edit"))

    async def test_CONTROL_X_unknown_download_shape_still_allows_matching_retained_asset(self):
        page, tx = XPage(), Tx()
        page.form.link.href = "https://video.twimg.com/tweet_video/opaque.mp4"
        await XENV["publish_x"](page, tx)
        self.assertEqual(len(tx.finals), 1)

    async def test_GUARD_X_redirect_during_length_wait_stops_before_compose(self):
        page, tx = XPage(), Tx()
        tx.options["duration_seconds"] = 176.4
        page.form.on_length = lambda: setattr(page, "url", "https://studio.x.com/library/13_88/edit")
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertEqual(page.form.compose.clicks, 0)
        self.assertFalse(tx.finals)
        self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])

    async def test_GUARD_X_redirect_during_caption_wait_does_not_fill_or_overwrite(self):
        page, tx = XPage(), Tx()
        page.caption.on_wait = lambda: setattr(page, "url", "https://studio.x.com/library/13_88/compose")
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertEqual(page.caption.value, "")
        self.assertFalse(tx.finals)
        self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])

    async def test_GUARD_X_final_callback_rechecks_after_submit_risk_wait(self):
        for target in ("https://studio.x.com/library/13_88/compose", "https://studio.x.com/library"):
            with self.subTest(target=target):
                page, tx = XPage(), Tx()
                tx.before_final = lambda p: setattr(p, "url", target)
                with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
                self.assertEqual(caught.exception.code, "wrong_work")
                self.assertFalse(tx.finals)
                self.assertFalse(tx.receipt["final_submit_attempted"])
                self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])

    async def test_GUARD_X_resumed_receipt_without_existing_option_retains_binding(self):
        page, tx = XPage(), Tx()
        original = tx.options.pop("existing_media_url")
        tx.receipt.update(media_selected=True, media_url=original, upload_filename=tx.media_name())
        page.form.change.on_wait = lambda: setattr(page, "url", "https://studio.x.com/library/13_88/edit")
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertEqual(tx.receipt["media_url"], original)
        self.assertFalse(tx.finals)

    async def test_GUARD_X_scheduled_final_rechecks_retained_asset_after_submit_wait(self):
        page, tx = SchedulePage(), Tx()
        tx.options["schedule"] = "2030-01-01T12:00:00+00:00"
        tx.receipt["schedule_rows_before"] = []
        tx.before_final = lambda p: setattr(p, "url", "https://studio.x.com/library/13_88/compose")
        with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
        self.assertEqual(caught.exception.code, "wrong_work")
        self.assertEqual(page.calendar_save.clicks, 1)
        self.assertFalse(tx.finals)
        self.assertEqual(tx.receipt["media_url"], tx.options["existing_media_url"])

    async def test_CONTROL_X_matching_scheduled_asset_reaches_one_final(self):
        page, tx = SchedulePage(), Tx()
        tx.options["schedule"] = "2030-01-01T12:00:00+00:00"
        tx.receipt["schedule_rows_before"] = []
        result = await XENV["publish_x"](page, tx)
        self.assertEqual(result["state"], "SYNTHETIC_SCHEDULE_REACHED")
        self.assertEqual(len(tx.finals), 1)

    async def test_CONTROL_X_title_and_description_drop_each_stop(self):
        for field in ("title", "description"):
            with self.subTest(field=field):
                page, tx = XPage(), Tx()
                getattr(page.form, field).drop_fill = True
                with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
                self.assertEqual(caught.exception.code, "metadata_mismatch")
                self.assertFalse(tx.finals)

    async def test_CONTROL_X_duration_missing_or_outside_tolerance_stops(self):
        for length in (None, 174.0, 178.0):
            with self.subTest(length=length):
                page, tx = XPage(), Tx()
                page.form.length = length
                tx.options["duration_seconds"] = 176.4
                with self.assertRaises(VpubError) as caught: await XENV["publish_x"](page, tx)
                self.assertEqual(caught.exception.code, "video_unverified")
                self.assertFalse(tx.finals)

    async def test_CONTROL_X_duration_within_tolerance_passes(self):
        page = XPage()
        for length in (175.4, 176.4, 177.4):
            page.form.length = length
            await XENV["x_video_is_whole"](page, page.form, 176.4)

    async def test_GUARD_IG_case_changed_shortcode_cannot_verify_requested_work(self):
        wanted, actual = "CabcDE12345", "cabcde12345"
        page = types.SimpleNamespace(url="about:blank", locator=lambda selector: Node(selector))
        async def goto(url, **kw): page.url = f"https://www.instagram.com/p/{actual}/"
        page.goto = goto
        async def owned(*a): return {wanted: {"id": wanted, "text": "same caption"}}
        async def no_links(*a): return {}
        env = dict(COMMON, works=owned, links=no_links, instagram_caption_says=yes)
        extract("browser_backends.py", {"verify"}, env)
        result = await env["verify"](page, "instagram", "owner", {"id": wanted, "expected_text": "same caption"})
        self.assertNotEqual(wanted, actual)
        self.assertEqual(result["state"], "SUBMITTED_UNVERIFIED")
        EVIDENCE["IG_SHORTCODE_CASE"] = {"requested": wanted, "landed": page.url, "state": result["state"]}

    async def test_CONTROL_IG_address_shapes_host_and_extra_path(self):
        check = COMMON["instagram_address_is"]
        for kind in ("p", "reel", "reels"):
            self.assertTrue(check(f"https://www.instagram.com/{kind}/CabcDE12345/", "owner", "CabcDE12345"))
            self.assertTrue(check(f"https://www.instagram.com/OWNER/{kind}/CabcDE12345/", "owner", "CabcDE12345"))
            self.assertFalse(check(f"https://www.instagram.com/OWNER/{kind}/cabcde12345/", "owner", "CabcDE12345"))
        for address in ("https://evil.example/p/CabcDE12345/", "https://www.instagram.com/p/XCabcDE12345/",
                        "https://www.instagram.com/p/CabcDE12345/extra", "https://www.instagram.com/other/p/CabcDE12345/"):
            self.assertFalse(check(address, "owner", "CabcDE12345"))

    async def test_CONTROL_IG_grid_keeps_p_and_reel_kind_and_id_case(self):
        node = Node()
        node.rows = [{"url": "https://www.instagram.com/owner/p/PHOTOabc/", "text": "photo"},
                     {"url": "https://www.instagram.com/reel/REELabc/", "text": "reel"}]
        result = await COMMON["links"](types.SimpleNamespace(locator=lambda s: node), "instagram", "owner")
        self.assertEqual(result["PHOTOabc"]["url"], "https://www.instagram.com/p/PHOTOabc/")
        self.assertEqual(result["REELabc"]["url"], "https://www.instagram.com/reel/REELabc/")

    async def douyin_case(self, change=None, stays_visible=False, words_initial="Approved words"):
        title, words = Node("title", "Approved title"), Node("words", words_initial)
        physical, control, save_button = Node("physical-cover-window"), Node("cover"), Node("save", "保存")
        physical.stays_visible = stays_visible
        class IndexedDialog(Node):
            def __init__(self):
                super().__init__("indexed-dialog")
                self.current = physical
            async def element_handle(self, **kwargs): return self.current
            async def click(self, **kwargs):
                # Insertion of a hidden window ahead of the still-visible original changes an index.
                self.current = Node("new-hidden-window")
            async def wait_for(self, **kwargs):
                if kwargs.get("state") == "hidden":
                    return await self.current.wait_for_element_state("hidden")
        dialog = IndexedDialog()
        page = types.SimpleNamespace(locator=lambda s: dialog if s == "[role=dialog]" else control,
                                     get_by_text=lambda *a, **k: control,
                                     get_by_role=lambda *a, **k: save_button)
        async def fields(*a): return title, words
        async def settled(*a):
            if change == "title": title.value = "Unapproved changed title"
            if change == "words": words.value = "Unapproved changed words"
            return "loaded"
        records = []
        risk_calls = 0
        async def risk(*args):
            nonlocal risk_calls
            risk_calls += 1
            if change == "final_title" and risk_calls == 2:
                title.value = "Unapproved changed title"
        env = dict(COMMON, atomic_json=lambda path, value: records.append(dict(value)), file_sha=lambda p: "sha",
                   douyin_editor_address=lambda wid: "https://synthetic.invalid/edit/"+wid,
                   douyin_fields=fields, douyin_card_words=COMMON["normalize"],
                   douyin_says_held=noop, choose_file=noop, douyin_cover_settles=settled,
                   douyin_cover_traffic=lambda p: lambda **kw: {}, DOUYIN_COVER_SECONDS=1,
                   douyin_can_press=noop, douyin_moved_on=lambda *a: async_value({"synthetic": True}),
                   douyin_cover_stuck=lambda *a: async_value("synthetic stuck"), risk_check=risk)
        extract("browser_edits.py", {"execute"}, env)
        payload = {"patch": {"portrait": str(self.root / "synthetic.jpg"), "portrait_sha256": "sha"},
                   "receipt": {"id": "7300000000000000001", "title": "Approved title"},
                   "edit_receipt_path": str(self.root / "synthetic-never-written.json")}
        try:
            result = await env["execute"](page, "douyin", "owner", payload)
        except VpubError as error:
            return error, title, words, save_button, physical, records
        return result, title, words, save_button, physical, records

    async def test_GUARD_DY_cover_only_blocks_changed_title(self):
        result, title, words, button, dialog, records = await self.douyin_case("title")
        self.assertEqual(button.clicks, 0)
        self.assertEqual(title.reads, 2)
        self.assertEqual(words.value, "Approved words")
        self.assertIsInstance(result, VpubError)
        self.assertEqual(result.code, "wrong_work")
        self.assertNotIn("EDIT_SUBMITTING", [r["state"] for r in records])
        EVIDENCE["DY_COVER_TITLE"] = {"title_at_save": title.value, "title_reads": title.reads,
                                      "work_save_clicks": button.clicks, "error": result.code}

    async def test_GUARD_DY_title_changed_in_submit_risk_wait_blocks_save(self):
        result, title, words, button, dialog, records = await self.douyin_case("final_title")
        self.assertEqual(result.code, "wrong_work")
        self.assertEqual(button.clicks, 0)

    async def test_CONTROL_DY_initially_empty_caption_may_finish_loading(self):
        result, title, words, button, dialog, records = await self.douyin_case("words", words_initial="")
        self.assertEqual(button.clicks, 1)
        self.assertEqual(result["state"], "EDIT_SUBMITTED_UNVERIFIED")

    async def test_CONTROL_DY_caption_change_blocks_work_save(self):
        result, title, words, button, dialog, records = await self.douyin_case("words")
        self.assertIsInstance(result, VpubError)
        self.assertEqual(result.code, "wrong_work")
        self.assertEqual(button.clicks, 0)

    async def test_CONTROL_DY_wait_uses_original_handle_and_blocks_if_visible(self):
        result, title, words, button, dialog, records = await self.douyin_case(stays_visible=True)
        self.assertEqual(result.code, "cover_not_ready")
        self.assertEqual(dialog.waited_handles, ["physical-cover-window"])
        self.assertEqual(button.clicks, 0)

    async def test_CONTROL_DY_unchanged_words_and_title_save_once(self):
        result, title, words, button, dialog, records = await self.douyin_case()
        self.assertEqual(button.clicks, 1)
        self.assertEqual(result["state"], "EDIT_SUBMITTED_UNVERIFIED")
        self.assertIn("EDIT_SUBMITTING", [r["state"] for r in records])

    async def test_CONTROL_YT_thumbnail_input_count_and_media_type(self):
        class Selected(Exception): pass
        for number, accept, expected in ((0, "image/png", "cover_unavailable"), (2, "image/png", "layout_changed"),
                                          (1, "video/mp4", "layout_changed"), (1, "", "layout_changed"),
                                          (1, "image/png,image/jpeg", "SELECTED")):
            with self.subTest(number=number, accept=accept):
                inputs = Node("file-loader", count=number, accept=accept)
                page = types.SimpleNamespace(locator=lambda s: inputs)
                selected = []
                async def choose(page, node, path): selected.append(path); raise Selected()
                env = dict(COMMON, STUDIO="https://studio.youtube.com", file_sha=lambda p: "sha",
                           edit_fields=lambda *a: async_value((Node("title"), Node("description"))), choose_file=choose)
                extract("youtube_studio.py", {"edit"}, env)
                payload = {"patch": {"cover": "synthetic.jpg", "cover_sha256": "sha"}, "receipt": {"id": "abcdefghijk"}}
                try: await env["edit"](page, payload, lambda **kw: kw, noop)
                except Selected: code = "SELECTED"
                except VpubError as error: code = error.code
                else: code = "UNEXPECTED_RETURN"
                self.assertEqual(code, expected)
                self.assertEqual(len(selected), int(expected == "SELECTED"))

async def async_value(value): return value

if __name__ == "__main__":
    unittest.main(verbosity=2)
