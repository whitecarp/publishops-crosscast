"""Independent confirmation-sheet races; real route, dynamic DOM, fake clock."""
from copy import copy
import unittest
from unittest.mock import patch

import test_work_delete_x as fixtures
from video_publisher import browser_runtime as runtime, work_delete_state as state, work_delete_x as route
from video_publisher.core import VpubError, atomic_json, home, read_json


class DialogPage(fixtures.XPage):
    """Only confirmation time advances; fixture locators and handles stay dynamic."""

    def __init__(self, marked, appear_at=500, roles=('dialog', 'alertdialog')):
        super().__init__(marked)
        self.appear_at, self.roles = appear_at, roles
        self.confirming = False
        self.elapsed, self.waits = 0, []
        self.wrappers, self.finals = [], []
        self.on_wait = self.on_render = None
        self.rendered = False

    def ask(self):
        self.marked()
        self.confirming = True
        self.render_if_ready()

    def render_if_ready(self):
        if self.rendered or self.appear_at is None or self.elapsed < self.appear_at:
            return
        self.rendered = True
        self.finals = [fixtures.Node(self.final_text, selectors={route.FINAL},
                                    event='final-delete', action=self.remove)
                       for _ in range(self.final_count)]
        children = self.finals
        for role in reversed(self.roles):
            wrapper = fixtures.Node(role=role, children=children)
            self.wrappers.insert(0, wrapper)
            children = [wrapper]
        self.tree.children.extend(children)
        if self.on_render:
            self.on_render(self)

    async def wait_for_timeout(self, milliseconds):
        if self.confirming:
            self.waits.append(milliseconds)
            self.elapsed += milliseconds
            self.render_if_ready()
            if self.on_wait:
                self.on_wait(self)

    async def goto(self, url, **kwargs):
        self.confirming = False
        await super().goto(url, **kwargs)

    def replace(self, old, replacement):
        for parent in [self.tree, *self.tree.descendants()]:
            for index, child in enumerate(parent.children):
                if child is old:
                    parent.children[index:index + 1] = replacement
                    return
        raise AssertionError('Mutation must reach an attached node')


