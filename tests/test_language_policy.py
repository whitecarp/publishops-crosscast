import unittest
from video_publisher.language_policy import route
from video_publisher.core import VpubError
from video_publisher.edits import validate_patch


class LanguagePolicyTests(unittest.TestCase):
    def test_chinese_scripts_are_simplified_only_for_chinese_platforms(self):
        for source in ('zh','zh-Hans','zh-Hant','zh-CN','zh-TW','cmn','yue-Hant'):
            for platform in ('bilibili','douyin'):
                self.assertEqual(['zh-Hans'],route(source,platform)['description_languages'])
            for platform in ('youtube','instagram','x'):
                self.assertEqual([source,'en'],route(source,platform)['description_languages'])

    def test_english_variants_and_other_languages(self):
        for source in ('en','en-US','en-GB'):
            self.assertEqual('target_only',route(source,'x')['mode'])
            self.assertEqual([source,'zh-Hans'],route(source,'douyin')['description_languages'])
        for source in ('ja','ko','fr','es'):
            self.assertEqual([source,'en'],route(source,'instagram')['description_languages'])
            self.assertEqual([source,'zh-Hans'],route(source,'bilibili')['description_languages'])

    def test_unknown_language_needs_source_inspection_not_account_locale(self):
        for source in ('und','mul','zxx'):
            with self.assertRaises(VpubError): route(source,'youtube')

    def test_edit_cannot_change_visibility_or_drop_required_language(self):
        base={'expected_identity':'creator','title':'Episode 02','description':'原文\n\nTranslation',
              'content_language':'zh-Hans','metadata_language':'en','metadata_mode':'bilingual'}
        self.assertIn('原文',validate_patch('instagram',base)['post_text'])
        with self.assertRaises(VpubError): validate_patch('instagram',{**base,'visibility':'public'})
        with self.assertRaises(VpubError): validate_patch('instagram',{**base,'metadata_mode':'target_only'})
