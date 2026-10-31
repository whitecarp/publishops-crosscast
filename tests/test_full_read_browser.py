from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from video_publisher import full_read_browser as f
from video_publisher.core import VpubError


def data(code,more,cursor):
    return {'data':{'fetch__MediaCollection':{'id':'123','media':{'edges':[
        {'node':{'code':c,'pk':str(100+i)}} for i,c in enumerate(code)],
        'page_info':{'has_next_page':more,'end_cursor':cursor}}}}}


class Response:
    status=200
    url='https://www.instagram.com/api/graphql'
    def __init__(self,value):self.value=value
    async def json(self):return self.value


class Page:
    def __init__(self,values):self.values=list(values);self.listener=None
    def on(self,event,callback):self.listener=callback
    def remove_listener(self,event,callback):self.listener=None
    def locator(self,*args):return SimpleNamespace(count=AsyncMock(return_value=0))
    async def evaluate(self,*args):await self.listener(Response(self.values.pop(0)))


class BrowserPagingTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self,values,pages=10,**payload):
        page=Page(values[1:])
        async def first(*args):
            await page.listener(Response(values[0]))
            return {'identity':'owner','items':[],'complete':False}
        with patch('video_publisher.collections_browser.execute',side_effect=first),patch.object(f,'risk_check',new=AsyncMock()),patch.object(f,'stop_check'):
            result=await f.execute(page,'instagram','owner',{'resource':'collection-items','collection_id':'123','max_pages':pages,**payload})
        self.assertIsNone(page.listener)
        return result

    async def test_two_actual_page_events_overlap_deduplicated_end_proven(self):
        result=await self.collect([data(['abcdef'],True,'next'),data(['abcdef','ghijkl'],False,None)])
        self.assertTrue(result['complete']);self.assertEqual(result['pages_read'],2)
        self.assertEqual([x['id'] for x in result['items']],['abcdef','ghijkl'])

    async def test_budget_and_repeated_page_never_prove_absence(self):
        item=data(['abcdef'],True,'next')
        self.assertFalse((await self.collect([item],1))['complete'])
        r=await self.collect([item,item]);self.assertFalse(r['complete']);self.assertEqual(r['stop_reason'],'repeated_page')

    def test_unrelated_null_graphql_result_ignored(self):
        self.assertIsNone(f.extract('instagram','collection-items','123','owner',Response(None),{'data':None,'errors':[]}))

    def test_wrong_collection_rejected(self):
        with self.assertRaises(VpubError):f.extract('instagram','collection-items','999','owner',Response(None),data(['abcdef'],False,None))

    def test_douyin_explicit_has_more_required(self):
        response=SimpleNamespace(url='https://creator.douyin.com/web/api/mix/list/')
        r=f.extract('douyin','collections',None,'owner',response,{'status_code':0,'mix_list':[],'has_more':False})
        self.assertTrue(r['complete'])
        with self.assertRaises(VpubError):f.extract('douyin','collections',None,'owner',response,{'status_code':0,'mix_list':[]})

    async def test_interrupted_replay_preserves_prior_rows_until_fresh_end(self):
        from copy import deepcopy
        run={'platform':'instagram','resource':'collection-items','collection_id':'123','identity':'owner',
             'items':[{'id':'oldrow'}]}
        writes=[]
        with patch('video_publisher.full_read.status',return_value=run),patch('video_publisher.full_read.save',side_effect=lambda r:writes.append(deepcopy(r))):
            partial=await self.collect([data(['abcdef'],True,'next')],1,read_run_id='test')
            self.assertEqual({x['id'] for x in partial['items']},{'oldrow','abcdef'})
            self.assertEqual(partial['retained_previous_items'],1)
            self.assertFalse(writes[-1]['complete'])
            fresh=await self.collect([data(['abcdef'],False,None)],1,read_run_id='test')
            self.assertTrue(fresh['complete'])
            self.assertEqual([x['id'] for x in fresh['items']],['abcdef'])

    async def test_actual_page_error_keeps_committed_partial_union(self):
        run={'platform':'instagram','resource':'collection-items','collection_id':'123','identity':'owner','items':[{'id':'oldrow'}]}
        with patch('video_publisher.full_read.status',return_value=run),patch('video_publisher.full_read.save'):
            with self.assertRaises(VpubError):
                await self.collect([data(['abcdef'],True,'next'),{'data':{'fetch__MediaCollection':{'id':'wrong'}}}],read_run_id='test')
        self.assertEqual({x['id'] for x in run['items']},{'oldrow','abcdef'})
