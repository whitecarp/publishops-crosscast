"""C52 regression probes: isolated stores and fake platform boundaries only."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, audit_export, audit_journal as journal, browser_edits, cli, core


class PresealTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='c52-offline-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, VPUB_HOME=str(self.root / 'vpub'), BILI_CLI_HOME=str(self.root / 'bili'))
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, journal, 'RUN', None)
        self.directory = core.home() / 'audit'

    def seed(self):
        self.assertTrue(journal.append('stage', lambda _: dict(stage='uploading')))

    def test_stop_is_latched_before_audit_lock_failure(self):
        observed = []
        def locked(*args, **kwargs):
            observed.append((core.home() / 'STOP.json').exists())
            raise core.VpubError('audit_busy', 'synthetic lock')
        with patch.object(journal, 'brief_lock', side_effect=locked), patch.object(sys, 'argv', ['vpub', 'stop']), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                cli.main()
        self.assertEqual(0, result.exception.code)
        self.assertEqual([True, True], observed)

    def test_platform_stop_precedes_exit_audit_lock_failure(self):
        observed = []
        def locked(*args, **kwargs):
            observed.append((core.home() / 'STOP.json').exists())
            raise core.VpubError('audit_busy', 'synthetic lock')
        with patch.object(journal, 'brief_lock', side_effect=locked), patch.object(adapters, 'browser_answer', side_effect=core.VpubError('human_verification_required', 'fake')):
            with self.assertRaises(core.VpubError) as result:
                adapters.browser_call('youtube', 'publish', 'stand-in')
        self.assertEqual('human_verification_required', result.exception.code)
        self.assertTrue(observed)
        self.assertTrue(all(observed))

    def test_edit_stop_during_marker_prevents_click_with_marker_preserved(self):
        self.edit_probe(stop=True)

    def test_edit_click_has_durable_marker_before_delivery(self):
        self.edit_probe(stop=False)

    def edit_probe(self, stop):
        output = self.root / 'edit.json'
        editor = AsyncMock()
        editor.inner_text.side_effect = ['old', 'new']
        button = AsyncMock()
        stages = []
        def stage(word):
            stages.append(word)
            if stop and word == 'pressing':
                core.atomic_json(core.home() / 'STOP.json', {'reason': 'synthetic'})
        async def click(**kwargs):
            self.assertEqual('EDIT_SUBMITTING', core.read_json(output)['state'])
            self.assertEqual('pressing', stages[-1])
            raise TimeoutError('synthetic delivered click')
        button.click.side_effect = click
        payload = dict(patch={}, receipt={'id': 'fake'}, edit_receipt_path=str(output))
        with patch.object(browser_edits, 'instagram_caption_editor', AsyncMock(return_value=editor)), patch.object(browser_edits, 'words_to_save', return_value='new'), patch.object(browser_edits, 'unique_visible', AsyncMock(return_value=button)), patch.object(browser_edits, 'risk_check', AsyncMock()), patch.object(browser_edits, 'stage', stage):
            with self.assertRaises(core.VpubError if stop else TimeoutError):
                asyncio.run(browser_edits.execute(MagicMock(), 'instagram', 'fake', payload))
        self.assertEqual('EDIT_SUBMITTING', core.read_json(output)['state'])
        self.assertEqual(0 if stop else 1, button.click.await_count)

    def test_missing_or_truncated_journal_cannot_pass_retained_head(self):
        self.seed()
        path = self.directory / 'journal.jsonl'
        original = path.read_bytes()
        for data in [None, b'', original[:len(original)//2]]:
            with self.subTest(data=data is None):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
                result = audit_export.export_data()['integrity']
                self.assertFalse(result['chain_ok'])
                self.assertTrue(result['chain_error'])

    def test_malformed_heads_are_reported_without_crashing(self):
        self.seed()
        for value in ['{', '[]', '5', '{}', 'null', '[true,"bad",0]']:
            with self.subTest(value=value):
                (self.directory / 'head.json').write_text(value)
                self.assertFalse(journal.verify_chain(self.directory)['ok'])
                self.assertFalse(audit_export.export_data()['integrity']['chain_ok'])

    def test_empty_new_journal_and_zero_head_are_valid(self):
        self.assertTrue(audit_export.export_data()['integrity']['chain_ok'])
        self.directory.mkdir()
        (self.directory / 'head.json').write_text(json.dumps([0, journal.ZERO, 0]))
        self.assertTrue(audit_export.export_data()['integrity']['chain_ok'])

    def test_lost_salt_is_not_regenerated_or_history_appended(self):
        self.seed()
        before = (self.directory / 'journal.jsonl').read_bytes()
        (self.directory / 'salt').unlink()
        self.assertFalse(journal.append('stage', lambda _: dict(stage='pressing')))
        self.assertFalse((self.directory / 'salt').exists())
        self.assertEqual(before, (self.directory / 'journal.jsonl').read_bytes())

    def test_bounded_export_without_salt_never_matches_null_references(self):
        ledger = core.Ledger()
        self.addCleanup(ledger.close)
        for i in range(3):
            ledger.claim('youtube', 'synthetic', str(i), {})
        (self.directory / 'salt').unlink()
        for bounds in [dict(since='2020-01-01T00:00:00Z'), dict(until='2099-01-01T00:00:00Z')]:
            with self.subTest(bounds=bounds):
                result = audit_export.export_data(**bounds)
                self.assertEqual([], result['publications'])
                self.assertEqual([], result['deletions'])


if __name__ == '__main__':
    unittest.main()
