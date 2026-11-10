"""A missing/loading player is not deletion; only the exact platform error is."""
from test_work_delete_youtube import OfflineCase, FakeYouTube, Node, CHANNEL, VIDEO, OTHER
from video_publisher import work_delete_state as state
from video_publisher import work_delete_youtube as route
from video_publisher.core import VpubError


class StudioAbsenceTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('youtube',VIDEO,CHANNEL)
        self.page=FakeYouTube(self.target)
        self.page.deleted=True

    async def test_last_short_empty_heading_with_upload_prompt_verifies_without_writes(self):
        self.page.pages['upload'] = [[(OTHER, 'Retained video')]]
        for heading, button in [('No content available', 'Upload videos'),
                                ('没有可显示的内容', '上传视频'), ('沒有可顯示的內容', '上傳影片')]:
            with self.subTest(heading=heading):
                self.page.empty_panels = [Node('empty', children=[
                    Node('empty_header', heading), Node('button', button)])]
                result = await route.execute(self.page, CHANNEL, self.target, 'verify', self.path)
                self.assertTrue(state.verified(result, self.target), result)
                self.assertEqual(0, result['platform_writes'])
                self.assert_unpressed()

        self.page.pages['upload'] = [[(None, 'Unresolved draft')]]
        result = await route.execute(self.page, CHANNEL, self.target, 'verify', self.path)
        self.assertFalse(result['list_complete'])
        self.assertFalse(state.verified(result, self.target))
        self.assert_unpressed()

    async def test_ambiguous_hidden_and_misleading_empty_panels_remain_incomplete(self):
        label = 'No content available'
        heading = Node('empty_header', label)
        for panels in ([Node('empty', children=[Node('empty_header', 'Loading'), Node('button', label)])],
                       [Node('empty', children=[Node('empty_header', label, visible=False)])],
                       [Node('empty', children=[heading, heading])],
                       [Node('empty', children=[heading]), Node('empty', 'Loading')],
                       [Node('empty', children=[heading], visible=False)],
                       [Node('empty', 'No content available yet')]):
            with self.subTest(panels=panels):
                self.page.empty_panels = panels
                result = await route.execute(self.page, CHANNEL, self.target, 'verify', self.path)
                self.assertTrue(result['page_gone'])
                self.assertFalse(result['list_absent'])
                self.assertFalse(result['list_complete'])
                self.assertEqual(0, result['platform_writes'])
                self.assert_unpressed()

    async def test_observed_uploader_removed_phrase_is_specific(self):
        self.page.gone_text='此视频已被上传者移除'
        self.assertTrue(await route.exact_gone(self.page,self.target))
        for text in ('视频无法播放','This video is unavailable.','Private video',
                     'A comment says: 此视频已被上传者移除','此视频已被上传者移除 extra'):
            self.page.gone_text=text
            self.assertFalse(await route.exact_gone(self.page,self.target))

    async def test_delayed_error_is_read_without_press(self):
        original=self.page.goto;waits=[]
        async def goto(*args,**kwargs):
            await original(*args,**kwargs);self.page.nodes=[]
        async def wait(ms):
            waits.append(ms)
            if len(waits)==3:self.page.nodes=[Node('gone','This video has been removed by the uploader.')]
        self.page.goto=goto;self.page.wait_for_timeout=wait
        self.assertTrue(await route.exact_gone(self.page,self.target))
        self.assertEqual([500]*3,waits)
        self.assertEqual((0,0),(self.page.menu_count,self.page.final_count))

    async def test_timeout_and_redirect_during_wait_never_prove_absence(self):
        original=self.page.goto
        async def goto(*args,**kwargs):
            await original(*args,**kwargs);self.page.nodes=[]
        self.page.goto=goto;waits=[]
        async def wait(ms):waits.append(ms)
        self.page.wait_for_timeout=wait
        self.assertFalse(await route.exact_gone(self.page,self.target))
        self.assertEqual([500]*20,waits)
        async def redirect(ms):self.page.url='https://www.youtube.com/watch?v=otherVideo1'
        self.page.wait_for_timeout=redirect
        self.assertFalse(await route.exact_gone(self.page,self.target))

    async def test_duplicate_error_panels_and_stop_during_wait_are_rejected(self):
        original=self.page.goto
        async def duplicate(*args,**kwargs):
            await original(*args,**kwargs);self.page.nodes.append(Node('gone',self.page.gone_text))
        self.page.goto=duplicate
        self.assertFalse(await route.exact_gone(self.page,self.target))
        async def empty(*args,**kwargs):
            await original(*args,**kwargs);self.page.nodes=[]
        self.page.goto=empty
        async def stop(ms):self.stopped()
        self.page.wait_for_timeout=stop
        with self.assertRaises(VpubError):await route.exact_gone(self.page,self.target)

    async def test_address_drift_while_reading_error_does_not_prove_target_gone(self):
        original=self.page.goto;reads=[]
        async def goto(*args,**kwargs):
            await original(*args,**kwargs)
            def text():
                reads.append(True)
                self.page.url='https://www.youtube.com/watch?v=otherVideo1'
                return 'This video has been removed by the uploader.'
            self.page.nodes[0].words=text
        self.page.goto=goto
        self.assertFalse(await route.exact_gone(self.page,self.target))
        self.assertTrue(reads)
