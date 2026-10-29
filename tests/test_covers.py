from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from video_publisher import cli, covers, native_browser
from video_publisher.core import atomic_json, file_sha, load_plan, read_json, VpubError


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, VPUB_HOME=str(self.root/'runtime'))
        self.env.start()
        video = self.root/'test.mp4'
        video.write_bytes(b'Synthetic offline fixture')
        self.source = self.root/'production.png'
        image = Image.new('RGB', (400, 200), 'red')
        image.paste('blue', (200, 0, 400, 200))
        image.save(self.source)
        self.manifest = self.root/'publish.json'
        self.value = cli.template(video, 'zh-Hans')
        self.value['platforms']['youtube']['category_id'] = '1'
        self.value['platforms']['x'] = {'method':'native','visibility':'account','ai_generated':True}
        atomic_json(self.manifest, self.value)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def prepare(self):
        return covers.prepare(self.manifest,self.source,self.root/'assets',self.root/'ready.json')

    def test_fit_preserves_edges_routes_platforms_and_is_idempotent(self):
        original = self.source.read_bytes()
        result = self.prepare()
        self.assertIn('x', result['skipped'])
        ready = read_json(result['manifest'])
        self.assertNotIn('x', ready['covers'])
        self.assertEqual('zh-Hans',ready['content_language'])
        self.assertEqual(self.value['platforms'],ready['platforms'])
        with Image.open(ready['covers']['douyin']['portrait']) as image:
            self.assertEqual((1080,1440), image.size)
            self.assertGreater(image.getpixel((5,720))[0],240)
            self.assertGreater(image.getpixel((1075,720))[2],240)
            self.assertLess(image.getpixel((540,5))[0],50)
        self.assertEqual(original,self.source.read_bytes())
        self.assertEqual('REUSED',self.prepare()['state'])
        self.source.write_bytes(b'changed')
        with self.assertRaises(VpubError):
            self.prepare()

    def test_preferred_platform_variant_and_corrupt_input(self):
        portrait=self.root/'portrait.png'
        Image.new('RGB',(30,40),'green').save(portrait)
        self.value['covers']={'douyin':{'portrait':str(portrait)}}
        atomic_json(self.manifest,self.value)
        result=self.prepare()
        asset=next(a for a in result['assets'] if a['platform']=='douyin' and a['role']=='portrait')
        self.assertEqual(file_sha(portrait),asset['source_sha256'])
        broken=self.root/'broken.jpg'
        broken.write_bytes(b'not an image')
        with self.assertRaises(VpubError):
            covers.prepare(self.manifest,broken,self.root/'bad-assets',self.root/'bad.json')
        self.assertFalse((self.root/'bad.json').exists())
        self.assertFalse((self.root/'bad-assets').exists())

    def test_dual_cover_snapshot_and_evidence_required(self):
        result=self.prepare()
        plan=load_plan(result['manifest'],['douyin'])
        key=cli.publish_plan(plan,plan['confirmation'])['results']['douyin']['key']
        native_browser.begin(key,'test_account')
        job=native_browser.request(key)
        variants=job['plan']['covers']['douyin']
        self.assertEqual({'landscape','portrait'},set(variants))
        self.assertTrue(all('/jobs/' in a['path'].replace('\\','/') for a in variants.values()))
        opts=job['options']
        evidence={k:opts[k] for k in ('title','description','tags','visibility','ai_generated')}
        evidence.update(id='12345',identity='test_account',page_url='https://www.douyin.com/video/12345',
                        outcome='verified',observation='Synthetic test fixture only.')
        target=self.root/'evidence.json'
        atomic_json(target,evidence)
        with self.assertRaises(VpubError):
            native_browser.record(key,target)
        evidence['cover_evidence']={r:{'source_sha256':a['sha256'],'observation':'Synthetic visual match.'} for r,a in variants.items()}
        atomic_json(target,evidence)
        self.assertEqual('AGENT_REPORTED_VERIFIED',native_browser.record(key,target)['state'])
        Path(variants['portrait']['path']).write_bytes(b'changed')
        with self.assertRaises(VpubError):
            native_browser.request(key)
