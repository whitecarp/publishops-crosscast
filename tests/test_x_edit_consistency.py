"""X edit text and durable recovery, entirely offline with fake adapters."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, edits
from video_publisher.core import Ledger, VpubError, atomic_json, digest, home, read_json, x_post_text


COPY = {'expected_identity': 'owner', 'title': 'Title', 'description': 'Caption',
        'content_language': 'en', 'metadata_language': 'en', 'metadata_mode': 'target_only'}


class XTextTests(unittest.TestCase):
    def test_edit_uses_the_publication_builder_with_tags_and_declaration(self):
        for ai in (True, False, None):
            with self.subTest(ai=ai):
                value = edits.validate_patch('x', {**COPY, 'tags': ['Game', '#游戏']}, ai_generated=ai)
                self.assertEqual(x_post_text('Title', 'Caption', ['Game', '#游戏'], ai, 'en'), value['post_text'])

    def test_shared_builder_keeps_existing_ai_wording_and_tags_when_omitted(self):
        for line in ('AI 生成内容 / AI-generated', 'AI 生成內容 / AI-generated', 'ai generated'):
            with self.subTest(line=line):
                before = 'Old\n\n#Game #游戏\n\n' + line
                self.assertEqual('Title\n\nCaption\n\n#Game #游戏\n\n' + line,
                                 x_post_text('Title', 'Caption', None, True, 'en', existing_text=before))

    def test_explicit_empty_tags_removes_the_tag_block_without_removing_ai(self):
        self.assertEqual('Title\n\nCaption\n\nAI-generated',
                         x_post_text('Title', 'Caption', [], False, existing_text='Old\n\n#Tag\n\nAI-generated'))

    def test_replacement_ai_line_does_not_duplicate_or_change_existing_wording(self):
        self.assertEqual('Title\n\nCaption\n\n#Tag\n\nAI 生成内容 / AI-generated',
                         x_post_text('Title', 'Caption\n\nAI-generated', None, True,
                                     existing_text='Old\n\n#Tag\n\nAI 生成内容 / AI-generated'))

    def test_wrapped_trailing_tags_are_preserved_as_the_shared_tag_block(self):
        self.assertEqual('Title\n\nCaption\n\n#Game #游戏',
                         x_post_text('Title', 'Caption', None, False, existing_text='Old\n\n#Game\n#游戏'))

    def test_invalid_tags_are_rejected_before_building_text(self):
        for tags in ('tag', [None], [''], ['line\nbreak'], ['two words'], ['#'], ['tag!']):
            with self.subTest(tags=tags), self.assertRaises(VpubError) as caught:
                edits.validate_patch('x', {**COPY, 'tags': tags})
            self.assertEqual('invalid_tags', caught.exception.code)

    def test_tags_contribute_to_preflight_limit_but_checks_remain_reads(self):
        value = {**COPY, 'description': 'a' * 260, 'tags': ['long_hashtag']}
        with self.assertRaises(VpubError) as caught:
            edits.validate_patch('x', value, ai_generated=False)
        self.assertEqual('x_text_too_long', caught.exception.code)
        self.assertIn('#long_hashtag', edits.validate_patch('x', value, verifying=True)['post_text'])


class XReceiptTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'VPUB_HOME': str(self.root / 'vpub'), 'BILI_CLI_HOME': str(self.root / 'bili')})
        env.start()
        self.addCleanup(env.stop)

    def test_old_patch_digest_cannot_bypass_an_ambiguous_press(self):
        old_path = home() / 'edits' / 'old-digest.json'
        old = {'id': '123', 'identity': 'owner', 'platform': 'x', 'state': 'EDIT_SUBMITTING', 'expected_text': 'Old intended text'}
        atomic_json(old_path, old)
        proposed = edits.validate_patch('x', {**COPY, 'tags': ['New']})
        with patch.object(adapters, 'browser_call') as call, self.assertRaises(VpubError) as caught:
            edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False)
        self.assertEqual('inspect_existing_edit', caught.exception.code)
        self.assertEqual(old, read_json(old_path))
        call.assert_not_called()

    def test_unavailable_check_keeps_attempted_words_and_blocks_a_retry(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        atomic_json(path, {'id': '123', 'identity': 'owner', 'platform': 'x', 'state': 'EDIT_SUBMITTING',
                           'expected_text': 'Frozen words\n\n#Original'})
        with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_SUBMITTED_UNVERIFIED',
                'platform_writes': 0, 'reason': 'version_chain_unavailable'}) as call:
            result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True)
            self.assertEqual('Frozen words\n\n#Original', result['expected_text'])
            self.assertEqual('EDIT_SUBMITTED_UNVERIFIED', read_json(path)['state'])
            with self.assertRaises(VpubError):
                edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False)
        call.assert_called_once()

    def test_verified_expected_text_survives_repeated_unavailable_mismatches(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        attempted = 'Frozen words\n\n#Original\n\nAI-generated'
        atomic_json(path, {'id': '123', 'identity': 'owner', 'platform': 'x', 'state': 'EDIT_VERIFIED',
                           'expected_text': attempted})
        # No post fields are available, so the backend cannot return expected_text.
        with patch.object(adapters, 'browser_call', return_value={'id': '123', 'identity': 'owner', 'platform': 'x',
                'state': 'EDIT_SUBMITTED_UNVERIFIED', 'platform_writes': 0, 'reason': 'version_chain_unavailable'}) as call:
            for _ in range(2):
                result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True)
                self.assertEqual('EDIT_MISMATCH', result['state'])
                self.assertEqual(attempted, read_json(path).get('expected_text'))
                self.assertEqual(attempted, result.get('expected_text'))
                self.assertTrue(call.call_args.kwargs['verify_only'])
        self.assertEqual(2, call.call_count)

    def test_verified_preserved_text_reaches_ledger_and_resume_without_losing_upload_proof(self):
        ledger = Ledger()
        key = ledger.claim('x', 'default', 'a' * 64, {})
        original = {'id': '123', 'identity': 'owner', 'method': 'browser', 'expected_text': 'Old',
                    'source_sha256': 'a' * 64, 'final_pressed': True}
        ledger.save(key, 'SUBMISSION_VERIFIED', {'identity': {'identity': 'owner'}, 'plan_hash': 'plan',
                    'options': {'ai_generated': True}, 'receipt': original.copy()})
        ledger.close()
        durable = home() / 'browser-receipts' / 'plan-x.json'
        atomic_json(durable, original)
        patch_path = self.root / 'patch.json'
        atomic_json(patch_path, COPY)
        final_text = 'Title\n\nCaption\n\n#Game\n\nAI 生成内容 / AI-generated'
        with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_VERIFIED', 'id': '123',
                'expected_text': final_text, 'platform_writes': 1}):
            edits.run(key, patch_path)
        ledger = Ledger()
        try:
            data = ledger.get(key)['data']
        finally:
            ledger.close()
        self.assertEqual(final_text, data['receipt']['expected_text'])
        self.assertEqual(final_text, data['options']['post_text'])
        self.assertEqual(original, data['edits'][0]['previous_receipt'])
        self.assertEqual({**original, 'title': 'Title', 'expected_text': final_text}, read_json(durable))


if __name__ == '__main__':
    unittest.main()
