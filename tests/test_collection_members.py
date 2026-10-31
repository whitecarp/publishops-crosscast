import unittest
import test_collections as base
from video_publisher import collections as c
from video_publisher.collections_instagram import members
from video_publisher.collections_browser import instagram_response
from video_publisher.collections_douyin import rows
from video_publisher.core import VpubError


class MembershipTests(unittest.TestCase):
    fake=base.CollectionTests.fake
    def setUp(self):
        base.CollectionTests.setUp(self);self.saved=['12345678'];self.cid='123';self.member_calls=[]
        original=self.fake
        def fake(p,op,owner,**kw):
            if op=='items':return {'identity':owner,'collection_id':self.cid,'items':[{'id':i} for i in self.saved],'complete':self.complete}
            if op in {'add','remove'}:
                self.member_calls.append(op);c.before_submit(dict(platform=p,operation=op,**kw),owner)
                if op=='add':self.saved.append(kw['work_id'])
                else:self.saved.remove(kw['work_id'])
                return {}
            return original(p,op,owner,**kw)
        self.mock.side_effect=fake

    def test_add_preserves_other_members(self):
        r=c.membership('douyin','add','123','23456789','a')
        self.assertEqual(r['state'],'CONFIRMED');self.assertEqual(self.saved,['12345678','23456789'])
        self.assertEqual(c.membership('douyin','add','123','23456789','a')['platform_writes'],0)
        self.assertEqual(self.member_calls,['add'])

    def test_remove_preserves_other_members(self):
        self.saved.append('23456789');r=c.membership('douyin','remove','123','23456789','r')
        self.assertEqual(r['state'],'CONFIRMED');self.assertEqual(self.saved,['12345678'])

    def test_partial_baseline_blocks_write(self):
        self.complete=False
        with self.assertRaises(VpubError):c.membership('douyin','add','123','23456789','a')
        self.assertFalse(self.member_calls)

    def test_noop_does_not_write(self):
        r=c.membership('douyin','add','123','12345678','a');self.assertEqual(r['platform_writes'],0)

    def test_readback_with_lost_other_member_is_not_success(self):
        r={'action':'add','identity':'owner','collection_id':'123','before_members':['12345678'],'work_id':'23456789'}
        result=c.checked_result(r,{'identity':'owner','collection_id':'123','complete':True,'items':[{'id':'23456789'}]})
        self.assertEqual(result['state'],'UNVERIFIED')

    def test_readback_wrong_folder_rejected(self):
        r={'action':'add','identity':'owner','collection_id':'123','before_members':[],'work_id':'23456789'}
        with self.assertRaises(VpubError):c.checked_result(r,{'identity':'owner','collection_id':'999','complete':True,'items':[]})

    def test_changed_receipt_baseline_blocks_submit(self):
        def fake(p,op,owner,**kw):
            if op=='items':return {'identity':owner,'collection_id':'123','complete':True,'items':[]}
            c.before_submit(dict(platform=p,operation=op,**{**kw,'before_members':['999']}),owner)
        self.mock.side_effect=fake
        r=c.membership('douyin','add','123','12345678','a')
        self.assertEqual(r['state'],'FAILED_BEFORE_SUBMIT');self.assertEqual(r['platform_writes'],0)

    def test_uncertain_member_write_verify_never_replays(self):
        r=c.membership('douyin','add','123','23456789','a');self.member_calls.clear()
        self.assertEqual(c.verify(r['key'])['state'],'CONFIRMED');self.assertFalse(self.member_calls)


class CollectionSchemaTests(unittest.TestCase):
    def ig(self,edges,more=False):return {'data':{'fetch__MediaCollection':{'id':'123','media':{'edges':edges,'page_info':{'has_next_page':more}}}}}

    def test_ig_members_bind_both_ids(self):
        r=members(self.ig([{'node':{'code':'Abcde12','pk':'12345678'}}]),'123')
        self.assertEqual(r['items'],[{'id':'Abcde12','media_id':'12345678'}]);self.assertTrue(r['complete'])

    def test_ig_pagination_is_not_complete(self):self.assertFalse(members(self.ig([],True),'123')['complete'])

    def test_ig_wrong_id_rejected(self):
        with self.assertRaises(VpubError):members(self.ig([]),'456')

    def test_ig_bad_member_rejected(self):
        with self.assertRaises(VpubError):members(self.ig([{'node':{'code':'Abcde12','pk':'../123'}}]),'123')

    def test_ig_all_saved_not_mutable(self):
        d={'data':{'viewer':{'collections_unified_with_auto_collections':{'edges':[{'node':{'__typename':'IGAllMediaAutoCollection','collection_id':'ALL_MEDIA_AUTO_COLLECTION'}}],'page_info':{'has_next_page':False}}}}}
        self.assertEqual(instagram_response(d),([],True))

    def test_douyin_empty_confirmed(self):self.assertTrue(rows({'status_code':0,'mix_list':[],'has_more':False},'owner')['complete'])

    def test_douyin_bad_owner_rejected(self):
        with self.assertRaises(VpubError):rows({'status_code':0,'mix_list':[{'author':{'unique_id':'other'}}],'has_more':False},'owner')

    def test_douyin_missing_schema_rejected(self):
        with self.assertRaises(VpubError):rows({'status_code':0},'owner')


if __name__=='__main__':unittest.main()
