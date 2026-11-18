"""Offline reproductions for November 17 defects 6-8; no browser is launched."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_work_delete_youtube as deletion
import test_youtube_browser as studio
from video_publisher import browser_forms as forms, work_delete_youtube as route
from video_publisher import audit_export
from video_publisher.core import VpubError, atomic_json, home


class DeletionDiagnosticsTests(deletion.OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('youtube', deletion.VIDEO, deletion.CHANNEL)
        self.page = deletion.FakeYouTube(self.target)
        self.page.deleted = True

    async def check(self, reason=None):
        result = await route.readback(self.page, self.target)
        self.assertTrue(result['page_gone'])
        self.assertEqual(reason is None, result['list_complete'], result)
        self.assertEqual(reason is None, result['list_absent'], result)
        self.assertEqual(0, result['platform_writes'])
        self.assert_unpressed()
        if reason:
            self.assertIn(reason, result['note'])
        self.assertNotIn('Private draft words', result['note'])
        self.assertNotIn(deletion.OTHER, result['note'])
        return result

    async def test_unresolved_draft_reproduces_pending_even_on_terminal_page(self):
        self.page.pages['upload'] = [[(None, 'Private draft words')]]
        await self.check('unresolved_rows')

    async def test_draft_with_id_but_no_extracted_title_is_incomplete(self):
        self.page.pages['upload'] = [[(deletion.OTHER, '')]]
        await self.check('blank_titles')

    async def test_fully_identified_draft_does_not_prevent_absence(self):
        self.page.pages['upload'] = [[(deletion.OTHER, 'Private draft words')]]
        await self.check()

    async def test_missing_paginator_does_not_prove_absence(self):
        self.page.pages['upload'] = [[(deletion.OTHER, 'Private draft words')]]
        self.page.pager = False
        await self.check('pagination_control_missing')

    async def test_repeated_page_is_not_a_complete_list(self):
        self.page.pages['upload'] = [[(deletion.OTHER, 'Words')], [(deletion.OTHER, 'Words')]]
        await self.check('repeated_ids')

    async def test_unresolved_draft_on_second_page_stays_incomplete(self):
        self.page.pages['upload'] = [[(deletion.OTHER, 'Words')], [(None, 'Private draft words')]]
        await self.check('unresolved_rows')

    async def test_complete_two_page_list_and_empty_short_tab(self):
        self.page.pages['upload'] = [[(deletion.OTHER, 'First')], [('draftId0001', 'Second')]]
        await self.check()

    async def test_empty_and_unclean_states_have_distinct_diagnostics(self):
        self.page.empty_panel = False
        await self.check('empty_unconfirmed')
        self.page.empty_panel = True
        self.page.clean = False
        await self.check('list_not_clean')

    async def test_target_found_on_later_page_is_never_absent(self):
        self.page.keep_target = True
        self.page.pages['upload'] = [[(deletion.OTHER, 'First')], [(deletion.VIDEO, 'Approved')]]
        result = await route.readback(self.page, self.target)
        self.assertTrue(result['present'])
        self.assertFalse(result['list_absent'])
        self.assertFalse(result['page_gone'])
        self.assert_unpressed()

    async def test_page_cap_diagnostic_keeps_absence_unverified(self):
        self.page.pages['upload'] = [[(f'video{i:06d}', f'Row {i}')] for i in range(21)]
        await self.check('page_limit')


class FormReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        env = patch.dict(os.environ, {name: str(root / name) for name in ('VPUB_HOME', 'BILI_CLI_HOME', 'ROUND2_HOME')})
        env.start(); self.addCleanup(env.stop)
        self.page = studio.FakeStudio(is_draft=True)
        self.tx = SimpleNamespace(options={'title': 'My title', 'description': 'Text'}, receipt={})
        self.tx.save = lambda **changes: self.tx.receipt.update(changes)
        self.final = AsyncMock(return_value={'state': 'synthetic_finished'})
        self.risk = AsyncMock()
        for p in (patch.object(forms, 'yt_final_steps', self.final), patch.object(forms, 'risk_check', self.risk),
                  patch('socket.create_connection', side_effect=AssertionError('Network forbidden'))):
            p.start(); self.addCleanup(p.stop)

    def matching(self):
        self.page.text.update({'#title-textarea #textbox': 'My title', '#description-textarea #textbox': 'Text'})

    async def finish(self):
        return await forms.yt_finish_draft(self.page, self.tx, studio.VIDEO)

    def untouched(self):
        self.assertEqual([], self.page.selected_files())
        self.assertFalse(any(e[0] == 'fill' for e in self.page.log))

    async def test_visible_empty_fields_populate_later_without_false_mismatch(self):
        waits = []
        async def wait(ms):
            waits.append(ms)
            if len(waits) == 3:
                self.matching()
        self.page.wait_for_timeout = wait
        self.assertEqual({'state': 'synthetic_finished'}, await self.finish())
        self.assertGreaterEqual(len(waits), 4)
        self.final.assert_awaited_once()
        self.untouched()

    async def test_delayed_description_is_checked_as_well_as_title(self):
        self.page.text['#title-textarea #textbox'] = 'My title'
        async def wait(ms): self.matching()
        self.page.wait_for_timeout = wait
        await self.finish()
        self.final.assert_awaited_once()
        self.untouched()

    async def test_permanent_mismatch_is_bounded_and_never_submits(self):
        self.matching(); self.page.text['#title-textarea #textbox'] = 'Private unrelated words'
        waits = []
        async def wait(ms): waits.append(ms)
        self.page.wait_for_timeout = wait
        with self.assertRaises(VpubError) as caught: await self.finish()
        self.assertEqual('metadata_mismatch', caught.exception.code)
        self.assertEqual(20000, sum(waits))
        self.assertNotIn('Private unrelated words', str(self.tx.receipt))
        self.assertFalse(self.tx.receipt['draft_metadata_check']['title_matches'])
        self.final.assert_not_awaited(); self.untouched()

    async def test_one_matching_sample_followed_by_change_never_submits(self):
        self.matching()
        async def wait(ms): self.page.text['#description-textarea #textbox'] = 'Changed'
        self.page.wait_for_timeout = wait
        with self.assertRaises(VpubError): await self.finish()
        self.final.assert_not_awaited(); self.untouched()

    async def test_empty_planned_description_is_valid(self):
        self.tx.options['description'] = ''
        self.matching(); self.page.text['#description-textarea #textbox'] = ''
        await self.finish()
        self.final.assert_awaited_once(); self.untouched()

    async def test_id_drift_during_population_is_refused(self):
        async def wait(ms):
            self.matching(); self.page.link = 'https://youtu.be/otherId0001'
        self.page.wait_for_timeout = wait
        with self.assertRaises(VpubError) as caught: await self.finish()
        self.assertEqual('wrong_work', caught.exception.code)
        self.final.assert_not_awaited(); self.untouched()

    async def test_stop_during_population_is_honored(self):
        async def wait(ms): atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop'})
        self.page.wait_for_timeout = wait
        with self.assertRaises(VpubError) as caught: await self.finish()
        self.assertNotEqual('metadata_mismatch', caught.exception.code)
        self.final.assert_not_awaited(); self.untouched()

    async def test_challenge_during_population_is_not_swallowed(self):
        self.risk.side_effect = [None, VpubError('human_verification_required', 'Synthetic challenge')]
        with self.assertRaises(VpubError) as caught: await self.finish()
        self.assertEqual('human_verification_required', caught.exception.code)
        self.final.assert_not_awaited()

    async def test_missing_upload_picker_has_specific_error_before_media_selection(self):
        picker = SimpleNamespace(count=AsyncMock(return_value=0))
        self.page.locator = lambda selector: picker
        with self.assertRaises(VpubError) as caught: await forms.douyin_upload_picker(self.page, self.tx)
        self.assertEqual('upload_picker_unavailable', caught.exception.code)
        self.assertEqual('waiting_for_upload_picker', self.tx.receipt['phase'])
        self.assertFalse(self.tx.receipt['upload_picker_check']['found'])
        self.assertNotIn('media_selected', self.tx.receipt)

    async def test_delayed_upload_picker_is_read_without_selecting_media(self):
        picker = SimpleNamespace(count=AsyncMock(side_effect=[0, 0, 1]))
        self.page.locator = lambda selector: picker
        await forms.douyin_upload_picker(self.page, self.tx)
        self.assertTrue(self.tx.receipt['upload_picker_check']['found'])
        self.assertNotIn('media_selected', self.tx.receipt)

    async def test_stop_during_picker_wait_is_honored(self):
        self.page.locator = lambda selector: SimpleNamespace(count=AsyncMock(return_value=0))
        async def wait(ms): atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop'})
        self.page.wait_for_timeout = wait
        with self.assertRaises(VpubError) as caught: await forms.douyin_upload_picker(self.page, self.tx)
        self.assertNotEqual('upload_picker_unavailable', caught.exception.code)

    async def test_publish_calls_picker_guard_before_upload(self):
        self.tx.identity = 'synthetic_owner'
        self.tx.resuming = False
        self.tx.options.update(tags=[])
        self.tx.media = lambda: self.fail('No media selection allowed')
        self.page.locator = lambda selector: SimpleNamespace(count=AsyncMock(return_value=0))
        with patch.object(forms, 'works', AsyncMock(return_value={})), patch.object(forms, 'douyin_cards_titled', AsyncMock(return_value=[])):
            with self.assertRaises(VpubError) as caught: await forms.publish_douyin(self.page, self.tx)
        self.assertEqual('upload_picker_unavailable', caught.exception.code)
        self.assertEqual('waiting_for_upload_picker', self.tx.receipt['phase'])


class ExportDiagnosisTests(unittest.TestCase):
    def test_specific_codes_survive_but_arbitrary_failure_contents_do_not(self):
        for code in ('browser_step_failed', 'metadata_mismatch', 'upload_picker_unavailable'):
            event = {'type': 'transition', 'phase': 'waiting_for_upload_picker', 'error_code': code,
                     'message': 'Private page contents', 'title': 'Private title', 'cookies': 'Secret'}
            exported = audit_export.project_event(event, lambda at: at)
            self.assertEqual(code, exported['error_code'])
            self.assertEqual('waiting_for_upload_picker', exported['phase'])
            self.assertNotIn('Private', str(exported))
            self.assertNotIn('Secret', str(exported))
        exported = audit_export.project_event({'type': 'transition', 'error_code': 'Private contents'}, lambda at: at)
        self.assertEqual('other', exported['error_code'])
