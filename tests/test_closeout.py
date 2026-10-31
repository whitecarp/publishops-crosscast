import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import adapters, process_lock
from video_publisher.core import VpubError, publication_lock, home, file_sha


class ProcessLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.dict(os.environ, VPUB_HOME=self.tmp.name)
        p.start(); self.addCleanup(p.stop)

    def child(self, code):
        return subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=15)

    def test_real_hard_exit_releases_kernel_lock_and_dead_marker(self):
        result = self.child('import os; from video_publisher.core import publication_lock;\nwith publication_lock(): os._exit(75)')
        self.assertEqual(result.returncode, 75)
        self.assertTrue((home() / 'publisher.lock').exists())
        with publication_lock():
            self.assertEqual(json.loads((home() / 'publisher.lock').read_text())['pid'], os.getpid())
        self.assertFalse((home() / 'publisher.lock').exists())

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


class YoutubeCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        cover = Path(self.tmp.name) / 'cover.jpg'; cover.write_bytes(b'cover')
        self.plan = {'cover': str(cover), 'cover_sha256': file_sha(cover)}
        self.opts = {'account': 'default', 'expected_channel_id': 'owner', 'visibility': 'private'}
        self.receipt = {'state': 'SUBMITTED', 'method': 'api', 'id': 'abcdefghijk',
                        'postprocess': {'cover': 'PENDING', 'playlist_ids': ['list']}}
        self.service = MagicMock()
        self.service.channels().list().execute.return_value = {'items': [{'id': 'owner'}]}
        self.service.videos().list().execute.return_value = {'items': [{'id': 'abcdefghijk',
            'snippet': {'channelId': 'owner'}, 'status': {'privacyStatus': 'private', 'uploadStatus': 'processed'}}]}
        self.service.thumbnails().set().execute.return_value = {'items': [{'default': {'url': 'https://example.test/cover.jpg'}}]}
        self.service.playlistItems().list().execute.return_value = {'items': [{'id': 'member'}]}
        self.events = []

    def finish(self, checkpoint=None):
        with patch.object(adapters, 'yt_service', return_value=self.service):
            return adapters.finish_youtube(self.plan, self.opts, self.receipt,
                checkpoint or (lambda value: self.events.append(deepcopy(value))))

    def test_known_id_resumes_missing_steps_without_video_upload(self):
        result = self.finish()
        self.assertEqual(result['state'], 'SUBMISSION_VERIFIED')
        self.assertEqual(result['postprocess']['cover'], 'APPLIED')
        self.assertEqual(result['playlists']['list']['state'], 'VERIFIED')
        self.service.videos().insert.assert_not_called()
        count = self.service.thumbnails().set.call_count
        self.finish()
        self.assertEqual(self.service.thumbnails().set.call_count, count)

    def test_killed_after_cover_submission_marker_never_resends(self):
        def killed(value):
            if value['postprocess']['cover'] == 'SUBMITTING': raise SystemExit(75)
        baseline = self.service.thumbnails().set.call_count
        with self.assertRaises(SystemExit): self.finish(killed)
        result = self.finish()
        self.assertEqual(result['state'], 'SUBMITTED_UNVERIFIED')
        self.assertEqual(self.service.thumbnails().set.call_count, baseline)

    def test_verify_does_not_call_uploaded_video_complete_with_pending_steps(self):
        with patch.object(adapters, 'yt_service', return_value=self.service):
            result = adapters.verify('youtube', 'default', self.receipt)
        self.assertEqual(result['state'], 'SUBMITTED_UNVERIFIED')

    def test_changed_cover_blocks_before_any_network(self):
        Path(self.plan['cover']).write_bytes(b'changed')
        with patch.object(adapters, 'yt_service') as service:
            with self.assertRaises(VpubError):
                adapters.finish_youtube(self.plan, self.opts, self.receipt, lambda value: None)
            service.assert_not_called()

    def test_different_video_owner_blocks_thumbnail_and_playlist_writes(self):
        self.service.videos().list().execute.return_value['items'][0]['snippet']['channelId'] = 'other'
        baseline = self.service.thumbnails().set.call_count
        with self.assertRaises(VpubError): self.finish()
        self.assertEqual(self.service.thumbnails().set.call_count, baseline)
        self.service.playlistItems().insert.assert_not_called()

    def test_timeout_stays_unverified_and_does_not_resend_cover(self):
        self.service.thumbnails().set().execute.side_effect = TimeoutError
        self.assertEqual(self.finish()['state'], 'SUBMITTED_UNVERIFIED')
        count = self.service.thumbnails().set.call_count
        self.finish()
        self.assertEqual(self.service.thumbnails().set.call_count, count)
