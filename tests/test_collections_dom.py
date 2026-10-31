import unittest
from unittest.mock import AsyncMock,patch
from video_publisher.collections_browser import root


class CollectionDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.page=await self.browser.new_page()
        await self.page.route('**/*',lambda route:route.abort())

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def test_unhydrated_tab_retries_only_navigation(self):
        await self.page.set_content('''<button role="tab" id="tab">Bookmarks</button><script>
        document.getElementById('tab').onclick=()=>{
            window.t=(window.t||0)+1;
            document.body.dataset.clicks=String(window.t);
            if(window.t===2){const b=document.createElement('button');b.setAttribute('aria-label','Create Folder');
                b.textContent='Create';b.onclick=()=>{document.body.dataset.writes='1'};document.body.append(b);}
        };</script>''')
        with patch.object(self.page,'goto',new=AsyncMock()):await root(self.page,'x','owner')
        self.assertEqual(await self.page.locator('body').get_attribute('data-clicks'),'2')
        self.assertIsNone(await self.page.locator('body').get_attribute('data-writes'))

    async def test_open_menu_is_not_toggled_closed(self):
        await self.page.set_content('''<button role="tab" onclick="document.body.dataset.clicks='1'">Bookmarks</button><button aria-label="Create Folder" onclick="document.body.dataset.writes='1'">Create</button>''')
        with patch.object(self.page,'goto',new=AsyncMock()):await root(self.page,'x','owner')
        self.assertIsNone(await self.page.locator('body').get_attribute('data-clicks'))
        self.assertIsNone(await self.page.locator('body').get_attribute('data-writes'))
