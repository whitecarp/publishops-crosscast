"""Offline exact Instagram ownership, destructive controls and complete-profile proof."""
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from video_publisher import work_delete_instagram as route, work_delete_state as state
from video_publisher.core import VpubError, atomic_json, home, read_json
from test_work_delete_x import BindingRegressions, FakePage, Node, OfflineCase, OWNER


WORK = 'AbC_dEf123'
HEADINGS = 'main h1, main h2, main [role="alert"]'
EMPTY_HEADINGS = 'main h1, main h2'


class InstagramPage(FakePage):
    def __init__(self, marked):
        super().__init__()
        self.marked = marked
        self.present, self.deleted, self.owner = True, False, OWNER
        self.exact_redirect, self.profile_redirect = None, None
        self.exact_message = 'This post has been deleted.'
        self.profile_message, self.counts, self.own_empty_button = 'No Posts Yet', ['0 posts'], ''
        self.exact_message_visible, self.profile_message_visible, self.count_visible = True, True, True
        self.profile_links = []
        self.permalink = f'/p/{WORK}/'
        self.menu_count, self.confirm_count, self.final_count, self.options_count = 1, 1, 1, 1
        self.delete_available, self.edit_available = True, True
        self.confirm_text = 'Delete post?'
        self.stop_at_final, self.fail_final, self.stays = False, False, False
        self.hostile_comment = ''

    def menu(self):
        for _ in range(self.menu_count):
            children = [Node('Cancel', role='button')]
            if self.edit_available:
                children.append(Node('Edit', role='button'))
            if self.delete_available:
                children.append(Node('Delete', role='button', event='menu-delete', action=self.ask))
            self.tree.children.append(Node('Post options', role='dialog', children=children))
        self.tree.children.append(Node('Delete', role='button', event='unrelated-delete'))

    def ask(self):
        self.marked()
        self.tree.children = [node for node in self.tree.children if node.role != 'dialog']
        for _ in range(self.confirm_count):
            controls = [Node('Delete', role='button', event='final-delete', action=self.remove) for _ in range(self.final_count)]
            self.tree.children.append(Node(self.confirm_text, role='dialog', children=controls + [Node('Cancel', role='button')]))
        if self.stop_at_final:
            atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop before final'})

    def remove(self):
        self.marked()
        if self.fail_final:
            raise TimeoutError('Synthetic final acknowledgement lost')
        self.deleted = not self.stays

    async def goto(self, url, **kwargs):
        self.visits.append(url)
        exact = '/p/' in urlsplit(url).path
        self.url = (self.exact_redirect if exact else self.profile_redirect) or url
        nodes = []
        if exact and self.present and not self.deleted:
            children = [Node(role='link', href=f'/{self.owner}/', selectors={
                f'header a[href="/{self.owner}/"]',
                f'main header a[href="/{self.owner}/"], article header a[href="/{self.owner}/"]'})]
            if self.permalink is not None:
                children.append(Node(role='link', href=self.permalink, selectors={'a[href]'}, event='post-permalink'))
            children.extend(Node('More options', role='button', event='target-options', action=self.menu) for _ in range(self.options_count))
            nodes.append(Node(selectors={'main'}, children=[Node(selectors={'article'}, children=children)]))
        elif exact:
            nodes.append(Node(self.exact_message, selectors={HEADINGS}, visible=self.exact_message_visible))
        else:
            nodes.extend(Node(href=href, selectors={'main a[href]'}) for href in self.profile_links)
            nodes.extend(Node(text, selectors={'main header li'}, visible=self.count_visible) for text in self.counts)
            nodes.append(Node(self.profile_message, selectors={EMPTY_HEADINGS}, visible=self.profile_message_visible))
            if self.own_empty_button:
                nodes.append(Node(self.own_empty_button, role='button', selectors={route.OWN_EMPTY_CONTROLS}))
        if self.hostile_comment:
            nodes.append(Node(self.hostile_comment, user_words=True))
        self.tree = Node(children=[self.navigation()] + nodes)


