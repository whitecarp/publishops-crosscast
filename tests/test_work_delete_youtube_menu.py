"""A Studio options menu may itself have dialog semantics; it is not confirmation."""
from test_work_delete_youtube import OfflineCase,FakeYouTube,Node,walk,CHANNEL,VIDEO
from video_publisher import work_delete_youtube as route
from video_publisher.core import VpubError,read_json


class WrappedMenuPage(FakeYouTube):
    def options(self,wid=None):
        super().options(wid)
        menu=self.nodes.pop()
        self.wrapper=Node('menu-wrapper',attrs={'role':'dialog'},children=[menu])
        self.nodes.append(self.wrapper)

    def delete_menu(self,wid=None):
        self.nodes.remove(self.wrapper)
        self.wrapper.connected=False
        super().delete_menu(wid)


class StudioMenuDialogTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('youtube',VIDEO,CHANNEL)
        self.page=WrappedMenuPage(self.target)

    async def run_route(self):
        return await route.execute(self.page,CHANNEL,self.target,'delete',self.path)

    async def test_menu_dialog_is_not_misread_as_unbound_confirmation(self):
        answer=await self.run_route()
        self.assertEqual((1,1),(self.page.menu_count,self.page.final_count))
        self.assertEqual([VIDEO],self.page.deleted_ids)
        self.assertTrue(read_json(self.path)['delete_attempted'])
        self.assertTrue(answer['page_gone'])

    async def test_unrelated_dialog_blocks_menu_press(self):
        original=self.page.options
        def options(wid=None):
            original(wid)
            self.page.nodes.append(Node('unrelated',attrs={'role':'dialog'}))
        self.page.options=options
        with self.assertRaises(VpubError):await self.run_route()
        self.assert_unpressed()

    async def test_wrapped_menu_replacement_during_account_check_refuses(self):
        def replace():
            if not hasattr(self.page,'wrapper'):return
            self.page.on_identity=None
            old=self.page.wrapper
            self.page.nodes.remove(old)
            # The same final item moved into a fresh wrapper must be refused.
            old.connected=False
            new=Node('menu-wrapper',attrs={'role':'dialog'},children=old.children)
            self.page.wrapper=new
            self.page.nodes.append(new)
        self.page.on_identity=replace
        with self.assertRaises(VpubError):await self.run_route()
        self.assert_unpressed()

    async def test_dialog_inserted_after_mark_blocks_menu_press(self):
        self.after_mark=lambda:self.page.nodes.append(Node('unrelated',attrs={'role':'alertdialog'}))
        with self.assertRaises(VpubError):await self.run_route()
        self.assertEqual((0,0),(self.page.menu_count,self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])
