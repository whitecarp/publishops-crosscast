import os
import json
import signal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, work_delete as wd, work_delete_state as state
from video_publisher.core import Ledger, VpubError, atomic_json, home, publication_lock, read_json, stop_check


IDS = {'bilibili': 'BV1234567890', 'youtube': 'abcDEF12_-3', 'instagram': 'Test_Post_12',
       'x': '2111111111111111111', 'douyin': '7700000000000000000'}
SUPPORTED = tuple(platform for platform in IDS if platform != 'bilibili')


class WorkDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'VPUB_HOME': str(Path(self.temp.name) / 'vpub'),
                                          'BILI_CLI_HOME': str(Path(self.temp.name) / 'bili')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self, platform='x', **changes):
        data = {'receipt': {'id': IDS[platform], 'state': 'SUBMISSION_VERIFIED'},
                'identity': {'identity': 'owner'}, 'title': 'CC test', 'started_at': '2026-11-03',
                'plan_hash': 'plan', 'approved_plan': {'video_sha256': 'sha'},
                'options': {'account': 'default', 'method': 'cli' if platform == 'bilibili' else 'browser'}}
        data.update(changes)
        ledger = Ledger()
        try:
            key = ledger.claim(platform, 'default', 'sha', data)
            ledger.save(key, 'SUBMISSION_VERIFIED', data)
            return key, ledger.get(key)
        finally:
            ledger.close()

    def code(self, key):
        with patch.object(wd, 'backend', side_effect=AssertionError('dry run contacted a platform')):
            return wd.run(key, dry_run=True)['confirmation']

    def deleted(self, target, **extra):
        return {'id': target['id'], 'identity': target['identity'], 'page_gone': True,
                'list_absent': True, 'list_complete': True, 'fresh': True, 'platform_writes': 0, **extra}

    def owned(self, target):
        return {'id': target['id'], 'identity': target['identity'], 'present': True, 'owned': True}

    def isolated_case(self, name):
        root = Path(self.temp.name) / name
        return patch.dict(os.environ, VPUB_HOME=str(root / 'vpub'), BILI_CLI_HOME=str(root / 'bili'))

    def assert_cancelled(self, path, target, *, attempted=False, exists=True):
        self.assertTrue((home() / 'STOP.json').exists())
        if exists:
            saved = read_json(path)
            self.assertEqual('DELETE_PENDING', saved['state'])
            self.assertIs(attempted, saved['delete_attempted'])
        else:
            self.assertFalse(path.exists())
        with self.assertRaises(VpubError):
            state.begin_press(path, target)
        with self.assertRaises(VpubError) as error:
            stop_check('x')
        self.assertEqual('stopped', error.exception.code)

    def test_each_platform_dry_run_and_person_code_are_required_without_account_calls(self):
        for platform in SUPPORTED:
            with self.subTest(platform=platform):
                key, _ = self.seed(platform)
                code = self.code(key)
                self.assertEqual(64, len(code))
                with patch.object(wd, 'backend') as backend, patch('video_publisher.audit.code_stamp', side_effect=AssertionError('refusal stamped')):
                    for supplied, person in ((None, False), (code, False), ('other', True)):
                        with self.assertRaises(VpubError) as error:
                            wd.run(key, confirm=supplied, person_approved=person)
                        self.assertEqual('confirmation_required', error.exception.code)
                    backend.assert_not_called()
                self.assertFalse(state.receipt_path(key).exists())

    def test_each_platform_already_gone_uses_two_absence_proofs_and_zero_writes(self):
        for platform in SUPPORTED:
            with self.subTest(platform=platform):
                key, original = self.seed(platform)
                code = self.code(key)
                with patch.object(wd, 'backend', side_effect=lambda t, op, p: self.deleted(t)) as backend:
                    result = wd.run(key, confirm=code, person_approved=True)
                self.assertEqual('DELETED_VERIFIED', result['state'])
                self.assertFalse(result['delete_attempted'])
                self.assertEqual(['inspect'], [call.args[1] for call in backend.call_args_list])
                ledger = Ledger()
                self.assertEqual(original, ledger.get(key))
                ledger.close()

    def test_each_platform_partial_or_wrong_account_never_deletes(self):
        for platform in SUPPORTED:
            with self.subTest(platform=platform):
                key, _ = self.seed(platform)
                with patch.object(wd, 'backend', side_effect=lambda t, op, p: self.deleted(t, identity='other', list_complete=False)) as backend:
                    with self.assertRaises(VpubError) as error:
                        wd.run(key, confirm=self.code(key), person_approved=True)
                self.assertEqual('deletion_target_unverified', error.exception.code)
                self.assertFalse(state.receipt_path(key).exists())
                self.assertEqual(['inspect'], [call.args[1] for call in backend.call_args_list])

    def test_each_platform_one_press_uncertainty_then_verify_only(self):
        for platform in SUPPORTED:
            with self.subTest(platform=platform):
                key, _ = self.seed(platform)
                code = self.code(key)
                def backend(target, operation, path):
                    if operation == 'inspect':
                        return self.owned(target)
                    self.assertEqual('delete', operation)
                    state.begin_press(path, target)
                    raise TimeoutError('private page words must not enter receipt')
                with patch.object(wd, 'backend', side_effect=backend):
                    answer = wd.run(key, confirm=code, person_approved=True)
                self.assertEqual('DELETE_PENDING', answer['state'])
                self.assertTrue(answer['delete_attempted'])
                self.assertNotIn('private page', state.receipt_path(key).read_text())
                with patch.object(wd, 'backend') as back, self.assertRaises(VpubError):
                    wd.run(key, confirm=code, person_approved=True)
                back.assert_not_called()
                with patch.object(wd, 'backend', side_effect=lambda t, op, p: self.deleted(t)) as back:
                    answer = wd.run(key, verify_only=True)
                self.assertEqual(['verify'], [call.args[1] for call in back.call_args_list])
                self.assertTrue(answer['deleted'])

    def test_distinct_creation_press_and_readback_builds_preserve_target_and_approval(self):
        key, _ = self.seed()
        code = self.code(key)
        path = state.receipt_path(key)
        create, press, read, later = ({"source_digest": c * 64} for c in "abcd")

        def backend(target, operation, path):
            if operation == "inspect":
                return self.owned(target)
            with patch("video_publisher.audit.code_stamp", return_value=press):
                state.begin_press(path, target)
            return self.deleted(target, list_complete=False)

        with patch("video_publisher.audit.code_stamp", side_effect=[create, read]), patch.object(wd, "backend", side_effect=backend):
            self.assertEqual("DELETE_PENDING", wd.run(key, confirm=code, person_approved=True)["state"])
        before = state.read_receipt(path)
        self.assertEqual((create, press, read), tuple(before[k] for k in ("code", "press_code", "readback_code")))
        self.assertEqual({"confirm_matched": True, "person_approved": True, "at": before["created_at"]}, before["approval"])
        with patch("video_publisher.audit.code_stamp", return_value=later), patch.object(wd, "backend", side_effect=lambda t, op, p: self.deleted(t)):
            self.assertEqual("DELETED_VERIFIED", wd.run(key, verify_only=True)["state"])
        after = state.read_receipt(path)
        for field in ("target", "code", "press_code", "approval", "attempted_at", "publication_receipt"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(later, after["readback_code"])

        legacy = {k: v for k, v in before.items() if k not in ("code", "press_code", "readback_code", "approval")}
        atomic_json(path, legacy)
        with patch("video_publisher.audit.code_stamp", return_value=later), patch.object(wd, "backend", side_effect=lambda t, op, p: self.deleted(t)):
            wd.run(key, verify_only=True)
        restored = state.read_receipt(path)
        self.assertEqual(later, restored["readback_code"])
        self.assertTrue({"approval", "code", "press_code"}.isdisjoint(restored))

    def test_each_proof_field_is_strict_and_target_bound(self):
        key, row = self.seed()
        target = wd.target_for(row)
        for field in ('page_gone', 'list_absent', 'list_complete', 'fresh'):
            for value in (False, None, 1, 'true'):
                self.assertFalse(state.verified(self.deleted(target, **{field: value}), target))
        for field in ('id', 'identity'):
            self.assertFalse(state.verified(self.deleted(target, **{field: 'another'}), target))

    def test_bilibili_is_explicitly_unsupported_before_account_access_or_record(self):
        key, original = self.seed('bilibili')
        with patch.object(wd, 'backend') as backend:
            for options in ({'dry_run': True}, {'confirm': 'any', 'person_approved': True}, {'verify_only': True}):
                with self.subTest(options=options), self.assertRaises(VpubError) as error:
                    wd.run(key, **options)
                self.assertEqual('unsupported_delete', error.exception.code)
            backend.assert_not_called()
        self.assertFalse(state.receipt_path(key).exists())
        ledger = Ledger(); self.assertEqual(original, ledger.get(key)); ledger.close()

    def test_changed_receipt_invalidates_code(self):
        key, row = self.seed()
        code = self.code(key)
        row['data']['receipt']['id'] = '2111111111111111112'
        ledger = Ledger(); ledger.save(key, row['state'], row['data']); ledger.close()
        with patch.object(wd, 'backend') as back, self.assertRaises(VpubError):
            wd.run(key, confirm=code, person_approved=True)
        back.assert_not_called()

    def test_adopted_draft_unknown_and_missing_owner_are_refused(self):
        key, row = self.seed()
        for status in ('EXISTING_VERIFIED', 'DRAFT_SAVED', 'PREVIEW_READY', 'SUBMITTED_UNVERIFIED', 'UNKNOWN'):
            with self.assertRaises(VpubError):
                wd.target_for({**row, 'state': status})
        for data in ({**row['data'], 'association': 'adopted'}, {**row['data'], 'identity': {}},
                     {**row['data'], 'started_at': None}, {**row['data'], 'approved_plan': None},
                     {**row['data'], 'options': {'submission_mode': 'draft_only'}}):
            with self.assertRaises(VpubError):
                wd.target_for({**row, 'data': data})
        for key in ('2111111111111111111', 'https://x.com/owner/status/2111111111111111111', '../bad'):
            with self.assertRaises(VpubError):
                wd.run(key, dry_run=True)

    def test_worker_must_hold_exact_attempt_and_marker_precedes_press(self):
        key, row = self.seed()
        target = {**wd.target_for(row), 'attempt': 'one'}
        path = state.receipt_path(key)
        atomic_json(path, {'state': 'DELETE_READY', 'target': target, 'delete_attempted': False})
        with self.assertRaises(VpubError):
            state.begin_press(path, {**target, 'attempt': 'old'})
        self.assertFalse(read_json(path)['delete_attempted'])
        state.begin_press(path, target)
        self.assertTrue(read_json(path)['delete_attempted'])
        with self.assertRaises(VpubError):
            state.begin_press(path, target)

    def test_stop_is_checked_at_last_press_and_never_cleared(self):
        key, row = self.seed()
        target = {**wd.target_for(row), 'attempt': 'one'}
        path = state.receipt_path(key)
        atomic_json(path, {'state': 'DELETE_READY', 'target': target, 'delete_attempted': False})
        atomic_json(home() / 'STOP.json', {'reason': 'synthetic stop'})
        with self.assertRaises(VpubError):
            state.begin_press(path, target)
        self.assertFalse(read_json(path)['delete_attempted'])
        self.assertTrue((home() / 'STOP.json').exists())

    def test_cli_pending_is_nonzero_and_dry_run_is_success(self):
        args = cli.parser().parse_args(['delete', 'a' * 64, '--dry-run'])
        self.assertTrue(args.dry_run)
        self.assertFalse(cli.finished({'state': 'DELETE_PENDING'}))
        self.assertTrue(cli.finished({'state': 'DELETE_PREVIEW'}))

    def test_malformed_existing_records_refuse_preview_action_verify_and_worker_writes(self):
        key, row = self.seed()
        code = self.code(key)
        target = {**wd.target_for(row), 'attempt': 'one'}
        good = {'state': 'DELETE_READY', 'target': target, 'delete_attempted': False}
        records = [{}, [], None, False, 0, '', {'state': 'UNKNOWN'},
                   {**good, 'state': 'UNKNOWN'}, {**good, 'state': []},
                   {**good, 'delete_attempted': None}, {**good, 'delete_attempted': True},
                   {**good, 'target': []}, {**good, 'target': {**target, 'attempt': ''}},
                   {**good, 'target': {k: v for k, v in target.items() if k != 'url'}},
                   {**good, 'target': {**target, 'url': ''}},
                   {**good, 'target': {**target, 'key': 'b' * 64}},
                   {**good, 'state': 'DELETED_VERIFIED'}]
        path = state.receipt_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        for raw in [*(json.dumps(record) for record in records), '', '{broken']:
            with self.subTest(raw=raw):
                path.write_text(raw, encoding='utf-8')
                before = path.read_bytes()
                with patch.object(wd, 'backend') as backend:
                    for options in ({'dry_run': True}, {'verify_only': True}, {'confirm': code, 'person_approved': True}):
                        with self.assertRaises(VpubError) as error:
                            wd.run(key, **options)
                        self.assertEqual('invalid_deletion_receipt', error.exception.code)
                    backend.assert_not_called()
                for action in (lambda: state.begin_press(path, target),
                               lambda: state.save_result(path, target, self.deleted(target))):
                    with self.assertRaises(VpubError) as error:
                        action()
                    self.assertEqual('invalid_deletion_receipt', error.exception.code)
                self.assertEqual(before, path.read_bytes())
                invalid, = wd.status_rows()
                self.assertEqual('DELETE_RECORD_INVALID', invalid['state'])
                self.assertIsNone(invalid['delete_attempted'])
                self.assertFalse(invalid['deleted'])
                self.assertEqual(key, invalid['key'])

    def test_worker_result_cannot_write_a_receipt_outside_its_bound_path(self):
        key, row = self.seed()
        target = {**wd.target_for(row), 'attempt': 'one'}
        outside = Path(self.temp.name) / (key + '.json')
        atomic_json(outside, {'state': 'DELETE_READY', 'target': target, 'delete_attempted': False})
        before = outside.read_bytes()
        for action in (lambda: state.begin_press(outside, target),
                       lambda: state.save_result(outside, target, self.deleted(target))):
            with self.assertRaises(VpubError):
                action()
        self.assertEqual(before, outside.read_bytes())

    def test_status_keeps_corrupt_deletion_visible_without_unvalidated_target_data(self):
        from video_publisher import overview
        key, _ = self.seed()
        path = state.receipt_path(key)
        atomic_json(path, {'state': 'UNKNOWN', 'target': {'identity': 'UNTRUSTED-PRIVATE', 'title': 'UNTRUSTED-PRIVATE'}})
        before = path.read_bytes()
        with patch.object(wd, 'backend') as backend, patch.object(overview, 'sign_in', return_value={'saved': False}):
            answer = overview.status()
        backend.assert_not_called()
        self.assertEqual(1, answer['actions']['unconfirmed_total'])
        self.assertEqual('DELETE_RECORD_INVALID', answer['deletions']['items'][0]['state'])
        self.assertNotIn('UNTRUSTED-PRIVATE', json.dumps(answer))
        self.assertEqual(before, path.read_bytes())

    def test_record_appearing_during_inspection_is_never_overwritten(self):
        key, _ = self.seed()
        code = self.code(key)
        def backend(target, operation, path):
            self.assertEqual('inspect', operation)
            atomic_json(path, {})
            return self.owned(target)
        with patch.object(wd, 'backend', side_effect=backend) as back, self.assertRaises(VpubError) as error:
            wd.run(key, confirm=code, person_approved=True)
        self.assertEqual('invalid_deletion_receipt', error.exception.code)
        self.assertEqual(['inspect'], [call.args[1] for call in back.call_args_list])
        self.assertEqual({}, read_json(state.receipt_path(key)))

    def test_corrupted_pending_record_cannot_grant_a_second_nonce(self):
        key, _ = self.seed()
        code, presses = self.code(key), []
        def backend(target, operation, path):
            if operation == 'inspect':
                return self.owned(target)
            state.begin_press(path, target)
            presses.append(target['attempt'])
            raise TimeoutError()
        with patch.object(wd, 'backend', side_effect=backend):
            self.assertEqual('DELETE_PENDING', wd.run(key, confirm=code, person_approved=True)['state'])
            atomic_json(state.receipt_path(key), {})
            with self.assertRaises(VpubError):
                wd.run(key, confirm=code, person_approved=True)
        self.assertEqual(1, len(presses))
        self.assertEqual({}, read_json(state.receipt_path(key)))

    def test_pending_preview_never_offers_a_new_confirmation(self):
        key, row = self.seed()
        target = {**wd.target_for(row), 'attempt': 'one'}
        path = state.receipt_path(key)
        atomic_json(path, {'state': 'DELETE_PENDING', 'target': target, 'delete_attempted': False})
        with patch.object(wd, 'backend') as backend, self.assertRaises(VpubError) as error:
            wd.run(key, dry_run=True)
        self.assertEqual('deletion_already_attempted', error.exception.code)
        backend.assert_not_called()

    def test_cancellation_before_and_after_atomic_ready_fences_the_worker(self):
        for failure in (KeyboardInterrupt, SystemExit):
            for after in (False, True):
                with self.subTest(failure=failure, after=after), self.isolated_case(f'ready-{failure.__name__}-{after}'):
                    key, _ = self.seed()
                    code, late = self.code(key), {}
                    def ready(path, saved):
                        late.update(path=path, target=saved['target'])
                        if after:
                            atomic_json(path, saved)
                        raise failure()
                    with patch.object(wd, 'atomic_json', side_effect=ready), \
                            patch.object(wd, 'backend', side_effect=lambda t, op, p: self.owned(t)) as back:
                        with self.assertRaises(failure):
                            wd.run(key, confirm=code, person_approved=True)
                    self.assertEqual(['inspect'], [call.args[1] for call in back.call_args_list])
                    self.assert_cancelled(late['path'], late['target'], exists=after)

    def test_cancellation_during_inspect_has_stop_but_no_writable_attempt(self):
        for failure in (KeyboardInterrupt, SystemExit):
            with self.subTest(failure=failure), self.isolated_case('inspect-' + failure.__name__):
                key, _ = self.seed()
                code, late = self.code(key), {}
                def backend(target, operation, path):
                    self.assertEqual('inspect', operation)
                    late.update(path=path, target=target)
                    raise failure()
                with patch.object(wd, 'backend', side_effect=backend), self.assertRaises(failure):
                    wd.run(key, confirm=code, person_approved=True)
                self.assert_cancelled(late['path'], late['target'], exists=False)

    def test_cancellation_before_and_after_worker_marker_never_allows_late_press(self):
        for failure in (KeyboardInterrupt, SystemExit):
            for marked in (False, True):
                with self.subTest(failure=failure, marked=marked), self.isolated_case(f'backend-{failure.__name__}-{marked}'):
                    key, _ = self.seed()
                    code, late = self.code(key), {}
                    def backend(target, operation, path):
                        if operation == 'inspect':
                            return self.owned(target)
                        late.update(path=path, target=target)
                        if marked:
                            state.begin_press(path, target)
                        raise failure()
                    with patch.object(wd, 'backend', side_effect=backend), self.assertRaises(failure):
                        wd.run(key, confirm=code, person_approved=True)
                    self.assert_cancelled(late['path'], late['target'], attempted=marked)

    def test_interruption_inside_atomic_worker_marker_keeps_actual_attempt_bit(self):
        for failure in (KeyboardInterrupt, SystemExit):
            for after in (False, True):
                with self.subTest(failure=failure, after=after), self.isolated_case(f'marker-{failure.__name__}-{after}'):
                    key, _ = self.seed()
                    code, late, injected = self.code(key), {}, []
                    def writer(path, saved):
                        if saved.get('delete_attempted') and not injected:
                            injected.append(True)
                            if after:
                                atomic_json(path, saved)
                            raise failure()
                        atomic_json(path, saved)
                    def backend(target, operation, path):
                        if operation == 'inspect':
                            return self.owned(target)
                        late.update(path=path, target=target)
                        state.begin_press(path, target)
                        self.fail('Interrupted marker cannot proceed to a control')
                    with patch.object(state, 'atomic_json', side_effect=writer), patch.object(wd, 'backend', side_effect=backend):
                        with self.assertRaises(failure):
                            wd.run(key, confirm=code, person_approved=True)
                    self.assert_cancelled(late['path'], late['target'], attempted=after)

    def test_cancellation_never_downgrades_a_verified_receipt(self):
        for failure in (KeyboardInterrupt, SystemExit):
            with self.subTest(failure=failure), self.isolated_case('verified-' + failure.__name__):
                key, _ = self.seed()
                code, verified = self.code(key), {}
                def backend(target, operation, path):
                    if operation == 'inspect':
                        return self.owned(target)
                    state.begin_press(path, target)
                    state.save_result(path, target, self.deleted(target))
                    verified.update(path=path, raw=path.read_bytes())
                    raise failure()
                with patch.object(wd, 'backend', side_effect=backend), self.assertRaises(failure):
                    wd.run(key, confirm=code, person_approved=True)
                self.assertEqual(verified['raw'], verified['path'].read_bytes())
                self.assertEqual('DELETED_VERIFIED', read_json(verified['path'])['state'])
                self.assertTrue((home() / 'STOP.json').exists())

    def test_interrupt_after_parent_saves_verified_result_keeps_exact_proof(self):
        key, _ = self.seed()
        code, verified = self.code(key), {}
        def backend(target, operation, path):
            if operation == 'inspect':
                return self.owned(target)
            state.begin_press(path, target)
            return self.deleted(target)
        def writer(path, saved):
            atomic_json(path, saved)
            if saved['state'] == 'DELETED_VERIFIED':
                verified.update(path=path, raw=path.read_bytes())
                raise KeyboardInterrupt()
        with patch.object(state, 'atomic_json', side_effect=writer), patch.object(wd, 'backend', side_effect=backend):
            with self.assertRaises(KeyboardInterrupt):
                wd.run(key, confirm=code, person_approved=True)
        self.assertEqual(verified['raw'], verified['path'].read_bytes())
        self.assertTrue((home() / 'STOP.json').exists())

    def test_repeated_interrupts_during_fence_cannot_release_publication_lock_early(self):
        key, _ = self.seed()
        code, late, fences = self.code(key), {}, []
        def backend(target, operation, path):
            if operation == 'inspect':
                return self.owned(target)
            late.update(path=path, target=target)
            raise KeyboardInterrupt()
        def writer(path, saved):
            if saved.get('cancelled_at'):
                self.assertTrue((home() / 'STOP.json').exists())
                with self.assertRaises(VpubError):
                    with publication_lock():
                        self.fail('Cancellation released the publication lock')
                fences.append(True)
                if len(fences) < 3:
                    raise (KeyboardInterrupt if len(fences) == 1 else SystemExit)()
            atomic_json(path, saved)
        with patch.object(state, 'atomic_json', side_effect=writer), patch.object(wd, 'backend', side_effect=backend):
            with self.assertRaises(KeyboardInterrupt):
                wd.run(key, confirm=code, person_approved=True)
        self.assertEqual(3, len(fences))
        self.assert_cancelled(late['path'], late['target'])

    def test_cancellation_preserves_existing_platform_or_malformed_stop_bytes(self):
        for raw in (b'{"source":"safety","platform":"x","code":"captcha","reason":"existing warning"}', b'{broken'):
            with self.subTest(raw=raw), self.isolated_case('stop-' + str(len(raw))):
                key, _ = self.seed()
                code = self.code(key)
                def backend(target, operation, path):
                    if operation == 'inspect':
                        return self.owned(target)
                    (home() / 'STOP.json').write_bytes(raw)
                    raise KeyboardInterrupt()
                with patch.object(wd, 'backend', side_effect=backend), self.assertRaises(KeyboardInterrupt):
                    wd.run(key, confirm=code, person_approved=True)
                self.assertEqual(raw, (home() / 'STOP.json').read_bytes())
                self.assertEqual('DELETE_PENDING', read_json(state.receipt_path(key))['state'])

    def test_real_sigint_at_fake_backend_wait_fences_before_propagating(self):
        key, _ = self.seed()
        code, late = self.code(key), {}
        def backend(target, operation, path):
            if operation == 'inspect':
                return self.owned(target)
            late.update(path=path, target=target)
            signal.raise_signal(signal.SIGINT)
            self.fail('SIGINT must not continue to a final press')
        with patch.object(wd, 'backend', side_effect=backend), self.assertRaises(KeyboardInterrupt):
            wd.run(key, confirm=code, person_approved=True)
        self.assert_cancelled(late['path'], late['target'])

    def test_status_shows_pending_and_deleted_without_rewriting_publication_history(self):
        from video_publisher import overview
        key, original = self.seed()
        target = {**wd.target_for(original), 'attempt': 'one'}
        path = state.receipt_path(key)
        atomic_json(path, {'state': 'DELETE_PENDING', 'target': target, 'delete_attempted': True})
        with patch.object(overview, 'sign_in', return_value={'saved': False}):
            pending = overview.status()
            self.assertEqual(1, pending['actions']['unconfirmed_total'])
            self.assertEqual('work_deletion', pending['actions']['unconfirmed'][0]['kind'])
            self.assertEqual(f'vpub delete {key} --verify-only', pending['actions']['unconfirmed'][0]['next_command'])
            state.save_result(path, target, self.deleted(target))
            done = overview.status()
        self.assertEqual(0, done['actions']['unconfirmed_total'])
        self.assertTrue(done['deletions']['items'][0]['deleted'])
        self.assertEqual({'SUBMISSION_VERIFIED': 1}, done['publications']['by_state'])
        ledger = Ledger(); self.assertEqual(original, ledger.get(key)); ledger.close()


if __name__ == '__main__':
    unittest.main()
