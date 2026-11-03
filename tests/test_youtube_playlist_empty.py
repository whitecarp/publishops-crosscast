"""Empty channel playlist lists, using local DOM fixtures and intercepted writes only.

The fixture is a supported DOM contract, not a claim of a live Studio field check.
An unfamiliar empty layout must remain incomplete.
"""
import os
from unittest.mock import AsyncMock, patch

from test_youtube_playlists import Browser, CHANNEL, FakeYouTube
from video_publisher import youtube_studio as yt
from video_publisher.core import VpubError, home, read_json


EMPTY = '<div class="no-content"><div class="no-content-header">No content available</div></div>'


def section(body):
    return '<ytcp-playlist-section-content style="display:block">' + body + '</ytcp-playlist-section-content>'


class EmptyChannelPlaylists(Browser):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        env = patch.dict(os.environ, {"BILI_CLI_HOME": str(home().parent / "bili")})
        env.start()
        self.addCleanup(env.stop)
        self.site.playlists.clear()
        self.empty_body = section(EMPTY)
        self.empty_pager = False
        original = FakeYouTube.list_page

        def list_page(site):
            html = original(site)
            if site.playlists:
                return html
            # No pagination when the channel has no playlists. Keep the native Create form.
            if not self.empty_pager:
                html = html.replace('<ytcp-icon-button id="navigate-after" aria-disabled="true"></ytcp-icon-button>', '')
            return self.empty_body + html

        target = patch.object(FakeYouTube, "list_page", list_page)
        target.start()
        self.addCleanup(target.stop)
        pause = patch.object(self.page, "wait_for_timeout", new=AsyncMock())
        pause.start()
        self.addCleanup(pause.stop)

    async def fast_appears(self, page, target, seconds):
        return bool(await target.count())

    async def refused(self, body):
        self.empty_body = body
        self.assertEqual(([], False), await yt.read_playlists(self.page, CHANNEL))
        with self.assertRaises(VpubError) as caught:
            await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"])
        self.assertEqual("read_incomplete", caught.exception.code)
        self.assertEqual([], self.site.writes)
        self.assertEqual([], list((home() / "youtube-auto-playlists").glob("*.json")))

    async def test_explicit_empty_list_allows_first_private_create_and_retains_marker(self):
        self.assertEqual(([], True), await yt.read_playlists(self.page, CHANNEL))
        made = await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"])
        self.assertEqual([{"id": "PLnewnewnewnewnewnewnew", "title": "Wanted"}], made)
        self.assertEqual("private", self.site.playlists[made[0]["id"]]["visibility"])
        self.assertEqual(1, len(self.site.writes))
        saved = [read_json(p) for p in (home() / "youtube-auto-playlists").glob("*.json")]
        self.assertEqual(["CREATED"], [r["state"] for r in saved])
        self.assertEqual([], await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"]))
        self.assertEqual(1, len(self.site.writes))
        # If a completed list disappears, even a proven empty state must not repeat Create.
        self.site.playlists.clear()
        with self.assertRaises(VpubError) as caught:
            await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"])
        self.assertEqual("playlist_unverified", caught.exception.code)
        self.assertEqual(1, len(self.site.writes))

    async def test_localized_explicit_empty_labels(self):
        for words in ("没有可显示的内容", "沒有可顯示的內容"):
            with self.subTest(words=words):
                self.empty_body = section(EMPTY.replace("No content available", words))
                self.assertEqual(([], True), await yt.read_playlists(self.page, CHANNEL))

    async def test_disabled_next_without_valid_empty_evidence_never_creates(self):
        self.empty_pager = True
        for body in (section(""), section('<div role="progressbar">Loading</div>'),
                     section('<div role="alert">Could not load playlists</div>'),
                     section('<div role="progressbar">Loading</div>' + EMPTY),
                     section('<div role="alert">Could not load playlists</div>' + EMPTY)):
            with self.subTest(body=body):
                await self.refused(body)

    async def test_disabled_next_with_explicit_empty_evidence_allows_one_create(self):
        self.empty_pager = True
        self.assertEqual(([], True), await yt.read_playlists(self.page, CHANNEL))
        self.assertEqual([{"id": "PLnewnewnewnewnewnewnew", "title": "Wanted"}],
                         await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"]))
        self.assertEqual(1, len(self.site.writes))

    async def test_filter_inputs_outside_section_block_both_empty_paths(self):
        for pager in (False, True):
            self.empty_pager = pager
            for control in ('<input type="search" value="Wanted">',
                            '<input type="text" value="Wanted">', '<input value="Wanted">'):
                with self.subTest(pager=pager, control=control):
                    await self.refused('<header>' + control + '</header>' + section(EMPTY))
            # An unused or hidden filter is not evidence of a filtered list.
            for control in ('<input type="search" value="">', '<input type="search" value="Wanted" hidden>'):
                with self.subTest(pager=pager, control=control):
                    self.empty_body = '<header>' + control + '</header>' + section(EMPTY)
                    self.assertEqual(([], True), await yt.read_playlists(self.page, CHANNEL))

    async def test_absent_hidden_loading_and_error_states_never_prove_empty(self):
        for body in (section(""), section(EMPTY.replace('class="no-content"', 'class="no-content" hidden')),
                     section('<div role="progressbar">Loading</div>' + EMPTY),
                     section('<div role="alert">Could not load playlists</div>' + EMPTY),
                     section(EMPTY.replace("No content available", "Could not load playlists")),
                     '<ytcp-playlist-section-content aria-busy="true">' + EMPTY + '</ytcp-playlist-section-content>'):
            with self.subTest(body=body):
                await self.refused(body)

    async def test_unrelated_text_dialog_and_wrong_section_never_prove_empty(self):
        for body in ('<p>No content available</p>', EMPTY,
                     '<ytcp-video-section-content>' + EMPTY + '</ytcp-video-section-content>',
                     section('<div role="dialog">' + EMPTY + '</div>'),
                     '<div role="dialog">' + section(EMPTY) + '</div>',
                     section(EMPTY.replace("No content available", "About No content available")),
                     section(EMPTY) + '<div role="dialog">Sign in</div>',
                     section(EMPTY) + section(EMPTY)):
            with self.subTest(body=body):
                await self.refused(body)

    async def test_partial_rows_and_filters_do_not_become_empty(self):
        for body in (section('<ytcp-playlist-row><a id="playlist-title-link">No content available</a></ytcp-playlist-row>' + EMPTY),
                     section('<ytcp-filter-chip>Title: missing</ytcp-filter-chip>' + EMPTY),
                     section('<input type="search" value="missing">' + EMPTY)):
            with self.subTest(body=body):
                await self.refused(body)

    async def test_auth_page_never_proves_empty(self):
        async def signin(route):
            # Change the address inside a fulfilled document: no HTTP redirect can escape interception.
            await route.fulfill(status=200, content_type="text/html", body=section(EMPTY)
                                + '<script>history.replaceState(null, "", "/signin")</script>')

        await self.page.route("**/content/playlists", signin)
        with self.assertRaises(VpubError) as caught:
            await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"])
        self.assertEqual("wrong_account", caught.exception.code)
        self.assertEqual([], self.site.writes)

    async def test_later_empty_panel_cannot_erase_partial_extraction(self):
        self.empty_body = section('<ytcp-playlist-row><a id="playlist-title-link" href="/playlist/PLother/edit">Other</a></ytcp-playlist-row>'
                                  '<ytcp-playlist-row><a id="playlist-title-link">Wanted</a></ytcp-playlist-row>')
        self.empty_body += '<ytcp-icon-button id="navigate-after" aria-disabled="false" onclick="lastPage(this)"></ytcp-icon-button>'
        self.empty_body += '<script>function lastPage(button) { button.remove(); document.querySelector("ytcp-playlist-section-content").innerHTML = ' + repr(EMPTY) + '; }</script>'
        rows, complete = await yt.read_playlists(self.page, CHANNEL)
        self.assertEqual(["Other"], [r["title"] for r in rows])
        self.assertFalse(complete)
        with self.assertRaises(VpubError) as caught:
            await yt.ensure_playlists(self.page, CHANNEL, ["Wanted"])
        self.assertEqual("read_incomplete", caught.exception.code)
        self.assertEqual([], self.site.writes)