class XDialogRegressions(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()
        self.reset_dialog()

    def reset_dialog(self, appear_at=500, roles=('dialog', 'alertdialog')):
        self.ready('x', fixtures.WORK)
        self.page = DialogPage(self.marked, appear_at, roles)

    async def execute(self, operation='delete'):
        return await route.execute(self.page, fixtures.OWNER, self.target, operation, str(self.path))

    def disposed(self):
        self.assertTrue(self.page.handles)
        self.assertTrue(all(handle.disposed for handle in self.page.handles))

    def pending(self, final_presses=0):
        self.assertEqual(['target-more', 'menu-delete'] + ['final-delete'] * final_presses,
                         self.page.events)
        self.marked()
        self.disposed()

    async def rejected(self, code='wrong_work'):
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual(code, error.exception.code)
        self.pending()

    async def cannot_retry(self):
        before = self.path.read_bytes()
        with self.assertRaises(VpubError) as error:
            state.begin_press(self.path, self.target)
        self.assertEqual('deletion_already_attempted', error.exception.code)
        self.assertEqual(before, self.path.read_bytes())
        self.page = DialogPage(self.marked, 0)
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('deletion_already_attempted', error.exception.code)
        self.assertEqual(['target-more'], self.page.events)
        self.disposed()
        self.assertEqual(before, self.path.read_bytes())
        self.page.events.clear()
        result = await self.execute('verify')
        self.assertFalse(state.verified(result, self.target))
        self.assertEqual([], self.page.events)
        self.assertEqual(before, self.path.read_bytes())

    def after_capture(self, mutate):
        """Mutate at the first guard after the sheet is available, even if late.

        Assert capture happened *before* this guard: a guard-first poll could
        silently bind the replacement and these tests must catch that ordering.
        """
        original = runtime.risk_check
        fired = []

        async def risk(page):
            await original(page)
            if page is not self.page or not page.confirming or not page.rendered or fired:
                return
            retained = [handle.nodes[0] for handle in page.handles]
            self.assertTrue(all(node in retained for node in [*page.finals, *page.wrappers]),
                            'The first ready sheet must be retained before its guard')
            fired.append(page.elapsed)
            mutate(page)

        return patch.object(runtime, 'risk_check', side_effect=risk), fired

    async def test_ready_at_zero_delayed_and_exact_deadline_presses_final_once(self):
        for delay in (0, 500, 3500, 10000):
            with self.subTest(delay=delay):
                self.reset_dialog(delay)
                result = await self.execute()
                self.assertEqual([500] * (delay // 500), self.page.waits)
                self.assertEqual(1, result['platform_writes'])
                self.assertTrue(state.verified(result, self.target))
                self.pending(final_presses=1)
                self.assertEqual('DELETED_VERIFIED', state.save_result(self.path, self.target, result)['state'])

    async def test_never_ready_or_after_deadline_stops_at_ten_seconds_and_cannot_retry(self):
        for delay in (None, 10500):
            with self.subTest(delay=delay):
                self.reset_dialog(delay)
                await self.rejected('layout_changed')
                self.assertEqual([500] * 20, self.page.waits)
                self.assertFalse(self.page.rendered)
                saved = state.save_result(self.path, self.target, error={'code': 'layout_changed'})
                self.assertEqual('DELETE_PENDING', saved['state'])
                self.assertTrue(saved['delete_attempted'])
                await self.cannot_retry()

    async def test_all_nested_dialog_and_alertdialog_ancestors_are_retained(self):
        for roles in (('dialog',), ('alertdialog',), ('dialog', 'dialog'),
                      ('alertdialog', 'dialog', 'alertdialog')):
            with self.subTest(roles=roles):
                self.reset_dialog(500, roles)
                armed, fired = self.after_capture(lambda page: None)
                with armed:
                    result = await self.execute()
                self.assertEqual([500], fired)
                self.assertTrue(state.verified(result, self.target))
                self.pending(final_presses=1)

    async def test_final_without_wrapper_waits_until_wrapper_is_ready(self):
        self.reset_dialog(0, ())

        def wrap(page):
            if page.elapsed == 1000:
                wrapper = fixtures.Node(role='alertdialog', children=page.finals)
                page.replace(page.finals[0], [wrapper])
                page.wrappers = [wrapper]

        self.page.on_wait = wrap
        result = await self.execute()
        self.assertEqual([500, 500], self.page.waits)
        self.assertTrue(state.verified(result, self.target))
        self.pending(final_presses=1)

    async def test_unwrapped_final_never_becomes_ready_or_clicks(self):
        self.reset_dialog(0, ())
        await self.rejected('layout_changed')
        self.assertEqual([500] * 20, self.page.waits)
        await self.cannot_retry()

    async def test_hidden_duplicates_and_hidden_unrelated_wrappers_are_not_candidates(self):
        def hidden(page):
            page.tree.children.extend([
                fixtures.Node(role='dialog', visible=False, children=[
                    fixtures.Node('Delete', selectors={route.FINAL}, event='WRONG-hidden')]),
                fixtures.Node(role='alertdialog', visible=False),
                fixtures.Node('Delete', selectors={route.FINAL}, visible=False, event='WRONG-hidden'),
            ])

        self.page.on_render = hidden
        result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.pending(final_presses=1)

    async def test_duplicate_visible_final_in_same_or_separate_wrapper_is_refused(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                self.reset_dialog(10000)
                if separate:
                    def duplicate(page):
                        page.tree.children.append(fixtures.Node(role='dialog', children=[
                            fixtures.Node('Delete', selectors={route.FINAL}, event='WRONG-duplicate')]))
                    self.page.on_render = duplicate
                else:
                    self.page.final_count = 2
                await self.rejected('layout_changed')
                self.assertEqual(10000, self.page.elapsed)
                await self.cannot_retry()

    async def test_unrelated_visible_dialog_alertdialog_or_menu_refuses_final(self):
        for role in ('dialog', 'alertdialog', 'menu'):
            with self.subTest(role=role):
                self.reset_dialog()
                self.page.on_render = lambda page: page.tree.children.append(fixtures.Node(role=role))
                await self.rejected('layout_changed')
                await self.cannot_retry()

    async def test_delayed_wrapper_replacement_removal_insertion_and_hiding_fail_closed(self):
        for delay in (0, 1500, 10000):
            for change in ('replace-outer', 'replace-inner', 'remove-outer', 'remove-inner',
                           'insert-outer', 'insert-inner', 'hide-inner'):
                with self.subTest(delay=delay, change=change):
                    self.reset_dialog(delay)

                    def mutate(page):
                        old = page.wrappers[-1 if change.endswith('inner') else 0]
                        if change.startswith('replace'):
                            page.replace(old, [copy(old)])
                        elif change.startswith('remove'):
                            page.replace(old, old.children)
                        elif change.startswith('insert'):
                            page.replace(old, [fixtures.Node(role='dialog', children=[old])])
                        else:
                            # Leave the final visible by moving it outside this wrapper.
                            page.replace(old, [old, *old.children])
                            old.children = []
                            old.visible = False

                    armed, fired = self.after_capture(mutate)
                    with armed:
                        await self.rejected()
                    self.assertEqual([delay], fired)

    async def test_same_wrappers_without_containment_are_rejected(self):
        def move(page):
            outer, inner = page.wrappers
            outer.children = []
            page.tree.children.append(inner)

        armed, fired = self.after_capture(move)
        with armed:
            await self.rejected('layout_changed')
        self.assertEqual([500], fired)

    async def test_delayed_final_replacement_at_first_ready_guard_is_rejected(self):
        for delay in (0, 1500, 10000):
            with self.subTest(delay=delay):
                self.reset_dialog(delay)

                def replace(page):
                    old = page.finals[0]
                    replacement = copy(old)
                    replacement.event = 'WRONG-replacement'
                    page.replace(old, [replacement])

                armed, fired = self.after_capture(replace)
                with armed:
                    await self.rejected()
                self.assertEqual([delay], fired)
                await self.cannot_retry()

    async def test_duplicate_inserted_after_capture_is_not_a_final_press(self):
        armed, fired = self.after_capture(
            lambda page: page.wrappers[-1].children.append(copy(page.finals[0])))
        with armed:
            await self.rejected('layout_changed')
        self.assertEqual([500], fired)

    async def test_retained_final_handle_does_not_retarget_at_click(self):
        fired = []

        def replace(handle):
            old = handle.nodes[0]
            if old.event == 'final-delete':
                fired.append(True)
                replacement = copy(old)
                replacement.event = 'WRONG-replacement'
                self.page.replace(old, [replacement])

        self.page.before_handle_click = replace
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.assertEqual([True], fired)
        self.pending()
        await self.cannot_retry()

    async def test_account_missing_or_changed_during_wait_even_at_deadline_refuses_final(self):
        for delay in (500, 10000):
            for missing in (False, True):
                with self.subTest(delay=delay, missing=missing):
                    self.reset_dialog(10000)

                    def drift(page):
                        if page.elapsed == delay:
                            profile = next(node for node in page.tree.descendants() if node.name == 'Profile')
                            if missing:
                                page.replace(profile, [])
                            else:
                                profile.href = '/someone_else/'

                    self.page.on_wait = drift
                    await self.rejected('wrong_account')
                    self.assertEqual(delay, self.page.elapsed)
                    await self.cannot_retry()

    async def test_url_drift_during_wait_even_at_deadline_refuses_final(self):
        exact = f'https://x.com/{fixtures.OWNER}/status/{fixtures.WORK}'
        for delay in (500, 10000):
            for address in (exact + '?other=1', exact + '#other', exact.replace('https:', 'http:'),
                            exact.replace('x.com', 'other.invalid'), exact.replace(fixtures.OWNER, 'someone_else'),
                            exact.replace(fixtures.WORK, '2111111111111111112')):
                with self.subTest(delay=delay, address=address):
                    self.reset_dialog(10000)
                    def drift(page):
                        if page.elapsed == delay:
                            page.url = address
                    self.page.on_wait = drift
                    await self.rejected('wrong_work')
                    self.assertEqual(delay, self.page.elapsed)

    async def test_stop_during_wait_even_at_deadline_preserves_pending_marker(self):
        for delay in (500, 10000):
            with self.subTest(delay=delay):
                self.reset_dialog(10000)
                def stop(page):
                    if page.elapsed == delay:
                        atomic_json(home() / 'STOP.json', {'reason': 'Synthetic confirmation wait STOP'})
                self.page.on_wait = stop
                try:
                    await self.rejected('stopped')
                    self.assertEqual(delay, self.page.elapsed)
                    before = self.path.read_bytes()
                    with self.assertRaises(VpubError) as error:
                        state.begin_press(self.path, self.target)
                    self.assertEqual('deletion_already_attempted', error.exception.code)
                    self.assertEqual(before, self.path.read_bytes())
                    self.assertEqual('Synthetic confirmation wait STOP', read_json(home() / 'STOP.json')['reason'])
                finally:
                    # This is the synthetic STOP in OfflineCase's disposable home.
                    (home() / 'STOP.json').unlink(missing_ok=True)

    async def test_final_acknowledgement_loss_has_one_press_and_durable_no_retry(self):
        self.page.fail_final = True
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.pending(final_presses=1)
        result = state.save_result(self.path, self.target, error={'code': 'TimeoutError'})
        self.assertEqual(('DELETE_PENDING', True), (result['state'], result['delete_attempted']))
        await self.cannot_retry()


if __name__ == '__main__':
    unittest.main()
