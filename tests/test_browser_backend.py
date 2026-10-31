import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, cli
from video_publisher.browser_backends import Transaction, execute, work_url, links
from video_publisher.browser_runtime import check_identity, profile_lock
from video_publisher.core import VpubError, file_sha, read_json


class BrowserSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, VPUB_HOME=self.tmp.name)
        self.env.start()
        video = Path(self.tmp.name)/'video.mp4'
        video.write_bytes(b'fixture-video')
        self.plan = {'video':str(video),'video_sha256':file_sha(video), 'confirmation':'a'*64,
                     'options':{'title':'Fixture', 'submission_mode':'publish'}}

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def test_timed_out_submit_is_durable_and_cannot_click_again(self):
        tx = Transaction('x','tester',self.plan)
        page = AsyncMock(); page.url='https://studio.x.com/library/13_123/compose'
        button = AsyncMock(); button.click.side_effect=TimeoutError()
        with patch('video_publisher.browser_backends.risk_check',new=AsyncMock()):
            with self.assertRaises(TimeoutError): asyncio.run(tx.submit(page,button))
            self.assertTrue(read_json(tx.path)['final_submit_attempted'])
            with self.assertRaises(VpubError) as error: asyncio.run(tx.submit(page,button))
            self.assertEqual('already_submitted',error.exception.code)
            self.assertEqual(1,button.click.await_count)

    def test_draft_only_has_no_final_click(self):
        self.plan['options']['submission_mode']='draft_only'
        tx=Transaction('instagram','tester',self.plan); button=AsyncMock()
        with self.assertRaises(VpubError): asyncio.run(tx.submit(AsyncMock(),button))
        button.click.assert_not_awaited()

    def test_existing_attempt_cannot_be_recreated_or_account_switched(self):
        tx=Transaction('x','tester',self.plan);tx.save()
        with self.assertRaises(VpubError): Transaction('x','tester',self.plan)
        with self.assertRaises(VpubError): Transaction('x','someone_else',self.plan,resume=True)

    def test_resume_after_submit_only_verifies(self):
        tx=Transaction('x','tester',self.plan);tx.save(final_submit_attempted=True,id='123')
        with patch('video_publisher.browser_backends.verify',new=AsyncMock(return_value={'state':'SUBMITTED_UNVERIFIED'})) as verify:
            result=asyncio.run(execute(AsyncMock(),'x','tester',{'action':'resume','plan':self.plan}))
        verify.assert_awaited_once();self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])

    def test_selected_instagram_media_cannot_be_uploaded_again_by_resume(self):
        tx=Transaction('instagram','tester',self.plan);tx.save(media_selected=True)
        with self.assertRaises(VpubError) as e:
            asyncio.run(execute(AsyncMock(),'instagram','tester',{'action':'resume','plan':self.plan}))
        self.assertEqual('inspect_existing_draft',e.exception.code)

    def test_changed_media_rejected_before_upload(self):
        tx=Transaction('douyin','tester',self.plan)
        Path(self.plan['video']).write_bytes(b'changed')
        with self.assertRaises(VpubError): tx.media()
        self.assertFalse(tx.receipt['media_selected'])

    def test_wrong_account_fails_preflight(self):
        with patch.object(adapters,'auth_status',return_value={'identity':'other'}):
            with self.assertRaises(VpubError) as e:
                adapters.preflight('instagram',{'method':'browser','account':'default','expected_identity':'tester'})
        self.assertEqual('wrong_account',e.exception.code)

    def test_profile_lock_rejects_concurrent_command_then_releases(self):
        with profile_lock('x','default'):
            with self.assertRaises(VpubError):
                with profile_lock('x','default'): pass
        with profile_lock('x','default'): pass

    def test_verification_ids_cannot_redirect_browser(self):
        for platform in ['x','douyin','instagram']:
            with self.assertRaises(VpubError): work_url(platform,'tester','https://example.com')

    def test_default_routes_and_explicit_native_fallback(self):
        self.assertEqual(['cli','native','browser','browser','browser'],
                         [adapters.default_method(p) for p in cli.PLATFORMS])
        with patch.object(adapters,'browser_call') as browser:
            result=adapters.auth_login('instagram','default',method='native')
        browser.assert_not_called();self.assertIsNone(result['logged_in'])

    def test_instagram_grid_links_accept_own_prefix_and_reject_other_author(self):
        page=MagicMock()
        page.locator.return_value.evaluate_all=AsyncMock(return_value=[
            {'url':'https://www.instagram.com/tester/reel/Abc_12/','text':'Fixture caption'},
            {'url':'https://www.instagram.com/other/reel/NotMine/','text':'Fixture caption'},
            {'url':'https://www.instagram.com/p/Legacy1/','text':'Older caption'}])
        found=asyncio.run(links(page,'instagram','tester'))
        self.assertEqual({'Abc_12','Legacy1'},set(found))
        self.assertEqual('Fixture caption',found['Abc_12']['text'])

    def test_finish_saves_verified_result_with_existing_phase(self):
        from video_publisher.browser_forms import finish
        tx=Transaction('x','tester',self.plan)
        with patch('video_publisher.browser_forms.works',new=AsyncMock(return_value={'123':{'id':'123','url':'https://x.com/tester/status/123','text':'Fixture'}})), \
             patch('video_publisher.browser_forms.verify',new=AsyncMock(return_value={'state':'SUBMISSION_VERIFIED','phase':'complete','id':'123'})):
            result=asyncio.run(finish(AsyncMock(),tx,[]))
        self.assertEqual('SUBMISSION_VERIFIED',result['state'])
        self.assertEqual('123',read_json(tx.path)['id'])

    def test_portrait_only_instagram_artwork_is_not_silently_ignored(self):
        self.plan['covers']={'instagram':{'portrait':{'path':'portrait.jpg'}}}
        tx=Transaction('instagram','tester',self.plan)
        self.assertEqual('portrait.jpg',tx.cover())
        self.plan['cover']='original.jpg'
        self.assertEqual('original.jpg',tx.cover())

    def test_resume_rechecks_cover_before_touching_saved_studio_media(self):
        cover=Path(self.tmp.name)/'cover.jpg';cover.write_bytes(b'original')
        self.plan.update(cover=str(cover),cover_sha256=file_sha(cover))
        tx=Transaction('x','tester',self.plan);tx.save(media_selected=True)
        cover.write_bytes(b'changed')
        with patch('video_publisher.browser_forms.publish_x',new=AsyncMock()) as publish:
            page=MagicMock();page.url='https://studio.x.com/library'
            page.screenshot=AsyncMock()
            page.locator.return_value.evaluate_all=AsyncMock(return_value=[])
            result=asyncio.run(execute(page,'x','tester',{'action':'resume','plan':self.plan}))
        self.assertEqual('source_changed',result['error']['code'])
        publish.assert_not_awaited()


if __name__=='__main__': unittest.main()
