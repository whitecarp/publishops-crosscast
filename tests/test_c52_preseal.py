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
        self.assertTrue(journal.append('transition', lambda ref: dict(ref=ref('a' * 64))))
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

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['vpub', *args]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                cli.main()
        return result.exception.code, json.loads(output.getvalue())

    def legacy_keyless(self):
        # The old implementation did not create a salt until record_ref ran.
        with patch.object(journal, 'ensure_salt'):
            self.assertEqual(0, self.run_cli('safety', 'status')[0])
        self.assertFalse((self.directory / 'salt').exists())

    def test_keyless_legacy_upgrade_preserves_chain_without_losses(self):
        self.legacy_keyless()
        before = (self.directory / 'journal.jsonl').read_bytes()
        before_export = audit_export.export_data()
        self.assertFalse(before_export['integrity']['salt_ok'])
        self.assertEqual('missing_salt_with_existing_history', before_export['integrity']['salt_error'])
        self.assertFalse((self.directory / 'salt').exists())  # Export is read-only.
        self.assertEqual(0, self.run_cli('safety', 'status')[0])
        self.assertEqual(32, len((self.directory / 'salt').read_bytes()))
        self.assertTrue((self.directory / 'journal.jsonl').read_bytes().startswith(before))
        output = self.root / 'export.json'
        self.assertEqual(0, self.run_cli('audit', 'export', '--out', str(output))[0])
        exported = json.loads(output.read_bytes())
        self.assertTrue(exported['integrity']['chain_ok'])
        self.assertTrue(exported['integrity']['salt_ok'])
        self.assertIsNone(exported['integrity']['salt_error'])
        self.assertEqual([], exported['losses'])
        self.assertEqual(0, exported['integrity']['dropped_events'])

    def test_first_ledger_claim_after_keyless_upgrade_gets_salt(self):
        self.legacy_keyless()
        ledger = core.Ledger()
        self.addCleanup(ledger.close)
        key = ledger.claim('youtube', 'synthetic', 'b' * 64, {})
        self.assertIsNotNone(key)
        result = audit_export.export_data()
        self.assertTrue(result['integrity']['salt_ok'])
        self.assertEqual(0, result['integrity']['dropped_events'])
        self.assertRegex(result['publications'][0]['ref'], r'^[a-f0-9]{16}$')

    def test_lost_salt_after_journal_damage_stays_missing_after_restore(self):
        self.legacy_keyless()
        journal_path = self.directory / 'journal.jsonl'
        head_path = self.directory / 'head.json'
        salt_path = self.directory / 'salt'
        prefix = journal_path.read_bytes()
        ledger = core.Ledger()
        self.addCleanup(ledger.close)
        key = ledger.claim('youtube', 'synthetic', 'b' * 64, {})
        self.assertTrue(journal.append('observation', lambda ref: dict(ref=ref(key), seen='absent')))
        core.atomic_json(core.home() / 'work-deletions' / (key + '.json'), {'state': 'DELETE_PENDING'})
        window = dict(since='2020-01-01T00:00:00Z')
        baseline = audit_export.export_data(**window)
        self.assertEqual((1, 1), (len(baseline['publications']), len(baseline['deletions'])))
        original = journal_path.read_bytes()
        retained_head = head_path.read_bytes()
        salt_path.unlink()

        # Construct an internally consistent keyless chain of the same length.
        # Only comparing its final digest to the retained head detects the swap.
        replacement = bytearray()
        previous = journal.ZERO
        for line in original.splitlines(keepends=True):
            event = json.loads(line)
            line = line.replace(event['prev'].encode(), previous.encode()).replace(b'"ref":', b'"tag":')
            replacement.extend(line)
            previous = journal.digest(line)
        self.assertEqual(len(original), len(replacement))
        self.assertNotEqual(json.loads(retained_head)[1], previous)

        for label, data in [('empty', b''), ('legacy prefix', prefix), ('same size', replacement)]:
            with self.subTest(damage=label):
                journal_path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, '^missing_salt_with_existing_history$'):
                    journal.ensure_salt(self.directory)
                self.assertFalse(salt_path.exists())
                prior_losses = len(list((self.directory / 'losses').glob('*.json')))
                self.assertEqual(0, self.run_cli('safety', 'status')[0])
                self.assertFalse(salt_path.exists())
                self.assertEqual(data, journal_path.read_bytes())
                self.assertEqual(retained_head, head_path.read_bytes())
                journal_path.write_bytes(original)
                restored = audit_export.export_data(**window)
                self.assertTrue(restored['integrity']['chain_ok'])
                self.assertFalse(restored['integrity']['salt_ok'])
                self.assertEqual('missing_salt_with_existing_history', restored['integrity']['salt_error'])
                self.assertIsNone(restored['site'])
                self.assertEqual([], restored['publications'])
                self.assertEqual([], restored['deletions'])
                self.assertEqual(['journal_read_failed'] * (prior_losses + 2),
                                 [loss['reason'] for loss in restored['losses']])

    def test_keyless_upgrade_recovers_crash_tail_beyond_retained_head(self):
        self.legacy_keyless()
        head_path = self.directory / 'head.json'
        journal_path = self.directory / 'journal.jsonl'
        retained_head = head_path.read_bytes()
        self.legacy_keyless()
        before = journal_path.read_bytes()
        head_path.write_bytes(retained_head)
        self.assertEqual(0, self.run_cli('safety', 'status')[0])
        self.assertEqual(32, len((self.directory / 'salt').read_bytes()))
        self.assertTrue(journal_path.read_bytes().startswith(before))
        chain = journal.verify_chain(self.directory)
        self.assertTrue(chain['ok'])
        self.assertEqual(len(before.splitlines()) + 2, chain['records'])
        self.assertEqual([], audit_export.export_data()['losses'])

    def test_standalone_audit_directory_creates_and_fsyncs_salt(self):
        folder = self.root / 'standalone-audit'
        folder.mkdir()
        with patch.object(journal.os, 'fsync', wraps=os.fsync) as sync:
            value = journal.ensure_salt(folder)
        self.assertEqual(32, len(value))
        sync.assert_called_once()
        self.assertEqual(value, journal.ensure_salt(folder))

    def test_salted_reference_shapes_refuse_replacement_and_report_loss(self):
        shapes = [dict(ref='a' * 16), dict(platforms={'youtube': {'ref': 'b' * 16}}),
                  dict(before={'ref': 'c' * 16}), dict(after={'ref': 'd' * 16}),
                  dict(media_ref='e' * 16)]
        for index, fields in enumerate(shapes):
            with self.subTest(fields=fields), patch.dict(os.environ, VPUB_HOME=str(self.root / str(index))):
                directory = core.home() / 'audit'
                self.assertTrue(journal.append('transition', lambda _: fields))
                (directory / 'salt').unlink()
                before = (directory / 'journal.jsonl').read_bytes()
                with self.assertRaisesRegex(ValueError, '^missing_salt_with_existing_history$'):
                    journal.ensure_salt(directory)
                self.assertEqual(0, self.run_cli('safety', 'status')[0])
                self.assertFalse((directory / 'salt').exists())
                self.assertEqual(before, (directory / 'journal.jsonl').read_bytes())
                result = audit_export.export_data(since='2020-01-01T00:00:00Z')
                self.assertFalse(result['integrity']['salt_ok'])
                self.assertEqual('missing_salt_with_existing_history', result['integrity']['salt_error'])
                self.assertIsNone(result['site'])
                self.assertEqual([], result['publications'])
                self.assertEqual([], result['deletions'])
                self.assertEqual(['journal_read_failed'] * 2, [r['reason'] for r in result['losses']])

    def test_head_without_journal_refuses_salt_replacement(self):
        self.seed()
        (self.directory / 'salt').unlink()
        (self.directory / 'journal.jsonl').unlink()
        self.assertFalse(journal.append('stage', lambda _: dict(stage='pressing')))
        self.assertFalse((self.directory / 'salt').exists())
        result = audit_export.export_data(until='2099-01-01T00:00:00Z')
        self.assertFalse(result['integrity']['salt_ok'])
        self.assertFalse(result['integrity']['chain_ok'])
        self.assertEqual('missing_salt_with_existing_history', result['integrity']['salt_error'])
        self.assertIsNone(result['site'])
        self.assertEqual([], result['publications'])
        self.assertEqual('journal_read_failed', result['losses'][0]['reason'])

    def test_reference_scan_accepts_whitespace_and_ignores_null_refs(self):
        self.directory.mkdir()
        file = self.directory / 'journal.jsonl'
        file.write_bytes(b'{"platforms": {"youtube": {"ref" : "0123456789abcdef"}}}\n')
        with self.assertRaisesRegex(ValueError, 'missing_salt_with_existing_history'):
            journal.ensure_salt(self.directory)
        file.write_bytes(b'{"ref": null, "media_ref": null, "code": "0123456789abcdef"}\n')
        self.assertEqual(32, len(journal.ensure_salt(self.directory)))

    def test_salt_directory_returns_typed_cli_error(self):
        self.directory.mkdir()
        (self.directory / 'salt').mkdir()
        status, result = self.run_cli('audit', 'export', '--out', str(self.root / 'export.json'))
        self.assertEqual(2, status)
        self.assertEqual('audit_export_invalid', result['error']['code'])
        self.assertEqual('Audit salt could not be read.', result['error']['message'])

    def test_salt_check_and_read_errors_return_typed_cli_error(self):
        self.seed()
        salt = self.directory / 'salt'
        for method in ['exists', 'lstat', 'read_bytes']:
            original = getattr(Path, method)
            def failing(path, *args, **kwargs):
                if path == salt:
                    raise PermissionError('PRIVATE sentinel')
                return original(path, *args, **kwargs)
            with self.subTest(method=method), patch.object(Path, method, failing):
                status, result = self.run_cli('audit', 'export', '--out', str(self.root / 'export.json'))
            self.assertEqual(2, status)
            self.assertEqual('audit_export_invalid', result['error']['code'])
            self.assertEqual('Audit salt could not be read.', result['error']['message'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_salt_symlinks_including_broken_are_rejected(self):
        self.seed()
        salt = self.directory / 'salt'
        original = Path.is_symlink
        for broken in [False, True]:
            if broken:
                salt.unlink()
            # Deterministic even on Windows hosts without symlink privilege.
            with self.subTest(broken=broken), patch.object(Path, 'is_symlink', lambda p: p == salt or original(p)):
                status, result = self.run_cli('audit', 'export', '--out', str(self.root / 'export.json'))
            self.assertEqual(2, status)
            self.assertEqual('audit_export_invalid', result['error']['code'])

    def test_invalid_salt_lengths_keep_existing_cli_error(self):
        self.directory.mkdir()
        for size in [0, 31, 33]:
            with self.subTest(size=size):
                (self.directory / 'salt').write_bytes(b'x' * size)
                status, result = self.run_cli('audit', 'export', '--out', str(self.root / 'export.json'))
                self.assertEqual(2, status)
                self.assertEqual('audit_export_invalid', result['error']['code'])
                self.assertEqual('Invalid audit salt.', result['error']['message'])

    def test_empty_export_salt_is_valid_without_creating_files(self):
        output = self.root / 'export.json'
        self.assertEqual(0, self.run_cli('audit', 'export', '--out', str(output))[0])
        result = json.loads(output.read_bytes())
        self.assertTrue(result['integrity']['salt_ok'])
        self.assertIsNone(result['integrity']['salt_error'])
        self.assertIsNone(result['site'])
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
