from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import adapters, cli, metadata, native_browser
from video_publisher.core import atomic_json, load_plan, read_json, VpubError


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, VPUB_HOME=str(self.root / 'runtime'))
        self.env.start()
        self.video = self.root / 'test.mp4'
        self.video.write_bytes(b'not a real video')
        self.cover = self.root / 'cover.jpg'
        self.cover.write_bytes(b'not a real cover; structural fixture')
        self.path = self.root / 'publish.json'
        self.value = cli.template(self.video, 'ja')
        self.value['platforms']['youtube']['category_id']='1'  # Synthetic fixture only.
        self.value['platforms']['instagram'] = {'method':'native','visibility':'account','ai_generated':True}
        self.value['platforms']['x'] = {'method':'native','visibility':'account','ai_generated':True}
        for platform in self.value['platforms'].values():
            platform['metadata_mode']='bilingual'  # Japanese content requires source plus every selected platform's target.
        atomic_json(self.path, self.value)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def prepared_response(self):
        job = metadata.request(self.path, bilingual=['youtube'])
        response = {'request_id':job['request_id'],'platforms':{p: {
            'title':'测试' if p in ('bilibili','douyin') else 'Test',
            'description':'内容' if p in ('bilibili','douyin') else 'Video description',
            'tags':['AI'],'metadata_language':t['language'],'metadata_mode':t['mode']}
            for p,t in job['targets'].items()}}
        response['platforms']['youtube']['category_id']='1'
        for p, text in response['platforms'].items():
            if job['targets'][p]['mode']=='bilingual':
                text['description_parts']=['原文の説明', text['description']]
                text['description']='\n\n'.join(text['description_parts'])
        req, out = self.root/'request.json', self.root/'response.json'
        atomic_json(req,job)
        atomic_json(out,response)
        return req,out,response

    def test_language_routing_and_video_language_preserved(self):
        req,response,_ = self.prepared_response()
        result = metadata.apply(self.path,req,response,self.root/'elsewhere/output.json')
        value = read_json(result['manifest'])
        self.assertEqual('ja',value['content_language'])
        self.assertEqual('zh-Hans',value['platforms']['douyin']['metadata_language'])
        self.assertEqual('en',value['platforms']['x']['metadata_language'])
        self.assertEqual('bilingual',value['platforms']['youtube']['metadata_mode'])
        self.assertEqual('bilingual',value['platforms']['bilibili']['metadata_mode'])
        self.assertEqual(self.value, read_json(self.path))
        self.assertEqual(0,result['platform_writes'])

    def test_model_cannot_change_privacy_or_insert_links(self):
        req,response,value = self.prepared_response()
        value['platforms']['youtube']['visibility'] = 'public'
        atomic_json(response,value)
        with self.assertRaises(VpubError):
            metadata.apply(self.path,req,response,self.root/'out.json')
        del value['platforms']['youtube']['visibility']
        value['platforms']['youtube']['description'] = 'https://example.com'
        atomic_json(response,value)
        with self.assertRaises(VpubError):
            metadata.apply(self.path,req,response,self.root/'out.json')
        self.assertFalse((self.root/'out.json').exists())

    def test_stale_input_rejected_before_output(self):
        req,response,_ = self.prepared_response()
        self.value['title']='Changed'
        atomic_json(self.path,self.value)
        with self.assertRaises(VpubError):
            metadata.apply(self.path,req,response,self.root/'out.json')

    def test_cover_edit_and_playlist_receipt_require_real_observations(self):
        plan=load_plan(self.path,['youtube'])
        key=cli.publish_plan(plan,plan['confirmation'])['results']['youtube']['key']
        identity='UC'+'a'*22
        native_browser.begin(key,identity)
        native_browser.revise_cover(key,self.cover,identity)
        job=native_browser.request(key)
        opts=job['options']
        evidence={k:opts[k] for k in ('title','description','tags','visibility','ai_generated','made_for_kids','category_id')}
        evidence.update(id='abcdefghijk',identity=identity,page_url='https://studio.youtube.com/video/abcdefghijk/edit',
                        outcome='verified',observation='Synthetic test only.')
        path=self.root/'evidence.json'
        atomic_json(path,evidence)
        with self.assertRaises(VpubError):
            native_browser.record(key,path)
        evidence.update(cover_source_sha256=job['plan']['cover_sha256'],cover_observation='Synthetic matching cover fixture.')
        atomic_json(path,evidence)
        self.assertEqual('AGENT_REPORTED_VERIFIED',native_browser.record(key,path)['state'])
        self.value['cover']=str(self.cover)
        self.value['platforms']['youtube']['playlist_titles']=['AI Girls at Work']
        self.value['platforms']['youtube']['chapters']=[{'start_seconds':0,'title':'Start'}]
        self.value['platforms']['youtube']['duration_seconds']=79
        atomic_json(self.path,self.value)
        plan=load_plan(self.path,['youtube'])
        native_browser.revise(key,self.path,plan['confirmation'],identity)
        with self.assertRaises(VpubError):
            native_browser.record(key,path)
        evidence['playlist_titles']=['AI Girls at Work']
        evidence['chapters']=self.value['platforms']['youtube']['chapters']
        evidence['chapters_observation']='Synthetic saved native chapters fixture.'
        atomic_json(path,evidence)
        self.assertEqual('AGENT_REPORTED_VERIFIED',native_browser.record(key,path)['state'])
        with self.assertRaises(VpubError):
            native_browser.begin(key,identity)

    def test_douyin_private_cover_browser_and_native(self):
        self.value['cover']=str(self.cover)
        self.value['platforms']['douyin']['visibility']='private'
        atomic_json(self.path,self.value)
        self.assertEqual('private',load_plan(self.path,['douyin'])['platforms']['douyin']['visibility'])
        self.value['platforms']['douyin']['method']='browser'
        atomic_json(self.path,self.value)
        self.assertEqual('browser', load_plan(self.path,['douyin'])['platforms']['douyin']['method'])

    def test_chapter_translation_preserves_timing(self):
        self.value['platforms']['youtube']['chapters']=[{'start_seconds':0,'title':'開始'}]
        self.value['platforms']['youtube']['duration_seconds']=79
        atomic_json(self.path,self.value)
        req,path,response=self.prepared_response()
        response['platforms']['youtube']['chapters']=[{'start_seconds':1,'title':'Start'}]
        atomic_json(path,response)
        with self.assertRaises(VpubError):
            metadata.apply(self.path,req,path,self.root/'output.json')
        response['platforms']['youtube']['chapters'][0]['start_seconds']=0
        atomic_json(path,response)
        result=metadata.apply(self.path,req,path,self.root/'output.json')
        self.assertEqual('Start',read_json(result['manifest'])['platforms']['youtube']['chapters'][0]['title'])


