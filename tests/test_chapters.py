import unittest
from video_publisher.chapters import validate
from video_publisher.core import VpubError


class ChapterTests(unittest.TestCase):
    def test_douyin_title_limit_does_not_truncate_or_change_other_platforms(self):
        chapter = [{'start_seconds': 0, 'title': '章' * 12}]
        self.assertEqual(chapter, validate(chapter, 'douyin', 'native', 79))
        chapter[0]['title'] += '节'
        with self.assertRaises(VpubError):
            validate(chapter, 'douyin', 'native', 79)
        self.assertEqual(chapter, validate(chapter, 'youtube', 'native', 79))
        self.assertEqual('章' * 12 + '节', chapter[0]['title'])

    def test_rejects_unsupported_routes_and_bad_times(self):
        valid=[{'start_seconds':0,'title':'Start'},{'start_seconds':19,'title':'Next'}]
        self.assertEqual(valid,validate(valid,'douyin','native',79))
        self.assertEqual(valid,validate(valid,'douyin','browser',79))
        for platform,method in [('x','native'),('youtube','api')]:
            with self.assertRaises(VpubError):
                validate(valid,platform,method,79)
        for values,duration in [([{'start_seconds':1,'title':'Start'}],79),
                                (valid,19), (valid,float('nan')),
                                (valid+[{'start_seconds':19,'title':'Duplicate'}],79),
                                ([{'start_seconds':False,'title':'Start'}],79)]:
            with self.assertRaises(VpubError):
                validate(values,'douyin','native',duration)
