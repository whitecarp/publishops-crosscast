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
        self.portrait=self.root/'portrait.png'
        Image.new('RGB',(300,400),'green').save(self.portrait)
        self.value = cli.template(video, 'zh-Hans')
        self.value['title'] = '封面测试'
        self.value['platforms']['youtube']['category_id'] = '1'
        self.value['platforms']['x'] = {'method':'native','visibility':'account','ai_generated':True}
        atomic_json(self.manifest, self.value)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def prepare(self):
        return covers.prepare(self.manifest,self.source,self.root/'assets',self.root/'ready.json',self.portrait)

    def test_covers_before_the_words_say_to_write_the_words_first(self):
        """From a field run: its skill listed covers before metadata, and covers prepare stopped on a new YouTube plan's category. With that set, the empty title would have stopped it next."""
        for platforms in (['youtube'], ['bilibili','youtube','douyin']):
            with self.subTest(platforms=platforms):
                value=cli.template(self.root/'test.mp4','zh-Hans')
                value['platforms']={name:value['platforms'][name] for name in platforms}
                fresh=self.root/('fresh-'+'-'.join(platforms)+'.json')
                atomic_json(fresh,value)
                with self.assertRaises(VpubError) as caught:
                    covers.prepare(fresh,self.source,self.root/'early',self.root/'early.json',self.portrait)
                self.assertEqual('copy_not_written',caught.exception.code)
                self.assertIn('vpub metadata request',caught.exception.hint)
                self.assertIn('vpub metadata apply',caught.exception.hint)
                self.assertFalse((self.root/'early').exists() or (self.root/'early.json').exists())

    def test_a_mistake_in_words_already_written_keeps_its_own_answer(self):
        from video_publisher.language_policy import route
        policy=route('zh-Hans','youtube')
        self.value['platforms']['youtube'].update(metadata_language=policy['language'],metadata_mode=policy['mode'],category_id='none')
        atomic_json(self.manifest,self.value)
        with self.assertRaises(VpubError) as caught:
            self.prepare()
        self.assertEqual('category_required',caught.exception.code)

    def wide_cover(self):
        """A 16:9 cover: a green subject on the left, a white title panel on the right."""
        path=self.root/'wide.png'
        image=Image.new('RGB',(1600,900),'black')
        image.paste('green',(0,0,800,900))
        image.paste('white',(800,200,1600,500))
        image.save(path)
        return path

    def test_portrait_stacks_the_parts_inside_the_safe_area(self):
        out=self.root/'portrait.jpg'
        result=covers.portrait(self.wide_cover(),['800,200,800,300','0,0,800,900'],out)
        self.assertEqual((1080,1920),Image.open(out).size)
        self.assertEqual(file_sha(out),result['sha256'])
        self.assertEqual(0,result['platform_writes'])
        left,top,width,height=covers.SAFE
        for part in result['parts']:
            x,y,w,h=part['placed']
            self.assertGreaterEqual(x,left);self.assertGreaterEqual(y,top)
            self.assertLessEqual(x+w,left+width);self.assertLessEqual(y+h,top+height)
        title,subject=result['parts']
        self.assertLess(title['placed'][1]+title['placed'][3],subject['placed'][1])
        image=Image.open(out).convert('RGB')
        def color_at(part):
            x,y,w,h=part['placed']
            return image.getpixel((x+w//2,y+h//2))
        self.assertGreater(min(color_at(title)),230)
        red,green,blue=color_at(subject)
        self.assertGreater(green,red+60);self.assertGreater(green,blue+60)

    def test_portrait_fills_the_edges_with_no_empty_bars(self):
        out=self.root/'portrait.jpg'
        covers.portrait(self.wide_cover(),['0,0,800,900'],out)
        image=Image.open(out).convert('RGB')
        # The blurred source behind the parts, not flat bars of one colour.
        self.assertTrue(any(low!=high for low,high in image.crop((0,0,1080,200)).getextrema()))

    def test_a_single_wide_band_is_refused_as_a_letterbox(self):
        out=self.root/'portrait.jpg'
        for boxes in (['0,0,1600,900'],['0,0,800,300']):
            with self.subTest(boxes=boxes):
                with self.assertRaises(VpubError) as caught:
                    covers.portrait(self.wide_cover(),boxes,out)
                self.assertEqual('would_letterbox',caught.exception.code)
        self.assertFalse(out.exists())

    def test_two_wide_parts_stacked_are_a_real_layout(self):
        result=covers.portrait(self.wide_cover(),['0,0,1600,900','0,0,1600,900'],self.root/'stack.jpg')
        self.assertEqual(2,len(result['parts']))

    def test_a_tall_stack_shrinks_to_stay_inside(self):
        result=covers.portrait(self.wide_cover(),['0,0,400,900','400,0,400,900','800,0,400,900'],self.root/'tall.jpg')
        height=sum(part['placed'][3] for part in result['parts'])+2*covers.GAP
        self.assertLessEqual(abs(height-(covers.SAFE[3]-2*covers.MARGIN)),3)

    def test_bad_parts_and_paths_are_refused_and_nothing_is_written(self):
        wide=self.wide_cover()
        for code,boxes,out in (('invalid_box',['0,0,1601,900'],'a.jpg'),('invalid_box',['0,0,50,900'],'a.jpg'),('invalid_box',['nonsense'],'a.jpg'),
                               ('invalid_box',['0,0,800,900']*4,'a.jpg'),('invalid_box',[],'a.jpg'),('invalid_output',['0,0,800,900'],'a.png')):
            with self.subTest(boxes=boxes,out=out):
                with self.assertRaises(VpubError) as caught:
                    covers.portrait(wide,boxes,self.root/out)
                self.assertEqual(code,caught.exception.code)
                self.assertFalse((self.root/out).exists())
        with self.assertRaises(VpubError) as caught:
            covers.portrait(self.root/'missing.png',['0,0,800,900'],self.root/'a.jpg')
        self.assertEqual('invalid_cover',caught.exception.code)

    def test_an_existing_cover_is_never_overwritten(self):
        out=self.root/'portrait.jpg'
        out.write_bytes(b'keep')
        with self.assertRaises(VpubError) as caught:
            covers.portrait(self.wide_cover(),['0,0,800,900'],out)
        self.assertEqual('already_exists',caught.exception.code)
        self.assertEqual(b'keep',out.read_bytes())

    def test_an_image_that_is_not_one_is_refused(self):
        bad=self.root/'bad.png'
        bad.write_bytes(b'not an image')
        with self.assertRaises(VpubError) as caught:
            covers.portrait(bad,['0,0,800,900'],self.root/'a.jpg')
        self.assertEqual('invalid_cover',caught.exception.code)

    def test_the_result_works_as_the_shared_portrait(self):
        out=self.root/'made.jpg'
        covers.portrait(self.wide_cover(),['0,0,800,900'],out)
        self.portrait=out
        ready=read_json(self.prepare()['manifest'])
        self.assertEqual(file_sha(out),file_sha(ready['covers']['douyin']['portrait']))

    def test_the_portrait_command_parses_and_runs(self):
        out=self.root/'cli.jpg'
        args=cli.parser().parse_args(['covers','portrait',str(self.wide_cover()),'--box','0,0,800,900','--out',str(out)])
        self.assertEqual('PREPARED',cli.run_command(args)['state'])
        self.assertTrue(out.is_file())

    def test_fit_preserves_edges_routes_platforms_and_is_idempotent(self):
        original = self.source.read_bytes()
        result = self.prepare()
        self.assertIn('x', result['skipped'])
        ready = read_json(result['manifest'])
        self.assertNotIn('x', ready['covers'])
        self.assertEqual('zh-Hans',ready['content_language'])
        self.assertEqual(self.value['platforms'],ready['platforms'])
        with Image.open(ready['covers']['douyin']['portrait']) as image:
            self.assertEqual((300,400), image.size)
        self.assertEqual(file_sha(self.portrait),file_sha(ready['covers']['douyin']['portrait']))
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

    def test_shared_portrait_is_identical_and_landscape_fallback_rejected(self):
        self.value['platforms']['instagram']={'method':'native','visibility':'account','ai_generated':True}
        atomic_json(self.manifest,self.value)
        with self.assertRaises(VpubError):
            covers.prepare(self.manifest,self.source,self.root/'missing',self.root/'missing.json')
        result=self.prepare()
        value=read_json(result['manifest'])
        self.assertEqual(value['covers']['douyin']['portrait'],value['covers']['instagram']['portrait'])

    def test_dual_cover_snapshot_and_evidence_required(self):
        result=self.prepare()
        plan=load_plan(result['manifest'],['douyin'])
        key=cli.publish_plan(plan,plan['confirmation'], reviewed=True)['results']['douyin']['key']
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
