import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_publisher import process_lock
from video_publisher.core import VpubError, publication_lock, home


class ProcessLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.dict(os.environ, VPUB_HOME=self.tmp.name, BILI_CLI_HOME=self.tmp.name + '/bili')
        p.start(); self.addCleanup(p.stop)

    def child(self, code):
        return subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=15)

    def test_real_hard_exit_releases_kernel_lock_and_dead_marker(self):
        result = self.child('import os; from video_publisher.core import publication_lock;\nwith publication_lock(): os._exit(75)')
        self.assertEqual(result.returncode, 75)
        self.assertTrue((home() / 'publisher.lock').exists())
        with publication_lock():
            self.assertEqual(json.loads((home() / 'publisher.lock').read_text())['pid'], os.getpid())
        self.assertTrue(process_lock.released(json.loads((home() / 'publisher.lock').read_text())))

    def test_real_child_cannot_enter_active_lock(self):
        with publication_lock():
            result = self.child('from video_publisher.core import publication_lock;\nwith publication_lock(): print("entered")')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'entered', result.stdout)
            self.assertTrue((home() / 'publisher.lock').exists())

    def test_live_legacy_owner_not_removed(self):
        for value in (str(os.getpid()), json.dumps({'pid': os.getpid()}), 'damaged', '{}', 'false'):
            marker = home() / 'publisher.lock'
            marker.write_text(value)
            with self.assertRaises(VpubError):
                with publication_lock(): pass
            self.assertEqual(marker.read_text(), value)
            marker.unlink()

    def test_release_never_removes_replacement_marker(self):
        with publication_lock():
            (home() / 'publisher.lock').write_text('{"pid": 42, "token": "other"}')
        self.assertTrue((home() / 'publisher.lock').exists())

    def test_profile_marker_recovers_after_hard_exit(self):
        result = self.child('import os; from video_publisher.browser_runtime import profile_lock;\nwith profile_lock("x", "default"): os._exit(75)')
        self.assertEqual(result.returncode, 75)
        from video_publisher.browser_runtime import profile_lock
        with profile_lock('x', 'default'): pass

    def test_live_process_detection_is_conservative(self):
        self.assertTrue(process_lock.alive(os.getpid()))
        self.assertTrue(process_lock.alive(None))
        self.assertTrue(process_lock.alive(False))

    def test_unreadable_marker_has_its_own_error_and_preserves_bytes(self):
        marker = home() / 'publisher.lock'
        original = b'{"pid": 42, "token": "offline-marker"}'
        marker.write_bytes(original)
        read_text = Path.read_text
        def denied(path, *args, **kwargs):
            if path == marker:
                raise PermissionError(13, 'synthetic access denied')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', denied):
            with self.assertRaises(VpubError) as caught:
                with publication_lock():
                    self.fail('unreadable marker admitted a command')
        self.assertEqual('lock_marker_unreadable', caught.exception.code)
        self.assertIn('ownership and permissions', caught.exception.hint)
        self.assertEqual(original, marker.read_bytes())
        # The failed attempt still releases its kernel guard.
        with patch.object(process_lock, 'alive', return_value=False), publication_lock():
            pass
