from pathlib import Path
import tempfile
import unittest
from PIL import Image
from video_publisher import instagram_media
from video_publisher.core import atomic_json, file_sha, load_plan, read_json, VpubError


class InstagramPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.video = self.root / 'source.mp4'
        self.video.write_bytes(b'original synthetic video bytes')
        self.cover = self.root / 'landscape.jpg'
        self.portrait = self.root / 'douyin-portrait.jpg'
        Image.new('RGB',(320,180),'red').save(self.cover)
        Image.new('RGB',(180,320),'blue').save(self.portrait)
        self.value = {'schema_version':1,'video':'source.mp4','cover':'landscape.jpg',
                      'title':'Example','description':'','tags':[],
                      'platforms':{'instagram':{'method':'native','visibility':'account','ai_generated':True},
                                   'douyin':{'method':'native','visibility':'private','ai_generated':True}},
                      'covers':{'douyin':{'portrait':'douyin-portrait.jpg'}}}
        self.manifest=self.root/'input.json'
        atomic_json(self.manifest,self.value)

    def tearDown(self):
        self.tmp.cleanup()

    def test_original_video_and_exact_douyin_cover_reuse(self):
        before=self.video.read_bytes()
        result=instagram_media.prepare(self.manifest,self.root/'ready.json')
        self.assertEqual('ORIGINAL_VIDEO',result['mode'])
        self.assertFalse(result['video_modified'])
        self.assertEqual(str(self.video),result['video'])
        self.assertEqual(before,self.video.read_bytes())
        self.assertEqual(file_sha(self.video),result['video_sha256'])
        self.assertEqual(str(self.portrait),result['mobile_cover']['path'])
        self.assertEqual(file_sha(self.portrait),result['mobile_cover']['sha256'])
        plan=load_plan(result['manifest'])
        self.assertEqual(['instagram'],list(plan['platforms']))
        self.assertEqual(file_sha(self.cover),plan['cover_sha256'])
        self.assertNotIn('instagram',plan.get('covers',{}))

    def test_repeat_is_idempotent_and_mismatch_rejected(self):
        out=self.root/'ready.json'
        instagram_media.prepare(self.manifest,out)
        self.assertEqual('REUSED',instagram_media.prepare(self.manifest,out)['state'])
        edited=read_json(out); edited['title']='Changed'; atomic_json(out,edited)
        with self.assertRaises(VpubError):
            instagram_media.prepare(self.manifest,out)

    def test_mobile_override_and_no_source_overwrite(self):
        other=self.root/'other.jpg'; Image.new('RGB',(180,240),'green').save(other)
        result=instagram_media.prepare(self.manifest,self.root/'ready.json',other)
        self.assertEqual(str(other),result['mobile_cover']['path'])
        for out in (self.manifest,self.video,self.cover,self.portrait):
            with self.assertRaises(VpubError):
                instagram_media.prepare(self.manifest,out)
        with self.assertRaises(VpubError):
            instagram_media.prepare(self.manifest,self.root/'bad.json',self.root/'missing.jpg')

    def test_missing_portrait_does_not_modify_video_or_invent_mobile_success(self):
        self.value.pop('covers'); atomic_json(self.manifest,self.value)
        result=instagram_media.prepare(self.manifest,self.root/'ready.json')
        self.assertIsNone(result['mobile_cover'])
        self.assertEqual(0,result['platform_writes'])
        self.assertEqual(file_sha(self.video),load_plan(result['manifest'])['video_sha256'])
