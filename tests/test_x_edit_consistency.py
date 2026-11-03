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
        proposed = edits.validate_patch('x', {**COPY, 'tags': ['New']})
        for state in ('EDIT_SUBMITTING', 'EDIT_SUBMITTED_UNVERIFIED'):
            for approved in (False, True):
                # Legacy records bind by publication key; adoption or a new key
                # still binds by exact platform, owner and post ID.
                for binding in ({'key': 'key'}, {'key': 'another-key', 'id': '123', 'identity': 'owner', 'platform': 'x'}):
                    with self.subTest(state=state, approved=approved, binding=binding):
                        atomic_json(old_path, {**binding, 'state': state, 'expected_text': 'Old intended text'})
                        before = {p.name: p.read_bytes() for p in old_path.parent.glob('*.json')}
                        with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_VERIFIED', 'platform_writes': 1}) as call:
                            with self.assertRaises(VpubError) as caught:
                                edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False, person_approved=approved)
                            call.assert_not_called()
                        self.assertEqual('inspect_existing_edit', caught.exception.code)
                        self.assertEqual(before, {p.name: p.read_bytes() for p in old_path.parent.glob('*.json')})

    def test_uncertain_edit_errors_require_inspection_without_suggesting_an_override(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        for state in ('EDIT_SUBMITTING', 'EDIT_SUBMITTED_UNVERIFIED'):
            for bound, approved in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(state=state, bound=bound, approved=approved):
                    old = {'state': state, **({'platform': 'x', 'identity': 'owner', 'id': '123'} if bound else {})}
                    atomic_json(path, old)
                    before = path.read_bytes()
                    with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_VERIFIED', 'platform_writes': 1}) as call, self.assertRaises(VpubError) as caught:
                        edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False, person_approved=approved)
                    self.assertEqual('inspect_existing_edit', caught.exception.code)
                    self.assertIn('may have succeeded', caught.exception.message)
                    self.assertIn('Do not retry', caught.exception.hint)
                    self.assertIn('Open the post on X', caught.exception.hint)
                    self.assertNotIn('--person-approved', caught.exception.hint)
                    self.assertEqual(before, path.read_bytes())
                    call.assert_not_called()

    def test_changed_patch_verification_cannot_clear_another_uncertain_record(self):
        old_path = home() / 'edits' / 'old-digest.json'
        atomic_json(old_path, {'key': 'old-key', 'platform': 'x', 'identity': 'owner', 'id': '123',
                               'state': 'EDIT_SUBMITTED_UNVERIFIED', 'expected_text': 'Original attempted text'})
        before = old_path.read_bytes()
        proposed = edits.validate_patch('x', {**COPY, 'description': 'Different caption'})
        response = {'platform': 'x', 'identity': 'owner', 'id': '123', 'state': 'EDIT_VERIFIED', 'platform_writes': 0}
        with patch.object(adapters, 'browser_call', return_value=response) as call:
            checked = edits.send('x', 'default', 'owner', {'id': '123'}, 'new-key', proposed, False, True, person_approved=True)
            self.assertEqual(('EDIT_VERIFIED', 0), (checked['state'], checked['platform_writes']))
            self.assertNotIn('person_approved', checked)
            self.assertTrue(call.call_args.kwargs['verify_only'])
            with self.assertRaises(VpubError) as caught:
                edits.send('x', 'default', 'owner', {'id': '123'}, 'new-key', proposed, False, False, person_approved=True)
            call.assert_called_once()
        self.assertEqual('inspect_existing_edit', caught.exception.code)
        self.assertEqual(before, old_path.read_bytes())

    def test_unrelated_uncertain_records_do_not_block_a_different_post(self):
        for index, changed in enumerate(({'platform': 'instagram'}, {'identity': 'other'}, {'id': '456'})):
            atomic_json(home() / 'edits' / f'unrelated-{index}.json',
                        {'key': f'other-{index}', 'platform': 'x', 'identity': 'owner', 'id': '123',
                         **changed, 'state': 'EDIT_SUBMITTING'})
        proposed = edits.validate_patch('x', COPY)
        with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_VERIFIED', 'platform_writes': 1}) as call:
            result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False)
        call.assert_called_once()
        self.assertEqual('EDIT_VERIFIED', result['state'])

    def test_browser_error_after_a_press_keeps_its_cause_and_inspection_guidance(self):
        proposed = edits.validate_patch('x', COPY)
        pending = {'state': 'EDIT_SUBMITTING', 'platform': 'x', 'identity': 'owner', 'id': '123',
                   'expected_text': proposed['post_text']}
        def timed_out(*args, **kwargs):
            atomic_json(kwargs['edit_receipt_path'], pending)
            raise VpubError('browser_timeout', 'Fake timeout after Update.', 'Inspect the original receipt.')
        with patch.object(adapters, 'browser_call', side_effect=timed_out) as call, self.assertRaises(VpubError) as caught:
            edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, False)
        self.assertEqual('browser_timeout', caught.exception.code)
        self.assertIn('Fake timeout after Update.', caught.exception.message)
        self.assertIn('may have succeeded', caught.exception.message)
        self.assertIn('Do not retry', caught.exception.hint)
        self.assertIn('Open the post on X', caught.exception.hint)
        self.assertNotIn('--person-approved', caught.exception.hint)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        self.assertEqual(pending, read_json(path))
        call.assert_called_once()

    def test_read_error_without_a_bound_attempt_keeps_its_original_meaning(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        for prior in (None, {'platform': 'x', 'identity': 'other', 'id': '123', 'state': 'EDIT_SUBMITTING'}):
            with self.subTest(prior=prior):
                if prior is not None: atomic_json(path, prior)
                error = VpubError('state_unavailable', 'No text available.', 'Original inspection hint.')
                with patch.object(adapters, 'browser_call', side_effect=error), self.assertRaises(VpubError) as caught:
                    edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True)
                self.assertIs(error, caught.exception)
                if prior is None: self.assertFalse(path.exists())
                else: self.assertEqual(prior, read_json(path))

    def test_read_only_recovery_keeps_warning_until_exact_text_is_verified(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        attempted = proposed['post_text'] + '\n\n#Original'
        atomic_json(path, {'state': 'EDIT_SUBMITTING', 'platform': 'x', 'identity': 'owner', 'id': '123',
                           'expected_text': attempted})
        answers = [{'state': 'EDIT_SUBMITTED_UNVERIFIED', 'reason': 'post_text_mismatch', 'platform_writes': 0},
                   {'state': 'EDIT_SUBMITTED_UNVERIFIED', 'reason': 'version_chain_unavailable', 'platform_writes': 0},
                   {'state': 'EDIT_VERIFIED', 'platform_writes': 0}]
        with patch.object(adapters, 'browser_call', side_effect=answers) as call:
            for answer in answers:
                result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True, person_approved=True)
                self.assertEqual(attempted, result['expected_text'])
                self.assertEqual(0, result['platform_writes'])
                self.assertNotIn('person_approved', result)
                self.assertIs(True, result['final_submit_attempted'])
                if answer['state'] != 'EDIT_VERIFIED':
                    self.assertEqual('EDIT_SUBMITTED_UNVERIFIED', result['state'])
                    self.assertIn('may have succeeded', result.get('message', ''))
                    self.assertIn('Do not retry', result.get('hint', ''))
                    self.assertNotIn('--person-approved', result['hint'])
                else:
                    self.assertEqual('EDIT_VERIFIED', result['state'])
                    for field in ('message', 'hint', 'reason'):
                        self.assertNotIn(field, result)
                durable = read_json(path)
                self.assertEqual(result, {k:durable[k] for k in result})
            self.assertTrue(all(c.kwargs['verify_only'] for c in call.call_args_list))

    def test_fresh_verify_mismatches_do_not_imply_an_attempted_edit(self):
        proposed = edits.validate_patch('x', COPY)
        response = {'id': '123', 'identity': 'owner', 'platform': 'x',
                    'state': 'EDIT_SUBMITTED_UNVERIFIED', 'platform_writes': 0}
        with patch.object(adapters, 'browser_call', return_value=response) as call:
            for _ in range(2):
                result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True)
                self.assertEqual('EDIT_MISMATCH', result['state'])
                self.assertIn('This call made no edit', result['message'])
                self.assertNotIn('may have succeeded', result['message'])
                self.assertNotIn('final_submit_attempted', result)
                self.assertNotIn('--person-approved', str(result))
            self.assertTrue(all(c.kwargs['verify_only'] for c in call.call_args_list))

    def test_unrelated_saved_receipt_cannot_supply_attempt_or_frozen_text(self):
        proposed = edits.validate_patch('x', COPY)
        path = home() / 'edits' / (digest(['key', proposed]) + '.json')
        for changed in ({'platform': 'instagram'}, {'identity': 'other'}, {'id': '456'}):
            with self.subTest(changed=changed):
                atomic_json(path, {'platform': 'x', 'identity': 'owner', 'id': '123', **changed,
                                   'state': 'EDIT_VERIFIED', 'platform_writes': 1, 'final_submit_attempted': True,
                                   'expected_text': 'Unrelated frozen words'})
                with patch.object(adapters, 'browser_call', return_value={'state': 'EDIT_SUBMITTED_UNVERIFIED', 'platform_writes': 0}):
                    result = edits.send('x', 'default', 'owner', {'id': '123'}, 'key', proposed, False, True)
                self.assertEqual('EDIT_MISMATCH', result['state'])
                self.assertNotIn('expected_text', result)
                self.assertNotIn('final_submit_attempted', result)
                self.assertIn('This call made no edit', result['message'])

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
