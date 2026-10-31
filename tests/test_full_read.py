import os
import tempfile
import unittest
from unittest.mock import patch
from video_publisher import full_read as f
from video_publisher.core import VpubError


def page(items,cursor=None,**kw):
    return {'identity':'owner','items':items,'next_cursor':cursor,'source':'test_api',**kw}


class FullReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=self.tmp.name);env.start();self.addCleanup(env.stop)

    @patch.object(f.operations_sync,'works')
    def test_pause_resume_deduplicates_overlap_and_retains_last_page(self,works):
        works.side_effect=[page([{'id':'a'}],'cursor2'),page([{'id':'a'},{'id':'b'}])]
        r=f.start('youtube','works',page_size=1,max_pages=1)
        self.assertEqual(r['state'],'PAUSED');self.assertEqual(r['tasks'][0]['cursor'],'cursor2')
        r=f.resume(r['run_id'],1)
        self.assertEqual([x['id'] for x in r['items']],['a','b']);self.assertTrue(r['complete'])
        f.resume(r['run_id']);self.assertEqual(works.call_count,2)
        self.assertEqual(works.call_args.args[-1],'cursor2')

    @patch.object(f.operations_sync,'works')
    def test_failed_page_preserves_cursor_and_successful_items(self,works):
        works.side_effect=[page([{'id':'a'}],'next'),TimeoutError()]
        r=f.start('youtube','works',max_pages=1)
        with self.assertRaises(TimeoutError):f.resume(r['run_id'])
        saved=f.status(r['run_id']);self.assertEqual(saved['pages_read'],1);self.assertEqual(saved['tasks'][0]['cursor'],'next')
        works.side_effect=[page([{'id':'b'}])]
        self.assertTrue(f.resume(r['run_id'])['complete'])

    @patch.object(f.operations_sync,'works')
    def test_account_change_does_not_advance_checkpoint(self,works):
        works.side_effect=[page([{'id':'a'}],'next'),{**page([{'id':'b'}]),'identity':'different'}]
        r=f.start('youtube','works',max_pages=1)
        with self.assertRaises(VpubError):f.resume(r['run_id'])
        self.assertEqual(f.status(r['run_id'])['pages_read'],1)

    @patch.object(f.operations_sync,'works')
    def test_repeated_cursor_is_not_complete(self,works):
        works.side_effect=[page([{'id':'a'}],'next'),page([{'id':'b'}],'next')]
        r=f.start('youtube','works',max_pages=1)
        with self.assertRaises(VpubError):f.resume(r['run_id'])
        self.assertFalse(f.status(r['run_id'])['complete'])

    @patch.object(f.operations_sync,'works')
    def test_inaccessible_work_prevents_complete_claim(self,works):
        works.return_value=page([],omitted_count=1)
        self.assertEqual(f.start('youtube','works')['state'],'INCOMPLETE')

    @patch.object(f.ops,'backend')
    def test_complete_includes_reply_pages_with_owner_reply(self,backend):
        backend.side_effect=[page([{'id':'root','reply_count':2}]),
            page([{'id':'root'},{'id':'c1','parent_id':'root'}],'reply2'),
            page([{'id':'root'},{'id':'c2','parent_id':'root','is_owner':True}])]
        r=f.start('youtube','comments','abcdefghijk',max_pages=1)
        self.assertFalse(r['complete'])
        r=f.resume(r['run_id'])
        self.assertTrue(r['complete']);self.assertEqual(len(r['items']),3)
        self.assertTrue(r['items'][0]['owner_replied'])

    @patch.object(f.ops,'backend')
    def test_missing_reply_count_keeps_incomplete_and_wrong_thread_fails(self,backend):
        backend.side_effect=[page([{'id':'root','reply_count':3}]),page([{'id':'c','parent_id':'root'}])]
        self.assertFalse(f.start('youtube','comments','abcdefghijk')['complete'])
        backend.side_effect=[page([{'id':'root','reply_count':1}]),page([{'id':'c','parent_id':'other'}])]
        with self.assertRaises(VpubError):f.start('youtube','comments','abcdefghijk')

    def test_unsupported_dom_feed_does_not_claim_full_history(self):
        with self.assertRaises(VpubError):f.start('instagram','comments','abcdef')
        with self.assertRaises(VpubError):f.start('youtube','works',max_pages=0)
