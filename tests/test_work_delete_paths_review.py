"""Independent offline audit of 57b8604 path binding and pending preservation.

The MSIX case models resolve spelling through a real disposable root junction;
it is not a claim about an installed MSIX account or browser session.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_work_delete_paths as fixtures
from video_publisher import core, work_delete_state as state


class DeletionPathReviewTests(unittest.TestCase):
    # Borrow setup/helpers only, without inheriting and rerunning its test suite.
    setUp = fixtures.DeletionPathTests.setUp
    enter_patch = fixtures.DeletionPathTests.enter_patch
    ready = fixtures.DeletionPathTests.ready
    invalid = fixtures.DeletionPathTests.invalid
    link_directory = fixtures.DeletionPathTests.link_directory
    virtual_home = fixtures.DeletionPathTests.virtual_home
    reparse = fixtures.DeletionPathTests.reparse

    def pending(self):
        path = self.ready()
        state.begin_press(path, self.target)
        saved = state.save_result(path, self.target, error={'code': 'synthetic_uncertain_press'})
        self.assertEqual(('DELETE_PENDING', True), (saved['state'], saved['delete_attempted']))
        return path

    def preserved(self, path, before):
        self.assertEqual(before, path.read_bytes())
        saved = core.read_json(path)
        self.assertEqual('DELETE_PENDING', saved['state'])
        self.assertTrue(saved['delete_attempted'])
        self.assertEqual(self.target, saved['target'])
        self.assertEqual('synthetic_uncertain_press', saved['error']['code'])

    def no_retry(self, path):
        with self.assertRaises(core.VpubError) as error:
            state.begin_press(path, self.target)
        self.assertEqual('deletion_already_attempted', error.exception.code)

    def test_pending_survives_alias_and_physical_root_reads_and_retry_refusal(self):
        with self.virtual_home() as (logical, physical):
            path = self.pending()
            before = path.read_bytes()
            self.assertEqual(physical / 'work-deletions' / path.name, path)
            for root in (logical, physical, logical):
                with self.subTest(root=root.name), patch.dict(os.environ, VPUB_HOME=str(root)):
                    canonical = state.receipt_path(fixtures.KEY)
                    self.assertEqual(path, canonical)
                    self.assertEqual(core.read_json(path), state.read_receipt(canonical))
                    self.no_retry(canonical)
                    with self.assertRaises(core.VpubError) as error:
                        state.refuse_other_action(fixtures.KEY)
                    self.assertEqual('deletion_record_exists', error.exception.code)
                    self.preserved(path, before)
            self.assertEqual([path], list(path.parent.glob('*.json')))

    def test_noncanonical_alias_argument_is_rejected_before_opening_another_lock(self):
        with self.virtual_home() as (logical, physical):
            path = self.pending()
            alias = logical / 'work-deletions' / path.name
            self.assertTrue(alias.samefile(path))
            before = path.read_bytes()
            with patch.object(state, 'receipt_turn') as lock:
                self.invalid(lambda: state.read_receipt(alias, missing_ok=True))
                self.invalid(lambda: state.begin_press(alias, self.target))
                self.invalid(lambda: state.save_result(alias, self.target))
                lock.assert_not_called()
            self.preserved(path, before)

    def test_alias_lock_contention_never_rewrites_pending_and_recovers_after_release(self):
        with self.virtual_home() as (logical, physical):
            path = self.pending()
            before = path.read_bytes()
            alias = logical / 'work-deletions' / path.name
            for held, caller_root in ((alias, physical), (path, logical)):
                with self.subTest(held=held), patch.dict(os.environ, VPUB_HOME=str(caller_root)):
                    with core.receipt_turn(held, seconds=0):
                        marker = Path(str(path) + '.turn').read_bytes()
                        with patch.object(state, 'receipt_turn',
                                          side_effect=lambda receipt: core.receipt_turn(receipt, seconds=0)):
                            for action in (lambda: state.begin_press(path, self.target),
                                           lambda: state.save_result(path, self.target, error={'code': 'other'})):
                                with self.assertRaises(core.VpubError) as error:
                                    action()
                                self.assertEqual('attempt_busy', error.exception.code)
                        self.assertEqual(marker, Path(str(path) + '.turn').read_bytes())
                        self.preserved(path, before)
                    self.no_retry(path)
                    self.preserved(path, before)

    def test_two_real_lock_contenders_allow_exactly_one_durable_begin_press(self):
        path = self.ready()
        barrier = threading.Barrier(2, timeout=5)

        def contender():
            barrier.wait()
            try:
                state.begin_press(path, self.target)
                return 'marked'
            except core.VpubError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(contender) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertCountEqual(['marked', 'deletion_already_attempted'], outcomes)
        before = path.read_bytes()
        self.no_retry(path)
        self.assertEqual(before, path.read_bytes())
        saved = state.read_receipt(path)
        self.assertEqual(('DELETE_PENDING', True), (saved['state'], saved['delete_attempted']))

    def test_home_switch_at_lock_boundary_preserves_original_pending(self):
        path = self.pending()
        before = path.read_bytes()
        other = self.root / 'different-home'
        real_lock = state.receipt_turn

        @contextmanager
        def switched(receipt):
            with real_lock(receipt), patch.dict(os.environ, VPUB_HOME=str(other)):
                yield

        with patch.object(state, 'receipt_turn', switched):
            self.invalid(lambda: state.save_result(path, self.target, error={'code': 'discarded'}))
        self.preserved(path, before)
        self.assertEqual([], list(other.rglob('*.json')))
        self.no_retry(path)

    def test_plain_directory_substitution_during_pending_read_is_refused_before_write(self):
        path = self.pending()
        before = path.read_bytes()
        original_read, original_stat = state.read_json, Path.lstat
        read = []

        def record_read(receipt):
            result = original_read(receipt)
            read.append(True)
            return result

        def swapped(candidate, *args, **kwargs):
            info = original_stat(candidate, *args, **kwargs)
            if read and candidate == path.parent:
                # Kernel identity substitution after a real pending-file read.
                return fixtures.SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                                st_ino=info.st_ino + 1,
                                                st_file_attributes=getattr(info, 'st_file_attributes', 0))
            return info

        with patch.object(state, 'read_json', record_read), patch.object(Path, 'lstat', swapped):
            self.invalid(lambda: state.save_result(path, self.target, error={'code': 'discarded'}))
        self.assertEqual([True], read)
        self.preserved(path, before)
        self.no_retry(path)

    def test_receipt_turn_and_guard_redirection_after_pending_read_preserve_receipt(self):
        path = self.pending()
        before = path.read_bytes()
        original_read = state.read_json
        for leaf in (path, Path(str(path) + '.turn'), Path(str(path) + '.turn.guard')):
            with self.subTest(leaf=leaf.name):
                entered = []
                redirect = self.reparse(leaf)

                def record_read(receipt):
                    result = original_read(receipt)
                    redirect.__enter__()
                    entered.append(True)
                    return result

                try:
                    with patch.object(state, 'read_json', record_read):
                        self.invalid(lambda: state.save_result(path, self.target, error={'code': 'discarded'}))
                finally:
                    if entered:
                        redirect.__exit__(None, None, None)
                self.assertEqual([True], entered)
                self.preserved(path, before)
                self.no_retry(path)

    def test_root_identity_change_after_pending_read_is_refused(self):
        path = self.pending()
        before = path.read_bytes()
        root = core.home()
        original_read, original_stat = state.read_json, Path.stat
        read = []

        def record_read(receipt):
            result = original_read(receipt)
            read.append(True)
            return result

        def replaced(candidate, *args, **kwargs):
            info = original_stat(candidate, *args, **kwargs)
            if read and candidate == root:
                return fixtures.SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                                st_ino=info.st_ino + 1)
            return info

        with patch.object(state, 'read_json', record_read), patch.object(Path, 'stat', replaced):
            self.invalid(lambda: state.save_result(path, self.target))
        self.assertEqual([True], read)
        self.preserved(path, before)

    def test_child_junction_to_pending_receipt_is_rejected_without_destination_changes(self):
        path = self.pending()
        before = path.read_bytes()
        redirected_home = self.root / 'redirected-home'
        redirected_home.mkdir()
        self.link_directory(redirected_home / 'work-deletions', path.parent)
        files_before = {item.name: item.read_bytes() for item in path.parent.iterdir() if item.is_file()}
        with patch.dict(os.environ, VPUB_HOME=str(redirected_home)), patch.object(state, 'receipt_turn') as lock:
            self.invalid(lambda: state.receipt_path(fixtures.KEY))
            self.invalid(lambda: state.read_receipt(path, missing_ok=True))
            self.invalid(lambda: state.begin_press(path, self.target))
            self.invalid(lambda: state.save_result(path, self.target))
            lock.assert_not_called()
        self.preserved(path, before)
        self.assertEqual(files_before,
                         {item.name: item.read_bytes() for item in path.parent.iterdir() if item.is_file()})

    def test_real_directory_in_each_leaf_position_is_rejected_before_lock(self):
        path = self.pending()
        before = path.read_bytes()
        for leaf in (Path(str(path) + '.turn'), Path(str(path) + '.turn.guard')):
            with self.subTest(leaf=leaf.name):
                retained = leaf.with_name(leaf.name + '.retained')
                leaf.rename(retained)
                try:
                    leaf.mkdir()
                    with patch.object(state, 'receipt_turn') as lock:
                        self.invalid(lambda: state.save_result(path, self.target))
                        lock.assert_not_called()
                    self.preserved(path, before)
                finally:
                    leaf.rmdir()
                    retained.rename(leaf)
        receipt = path.with_name(path.name + '.retained')
        path.rename(receipt)
        try:
            path.mkdir()
            with patch.object(state, 'receipt_turn') as lock:
                self.invalid(lambda: state.read_receipt(path, missing_ok=True))
                self.invalid(lambda: state.begin_press(path, self.target))
                lock.assert_not_called()
            self.assertEqual(before, receipt.read_bytes())
        finally:
            path.rmdir()
            receipt.rename(path)
        self.preserved(path, before)

    def test_malformed_pending_is_never_reset_or_overwritten_including_cancel(self):
        path = self.pending()
        for content in (b'{"state":"DELETE_PENDING",', b'null', b'[]',
                        b'{"state":"DELETE_PENDING","delete_attempted":true}'):
            with self.subTest(content=content):
                path.write_bytes(content)
                for action in (lambda: state.read_receipt(path, missing_ok=True),
                               lambda: state.begin_press(path, self.target),
                               lambda: state.save_result(path, self.target),
                               lambda: state.cancel(path, self.target)):
                    self.invalid(action)
                    self.assertEqual(content, path.read_bytes())

    def test_cancel_keeps_attempt_marker_target_and_error_and_refuses_retry(self):
        with self.virtual_home() as (logical, physical):
            path = self.pending()
            original = core.read_json(path)
            with patch.dict(os.environ, VPUB_HOME=str(physical)):
                state.cancel(path, self.target)
            saved = state.read_receipt(path)
            for field in ('state', 'target', 'delete_attempted', 'attempted_at', 'error'):
                self.assertEqual(original[field], saved[field])
            self.assertIn('cancelled_at', saved)
            self.assertTrue((logical / 'STOP.json').samefile(physical / 'STOP.json'))
            before = path.read_bytes()
            self.no_retry(path)
            self.assertEqual(before, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
