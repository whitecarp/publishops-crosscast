"""A missing/loading player is not deletion; only the exact platform error is."""
from test_work_delete_youtube import OfflineCase,FakeYouTube,Node,CHANNEL,VIDEO
from video_publisher import work_delete_youtube as route
from video_publisher.core import VpubError


class StudioAbsenceTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('youtube',VIDEO,CHANNEL)
        self.page=FakeYouTube(self.target)
        self.page.deleted=True

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
