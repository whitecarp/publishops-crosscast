"""Late publication writers cannot invalidate a separately recorded deletion.

Real CLI/ledger/lock paths, fake platform replies, and two isolated homes only.
"""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_deletion as handoff_tests
from video_publisher import cli, work_delete, work_delete_state
from video_publisher.core import Ledger, VpubError, atomic_json, home, load_plan, publication_lock, read_json
from video_publisher.language_policy import route


class DeletionCoexistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {
            'VPUB_HOME': str(Path(temp.name) / 'vpub'),
            'BILI_CLI_HOME': str(Path(temp.name) / 'bili'),
        })
        env.start()
        self.addCleanup(env.stop)
        for owner, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                            (socket, 'getaddrinfo'), (subprocess, 'Popen')):
            guard = patch.object(owner, name, side_effect=AssertionError('Offline deletion fixture only'))
            guard.start()
            self.addCleanup(guard.stop)
        # This fixture creates the native job through publish/begin/record, without UI.
        self.native = handoff_tests.DeletionTests('test_wrong_identity_id_and_missing_absence_fail_closed')
        self.native.setUp()
        self.addCleanup(self.native.tearDown)

    @staticmethod
    def row(key):
        ledger = Ledger()
        try:
            return ledger.get(key)
        finally:
            ledger.close()

    def uncertain_delete(self, key):
        code = work_delete.run(key, dry_run=True)['confirmation']

        def backend(target, operation, path):
            if operation == 'inspect':
                return {'id': target['id'], 'identity': target['identity'], 'owned': True, 'present': True}
            self.assertEqual('delete', operation)
            work_delete_state.begin_press(path, target)  # Intent only; no UI click.
            raise TimeoutError('Synthetic uncertain deletion')

        with patch.object(work_delete, 'backend', side_effect=backend):
            answer = work_delete.run(key, confirm=code, person_approved=True)
        self.assertEqual('DELETE_PENDING', answer['state'])
        self.assertTrue(answer['delete_attempted'])

    @contextmanager
    def deletion_after_cli_check(self, key):
        """The other controller finishes between the entry check and locked handler."""
        original = work_delete_state.refuse_other_action
        entered = []

        def check(wanted):
            original(wanted)
            if not entered:
                self.assertEqual(key, wanted)
                entered.append(True)
                self.uncertain_delete(key)

        with patch.object(work_delete_state, 'refuse_other_action', side_effect=check):
            yield
        self.assertEqual([True], entered)

    def assert_blocked(self, operation):
        with self.assertRaises(VpubError) as caught:
            operation()
        self.assertEqual('deletion_record_exists', caught.exception.code)
        self.assertIn('--verify-only', caught.exception.hint)

    def recover_and_check(self, before, durable=None, original_bytes=None):
        key = before['key']
        self.assertEqual(before, self.row(key))
        if durable:
            self.assertEqual(original_bytes, durable.read_bytes())

        def verify(target, operation, path):
            self.assertEqual('verify', operation)
            return {'id': target['id'], 'identity': target['identity'], 'page_gone': True,
                    'list_absent': True, 'list_complete': True, 'fresh': True}

        with patch.object(work_delete, 'backend', side_effect=verify) as backend:
            self.assertTrue(work_delete.run(key, verify_only=True)['deleted'])
        backend.assert_called_once()
        self.assertEqual(before, self.row(key))
        if durable:
            self.assertEqual(original_bytes, durable.read_bytes())

    def browser_publication(self):
        value = cli.template(self.native.root / 'video.mp4', 'en')
        value['title'] = 'Synthetic browser publication'
        owner = 'UC' + 'a' * 22
        value['platforms'] = {'youtube': {'method': 'browser', 'visibility': 'public',
                                         'ai_generated': True, 'made_for_kids': False,
                                         'category_id': '22',
                                         'expected_channel_id': owner}}
        manifest = self.native.root / 'browser-plan.json'
        atomic_json(manifest, value)
        plan = load_plan(manifest)
        receipt = {'id': 'abcDEF12_-3', 'state': 'SUBMISSION_VERIFIED',
                   'method': 'browser', 'identity': owner, 'title': plan['platforms']['youtube']['title']}
        with patch.object(cli.adapters, 'preflight', return_value={'identity': owner}), \
                patch.object(cli.adapters, 'publish', return_value=receipt):
            answer = cli.publish_plan(plan, plan['confirmation'], reviewed=True)
        key = answer['results']['youtube']['key']
        durable = home() / 'browser-receipts' / (plan['confirmation'] + '-youtube.json')
        atomic_json(durable, receipt)
        return self.row(key), durable

    def test_late_native_record_after_cli_check_preserves_original(self):
        before = self.row(self.native.key)
        proof = read_json(self.native.evidence)
        proof.update(outcome='uncertain', observation='Synthetic late read cannot confirm the post')
        atomic_json(self.native.evidence, proof)
        args = cli.parser().parse_args(['browser', 'record', self.native.key,
                                       '--evidence', str(self.native.evidence)])
        with self.deletion_after_cli_check(self.native.key):
            self.assert_blocked(lambda: cli.run_command(args))
        self.recover_and_check(before)

    def test_in_flight_verify_cannot_replace_ledger_or_browser_receipt(self):
        before, durable = self.browser_publication()
        original_bytes = durable.read_bytes()
        key = before['key']

        def late_read(platform, account, receipt, checkpoint=None):
            self.assertIsNone(checkpoint)  # This worker has read-only authority.
            self.uncertain_delete(key)
            return {**receipt, 'state': 'SUBMITTED_UNVERIFIED', 'note': 'Post not found in a fresh read'}

        args = cli.parser().parse_args(['verify', key])
        with patch.object(cli.adapters, 'verify', side_effect=late_read) as backend:
            self.assert_blocked(lambda: cli.run_command(args))
        backend.assert_called_once()
        self.recover_and_check(before, durable, original_bytes)

    def test_resume_rechecks_after_cli_check_before_any_backend(self):
        before, durable = self.browser_publication()
        original_bytes = durable.read_bytes()
        args = cli.parser().parse_args(['resume', before['key']])
        with self.deletion_after_cli_check(before['key']), \
                patch.object(cli.adapters, 'browser_call', return_value=before['data']['receipt']) as backend:
            self.assert_blocked(lambda: cli.run_command(args))
        backend.assert_not_called()
        self.recover_and_check(before, durable, original_bytes)

    def test_locked_verification_cannot_start_followups_after_deletion(self):
        before, durable = self.browser_publication()
        original_bytes = durable.read_bytes()
        self.uncertain_delete(before['key'])
        ledger = Ledger()
        try:
            with publication_lock(), patch.object(cli.adapters, 'verify', return_value=before['data']['receipt']) as backend:
                self.assert_blocked(lambda: cli.verify_attempt(ledger, before, True))
            backend.assert_not_called()
        finally:
            ledger.close()
        self.recover_and_check(before, durable, original_bytes)

    def test_edit_rechecks_under_its_existing_lock(self):
        before, durable = self.browser_publication()
        original_bytes = durable.read_bytes()
        policy = route('en', 'youtube')
        edit = self.native.root / 'edit.json'
        atomic_json(edit, {'expected_identity': before['data']['identity']['identity'],
                          'title': 'Synthetic correction', 'description': 'Synthetic changed description',
                          'content_language': 'en', 'metadata_mode': policy['mode'],
                          'metadata_language': policy['language']})
        args = cli.parser().parse_args(['edit', before['key'], str(edit)])
        with self.deletion_after_cli_check(before['key']), \
                patch.object(cli.adapters, 'browser_call') as backend:
            self.assert_blocked(lambda: cli.run_command(args))
        backend.assert_not_called()
        self.assertEqual([], list((home() / 'edits').glob('*.json')))
        self.recover_and_check(before, durable, original_bytes)

    def test_publish_still_skips_existing_verified_row_during_deletion(self):
        before = self.row(self.native.key)
        self.uncertain_delete(self.native.key)
        with patch.object(cli.adapters, 'preflight') as preflight, patch.object(cli.adapters, 'publish') as publish:
            answer = cli.publish_plan(self.native.plan, self.native.plan['confirmation'], reviewed=True)
        self.assertEqual('SKIPPED_EXISTING', answer['results']['instagram']['state'])
        preflight.assert_not_called()
        publish.assert_not_called()
        self.recover_and_check(before)

    def test_without_deletion_verification_still_updates_both_stores(self):
        before, durable = self.browser_publication()
        result = {**before['data']['receipt'], 'state': 'SUBMITTED_UNVERIFIED', 'note': 'Synthetic new read'}
        args = cli.parser().parse_args(['verify', before['key']])
        with patch.object(cli.adapters, 'verify', return_value=result):
            self.assertEqual(result['state'], cli.run_command(args)['state'])
        from video_publisher.audit import code_stamp
        expected = {**result, 'code_last': code_stamp()}
        self.assertEqual(expected, self.row(before['key'])['data']['receipt'])
        self.assertEqual(expected, read_json(durable))

    def test_delete_interrupt_json_directs_to_verify_without_clearing_stop(self):
        before = self.row(self.native.key)
        key = before['key']
        code = work_delete.run(key, dry_run=True)['confirmation']

        def backend(target, operation, path):
            if operation == 'inspect':
                return {'id': target['id'], 'identity': target['identity'], 'owned': True, 'present': True}
            self.assertEqual('delete', operation)
            work_delete_state.begin_press(path, target)
            raise KeyboardInterrupt()

        output = io.StringIO()
        with patch.object(cli.sys, 'argv', ['vpub', 'delete', key, '--confirm', code, '--person-approved']), \
                patch.object(cli.sys, 'stdout', output), patch.object(work_delete, 'backend', side_effect=backend) as calls, \
                self.assertRaises(SystemExit) as caught:
            cli.main()
        self.assertEqual(3, caught.exception.code)
        answer = json.loads(output.getvalue())
        self.assertFalse(answer['ok'])
        self.assertEqual('interrupted', answer['error']['code'])
        hint = answer['error']['hint']
        self.assertIn('Deletion may already have reached the platform', hint)
        self.assertIn('Never repeat Delete', hint)
        self.assertNotIn('upload', hint.lower())
        self.assertIn(f'vpub delete {key} --verify-only', hint)
        self.assertLess(hint.index('Only a person'), hint.index(f'vpub delete {key}'))
        self.assertEqual(['inspect', 'delete'], [call.args[1] for call in calls.call_args_list])
        self.assertEqual('DELETE_PENDING', read_json(work_delete_state.receipt_path(key))['state'])
        stop = home() / 'STOP.json'
        stop_bytes = stop.read_bytes()
        with patch.object(work_delete, 'backend') as read, self.assertRaises(VpubError) as blocked:
            work_delete.run(key, verify_only=True)
        self.assertEqual('stopped', blocked.exception.code)
        read.assert_not_called()
        self.assertEqual(stop_bytes, stop.read_bytes())
        self.assertEqual(before, self.row(key))


if __name__ == '__main__':
    unittest.main()
