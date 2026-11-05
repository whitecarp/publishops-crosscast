"""Offline Creator Center fake-page regressions; no source-platform reads."""
from urllib.parse import urlsplit
import unittest

from test_work_delete_youtube import FakeYouTube, Node, OfflineCase, walk
from video_publisher import work_delete_douyin as route, work_delete_state as state
from video_publisher.core import VpubError, home, read_json


OWNER = 'Recorded.Owner'
WORK = '7700000000000000000'
OTHER = '7700000000000000001'


class Popup:
    def __init__(self, page):
        self.page, self.detail = page, None

    async def __aenter__(self):
        self.page.popup = self
        return self

    async def __aexit__(self, *args):
        if self.detail is None:
            raise TimeoutError('Private popup body must not escape')

    @property
    def value(self):
        async def opened():
            return self.detail
        return opened()


class Detail:
    def __init__(self, page, wid):
        self.page, self.events = page, page.events
        self.url = page.detail_override or 'https://creator.douyin.com' + route.DETAIL + wid
        self.risk, self.closed = page.popup_risk, False

    async def wait_for_url(self, *args, **kwargs):
        pass

    async def opener(self):
        return None if self.page.foreign_popup else self.page

    async def close(self):
        self.closed = True


class HomeTab:
    """Creator Center's home page in a second tab of the same browser."""
    def __init__(self, page):
        self.page, self.live_owner, self.on_identity, self.url = page, page.home_owner, None, ''

    async def goto(self, url, **kwargs):
        self.url = url
        self.page.home_reads.append(url)

    async def wait_for_timeout(self, ms):
        pass

    async def close(self):
        pass


class HomeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return HomeTab(self.page)


class FakeDouyin(FakeYouTube):
    @property
    def context(self):
        return HomeContext(self)

    def __init__(self, target):
        super().__init__(target)
        self.home_owner, self.home_reads = target['identity'], []
        self.cards = [(WORK, 'Approved')]
        self.complete, self.show_terminal, self.private = True, True, set()
        self.detail_override, self.after_detail = None, None
        self.foreign_popup, self.popup_risk = False, False
        self.popup, self.opened = None, []
        self.gone_text = '该作品已被删除'

    def select(self, selector, nodes):
        mapping = {route.bb.DOUYIN_CARD: 'dycard', route.bb.DOUYIN_NAME: 'dytitle',
                   '.semi-modal-content': 'modal', '[role="alert"]': 'alert'}
        if selector not in mapping:
            raise AssertionError('Unexpected Douyin selector: ' + selector)
        return [n for n in nodes if n.kind == mapping[selector]]

    async def goto(self, url, **kwargs):
        self.events.append('read')
        if self.navigation_timeout:
            raise TimeoutError('private body')
        self.url = url
        if urlsplit(url).netloc == 'www.douyin.com':
            self.url = self.redirect_watch or url
            self.nodes = [Node('alert', self.gone_text, attrs={'role': 'alert'})] if self.deleted else []
        else:
            self.url = self.redirect_list or url
            self.render()

    def render(self):
        for node in walk(self.nodes):
            node.connected = False
        self.nodes = []
        for wid, title in self.cards:
            if self.deleted and wid == self.target['id'] and not self.keep_target:
                continue
            children = [Node('dytitle', title, action=lambda wid=wid: self.open_detail(wid)),
                        Node('delete', '删除作品', action=lambda wid=wid: self.delete_menu(wid))]
            if wid in self.private:
                children.append(Node('private', '私密'))
            self.nodes.append(Node('dycard', children=children))
        if self.show_terminal:
            self.nodes.append(Node('end', '没有更多作品' if self.nodes else '暂无作品'))

    def expect_popup(self, **kwargs):
        return Popup(self)

    def open_detail(self, wid):
        self.events.append('detail')
        self.popup.detail = Detail(self, wid)
        self.opened.append(self.popup.detail)
        if self.after_detail:
            self.after_detail()

    def delete_menu(self, wid=None):
        self.menu_count += 1
        self.events.append('delete-menu')
        self.nodes.append(Node('modal', '确定要移除此作品吗\n取消\n确定', children=[
            Node('final', '确定', attrs={'role': 'button'}, action=lambda: self.final(wid))]))
        if self.on_delete_menu:
            self.on_delete_menu()


class DouyinDeletionTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('douyin', WORK, OWNER)
        self.page = FakeDouyin(self.target)

    async def run_route(self, operation='delete', **changes):
        return await route.execute(self.page, OWNER, self.target | changes, operation, self.path)

    async def test_dialog_replaced_after_identity_check_cannot_retarget_final(self):
        def replace_dialog(node, script):
            if node.kind == 'modal' and 'node ===' in script:
                self.page.on_evaluate = None
                for old in walk([node]):
                    old.connected = False
                self.page.nodes.remove(node)
                self.page.delete_menu(OTHER)
        self.page.on_evaluate = replace_dialog
        await self.refusal_or_result()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_owner_changed_during_post_marker_guard_prevents_delete(self):
        def change_owner(page):
            if page is self.page and 'mark' in page.events and not page.menu_count:
                page.live_owner = 'someone_else'
        self.on_risk = change_owner
        await self.refusal_or_result()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_owner_changed_during_final_guard_prevents_final(self):
        def change_owner(page):
            if page is self.page and page.menu_count:
                page.live_owner = 'someone_else'
        self.on_risk = change_owner
        await self.refusal_or_result()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)

    async def test_stop_during_post_marker_binding_read_prevents_delete(self):
        def stop_during_read():
            if 'mark' in self.page.events:
                self.stopped()
        self.page.on_identity = stop_during_read
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_stop_during_final_binding_read_prevents_final(self):
        def stop_during_read(node, script):
            if node.kind == 'modal' and 'node.contains(child)' in script:
                self.stopped()
        self.page.on_evaluate = stop_during_read
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)

    async def test_wrong_identity_id_url_and_platform_fail_before_reads(self):
        for changes in ({'identity': OWNER.lower()}, {'id': OTHER}, {'id': '../bad'},
                        {'url': 'https://example.invalid/'}, {'platform': 'youtube'}):
            with self.subTest(changes=changes), self.assertRaises(VpubError):
                await self.run_route(**changes)
        self.assertEqual([], self.page.events)
        self.assert_unpressed()

    async def test_live_owner_or_creator_redirect_refuses(self):
        self.page.live_owner = 'someone_else'
        with self.assertRaises(VpubError):
            await self.run_route()
        self.page.live_owner = OWNER
        self.page.redirect_list = 'https://creator.douyin.com/login'
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_same_title_wrong_popup_id_is_not_owned_target(self):
        self.page.cards = [(OTHER, 'Approved')]
        answer = await self.run_route()
        self.assertFalse(answer['present'] or answer['owned'] or answer['page_gone'])
        self.assert_unpressed()
        self.assertTrue(all(detail.closed for detail in self.page.opened))

    async def test_duplicate_exact_ids_refuse_even_with_different_titles(self):
        self.page.cards = [(WORK, 'Approved'), (WORK, 'Other title')]
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_a_work_list_without_its_account_number_is_confirmed_on_the_home_page(self):
        # A real run found Creator Center's work list no longer prints 抖音号, though its home page still does.
        route.HOME_CONFIRMED.clear()
        self.page.live_owner = None
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))
        self.assertEqual([route.rt.URLS['douyin']], self.page.home_reads)

    async def test_a_home_page_showing_another_account_or_none_refuses(self):
        for shown in ('someone_else', None):
            with self.subTest(shown=shown):
                route.HOME_CONFIRMED.clear()
                self.page = FakeDouyin(self.target)
                self.page.live_owner, self.page.home_owner = None, shown
                with self.assertRaises(VpubError) as caught:
                    await self.run_route()
                self.assertEqual('wrong_account', caught.exception.code)
                self.assertEqual([], self.page.deleted_ids)

    async def test_duplicate_title_with_different_ids_binds_original_card(self):
        self.page.cards = [(OTHER, 'Approved'), (WORK, 'Approved')]
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))

    async def test_private_unresolved_card_blocks_full_absence_without_title_fallback(self):
        self.page.deleted = True
        self.page.cards = [(OTHER, 'Approved')]
        self.page.private = {OTHER}
        answer = await self.run_route('verify')
        self.assertFalse(answer['list_complete'])
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual([], self.page.opened)

    async def test_private_target_does_not_use_title_alias_to_delete(self):
        self.page.private = {WORK}
        answer = await self.run_route()
        self.assertFalse(answer['present'] or answer['owned'])
        self.assert_unpressed()

    async def test_card_replacement_after_popup_refuses(self):
        self.page.after_detail = self.page.render
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_earlier_same_title_card_replaced_during_later_popup_keeps_pending(self):
        second = '7700000000000000002'
        self.page.deleted = True
        self.page.cards = [(OTHER, 'Approved'), (second, 'Second')]
        replaced = []

        def replace_earlier():
            if self.page.opened[-1].url.endswith('/' + second):
                old = self.page.nodes[0]
                old.connected = False
                replaced.append(old)
                # Same title and card count, different exact ID. The later popup
                # did not change its own card; it changed one already inspected.
                self.page.nodes[0] = Node('dycard', children=[
                    Node('dytitle', 'Approved', action=lambda: self.page.open_detail(WORK)),
                    Node('delete', '删除作品', action=lambda: self.fail('Verify must not delete'))])

        self.page.after_detail = replace_earlier
        answer = await self.run_route('verify')
        first = self.page.locator(route.bb.DOUYIN_CARD).nth(0)
        handle = await first.element_handle()
        try:
            actual_id = await route.card_id(self.page, self.target, first, handle, 'Approved')
        finally:
            await handle.dispose()
        self.assertEqual(WORK, actual_id)
        self.assertFalse(answer['list_complete'])
        self.assertFalse(answer['list_absent'])
        self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, answer)['state'])
        self.assert_unpressed()
        self.assertTrue(replaced and all(node.disposed for node in replaced))

    async def test_earlier_card_retitled_without_node_replacement_keeps_pending(self):
        second = '7700000000000000002'
        self.page.deleted = True
        self.page.cards = [(OTHER, 'First'), (second, 'Second')]

        def retitle_earlier():
            if self.page.opened[-1].url.endswith('/' + second):
                self.page.nodes[0].children[0].text = 'Changed while reading'

        self.page.after_detail = retitle_earlier
        answer = await self.run_route('verify')
        self.assertFalse(answer['list_complete'])
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, answer)['state'])
        self.assert_unpressed()

    async def test_stable_multiple_other_cards_still_prove_complete_absence(self):
        self.page.deleted = True
        self.page.cards = [(OTHER, 'First'), ('7700000000000000002', 'Second')]
        answer = await self.run_route('verify')
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual('DELETED_VERIFIED', state.save_result(self.path, self.target, answer)['state'])
        self.assert_unpressed()

    async def test_recycled_node_with_same_title_rechecks_id_before_delete(self):
        def recycle():
            self.page.nodes[0].children[0].action = lambda: self.page.open_detail(OTHER)
        self.page.after_detail = recycle
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_foreign_popup_or_wrong_origin_is_not_card_id(self):
        for address in (f'https://evil.invalid{route.DETAIL}{WORK}',
                        f'https://creator.douyin.com{route.DETAIL}{WORK}?other=1'):
            self.page.detail_override = address
            with self.assertRaises(VpubError):
                await self.run_route()
        self.page.detail_override = None
        self.page.foreign_popup = True
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_each_refused_detail_page_says_which_check_refused_it(self):
        """A field run's delete stopped here every time with one message that couldn't say why. Only a part's name is given, never its value."""
        for address, says in ((f'https://evil.invalid{route.DETAIL}{WORK}', 'somewhere other than Creator Center'),
                              (f'https://creator.douyin.com{route.DETAIL}{WORK}?enter_from=secret-value', 'carries more than the work (enter_from)')):
            self.page.detail_override = address
            with self.subTest(address=address), self.assertRaises(VpubError) as caught:
                await self.run_route()
            self.assertIn(says, caught.exception.message)
            self.assertNotIn('secret-value', caught.exception.message)
        self.page.detail_override = None
        self.page.foreign_popup = True
        with self.assertRaises(VpubError) as caught:
            await self.run_route()
        self.assertIn("wasn't opened by the works list itself", caught.exception.message)
        self.assert_unpressed()

    async def test_inspect_and_verify_present_have_no_mutating_controls(self):
        for op in ('inspect', 'verify'):
            answer = await self.run_route(op)
            self.assertTrue(answer['present'] and answer['owned'])
            self.assert_safe_result(answer)
        self.assert_unpressed()

    async def test_already_gone_requires_fresh_full_own_list_and_zero_write(self):
        self.page.deleted = True
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(0, answer['platform_writes'])
        self.assert_safe_result(answer)
        self.assert_unpressed()

    async def test_partial_list_remains_pending_before_and_after_delete(self):
        self.page.complete = False
        answer = await self.run_route()
        self.assertTrue(answer['page_gone'])
        self.assertFalse(answer['list_complete'] or answer['list_absent'])
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual(1, self.page.final_count)
        answer = await self.run_route('verify')
        self.assertEqual(0, answer['platform_writes'])
        self.assertEqual(1, self.page.final_count)

    async def test_unavailable_access_denied_and_wrong_url_never_gone(self):
        self.page.deleted = True
        for text in ('暂时无法查看', '无权访问', '作品不存在', '暂无作品', '内容不可用'):
            self.page.gone_text = text
            self.assertFalse((await self.run_route('verify'))['page_gone'])
        self.page.gone_text = '该作品已被删除'
        self.page.redirect_watch = f'https://www.douyin.com/video/{OTHER}'
        self.assertFalse((await self.run_route('verify'))['page_gone'])

    async def test_mark_immediately_guard_then_single_final_and_readback(self):
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(1, answer['platform_writes'])
        events = self.page.events
        index = events.index('mark')
        self.assertEqual(['mark', 'risk', 'delete-menu'], events[index:index + 3])
        self.assertEqual('risk', events[events.index('final') - 1])
        self.assertIn('read', events[events.index('final') + 1:])
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])
        self.assert_safe_result(answer)

    async def test_stop_before_and_after_marker_prevents_first_delete(self):
        self.stopped()
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()
        (home() / 'STOP.json').unlink()
        self.after_mark = self.stopped
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_risk_immediately_after_marker_prevents_press(self):
        self.after_mark = lambda: setattr(self.page, 'risk', True)
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual(0, self.page.menu_count)

    async def test_popup_risk_refuses_without_deletion_marker(self):
        self.page.popup_risk = True
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_stop_or_identity_change_after_menu_prevents_final(self):
        self.page.on_delete_menu = self.stopped
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((1, 0), (self.page.menu_count, self.page.final_count))

    async def test_changed_live_owner_after_menu_prevents_final(self):
        self.page.on_delete_menu = lambda: setattr(self.page, 'live_owner', 'someone_else')
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual(0, self.page.final_count)

    async def test_replaced_same_title_card_after_menu_cannot_redirect_final(self):
        def replace_card():
            old = self.page.nodes[0]
            old.connected = False
            self.page.nodes[0] = Node('dycard', children=old.children)
        self.page.on_delete_menu = replace_card
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual(0, self.page.final_count)

    async def test_final_timeout_keeps_marker_no_repress_then_read_only_verify(self):
        self.page.final_timeout = True
        answer = await self.run_route()
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual(1, answer['platform_writes'])
        self.assert_safe_result(answer)
        self.page.keep_target = True
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))
        self.page.keep_target = False
        answer = await self.run_route('verify')
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(1, self.page.final_count)

    async def test_timeout_empty_unknown_page_never_proves_gone(self):
        self.page.navigation_timeout = True
        answer = await self.run_route('verify')
        self.assertFalse(answer['fresh'] or answer['page_gone'] or answer['list_complete'])
        self.assert_unpressed()

    async def test_empty_rows_without_terminal_are_pending(self):
        self.page.deleted = True
        self.page.show_terminal = False
        answer = await self.run_route('verify')
        self.assertTrue(answer['page_gone'])
        self.assertFalse(answer['list_complete'] or answer['list_absent'])
        self.assert_unpressed()

    async def test_replaced_first_delete_control_is_not_retargeted(self):
        def swap(page):
            if 'mark' in page.events:
                card = page.nodes[0]
                card.children[-1].connected = False
                card.children[-1] = Node('delete', '删除作品', action=lambda: self.fail('Retargeted delete'))
        self.on_risk = swap
        with self.assertRaises(VpubError) as raised:
            await self.run_route()
        self.assertEqual('wrong_work', raised.exception.code)
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_replaced_final_control_is_not_retargeted(self):
        def swap(page):
            if page is self.page and page.menu_count:
                modal = page.nodes[-1]
                modal.children[-1].connected = False
                modal.children[-1] = Node('final', '确定', attrs={'role': 'button'},
                                         action=lambda: self.fail('Retargeted final'))
        self.on_risk = swap
        with self.assertRaises(VpubError) as raised:
            await self.run_route()
        self.assertEqual('wrong_work', raised.exception.code)
        self.assertEqual(0, self.page.final_count)
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_wrong_attempt_and_generation_do_not_press(self):
        for changes in ({'attempt': 'other'}, {'generation': 'other'}, {'account': 'other'}):
            with self.subTest(changes=changes), self.assertRaises(VpubError):
                await self.run_route(**changes)
        self.assert_unpressed()

    async def test_unsupported_operation_refuses(self):
        with self.assertRaises(VpubError):
            await self.run_route('remove')
        self.assert_unpressed()


if __name__ == '__main__':
    unittest.main()
