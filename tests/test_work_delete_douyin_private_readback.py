"""A private card's own editor can identify it during an existing deletion read-back."""
from unittest.mock import AsyncMock, patch

from test_work_delete_douyin import Detail, FakeDouyin, OTHER, OWNER, WORK
from test_work_delete_youtube import Locator, Node, OfflineCase
from video_publisher import work_delete_douyin as route, work_delete_state as state
from video_publisher.core import VpubError


class PrivateEditorPage(FakeDouyin):
    def __init__(self, target):
        super().__init__(target)
        self.private = {OTHER}
        self.cards = [(OTHER, 'Same title')]
        self.deleted = True
        self.editor_url = route.bb.douyin_editor_address(OTHER)
        self.editor_links = []
        self.link = False
        self.in_place = False
        self.wrong_trigger = False

    def render(self):
        super().render()
        for card in self.nodes:
            if any(child.kind == 'private' for child in card.children):
                card.children.append(Node('edit-label', '编辑作品'))

    def select(self, selector, nodes):
        kinds = {'ul.semi-dropdown-menu[class*="edit-menu-"] > li.semi-dropdown-item': 'edit-menu',
                 '.semi-dropdown-showing': 'showing',
                 '[class*="edit-btn-"].semi-dropdown-showing': 'showing', 'a[href]': 'link'}
        if selector in kinds:
            return [n for n in nodes if n.kind == kinds[selector]]
        return super().select(selector, nodes)

    def hover(self, locator):
        hovered = locator.one()
        if hovered.kind == 'dytitle':
            self.nodes = [n for n in self.nodes if n.kind != 'edit-menu']
            for n in self.nodes:
                n.children = [child for child in n.children if child.kind != 'showing']
        if hovered.kind != 'edit-label':
            return
        card = next(n for n in self.nodes if hovered in n.children)
        if self.wrong_trigger:
            self.nodes.append(Node('showing'))
        else:
            card.children.append(Node('showing'))
        links = []
        if self.link:
            href = self.editor_links.pop(0) if self.editor_links else self.editor_url
            links.append(Node('link', attrs={'href': href}))
        self.nodes.append(Node('edit-menu', '编辑作品', children=links, action=self.open_editor))

    def open_editor(self):
        self.events.append('open-editor')
        if self.in_place:
            self.url = self.editor_url
            self.nodes = []
            return
        detail = Detail(self, OTHER)
        detail.url = self.editor_url
        self.popup.detail = detail
        self.opened.append(detail)


class PrivateEditorReadbackTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('douyin', WORK, OWNER)
        self.page = PrivateEditorPage(self.target)

        async def hover(locator, **kwargs):
            locator.page.hover(locator)

        hover_patch = patch.object(Locator, 'hover', hover)
        hover_patch.start()
        self.addCleanup(hover_patch.stop)
        self.title_box = AsyncMock()
        self.title_box.input_value.return_value = 'Same title'
        caption = AsyncMock()
        caption.inner_text.return_value = ''
        boxes = patch.object(route.bb, 'douyin_editor_boxes', return_value=(self.title_box, caption))
        boxes.start()
        self.addCleanup(boxes.stop)

    async def verify(self):
        result = await route.execute(self.page, OWNER, self.target, 'verify', self.path)
        self.assertEqual(0, result['platform_writes'])
        self.assert_unpressed()
        return result

    async def test_child_editor_or_its_link_proves_distinct_private_id(self):
        for link in (False, True):
            with self.subTest(link=link):
                self.page = PrivateEditorPage(self.target)
                self.page.link = link
                result = await self.verify()
                self.assertTrue(state.verified(result, self.target), result)
                self.assertIn('private_unresolved=0', result['note'])
                self.assertEqual(0 if link else 1, self.page.events.count('open-editor'))
                self.assertTrue(all(tab.closed for tab in self.page.opened))

    async def test_private_target_cannot_be_reported_absent(self):
        self.page.cards = [(WORK, 'Same title')]
        self.page.private = {WORK}
        self.page.keep_target = True
        self.page.editor_url = route.bb.douyin_editor_address(WORK)
        result = await self.verify()
        self.assertTrue(result['present'] and result['owned'])
        self.assertFalse(result['page_gone'] or result['list_absent'])

    async def test_foreign_editor_ambiguous_query_and_unbound_menu_stay_pending(self):
        for field, value in [('foreign_popup', True), ('wrong_trigger', True),
                             ('editor_url', self.page.editor_url + '&mid=' + WORK),
                             ('editor_url', self.page.editor_url + '&unrecognized=x'),
                             ('editor_url', self.page.editor_url.replace('creator.douyin.com', 'other.invalid'))]:
            with self.subTest(field=field, value=value):
                self.page = PrivateEditorPage(self.target)
                setattr(self.page, field, value)
                result = await self.verify()
                self.assertFalse(result['list_complete'] or result['list_absent'])

    async def test_in_place_editor_cannot_confirm_an_inventory_it_replaced(self):
        self.page.in_place = True
        result = await self.verify()
        self.assertFalse(result['list_complete'])
        self.assertIn('editor_replaced_list', result['note'])

    async def test_editor_content_that_disagrees_with_bound_card_is_unresolved(self):
        self.title_box.input_value.return_value = 'Different work'
        result = await self.verify()
        self.assertFalse(result['list_absent'] or result['list_complete'])

    async def test_two_same_title_private_cards_need_two_distinct_bound_ids(self):
        second = '7700000000000000002'
        self.page.cards = [(OTHER, 'Same title'), (second, 'Same title')]
        self.page.private = {OTHER, second}
        self.page.link = True
        for last, complete in ((second, True), (OTHER, False)):
            with self.subTest(last=last):
                self.page.editor_links = [route.bb.douyin_editor_address(wid) for wid in (OTHER, last)]
                self.assertEqual(complete, (await self.verify())['list_complete'])
                self.assertEqual([], self.page.editor_links)

    async def test_challenge_on_private_editor_stops_without_delete(self):
        self.page.popup_risk = True
        with self.assertRaises(VpubError) as failure:
            await self.verify()
        self.assertEqual('human_verification_required', failure.exception.code)
        self.assert_unpressed()

    async def test_write_path_never_opens_a_private_editor(self):
        self.page.cards = [(WORK, 'Approved')]
        self.page.private = {WORK}
        self.page.deleted = False
        self.page.editor_url = route.bb.douyin_editor_address(WORK)
        for op in ('inspect', 'delete'):
            result = await route.execute(self.page, OWNER, self.target, op, self.path)
            self.assertFalse(result['owned'])
            self.assertEqual(0, result['platform_writes'])
        self.assertNotIn('open-editor', self.page.events)
        self.assert_unpressed()