class InstagramDeletionTests(BindingRegressions, OfflineCase):
    def setUp(self):
        super().setUp()
        self.ready('instagram', WORK)
        self.page = InstagramPage(self.marked)

    async def execute(self, operation='delete', actual=OWNER):
        return await route.execute(self.page, actual, self.target, operation, str(self.path))

    async def test_requested_url_cannot_authorize_a_different_owned_article(self):
        self.page.permalink = '/p/OtherOwned99/'
        with self.assertRaises(VpubError):
            await self.execute()
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_loading_target_does_not_lend_its_shortcode_to_another_owned_article(self):
        self.page.permalink = '/p/OtherOwned99/'
        original = self.page.goto
        async def loading_target(url, **kwargs):
            await original(url, **kwargs)
            main = next(n for n in self.page.tree.descendants() if 'main' in n.selectors)
            main.children.insert(0, Node(selectors={'article'}, children=[
                Node(role='link', href=f'/p/{WORK}/', selectors={'a[href]'})]))
        with patch.object(self.page, 'goto', side_effect=loading_target), self.assertRaises(VpubError):
            await self.execute()
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_target_permalink_outside_owned_article_cannot_bind_options(self):
        self.page.permalink = '/p/OtherOwned99/'
        original = self.page.goto
        async def outside(url, **kwargs):
            await original(url, **kwargs)
            main = next(n for n in self.page.tree.descendants() if 'main' in n.selectors)
            main.children.append(Node(role='link', href=f'/p/{WORK}/', selectors={'a[href]'}))
        with patch.object(self.page, 'goto', side_effect=outside), self.assertRaises(VpubError):
            await self.execute()
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_same_owner_other_article_does_not_supply_the_selected_controls(self):
        original = self.page.goto
        async def unrelated(url, **kwargs):
            await original(url, **kwargs)
            if '/p/' in urlsplit(url).path and not self.page.deleted:
                main = next(n for n in self.page.tree.descendants() if 'main' in n.selectors)
                main.children.insert(0, Node(selectors={'article'}, children=[
                    Node(role='link', href=f'/{OWNER}/', selectors={f'header a[href="/{OWNER}/"]'}),
                    Node(role='link', href='/p/OtherOwned99/', selectors={'a[href]'}),
                    Node('More options', role='button', event='WRONG-owned-options', action=lambda: self.fail('Wrong owned article'))]))
        with patch.object(self.page, 'goto', side_effect=unrelated):
            result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)

    async def test_missing_or_nonexact_permalink_refuses_before_options(self):
        for href in (None, f'/p/{WORK.lower()}/', f'/p/{WORK}extra/', f'https://other.invalid/p/{WORK}/',
                     f'/p/{WORK}/?other=1', f'/p/{WORK}/#other'):
            with self.subTest(href=href):
                self.reset_case()
                self.page.permalink = href
                with self.assertRaises(VpubError):
                    await self.execute()
                self.assertEqual([], self.page.events)
                self.unpressed(self.page)

    async def test_supported_exact_permalink_variants_bind_one_post(self):
        for href in (f'/reel/{WORK}/', f'/reels/{WORK}', f'/{OWNER}/p/{WORK}/',
                     f'https://www.instagram.com/p/{WORK}/'):
            with self.subTest(href=href):
                self.reset_case()
                self.page.permalink = href
                self.assertTrue(state.verified(await self.execute(), self.target))
                self.assertEqual(1, self.page.events.count('final-delete'))

    async def test_permalink_changed_on_same_node_blocks_each_control_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def change(page):
                    next(n for n in page.tree.descendants() if n.event == 'post-permalink').href = '/p/OtherOwned99/'
                armed, fired = self.at_boundary(stage, change)
                with armed, self.assertRaises(VpubError):
                    await self.execute()
                self.boundary_stopped(stage, fired)

    async def test_conflicting_permalink_in_same_scope_blocks_each_control_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def conflict(page):
                    article = next(n for n in page.tree.descendants() if 'article' in n.selectors)
                    article.children.append(Node(role='link', href='/p/OtherOwned99/', selectors={'a[href]'}))
                armed, fired = self.at_boundary(stage, conflict)
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_scope_with_target_and_other_post_permalink_refuses_initial_selection(self):
        original = self.page.goto
        async def conflict(url, **kwargs):
            await original(url, **kwargs)
            article = next(n for n in self.page.tree.descendants() if 'article' in n.selectors)
            article.children.append(Node(role='link', href='/p/OtherOwned99/', selectors={'a[href]'}))
        with patch.object(self.page, 'goto', side_effect=conflict), self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('wrong_work', caught.exception.code)
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_profile_identity_drift_during_count_read_is_not_accepted(self):
        self.page.present = False
        fired = []
        def after(kind, nodes):
            if kind == 'text' and not fired and any(n.text == '0 posts' for n in nodes):
                fired.append(True)
                next(n for n in self.page.tree.descendants() if n.name == 'Profile').href = '/someone_else/'
        self.page.after_read = after
        result = await self.execute('verify')
        self.assertTrue(fired)
        self.profile_pending(result)

    async def test_profile_origin_drift_after_scrolling_keeps_incomplete_evidence_false(self):
        self.page.present = False
        self.page.profile_message, self.page.counts = '', ['2 posts']
        original = self.page.wheel
        async def moved(x, y):
            await original(x, y)
            self.page.url = f'https://other.invalid/{OWNER}/'
        with patch.object(self.page, 'wheel', side_effect=moved):
            result = await self.execute('verify')
        self.assertEqual(1, self.page.scrolls)
        self.profile_pending(result)

    async def test_empty_profile_on_other_origin_is_not_verified(self):
        self.page.present = False
        self.page.profile_redirect = f'https://other.invalid/{OWNER}/'
        result = await self.execute('verify')
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'] or result['list_complete'])
        self.unpressed(self.page)

    async def test_unrelated_article_cannot_borrow_owner_from_main(self):
        original = self.page.goto
        async def separated(url, **kwargs):
            await original(url, **kwargs)
            main = next(n for n in self.page.tree.descendants() if 'main' in n.selectors)
            article = main.children[0]
            author = article.children.pop(0)
            main.children.insert(0, author)
        with patch.object(self.page, 'goto', side_effect=separated), self.assertRaises(VpubError):
            await self.execute()
        self.unpressed(self.page)

    async def test_main_fallback_without_articles_stays_bound_and_disposes_handles(self):
        original = self.page.goto
        async def no_articles(url, **kwargs):
            await original(url, **kwargs)
            for node in self.page.tree.descendants():
                node.selectors.discard('article')
        with patch.object(self.page, 'goto', side_effect=no_articles):
            result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(1, self.page.events.count('final-delete'))
        self.assertTrue(all(handle.disposed for handle in self.page.handles))

    async def test_wrong_scheme_or_authority_never_opens_menu(self):
        for address in (f'http://www.instagram.com/p/{WORK}/', f'https://www.instagram.com:444/p/{WORK}/'):
            with self.subTest(address=address):
                self.reset_case()
                self.page.exact_redirect = address
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.unpressed(self.page)

    async def test_wrong_account_is_refused_before_navigation(self):
        with self.assertRaises(VpubError) as caught:
            await self.execute(actual='someone_else')
        self.assertEqual('wrong_account', caught.exception.code)
        self.assertEqual([], self.page.visits)
        self.unpressed(self.page)

    async def test_wrong_exact_id_shortcode_case_or_host_is_refused(self):
        for address in (f'https://www.instagram.com/p/{WORK.lower()}/', 'https://www.instagram.com/p/OtherWork/',
                        f'https://other.invalid/p/{WORK}/'):
            with self.subTest(address=address):
                self.page = InstagramPage(self.marked)
                self.page.exact_redirect = address
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.unpressed(self.page)

    async def test_other_author_and_missing_edit_or_delete_cannot_authorize_deletion(self):
        self.page.owner = 'other'
        answer = await self.execute()
        self.assertFalse(answer['page_gone'])
        self.assertFalse(state.verified(answer, self.target))
        self.unpressed(self.page)
        for field in ('edit_available', 'delete_available'):
            with self.subTest(field=field):
                self.page = InstagramPage(self.marked)
                setattr(self.page, field, False)
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('layout_changed', caught.exception.code)
                self.unpressed(self.page)

    async def test_inspect_and_verify_only_check_owner_menu_then_escape(self):
        for operation in ('inspect', 'verify'):
            self.page = InstagramPage(self.marked)
            result = await self.execute(operation)
            self.assertTrue(result['owned'])
            self.assertTrue(result['present'])
            self.assertEqual(['target-options', 'escape'], self.page.events)
            self.unpressed(self.page)

    async def test_mark_precedes_delete_and_exact_confirmation_is_pressed_once(self):
        result = await self.execute()
        self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)
        self.assertEqual([f'https://www.instagram.com/p/{WORK}/'] * 2 + [f'https://www.instagram.com/{OWNER}/'], self.page.visits)
        self.assertEqual(1, result['platform_writes'])
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual('DELETED_VERIFIED', state.save_result(self.path, self.target, result)['state'])

    async def test_multiple_options_menus_dialogs_or_final_buttons_fail_closed(self):
        for field in ('options_count', 'menu_count', 'confirm_count', 'final_count'):
            with self.subTest(field=field):
                self.ready('instagram', WORK)
                self.page = InstagramPage(self.marked)
                setattr(self.page, field, 2)
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('layout_changed', caught.exception.code)
                self.assertNotIn('final-delete', self.page.events)
                self.assertEqual(field in {'confirm_count', 'final_count'}, read_json(self.path)['delete_attempted'])

    async def test_unrelated_confirmation_cannot_supply_final_delete(self):
        self.page.confirm_text = 'Delete account?'
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('layout_changed', caught.exception.code)
        self.assertNotIn('final-delete', self.page.events)
        self.marked()

    async def test_stop_appearing_before_final_preserves_pending_and_stop(self):
        self.page.stop_at_final = True
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('stopped', caught.exception.code)
        self.assertEqual(['target-options', 'menu-delete'], self.page.events)
        self.marked()
        self.assertTrue((home() / 'STOP.json').exists())

    async def test_uncertain_final_cannot_be_repeated_and_verify_has_no_delete(self):
        self.page.fail_final = True
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.marked()
        self.page = InstagramPage(self.marked)
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('deletion_already_attempted', caught.exception.code)
        self.assertEqual(['target-options'], self.page.events)
        self.page.events.clear()
        self.assertFalse(state.verified(await self.execute('verify'), self.target))
        self.assertEqual(['target-options', 'escape'], self.page.events)

    async def test_already_deleted_exact_post_and_empty_zero_profile_press_nothing(self):
        self.page.present = False
        result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_generic_unavailable_and_hostile_comment_are_not_gone_evidence(self):
        self.page.present = False
        self.page.exact_message = "Sorry, this page isn't available."
        self.page.hostile_comment = 'This post has been deleted.'
        result = await self.execute()
        self.assertFalse(result['page_gone'])
        self.assertFalse(state.verified(result, self.target))
        self.unpressed(self.page)

    async def test_partial_grid_or_target_still_in_grid_never_proves_deletion(self):
        for wid in ('DifferentPost', WORK):
            with self.subTest(wid=wid):
                self.ready('instagram', WORK)
                self.page = InstagramPage(self.marked)
                self.page.profile_message = ''
                self.page.counts = ['2 posts']
                self.page.profile_links = [f'https://www.instagram.com/p/{wid}/']
                result = await self.execute()
                self.assertFalse(result['list_complete'])
                self.assertEqual(wid != WORK, result['list_absent'])
                self.assertFalse(state.verified(result, self.target))
                self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, result)['state'])
                self.assertEqual(1, self.page.events.count('final-delete'))

    async def test_exact_post_remaining_after_press_reports_present_and_pending(self):
        self.page.stays = True
        result = await self.execute()
        self.assertTrue(result['present'] and result['owned'])
        self.assertFalse(state.verified(result, self.target))
        self.assertEqual(1, self.page.events.count('final-delete'))

    async def test_own_empty_profile_offering_first_photo_counts_as_complete(self):
        # A real own profile with nothing left showed "0 posts" and "Share your first photo", never "No Posts Yet".
        self.page.present = False
        self.page.profile_message, self.page.own_empty_button = 'Getting Started', 'Share your first photo'
        result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.unpressed(self.page)

    async def test_first_photo_offer_without_exact_zero_or_with_grid_links_stays_pending(self):
        for counts, links in [(['1 post'], []), ([], []), (['0 posts'], ['/p/OTHER123/'])]:
            with self.subTest(counts=counts, links=links):
                self.page = InstagramPage(self.marked)
                self.page.present = False
                self.page.profile_message, self.page.own_empty_button = '', 'Share your first photo'
                self.page.counts, self.page.profile_links = counts, links
                self.assertFalse(state.verified(await self.execute(), self.target))
                self.unpressed(self.page)

    async def test_empty_words_without_exact_zero_count_or_on_wrong_profile_stay_pending(self):
        for counts, redirect in [([], None), (['1 post'], None), (['0 posts', '0 posts'], None), (['0 posts'], 'https://www.instagram.com/other/')]:
            with self.subTest(counts=counts, redirect=redirect):
                self.page = InstagramPage(self.marked)
                self.page.present = False
                self.page.counts, self.page.profile_redirect = counts, redirect
                self.assertFalse(state.verified(await self.execute(), self.target))
                self.unpressed(self.page)

    async def test_hidden_empty_state_is_not_complete_deletion_proof(self):
        for field, proof in [('exact_message_visible', 'page_gone'), ('profile_message_visible', 'list_complete'),
                             ('count_visible', 'list_complete')]:
            with self.subTest(hidden=field):
                self.page = InstagramPage(self.marked)
                self.page.present = False
                setattr(self.page, field, False)
                result = await self.execute()
                self.assertFalse(result[proof])
                self.assertFalse(state.verified(result, self.target))
                self.unpressed(self.page)


if __name__ == '__main__':
    unittest.main()
