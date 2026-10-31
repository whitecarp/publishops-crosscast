import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_publisher import upload_resume as u
from video_publisher.core import VpubError, file_sha, read_json

URI='https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=test'


class Response(dict):
    def __init__(self, status, **headers):
        super().__init__(headers)
        self.status=status


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=self.tmp.name);env.start();self.addCleanup(env.stop)
        video=Path(self.tmp.name)/'video.mp4';video.write_bytes(b'a'*(256*1024)+b'b'*(256*1024)+b'end')
        self.plan={'video':str(video),'video_sha256':file_sha(video),'video_bytes':video.stat().st_size}
        self.calls=[];self.events=[];self.responses=[]
        def request(uri,method,body=None,headers=None):
            self.calls.append((uri,method,body,headers))
            value=self.responses.pop(0)
            if isinstance(value,Exception):raise value
            return value
        self.req=SimpleNamespace(http=SimpleNamespace(request=request),uri=URI.split('&upload_id')[0],method='POST',
            headers={},body='{"status":{"privacyStatus":"private"}}',body_size=38,
            resumable=SimpleNamespace(mimetype=lambda:'video/mp4'))

    def run_upload(self,**kw):
        return u.run(self.req,self.plan,'default','owner',self.events.append,chunk_size=256*1024,**kw)

    def init(self):return (Response(200,location=URI),b'')
    def partial(self,n):return (Response(308,range=f'bytes=0-{n-1}'),b'')
    def done(self):return (Response(201),b'{"id":"abcdefghijk"}')

    def test_resume_probes_server_and_skips_accepted_chunk_after_lost_response(self):
        self.responses=[self.init(),TimeoutError()]
        with self.assertRaises(VpubError):self.run_upload()
        self.responses=[self.partial(256*1024),self.partial(512*1024),self.done()]
        value=self.run_upload()
        self.assertEqual(value['id'],'abcdefghijk')
        self.assertEqual(sum(c[1]=='POST' for c in self.calls),1)
        self.assertEqual(self.calls[2][3]['Content-Range'],f'bytes */{self.plan["video_bytes"]}')
        self.assertEqual(self.calls[3][2],b'b'*(256*1024))
        self.assertNotIn('upload_id',json.dumps(self.events))

    def test_lost_final_response_returns_same_id_without_reupload(self):
        self.responses=[self.init(),TimeoutError()]
        with self.assertRaises(VpubError):self.run_upload()
        self.responses=[self.done()]
        self.assertEqual(self.run_upload()['id'],'abcdefghijk')
        self.assertIsNone(self.calls[-1][2])
        before=len(self.calls);self.run_upload();self.assertEqual(len(self.calls),before)

    def test_pause_saves_offset_then_new_call_continues(self):
        self.responses=[self.init(),self.partial(256*1024)]
        self.assertEqual(self.run_upload(max_chunks=1)['state'],'UPLOAD_PAUSED')
        self.responses=[self.partial(256*1024),self.partial(512*1024),self.done()]
        self.assertEqual(self.run_upload()['state'],'SUBMITTED')

    def test_session_expiry_does_not_create_second_upload(self):
        self.responses=[self.init(),TimeoutError()]
        with self.assertRaises(VpubError):self.run_upload()
        self.responses=[(Response(404),b'')]
        with self.assertRaisesRegex(VpubError,'HTTP 404'):self.run_upload()
        self.assertEqual(sum(c[1]=='POST' for c in self.calls),1)

    def test_missing_initialization_response_never_retries_post(self):
        self.responses=[TimeoutError()]
        with self.assertRaises(VpubError):self.run_upload()
        with self.assertRaises(VpubError):self.run_upload()
        self.assertEqual(len(self.calls),1)

    def test_changed_source_or_metadata_sends_nothing(self):
        self.responses=[self.init(),self.partial(256*1024)]
        self.run_upload(max_chunks=1);before=len(self.calls)
        self.req.body='different'
        with self.assertRaises(VpubError):self.run_upload()
        self.assertEqual(len(self.calls),before)
        Path(self.plan['video']).write_bytes(b'changed')
        with self.assertRaises(VpubError):self.run_upload()
        self.assertEqual(len(self.calls),before)

    def test_account_changed_fails_before_query(self):
        self.responses=[self.init(),self.partial(256*1024)];self.run_upload(max_chunks=1)
        before=len(self.calls)
        with self.assertRaises(VpubError):u.run(self.req,self.plan,'default','other',self.events.append)
        self.assertEqual(len(self.calls),before)

    def test_changed_cover_fails_before_network(self):
        from video_publisher import adapters
        plan={**self.plan,'cover':self.plan['video'],'cover_sha256':'different'}
        with patch.object(adapters,'yt_service') as service:
            with self.assertRaises(VpubError):adapters.publish_youtube(plan,{'account':'default'},self.events.append)
            service.assert_not_called()

    def test_regressing_offset_and_untrusted_destination_rejected(self):
        self.responses=[self.init(),self.partial(256*1024)];self.run_upload(max_chunks=1)
        self.responses=[self.partial(1)]
        with self.assertRaises(VpubError):self.run_upload()
        for value in ('https://evil.example/upload/youtube/v3/videos','http://www.googleapis.com/upload/youtube/v3/videos',
                      'https://www.googleapis.com@evil.example/upload/youtube/v3/videos'):
            with self.assertRaises(VpubError):u.session_url(value)
