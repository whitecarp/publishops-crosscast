from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from video_publisher import adapters, edits, browser_edits, browser_backends
from video_publisher.core import VpubError, atomic_json, digest


class InstagramCaptionTests(unittest.IsolatedAsyncioTestCase):
    async def check_caption(self, before, after=None, verify_only=False):
        page=MagicMock()
        # Comments/translated body text may contain the desired copy while the
        # persisted caption is stale. It must never be accepted as the caption.
        page.locator.return_value.inner_text=AsyncMock(return_value='中文原文\n\nEnglish')
        done=MagicMock();done.click=AsyncMock()
        page.get_by_role.return_value=done
        editor=MagicMock();editor.inner_text=AsyncMock(return_value=before)
        editor.wait_for=AsyncMock()
        async def fill(text): editor.inner_text.return_value=text
        editor.fill=AsyncMock(side_effect=fill)
        reopened=MagicMock();reopened.inner_text=AsyncMock(return_value=after)
        with tempfile.TemporaryDirectory() as tmp:
            payload={'patch':{'post_text':'中文原文\n\nEnglish'},'receipt':{'id':'Reel123'},
                     'edit_receipt_path':str(Path(tmp)/'edit.json'),'verify_only':verify_only}
            with patch.object(browser_edits,'instagram_caption_editor',new_callable=AsyncMock,side_effect=[editor,reopened]), \
                 patch.object(browser_edits,'unique_visible',new_callable=AsyncMock,return_value=done), \
                 patch.object(browser_edits,'risk_check',new_callable=AsyncMock):
                result=await browser_edits.execute(page,'instagram','owner',payload)
        return result,done,editor

    async def test_body_match_cannot_hide_english_only_saved_caption(self):
        result,done,editor=await self.check_caption('English only',verify_only=True)
        self.assertEqual('EDIT_MISMATCH',result['state'])
        self.assertEqual('English only',result['caption'])
        done.click.assert_not_called();editor.fill.assert_not_called()

    async def test_matching_saved_caption_is_read_without_another_save(self):
        result,done,editor=await self.check_caption('中文原文\n\nEnglish')
        self.assertEqual('EDIT_VERIFIED',result['state'])
        self.assertEqual(0,result['platform_writes'])
        done.click.assert_not_called();editor.fill.assert_not_called()

    async def test_save_rejected_by_platform_does_not_report_success(self):
        result,done,_=await self.check_caption('English only',after='English only')
        self.assertEqual('EDIT_SUBMITTED_UNVERIFIED',result['state'])
        self.assertEqual('English only',result['caption'])
        done.click.assert_awaited_once()

    async def test_saved_bilingual_caption_is_reopened_and_read_back(self):
        result,done,_=await self.check_caption('English only',after='中文原文\n\nEnglish')
        self.assertEqual('EDIT_VERIFIED',result['state'])
        self.assertEqual('中文原文\n\nEnglish',result['caption'])
        self.assertEqual('work_caption_editor',result['verification_source'])
        done.click.assert_awaited_once()

    async def test_wrong_work_redirect_cannot_open_editor(self):
        page=MagicMock();page.url='https://www.instagram.com/p/OtherWork/'
        page.goto=AsyncMock();page.locator.return_value.wait_for=AsyncMock()
        with self.assertRaises(VpubError) as error:
            await browser_edits.instagram_caption_editor(page,'Reel123')
        self.assertEqual('wrong_work',error.exception.code)
        page.locator.return_value.click.assert_not_called()

    async def test_delayed_edit_menu_waits_before_unique_control_check(self):
        page=MagicMock();page.url='https://www.instagram.com/p/Reel123/'
        page.goto=AsyncMock()
        more=MagicMock();more.wait_for=AsyncMock();more.click=AsyncMock()
        edit=MagicMock();edit.wait_for=AsyncMock();edit.click=AsyncMock()
        editor=MagicMock();editor.wait_for=AsyncMock()
        page.locator.return_value=more
        page.get_by_role.side_effect=[edit,editor]
        async def unique(locator,label):
            if label=='work Edit': edit.wait_for.assert_awaited_once()
            return locator
        with patch.object(browser_edits,'unique_visible',new=AsyncMock(side_effect=unique)),patch.object(browser_edits,'risk_check',new=AsyncMock()):
            result=await browser_edits.instagram_caption_editor(page,'Reel123')
        self.assertIs(result,editor)
        edit.click.assert_awaited_once()


