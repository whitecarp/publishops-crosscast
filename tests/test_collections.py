import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from video_publisher import collections as c
from video_publisher.collections_browser import folder_response, created_id, instagram_response
from video_publisher.core import VpubError, read_json


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.hp=patch.object(c,'home',return_value=Path(self.tmp.name));self.hp.start();self.addCleanup(self.hp.stop)
        self.ip=patch.object(c,'identity',return_value='owner');self.ip.start();self.addCleanup(self.ip.stop)
        self.lp=patch.object(c,'publication_lock');self.lp.start();self.addCleanup(self.lp.stop)
        self.sp=patch.object(c,'stop_check');self.sp.start();self.addCleanup(self.sp.stop)
        self.rows=[];self.calls=[];self.complete=True
        self.cp=patch.object(c,'call',side_effect=self.fake);self.mock=self.cp.start();self.addCleanup(self.cp.stop)

    def fake(self,p,op,owner,**kw):
        self.calls.append(op)
        if op=='list':return {'identity':owner,'items':[dict(x) for x in self.rows],'complete':self.complete}
        c.before_submit(dict(platform=p,operation=op,**kw),owner)
        if op=='create':self.rows.append({'id':'123','title':kw['title']})
        if op=='rename':self.rows[0]['title']=kw['title']
        if op=='delete':self.rows=[]
        return {'collection_id':'123' if op=='create' else None}

    def create(self):return c.change('x','create',title='Test',request_id='create-1')

    def test_create_records_returned_id_and_reads_back(self):
        r=self.create();self.assertEqual(r['state'],'CONFIRMED');self.assertEqual(r['collection_id'],'123')
        self.assertEqual(self.calls,['list','create','list'])

    def test_repeat_is_local_no_browser_no_submit(self):
        self.create();self.calls.clear();r=self.create()
        self.assertTrue(r['reused']);self.assertEqual(r['platform_writes'],0);self.assertEqual(self.calls,[])

    def test_request_id_cannot_describe_different_intent(self):
        self.create();self.calls.clear()
        with self.assertRaises(VpubError):c.change('x','create',title='Other',request_id='create-1')
        self.assertEqual(self.calls,[])

    def test_verify_only_reads(self):
        r=self.create();self.calls.clear();v=c.verify(r['key'])
        self.assertEqual(v['state'],'CONFIRMED');self.assertEqual(v['platform_writes'],0)
        self.assertEqual(self.calls,['list']);self.assertEqual(read_json(c.receipt_path(r['key']))['platform_writes'],1)

    def test_uncertain_create_not_adopted_by_same_title(self):
        def lost(p,op,owner,**kw):
            if op=='create':
                self.fake(p,op,owner,**kw);raise TimeoutError()
            return self.fake(p,op,owner,**kw)
        self.mock.side_effect=lost;r=self.create()
        self.assertEqual(r['state'],'UNVERIFIED');self.assertIsNone(r['collection_id'])
        self.assertEqual(c.verify(r['key'])['state'],'UNVERIFIED')

    def test_failure_before_submit_cannot_adopt_unrelated_object(self):
        def failed(p,op,owner,**kw):
            if op=='create':raise TimeoutError()
            return self.fake(p,op,owner,**kw)
        self.mock.side_effect=failed;r=self.create();self.rows=[{'id':'999','title':'Test'}];self.calls.clear()
        self.assertEqual(c.verify(r['key'])['state'],'FAILED_BEFORE_SUBMIT');self.assertEqual(self.calls,[])

    def test_rename_noop(self):
        self.rows=[{'id':'123','title':'Test'}]
        r=c.change('x','rename','123',title='Test',request_id='rename-1')
        self.assertEqual(r['state'],'CONFIRMED');self.assertEqual(self.calls,['list']);self.assertEqual(r['platform_writes'],0)

    def test_rename_exact_id(self):
        self.rows=[{'id':'123','title':'Old'}]
        self.assertEqual(c.change('x','rename','123',title='New',request_id='rename-1')['state'],'CONFIRMED')

    def test_unknown_target_never_submits(self):
        with self.assertRaises(VpubError):c.change('x','delete','999',request_id='delete-1')
        self.assertEqual(self.calls,['list'])

    def test_delete_absence_on_partial_page_not_success(self):
        self.rows=[{'id':'123','title':'Test'}];self.complete=False
        r=c.change('x','delete','123',request_id='delete-1');self.assertEqual(r['state'],'UNVERIFIED')

    def test_delete_complete_readback(self):
        self.rows=[{'id':'123','title':'Test'}]
        r=c.change('x','delete','123',request_id='delete-1');self.assertEqual(r['state'],'CONFIRMED')

    def test_wrong_account_stops_before_write(self):
        self.mock.return_value={'identity':'other','items':[],'complete':True};self.mock.side_effect=None
        with self.assertRaises(VpubError):self.create()
        self.assertEqual(self.mock.call_count,1)

    def test_duplicate_title_blocked(self):
        self.rows=[{'id':'999','title':'Test'}]
        with self.assertRaises(VpubError):self.create()
        self.assertEqual(self.calls,['list'])

    def test_invalid_inputs_do_not_launch_browser(self):
        for kwargs in ({'request_id':'../bad'},{'request_id':'good','title':'x'*26},{'request_id':'good','title':'\nTest'}):
            with self.assertRaises(VpubError):c.change('x','create',**kwargs)
        for value in ('../123','https://x.com/123','abc',123):
            with self.assertRaises((VpubError,TypeError)):c.listing('x',value)
        self.assertEqual(self.calls,[])

    def test_unverified_features_rejected_before_login(self):
        with self.assertRaises(VpubError):c.change('douyin','create',title='Test',request_id='t')
        self.assertEqual(self.calls,[])

    def test_receipt_cannot_be_submitted_twice(self):
        r=self.create()
        with self.assertRaises(VpubError):c.before_submit({'receipt_key':r['key'],'platform':'x','operation':'create','title':'Test'},'owner')

    def test_receipt_cannot_be_used_for_another_account(self):
        r=self.create()
        with self.assertRaises(VpubError):c.before_submit({'receipt_key':r['key'],'platform':'x','operation':'create','title':'Test'},'other')

    def test_readback_cannot_cross_identity(self):
        r=self.create()
        with self.assertRaises(VpubError):c.checked_result(r,{'identity':'other','items':self.rows,'complete':True})

    def test_parser_rejects_missing_schema_instead_of_empty(self):
        for data in ({},{'errors':[]},{'data':None}):
            with self.assertRaises(VpubError):folder_response(data)

    def response(self,items,info):
        return {'data':{'viewer':{'user_results':{'result':{'bookmark_collections_slice':{'items':items,'slice_info':info}}}}}}

    def test_response_pagination_is_not_complete(self):
        rows,complete=folder_response(self.response([{'id':'123','name':'Test','media':{'private':'omitted'}}],{'next_cursor':'abc'}))
        self.assertFalse(complete);self.assertEqual(rows,[{'id':'123','title':'Test'}])

    def test_response_invalid_or_duplicated_id_is_rejected(self):
        for rows in ([{'id':'../123','name':'Test'}],[{'id':'123','name':'A'},{'id':'123','name':'B'}],[None]):
            with self.assertRaises(VpubError):folder_response(self.response(rows,{}))

    def test_response_explicit_empty_page(self):
        self.assertEqual(folder_response(self.response([],{})),([],True))

    def test_create_id_comes_from_specific_mutation_response(self):
        self.assertEqual(created_id('x',{'data':{'bookmark_collection_create':{'id':'123','name':'Test'}}},'Test'),'123')
        self.assertEqual(created_id('instagram',{'data':{'xig_media_collection_create':{'collection':{'id':'234','name':'Test'}}}},'Test'),'234')

    def test_unrelated_matching_name_response_cannot_prove_creation(self):
        self.assertIsNone(created_id('x',{'data':{'viewer':{'items':[{'id':'123','name':'Test'}]}}},'Test'))

    def test_creation_identity_mismatch_rejected(self):
        for value in ({'id':'123','name':'Other'},{'id':'../123','name':'Test'},None):
            with self.assertRaises(VpubError):created_id('x',{'data':{'bookmark_collection_create':value}},'Test')

    def test_instagram_explicit_pagination(self):
        node={'__typename':'MediaCollection','collection_id':'123','collection_name':'Test'}
        data={'data':{'viewer':{'collections_unified_with_auto_collections':{'edges':[{'node':node}],'page_info':{'has_next_page':True}}}}}
        self.assertEqual(instagram_response(data),([{'id':'123','title':'Test'}],False))
        data['data']['viewer']['collections_unified_with_auto_collections']['page_info']['has_next_page']=False
        self.assertTrue(instagram_response(data)[1])

    def test_instagram_unknown_type_not_silently_skipped(self):
        data={'data':{'viewer':{'collections_unified_with_auto_collections':{'edges':[{'node':{'__typename':'Unknown'}}],'page_info':{'has_next_page':False}}}}}
        with self.assertRaises(VpubError):instagram_response(data)

    def test_instagram_missing_pagination_not_claimed_complete(self):
        for info in ({},None,{'has_next_page':'false'}):
            data={'data':{'viewer':{'collections_unified_with_auto_collections':{'edges':[],'page_info':info}}}}
            with self.assertRaises(VpubError):instagram_response(data)


if __name__=='__main__':unittest.main()
