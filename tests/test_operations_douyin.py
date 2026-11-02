from copy import deepcopy
import unittest
from unittest.mock import AsyncMock,patch
from video_publisher.operations_douyin import acknowledgement, creator_target
from video_publisher import operations_browser as browser
from video_publisher.core import VpubError

class DouyinReplyTests(unittest.TestCase):
    def setUp(self):
        self.comment={'cid':'reply123','aweme_id':'work123','reply_id':'root123','text':'你好',
                      'user':{'unique_id':'owner','nickname':'Creator'}}

    def test_acknowledgement_never_claims_fresh_visibility(self):
        r=acknowledgement({'status_code':0,'comment':self.comment},'work123','root123','你好','owner')
        self.assertEqual('REPLY_UNVERIFIED',r['state']);self.assertTrue(r['send_acknowledged'])
        self.assertEqual('reply123',r['reply_id']);self.assertEqual('root123',r['acknowledged_parent_id'])

    def test_empty_http_success_and_rejection_do_not_claim_success(self):
        for value in [None,{}, {'status_code':0}]:
            self.assertFalse(acknowledgement(value,'work123','root123','你好','owner')['send_acknowledged'])
        self.assertEqual('REPLY_REJECTED',acknowledgement({'status_code':8},'work123','root123','你好','owner')['state'])

    def test_ack_requires_same_work_parent_author_and_text(self):
        for key,value in [('aweme_id','other'),('reply_id','other'),('text','different'),('user',{'unique_id':'other'}),('cid',None)]:
            c=deepcopy(self.comment);c[key]=value
            self.assertFalse(acknowledgement({'status_code':0,'comment':c},'work123','root123','你好','owner')['send_acknowledged'])

    def test_creator_target_cannot_cross_works_or_ignore_edited_text(self):
        data={'status_code':0,'comments':[self.comment]};target={'id':'reply123','text':'你好'}
        self.assertEqual(self.comment,creator_target(data,'work123',target))
        with self.assertRaises(VpubError):creator_target(data,'other',target)
        with self.assertRaises(VpubError):creator_target(data,'work123',{**target,'text':'Changed'})


class DouyinReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_absolute_time_uses_fresh_id_and_exact_nested_parent(self):
        request={'expected_identity':'owner','text':'你好','comment':{'id':'root123','text':'你好'}}
        saved={'state':'REPLY_UNVERIFIED','at':'2026-10-30T00:00:00Z','submitted_text':'你好','before_reply_ids':['root123']}
        child={'id':'reply123','text':'你好','is_owner':True,'parent_id':'root123','published_at':None}
        payload={'work_id':'work123','operation':'verify_reply','reply_request':request,'existing_reply':saved}
        with (patch.object(browser,'open_work',AsyncMock(return_value='https://www.douyin.com/video/work123')),
              patch.object(browser,'comments',AsyncMock(return_value={'items':[child]}))):
            result=await browser.execute(None,'douyin','owner',payload)
            self.assertEqual('REPLY_VERIFIED',result['state'])
            child['parent_id']=None
            result=await browser.execute(None,'douyin','owner',payload)
            self.assertEqual('REPLY_UNVERIFIED',result['state'])