class EditFreshnessTests(unittest.TestCase):
    def test_old_verified_receipt_does_not_skip_current_platform_read(self):
        value={'expected_identity':'owner','title':'Title','description':'中文原文\n\nEnglish',
               'content_language':'zh-Hans','metadata_language':'en','metadata_mode':'bilingual'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,VPUB_HOME=tmp):
            patch_path=Path(tmp)/'patch.json';atomic_json(patch_path,value)
            checked=edits.validate_patch('instagram',value)
            edit_id=digest(['work-key',checked])
            atomic_json(Path(tmp)/'edits'/(edit_id+'.json'),{'state':'EDIT_VERIFIED','id':'Reel123'})
            ledger=MagicMock()
            ledger.get.return_value={'platform':'instagram','account':'default','state':'SUBMISSION_VERIFIED',
                'data':{'identity':{'identity':'owner'},'receipt':{'id':'Reel123'},'options':{}}}
            with patch.object(edits,'Ledger',return_value=ledger), patch.object(adapters,'browser_call',
                return_value={'state':'EDIT_VERIFIED','id':'Reel123','platform_writes':1}) as browser:
                result=edits.run('work-key',patch_path)
            browser.assert_called_once()
            self.assertEqual(1,result['platform_writes'])


class YouTubeEditTests(unittest.TestCase):
    def setUp(self):
        self.before={'id':'video123','snippet':{'channelId':'channel123','title':'Old','description':'Old copy',
                     'categoryId':'1','tags':['animation'],'defaultLanguage':'zh-Hans','defaultAudioLanguage':'zh-Hans'},
                     'status':{'privacyStatus':'private'}}
        self.patch={'expected_identity':'channel123','title':'New','description':'原文\n\nTranslation'}
        self.service=MagicMock()
        self.service.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'channel123'}]}
        self.service.videos.return_value.list.return_value.execute.return_value={'items':[self.before]}





class ChapterRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_work_id_does_not_open_editor(self):
        receipt={'state':'SUBMITTED_UNVERIFIED','pending_chapters':{'chapters':[]}}
        with patch.object(browser_edits,'execute',new_callable=AsyncMock) as execute:
            result=await browser_backends.complete_pending_chapters(None,'douyin','creator',receipt)
        execute.assert_not_called()
        self.assertEqual('PENDING_WORK_ID',result['chapters_state'])

    async def test_saved_or_uncertain_chapters_never_get_second_save(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,VPUB_HOME=tmp):
            receipt={'id':'123','state':'SUBMISSION_VERIFIED','pending_chapters':{'chapters':[]}}
            for state,expected in [('EDIT_SUBMITTING','SAVE_UNVERIFIED'),('EDIT_SUBMITTED_UNVERIFIED','SAVE_UNVERIFIED'),('EDIT_VERIFIED','VERIFIED')]:
                atomic_json(Path(tmp)/'edits/douyin-chapters-123.json',{'state':state})
                with patch.object(browser_edits,'execute',new_callable=AsyncMock) as execute:
                    result=await browser_backends.complete_pending_chapters(None,'douyin','creator',receipt)
                execute.assert_not_called()
                self.assertEqual(expected,result['chapters_state'])

    def test_times_must_belong_to_matching_titles(self):
        chapters=[{'start_seconds':0,'title':'Opening'},{'start_seconds':14,'title':'Question'}]
        self.assertTrue(browser_edits.chapter_text_matches('章节数：2\n00:00\nOpening\n00:14\nQuestion',chapters))
        self.assertFalse(browser_edits.chapter_text_matches('章节数：2\n00:14\nOpening\n00:00\nQuestion',chapters))
        self.assertFalse(browser_edits.chapter_text_matches('00:00 Opening 00:14 Question',chapters))

    def test_edit_does_not_silently_ignore_a_second_operation(self):
        with self.assertRaises(VpubError):
            edits.validate_patch('douyin',{'expected_identity':'creator','portrait':'p.png','chapters':[]})
