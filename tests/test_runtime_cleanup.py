"""Deferred disposable files; fake workers only, with isolated account homes."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import core, hidden_desktop, overview, process_lock, progress, runtime_files, runtime_delete, runtime_scratch


class RuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, VPUB_HOME=str(self.root / 'vpub'), BILI_CLI_HOME=str(self.root / 'bili'))
        env.start()
        self.addCleanup(env.stop)

    def worker(self, argv, timeout):
        request = core.read_json(argv[-1])
        core.atomic_json(request['result_path'], {'ok': True, 'data': {'state': 'SUBMISSION_VERIFIED'}})

    def finished_worker(self):
        with patch.object(hidden_desktop, 'run', side_effect=self.worker):
            return hidden_desktop.call_worker({'action': 'publish'}, 1)

    def test_sixty_publish_lifecycles_do_not_attempt_any_unlink(self):
        source = self.root / 'original.mp4'
        source.write_bytes(b'approved media')
        sha = core.file_sha(source)
        with patch('os.unlink', side_effect=AssertionError('inline deletion')) as unlink, contextlib.redirect_stderr(io.StringIO()):
            for index in range(60):
                with core.publication_lock(), progress.relay('youtube', {}):
                    self.assertTrue(overview.workspace_busy())
                    target = core.home() / 'jobs' / str(index) / 'video.mp4'
                    core.snapshot_file(source, target, sha, link=True)
                    self.assertEqual('SUBMISSION_VERIFIED', self.finished_worker()['data']['state'])
                self.assertFalse(overview.workspace_busy())
            unlink.assert_not_called()
        self.assertEqual(180, len(list((core.home() / 'browser-jobs').iterdir())))
        self.assertTrue((core.home() / 'publisher.lock.guard').exists())

    @unittest.skipUnless(os.name == 'nt', 'Safe explicit cleanup currently requires Windows')
    def test_preview_preserves_and_apply_removes_only_registered_exited_owner_files(self):
        self.finished_worker()
        unknown = core.home() / 'browser-jobs' / ('f' * 32 + '.json')
        native = core.home() / 'browser-jobs' / ('a' * 64 + '.json')
        receipt = core.home() / 'browser-receipts' / 'keep.json'
        for path in (unknown, native, receipt):
            core.atomic_json(path, {'keep': True})
        before = {p: p.read_bytes() for p in (core.home() / 'browser-jobs').iterdir()}
        with patch.object(runtime_files, 'alive', return_value=False):
            preview = overview.cleanup()
            self.assertEqual(2, len(preview['runtime_files']['removable'][0]['files']))
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            result = overview.cleanup(apply=True)
        self.assertEqual(1, len(result['runtime_files']['removed']))
        for path in (unknown, native, receipt):
            self.assertEqual({'keep': True}, core.read_json(path))
        self.assertTrue((core.home() / 'publisher.lock.guard').exists())

    @unittest.skipUnless(os.name == 'nt', 'Safe explicit cleanup currently requires Windows')
    def test_live_owner_and_uncertain_worker_are_never_cleaned(self):
        self.finished_worker()
        result = overview.cleanup(apply=True)['runtime_files']
        self.assertEqual([], result['removed'])
        self.assertEqual('owner_active_or_unknown', result['kept'][0]['kept_because'])
        def uncertain(argv, timeout):
            self.worker(argv, timeout)
            raise subprocess.TimeoutExpired(argv, timeout)
        with patch.object(hidden_desktop, 'run', side_effect=uncertain):
            self.assertTrue(hidden_desktop.call_worker({'action': 'publish'}, 1)['ok'])
        with patch.object(runtime_files, 'alive', return_value=False):
            result = overview.cleanup(apply=True)['runtime_files']
        self.assertEqual('worker_completion_unknown', result['kept'][0]['kept_because'])
        self.assertEqual(2, len(list((core.home() / 'browser-jobs').iterdir())))

    def test_changed_or_replaced_file_is_retained(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                path = core.home() / 'browser-jobs' / ('e' * 32 + '.stages')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'original')
                runtime_files.defer(path)
                if replaced:
                    replacement = self.root / 'replacement'
                    replacement.write_bytes(b'original')
                    os.replace(replacement, path)
                else:
                    path.write_bytes(b'changed')
                with patch.object(runtime_files, 'alive', return_value=False):
                    result = runtime_files.cleanup(apply=True)
                self.assertEqual([], result['removed'])
                self.assertTrue(path.exists())

    def test_unknown_manifest_and_outside_path_are_left_alone(self):
        outside = self.root / '.vpub-abcdefgh'
        outside.write_bytes(b'private original')
        folder = core.home() / 'runtime-cleanup'
        core.atomic_json(folder / ('b' * 32 + '.json'), {'schema': 'unknown', 'pid': 1})
        core.atomic_json(folder / ('c' * 32 + '.json'), {'schema': 'crosscast-runtime-cleanup-v1', 'pid': 1,
            'ready': True, 'files': [{'path': '../.vpub-abcdefgh', **runtime_files._stamp(outside)}]})
        with patch.object(runtime_files, 'alive', return_value=False):
            self.assertEqual([], runtime_files.cleanup(apply=True)['removed'])
        self.assertEqual(b'private original', outside.read_bytes())
        self.assertEqual(2, len(list(folder.iterdir())))

    def test_reparse_replacement_is_never_followed(self):
        path = core.home() / 'browser-jobs' / ('d' * 32 + '.stages')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        runtime_files.defer(path)
        outside_folder = self.root / 'outside'
        outside_folder.mkdir()
        outside = outside_folder / path.name
        outside.write_bytes(b'private original')
        path.unlink()
        path.parent.rmdir()
        if os.name == 'nt':
            # A junction needs no symlink privilege. Both ends are disposable
            # fixture directories; no original or account directory is involved.
            self.assertEqual(core.home(), path.parent.parent)
            made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(path.parent), str(outside_folder)],
                                  capture_output=True, timeout=10)
            self.assertEqual(0, made.returncode)
        else:
            path.parent.symlink_to(outside_folder, target_is_directory=True)
        with patch.object(runtime_files, 'alive', return_value=False):
            self.assertEqual([], runtime_files.cleanup(apply=True)['removed'])
        self.assertTrue(path.exists())
        self.assertEqual(b'private original', outside.read_bytes())

    def test_failed_atomic_write_is_retained_without_masking_original_error(self):
        path = core.home() / 'receipt.json'
        with patch.object(core.os, 'replace', side_effect=OSError('fixture write failed')), \
                patch('os.unlink', side_effect=AssertionError('inline deletion')) as unlink:
            with self.assertRaisesRegex(OSError, 'fixture write failed'):
                core.atomic_json(path, {'state': 'SUBMITTED_UNVERIFIED'})
            unlink.assert_not_called()
        self.assertFalse(path.exists())
        self.assertEqual(1, len(list(core.home().glob('.vpub-*'))))

    def test_copy_fallback_and_bad_hash_never_delete_or_bypass_hash(self):
        source = self.root / 'original.mp4'
        source.write_bytes(b'approved')
        target = core.home() / 'jobs' / 'copy' / 'video.mp4'
        with patch.object(core.os, 'link', side_effect=OSError('no hardlinks')), \
                patch('os.unlink', side_effect=AssertionError('inline deletion')) as unlink:
            core.snapshot_file(source, target, core.file_sha(source), link=True)
            self.assertEqual(source.read_bytes(), target.read_bytes())
            bad = target.with_name('bad.mp4')
            with self.assertRaises(core.VpubError):
                core.snapshot_file(source, bad, '0' * 64, link=True)
            self.assertFalse(bad.exists())
            unlink.assert_not_called()

    def test_hardlink_candidate_collision_preserves_the_unknown_file(self):
        source = self.root / 'original.mp4'
        source.write_bytes(b'approved')
        folder = core.home() / 'jobs' / 'collision'
        folder.mkdir(parents=True)
        existing = folder / ('.vpub-' + 'f' * 32)
        existing.write_bytes(b'not ours')
        nonce = type('Nonce', (), {'hex': 'f' * 32})()
        with patch.object(core.uuid, 'uuid4', return_value=nonce):
            core.snapshot_file(source, folder / 'video.mp4', core.file_sha(source), link=True)
        self.assertEqual(b'not ours', existing.read_bytes())
        self.assertEqual(b'approved', (folder / 'video.mp4').read_bytes())

    def test_released_marker_does_not_change_kernel_guard_or_allow_a_second_owner(self):
        guard = core.home() / 'publisher.lock.guard'
        for _ in range(2):
            with core.publication_lock():
                with self.assertRaises(core.VpubError):
                    with core.publication_lock():
                        self.fail('second owner entered')
                identity = guard.stat().st_ino
            self.assertTrue(process_lock.released(core.read_json(core.home() / 'publisher.lock')))
            self.assertEqual(identity, guard.stat().st_ino)

    def test_cleanup_is_blocked_by_active_publisher(self):
        self.finished_worker()
        with core.publication_lock(), self.assertRaises(core.VpubError):
            overview.cleanup(apply=True)

    def disposable(self):
        path = core.home() / 'browser-jobs' / ('9' * 32 + '.stages')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'owned runtime data')
        runtime_files.defer(path)
        return path

    def test_replacement_after_last_path_check_is_not_deleted(self):
        path = self.disposable()
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'unknown replacement')
        original = runtime_delete.remove
        def switched(target, expected, **kwargs):
            if target == path:
                os.replace(replacement, target)
            return original(target, expected, **kwargs)
        with patch.object(runtime_delete, 'remove', side_effect=switched), patch.object(runtime_files, 'alive', return_value=False):
            self.assertEqual([], runtime_files.cleanup(apply=True)['removed'])
        self.assertEqual(b'unknown replacement', path.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows handle disposition')
    def test_held_handle_prevents_replacement_during_validation(self):
        path = self.disposable()
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'unknown replacement')
        expected, original_read, tried = runtime_files._stamp(path), os.read, []
        def attempt_replace(fd, size):
            if not tried:
                tried.append(True)
                with self.assertRaises(PermissionError):
                    os.replace(replacement, path)
            return original_read(fd, size)
        with patch.object(runtime_delete.os, 'read', side_effect=attempt_replace):
            runtime_delete.remove(path, expected)
        self.assertEqual([True], tried)
        self.assertFalse(path.exists())
        self.assertEqual(b'unknown replacement', replacement.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction and handle disposition')
    def test_ancestor_junction_to_identical_outside_hardlink_is_refused(self):
        path = self.disposable()
        outside = self.root / 'outside'
        outside.mkdir()
        other = outside / path.name
        os.link(path, other)
        original = runtime_delete.remove
        def switched(target, expected, **kwargs):
            if target == path:
                backup = self.root / 'owned-backup'
                self.assertEqual(core.home(), path.parent.parent)
                self.assertEqual(self.root, backup.parent)
                path.parent.rename(backup)
                made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(path.parent), str(outside)], capture_output=True, timeout=10)
                self.assertEqual(0, made.returncode)
            return original(target, expected, **kwargs)
        with patch.object(runtime_delete, 'remove', side_effect=switched), patch.object(runtime_files, 'alive', return_value=False):
            self.assertEqual([], runtime_files.cleanup(apply=True)['removed'])
        self.assertEqual(b'owned runtime data', other.read_bytes())

    def test_retained_hardlink_reports_no_freed_bytes(self):
        path = self.disposable()
        original = self.root / 'original'
        os.link(path, original)
        with patch.object(runtime_files, 'alive', return_value=False):
            self.assertEqual(0, runtime_files.cleanup()['reclaimable_bytes'])
            result = runtime_files.cleanup(apply=True)
        self.assertEqual(0, result['freed_bytes'])
        self.assertEqual(b'owned runtime data', original.read_bytes())
        self.assertIn('not measured free disk space', result['byte_accounting'])

    def test_unsupported_safe_delete_primitive_retains_files(self):
        path = self.disposable()
        with patch.object(runtime_files, 'alive', return_value=False), \
                patch.object(runtime_delete, 'remove', side_effect=OSError('safe primitive unavailable')):
            self.assertEqual([], runtime_files.cleanup(apply=True)['removed'])
        self.assertTrue(path.exists())

    def test_scratch_retains_unknown_added_files_and_final_outputs(self):
        with runtime_scratch.scratch(self.root) as scratch:
            (scratch / 'owned.json').write_bytes(b'owned')
        (scratch / 'unknown.json').write_bytes(b'unknown')
        with runtime_scratch.scratch(self.root) as promoted:
            (promoted / 'final.jpg').write_bytes(b'final output')
            promoted.rename(self.root / 'final')
        with patch.object(runtime_files, 'alive', return_value=False):
            result = runtime_files.cleanup(apply=True)
        self.assertEqual([], result['removed'])
        self.assertTrue((scratch / 'unknown.json').exists())
        self.assertTrue((self.root / 'final/final.jpg').exists())


if __name__ == '__main__':
    unittest.main()
