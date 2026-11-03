"""Deletion-local path binding; disposable homes, synthetic records and no accounts."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_work_delete as fixtures
from video_publisher import core, work_delete as wd, work_delete_state as state


KEY = 'a' * 64


class DeletionPathTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32')
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self.assertTrue(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
        temporary = tempfile.TemporaryDirectory(prefix='delete-paths-')
        self.root = Path(temporary.name).resolve()
        def cleanup():
            self.assertEqual(self.root, Path(temporary.name).resolve())
            temporary.cleanup()
        self.addCleanup(cleanup)
        self.enter_patch(patch.dict(os.environ, VPUB_HOME=str(self.root / 'home'),
                                   BILI_CLI_HOME=str(self.root / 'bili')))
        self.enter_patch(patch('socket.socket.connect', side_effect=AssertionError('Offline fixture')))
        self.enter_patch(patch.object(wd, 'backend', side_effect=AssertionError('No account calls')))
        self.target = {'key': KEY, 'platform': 'x', 'id': '2111111111111111111', 'identity': 'owner',
                       'account': 'default', 'title': 'Synthetic', 'generation': 'one', 'attempt': 'one',
                       'url': 'https://x.com/owner/status/2111111111111111111'}

    def enter_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def ready(self):
        path = state.receipt_path(KEY)
        core.atomic_json(path, {'state': 'DELETE_READY', 'target': self.target, 'delete_attempted': False})
        return path

    def invalid(self, action):
        with self.assertRaises(core.VpubError) as caught:
            action()
        self.assertEqual('invalid_deletion_receipt', caught.exception.code)

    def link_directory(self, link, destination):
        # Both paths are fresh fixture directories; no account or real home.
        self.assertTrue(link.parent.resolve().is_relative_to(self.root))
        self.assertTrue(destination.resolve().is_relative_to(self.root))
        if os.name == 'nt':
            made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(destination)],
                                  capture_output=True, timeout=10)
            self.assertEqual(0, made.returncode, made.stderr.decode(errors='replace'))
        else:
            link.symlink_to(destination, target_is_directory=True)

    @contextmanager
    def virtual_home(self):
        """Real samefile/locking through an alias; emulate only MSIX resolve timing.

        This root alias is test scaffolding, not permission for child junctions.
        Ordinary core.home resolves a configured root alias before this check.
        """
        physical, logical = self.root / 'physical', self.root / 'logical'
        physical.mkdir()
        self.link_directory(logical, physical)
        resolve = Path.resolve
        def resolve_virtual(path, *args, **kwargs):
            absolute = path.absolute()
            if absolute == logical:
                return logical
            if absolute.is_relative_to(logical) and not (physical / 'work-deletions').exists():
                return absolute
            return resolve(path, *args, **kwargs)
        with patch.dict(os.environ, VPUB_HOME=str(logical)), patch.object(Path, 'resolve', resolve_virtual):
            self.assertEqual(logical, core.home())
            self.assertTrue(logical.samefile(physical))
            yield logical, physical

    @contextmanager
    def reparse(self, selected):
        lstat = Path.lstat
        def flagged(path, *args, **kwargs):
            if path == selected:
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=2,
                                       st_file_attributes=0x400)
            return lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', flagged):
            yield

    def test_directory_materializes_before_receipt_and_lock_paths_are_returned(self):
        directory = core.home() / 'work-deletions'
        self.assertFalse(directory.exists())
        path = state.receipt_path(KEY)
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.resolve() / (KEY + '.json'), path)
        self.assertTrue(path.parent.parent.samefile(core.home()))
        self.assertEqual([], list(path.parent.iterdir()))

    def test_invalid_key_does_not_materialize_directory(self):
        directory = core.home() / 'work-deletions'
        for key in ('../outside', 'A' * 64, 'a' * 63, None):
            with self.subTest(key=key), self.assertRaises(core.VpubError) as caught:
                state.receipt_path(key)
            self.assertEqual('invalid_receipt_key', caught.exception.code)
        self.assertFalse(directory.exists())

    def test_virtual_alias_revealed_after_creation_keeps_one_pending_receipt(self):
        with self.virtual_home() as (logical, physical):
            unpublished = logical / 'work-deletions' / (KEY + '.json')
            self.assertEqual(unpublished, unpublished.resolve())
            path = self.ready()
            self.assertEqual(physical / 'work-deletions' / (KEY + '.json'), path)
            self.assertTrue(unpublished.samefile(path))
            state.begin_press(path, self.target)
            state.save_result(path, self.target, error={'code': 'layout_changed'})
            before = path.read_bytes()
            for root in (logical, physical):
                with patch.dict(os.environ, VPUB_HOME=str(root)):
                    self.assertEqual(path, state.receipt_path(KEY))
                    with self.assertRaises(core.VpubError) as caught:
                        state.begin_press(path, self.target)
                    self.assertEqual('deletion_already_attempted', caught.exception.code)
                    with self.assertRaises(core.VpubError) as caught:
                        state.refuse_other_action(KEY)
                    self.assertEqual('deletion_record_exists', caught.exception.code)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(1, len(list(path.parent.glob('*.json'))))

    def test_virtual_and_physical_paths_share_the_existing_kernel_lock(self):
        with self.virtual_home() as (logical, physical):
            canonical = state.receipt_path(KEY)
            alias = logical / 'work-deletions' / canonical.name
            with core.receipt_turn(canonical, seconds=0):
                with self.assertRaises(core.VpubError) as caught:
                    with core.receipt_turn(alias, seconds=0):
                        self.fail('Alias obtained a second lock')
                self.assertEqual('attempt_busy', caught.exception.code)
                self.assertTrue(Path(str(alias) + '.turn.guard').samefile(Path(str(canonical) + '.turn.guard')))

    def test_native_core_alias_flow_preserves_publication_and_allows_only_verification(self):
        with self.virtual_home() as (logical, physical):
            key, original = fixtures.WorkDeletionTests.seed(self)
            code = fixtures.WorkDeletionTests.code(self, key)
            calls = []
            def backend(target, operation, path):
                calls.append(operation)
                self.assertEqual(physical / 'work-deletions', path.parent)
                if operation == 'delete':
                    state.begin_press(path, target)
                    raise core.VpubError('layout_changed', 'Synthetic uncertain control')
                return fixtures.WorkDeletionTests.owned(self, target)
            with patch.object(wd, 'backend', side_effect=backend):
                self.assertEqual('DELETE_PENDING', wd.run(key, confirm=code, person_approved=True)['state'])
                with patch.dict(os.environ, VPUB_HOME=str(physical)):
                    with self.assertRaises(core.VpubError) as caught:
                        wd.run(key, confirm=code, person_approved=True)
                    self.assertEqual('deletion_already_attempted', caught.exception.code)
                    self.assertEqual('DELETE_PENDING', wd.run(key, verify_only=True)['state'])
            self.assertEqual(['inspect', 'delete', 'verify'], calls)
            ledger = core.Ledger()
            try:
                self.assertEqual(original, ledger.get(key))
            finally:
                ledger.close()

    def test_child_junction_or_symlink_is_refused_without_touching_destination(self):
        directory = core.home() / 'work-deletions'
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'untouched'
        sentinel.write_bytes(b'original')
        self.link_directory(directory, outside)
        self.invalid(lambda: state.receipt_path(KEY))
        self.assertEqual([sentinel], list(outside.iterdir()))
        self.assertEqual(b'original', sentinel.read_bytes())

    def test_each_receipt_turn_and_guard_reparse_leaf_is_refused_before_lock(self):
        path = self.ready()
        original = path.read_bytes()
        for leaf in (path, Path(str(path) + '.turn'), Path(str(path) + '.turn.guard')):
            with self.subTest(leaf=leaf.name), self.reparse(leaf), patch.object(state, 'receipt_turn') as turn:
                for action in (lambda: state.receipt_path(KEY), lambda: state.read_receipt(path),
                               lambda: state.begin_press(path, self.target), lambda: state.save_result(path, self.target)):
                    self.invalid(action)
                turn.assert_not_called()
        self.assertEqual(original, path.read_bytes())

    def test_dangling_receipt_symlink_is_not_treated_as_missing(self):
        path = state.receipt_path(KEY)
        try:
            path.symlink_to(self.root / 'missing')
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Host lacks file-symlink privilege; reparse attributes are tested separately')
            raise
        self.invalid(lambda: state.read_receipt(path, missing_ok=True))
        self.assertTrue(path.is_symlink())
        self.assertFalse((self.root / 'missing').exists())

    def test_resolved_child_must_keep_exact_name_and_same_parent_identity(self):
        directory = state.receipt_path(KEY).parent
        for different in (self.root / 'outside' / 'work-deletions', core.home() / 'other-name'):
            with self.subTest(destination=different.name):
                different.mkdir(parents=True)
                resolve = Path.resolve
                def redirected(path, *args, **kwargs):
                    return different if path == directory else resolve(path, *args, **kwargs)
                with patch.object(Path, 'resolve', redirected):
                    self.invalid(lambda: state.receipt_path(KEY))
                self.assertEqual([], list(different.iterdir()))

    def test_identity_lookup_error_fails_closed(self):
        with patch.object(Path, 'samefile', side_effect=PermissionError('Synthetic denied identity')):
            self.invalid(lambda: state.receipt_path(KEY))

    def test_external_receipt_is_rejected_before_its_lock_is_opened(self):
        path = self.root / (KEY + '.json')
        path.write_bytes(b'outside')
        with patch.object(state, 'receipt_turn') as turn:
            self.invalid(lambda: state.begin_press(path, self.target))
            self.invalid(lambda: state.save_result(path, self.target))
            turn.assert_not_called()
        self.assertEqual(b'outside', path.read_bytes())

    def test_reparse_substitution_is_rechecked_after_lock_acquisition(self):
        path = self.ready()
        original = path.read_bytes()
        turn = state.receipt_turn
        for leaf in (path, Path(str(path) + '.turn'), Path(str(path) + '.turn.guard')):
            with self.subTest(leaf=leaf.name):
                @contextmanager
                def replaced(receipt):
                    with turn(receipt):
                        with self.reparse(leaf):
                            yield
                with patch.object(state, 'receipt_turn', replaced):
                    self.invalid(lambda: state.begin_press(path, self.target))
                    self.invalid(lambda: state.save_result(path, self.target))
                self.assertEqual(original, path.read_bytes())

    def test_plain_directory_replacement_before_press_marker_is_rejected(self):
        path = self.ready()
        original = path.read_bytes()
        lstat, stop = Path.lstat, state.stop_check
        replaced = []
        def changed(path_to_stat, *args, **kwargs):
            value = lstat(path_to_stat, *args, **kwargs)
            if replaced and path_to_stat == path.parent:
                return SimpleNamespace(st_mode=value.st_mode, st_dev=value.st_dev, st_ino=value.st_ino + 1,
                                       st_file_attributes=getattr(value, 'st_file_attributes', 0))
            return value
        def checked(platform):
            stop(platform)
            replaced.append(True)
        with patch.object(Path, 'lstat', changed), patch.object(state, 'stop_check', checked):
            self.invalid(lambda: state.begin_press(path, self.target))
        self.assertEqual([True], replaced)
        self.assertEqual(original, path.read_bytes())

    def test_stop_and_stale_nonce_still_refuse_a_press(self):
        path = self.ready()
        original = path.read_bytes()
        with self.assertRaises(core.VpubError) as caught:
            state.begin_press(path, {**self.target, 'attempt': 'stale'})
        self.assertEqual('deletion_already_attempted', caught.exception.code)
        core.atomic_json(core.home() / 'STOP.json', {'reason': 'Synthetic STOP'})
        with self.assertRaises(core.VpubError) as caught:
            state.begin_press(path, self.target)
        self.assertEqual('stopped', caught.exception.code)
        self.assertEqual(original, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
