import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, cli
from video_publisher.browser_backends import Transaction, execute, work_url, links
from video_publisher.browser_runtime import profile_lock
from video_publisher.core import Ledger, VpubError, file_sha, read_json


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

    def test_a_missing_receipt_is_no_reason_to_upload_again(self):
        """From a review: the file going missing reset an attempt whose record said a file was already chosen, and reached the upload again."""
        for prior in ({'media_selected':True},{'final_submit_attempted':True}):
            with self.subTest(prior=prior), self.assertRaises(VpubError) as caught:
                Transaction('x','tester',self.plan,resume=True,prior_receipt=prior)
            self.assertEqual('receipt_missing',caught.exception.code)

    def test_an_attempt_that_never_started_can_still_start(self):
        for prior in (None,{},{'media_selected':False,'final_submit_attempted':False,'state':'PREPARING'}):
            with self.subTest(prior=prior):
                self.assertFalse(Transaction('x','tester',self.plan,resume=True,prior_receipt=prior).receipt['media_selected'])

    def test_a_receipt_that_is_there_decides_whatever_the_record_says(self):
        first=Transaction('x','tester',self.plan)
        first.save(media_selected=True,upload_filename='clip.mp4')
        again=Transaction('x','tester',self.plan,resume=True,prior_receipt={'media_selected':True})
        self.assertEqual('clip.mp4',again.receipt['upload_filename'])

    def test_resume_after_submit_only_verifies(self):
        tx=Transaction('x','tester',self.plan);tx.save(final_submit_attempted=True,id='123')
        with patch('video_publisher.browser_backends.verify',new=AsyncMock(return_value={'state':'SUBMITTED_UNVERIFIED'})) as verify:
            result=asyncio.run(execute(AsyncMock(),'x','tester',{'action':'resume','plan':self.plan}))
        verify.assert_awaited_once();self.assertEqual('SUBMITTED_UNVERIFIED',result['state'])

    def test_resume_on_douyin_goes_to_its_form_to_look_for_the_kept_draft(self):
        tx=Transaction('douyin','tester',self.plan);tx.save(media_selected=True,upload_filename='x.mp4',before_ids=[])
        with patch('video_publisher.browser_forms.publish_douyin',new=AsyncMock(return_value={'state':'SUBMITTED_UNVERIFIED'})) as publish:
            asyncio.run(execute(AsyncMock(),'douyin','tester',{'action':'resume','plan':self.plan}))
        publish.assert_awaited_once()

    def resume_record(self, *flags, method='browser'):
        cover={'path':str(Path(self.tmp.name)/'cover.jpg'),'sha256':'b'*64}
        plan={**self.plan,'cover':cover['path'],'covers':{'douyin':{'portrait':cover},'x':{'landscape':cover}}}
        ledger=Ledger()
        key=ledger.claim('douyin','default','c'*64,{'options':{'method':method},'upload_plan':plan,'approved_plan':plan,'identity':{'identity':'tester'},'receipt':{'state':'PREPARATION_INCOMPLETE'}})
        ledger.close()
        with patch.object(adapters,'browser_call',return_value={'state':'SUBMITTED_UNVERIFIED'}) as call:
            return cli.execute(cli.parser().parse_args(['resume',key,*flags])),call

    def test_a_resume_keeps_its_cover_unless_told_otherwise(self):
        result,call=self.resume_record()
        self.assertEqual(self.resume_cover_paths(call),(True,True))
        self.assertNotIn('cover_left_out',result)

    def test_a_resume_can_leave_out_a_cover_on_this_platform_only(self):
        result,call=self.resume_record('--no-cover')
        self.assertEqual(self.resume_cover_paths(call),(False,False))
        self.assertEqual(['x'],list(call.call_args.kwargs['plan']['covers']))
        self.assertIs(True,result['cover_left_out'])

    def resume_cover_paths(self,call):
        plan=call.call_args.kwargs['plan']
        return bool(plan.get('cover')),'douyin' in plan['covers']

    def test_leaving_out_a_cover_is_only_for_a_browser_attempt(self):
        with self.assertRaises(VpubError) as caught:
            self.resume_record('--no-cover',method='cli')
        self.assertEqual('invalid_resume',caught.exception.code)

    def test_a_resume_can_be_given_a_cover_its_plan_did_not_have(self):
        image=Path(self.tmp.name)/'new-cover.jpg';image.write_bytes(b'cover')
        _,call=self.resume_record('--cover',str(image))
        plan=call.call_args.kwargs['plan']
        self.assertEqual((str(image.resolve()),file_sha(image)),(plan['cover'],plan['cover_sha256']))
        self.assertEqual(['x'],list(plan['covers']))

    def test_a_cover_that_is_not_an_image_is_refused_before_anything_opens(self):
        wrong=Path(self.tmp.name)/'cover.gif';wrong.write_bytes(b'cover')
        with self.assertRaises(VpubError) as caught:
            self.resume_record('--cover',str(wrong))
        self.assertEqual('invalid_cover',caught.exception.code)

    def test_a_cover_that_is_not_there_is_refused_before_anything_opens(self):
        with self.assertRaises(VpubError) as caught:
            self.resume_record('--cover',str(Path(self.tmp.name)/'missing.jpg'))
        self.assertEqual('invalid_cover',caught.exception.code)

    def test_a_cover_can_not_be_both_added_and_left_out(self):
        with self.assertRaises(SystemExit),patch('sys.stderr'):
            cli.parser().parse_args(['resume','key','--no-cover','--cover','a.jpg'])

    def test_giving_a_cover_is_only_for_a_browser_attempt(self):
        image=Path(self.tmp.name)/'new-cover.jpg';image.write_bytes(b'cover')
        with self.assertRaises(VpubError) as caught:
            self.resume_record('--cover',str(image),method='cli')
        self.assertEqual('invalid_resume',caught.exception.code)

    def test_an_instagram_attempt_that_never_pressed_share_starts_over(self):
        tx=Transaction('instagram','tester',self.plan);tx.save(media_selected=True,hint='Inspect it',blocked_phase='preview')
        with patch('video_publisher.browser_forms.publish_instagram',new=AsyncMock(side_effect=lambda page,tx: tx.save(state='SUBMITTED_UNVERIFIED'))) as publish:
            result=asyncio.run(execute(AsyncMock(),'instagram','tester',{'action':'resume','plan':self.plan}))
        publish.assert_awaited_once()
        self.assertNotIn('hint',result)
        self.assertNotIn('blocked_phase',result)

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
        self.assertEqual(['cli','browser','browser','browser','browser'],
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
        import time
        from video_publisher.browser_forms import finish
        tx=Transaction('x','tester',self.plan)
        post=str((int(time.time()*1000)-1288834974657)<<22)  # An X post's ID carries the moment it was made.
        with patch('video_publisher.browser_forms.works',new=AsyncMock(return_value={post:{'id':post,'url':f'https://x.com/tester/status/{post}','text':'Fixture'}})), \
             patch('video_publisher.browser_forms.verify',new=AsyncMock(return_value={'state':'SUBMISSION_VERIFIED','phase':'complete','id':post})):
            result=asyncio.run(finish(AsyncMock(),tx,[]))
        self.assertEqual('SUBMISSION_VERIFIED',result['state'])
        self.assertEqual(post,read_json(tx.path)['id'])

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

    def test_resume_without_a_saved_receipt_starts_because_no_upload_ever_began(self):
        tx=Transaction('x','tester',self.plan,resume=True)
        self.assertFalse(tx.receipt['media_selected'])
        self.assertFalse(tx.receipt['final_submit_attempted'])

    def test_interrupted_named_copy_is_replaced_and_needs_no_second_copy_of_the_video(self):
        tx=Transaction('douyin','tester',self.plan)
        source=Path(self.plan['video'])
        named=source.with_name('crosscast-'+self.plan['video_sha256'][:16]+source.suffix)
        named.write_bytes(b'fixture')
        self.assertEqual(source.read_bytes(),tx.media().read_bytes())
        self.assertTrue(os.path.samefile(source,named))


if __name__=='__main__': unittest.main()
