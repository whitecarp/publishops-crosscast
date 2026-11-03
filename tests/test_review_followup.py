"""Review residuals: synthetic media and accounts, no platform contact."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, core, native_browser, publication_review


def run(*words):
    return cli.execute(cli.parser().parse_args([str(w) for w in words]))


class ConsentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, VPUB_HOME=str(self.root/'home'), BILI_CLI_HOME=str(self.root/'bili'))
        env.start()
        self.addCleanup(env.stop)
        self.video = self.root/'fixture.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + bytes(64))

    def prior(self, platform):
        db = core.Ledger()
        try:
            data = {'options': {'submission_mode': 'publish'}}
            key = db.claim(platform, 'default', 'unrelated-prior', data)
            db.save(key, 'SUBMISSION_VERIFIED', data)
        finally:
            db.close()

    def draft(self):
        manifest = self.root/'draft.json'
        value = cli.template(self.video)
        value['title'] = 'Synthetic review draft'
        value['platforms'] = {'x': {'method': 'native', 'visibility': 'account',
                                  'ai_generated': False, 'submission_mode': 'draft_only'}}
        core.atomic_json(manifest, value)
        plan = core.load_plan(manifest)
        key = cli.publish_plan(plan, plan['confirmation'], reviewed=True)['results']['x']['key']
        native_browser.begin(key, 'fixture_owner')
        evidence = self.root/'evidence.json'
        core.atomic_json(evidence, {'outcome': 'draft', 'identity': 'fixture_owner',
            'page_url': 'https://x.com/compose/post', 'post_text': plan['platforms']['x']['post_text'],
            'media_preview': True, 'draft_saved': True, 'observation': 'Offline test only.'})
        native_browser.record(key, evidence)
        return key

    def record(self, key):
        db = core.Ledger()
        try:
            return db.get(key)
        finally:
            db.close()

    def test_authorize_draft_requires_review_and_changes_nothing(self):
        key = self.draft()
        before = self.record(key)
        job = core.home()/'browser-jobs'/f'{key}.json'
        raw = job.read_bytes()
        answer = native_browser.authorize_publish(key, 'fixture_owner')
        self.assertEqual('NEEDS_A_PERSON', answer['state'])
        self.assertFalse(answer['final_submission_allowed'])
        self.assertEqual('publish', answer['review']['platforms']['x']['submission_mode'])
        self.assertNotEqual(before['data']['plan_hash'], answer['confirmation'])
        self.assertEqual(before, self.record(key))
        self.assertEqual(raw, job.read_bytes())

    def test_another_platforms_publication_does_not_waive_review(self):
        self.prior('bilibili')
        plan = {'platforms': {'youtube': {'submission_mode': 'publish'}}}
        answer = publication_review.requirements(plan)
        self.assertTrue(answer['review_required'])
        self.assertEqual(['youtube'], answer['first_publication_platforms'])

    def test_mixed_plan_reviews_only_unpublished_non_draft_platforms(self):
        self.prior('bilibili')
        plan = {'platforms': {'bilibili': {}, 'youtube': {}, 'x': {'submission_mode': 'draft_only'}}}
        self.assertEqual(['youtube'], publication_review.requirements(plan)['first_publication_platforms'])
        self.prior('youtube')
        self.assertFalse(publication_review.requirements(plan)['review_required'])

    def prepared(self):
        result = run('prepare', self.video, '--platforms', 'youtube', '--language', 'en',
                     '--visibility', 'private', '--ai-generated', 'no')
        folder = Path(result['folder'])
        words = core.read_json(folder/'copy.json')
        words['platforms']['youtube'].update(title='A boat', description='A boat.', category_id='1', tags=[])
        core.atomic_json(folder/'copy.json', words)
        return folder

    def test_confirmation_code_alone_never_attests_person_review(self):
        folder = self.prepared()
        waiting = run('go', folder)
        with patch.object(cli, '_publish_platform') as send:
            again = run('go', folder, '--confirm', waiting['confirmation'])
            self.assertEqual('NEEDS_A_PERSON', again['state'])
            send.assert_not_called()

    def test_native_review_needs_both_flags_and_preserves_same_upload(self):
        key = self.draft()
        before = self.record(key)
        job = core.home()/'browser-jobs'/f'{key}.json'
        raw = job.read_bytes()
        waiting = native_browser.authorize_publish(key, 'fixture_owner')
        code = waiting['confirmation']
        self.assertEqual('NEEDS_A_PERSON', native_browser.authorize_publish(key, 'fixture_owner', code)['state'])
        self.assertEqual('NEEDS_A_PERSON', native_browser.authorize_publish(key, 'fixture_owner', person_approved=True)['state'])
        with self.assertRaises(core.VpubError) as error:
            native_browser.authorize_publish(key, 'fixture_owner', before['data']['plan_hash'], person_approved=True)
        self.assertEqual('confirmation_required', error.exception.code)
        self.assertEqual(before, self.record(key))
        self.assertEqual(raw, job.read_bytes())
        result = run('browser', 'authorize-publish', key, '--identity', 'fixture_owner',
                     '--confirm', code, '--person-approved')
        self.assertTrue(result['final_submission_allowed'])
        self.assertEqual(0, result['platform_writes'])
        after = native_browser.request(key)
        self.assertEqual(json.loads(raw)['plan']['video'], after['plan']['video'])
        self.assertEqual(before['data']['receipt'], self.record(key)['data']['receipt'])
        with self.assertRaises(core.VpubError):
            native_browser.authorize_publish(key, 'fixture_owner', code, person_approved=True)

    def test_native_existing_platform_still_respects_saved_show_first(self):
        self.prior('x')
        key = self.draft()
        core.atomic_json(core.home()/'config.json', {'show_first': True})
        waiting = native_browser.authorize_publish(key, 'fixture_owner')
        self.assertEqual(['show_first', 'draft_promotion'], waiting['review_reasons'])
        self.assertEqual('NEEDS_A_PERSON', native_browser.authorize_publish(key, 'fixture_owner', waiting['confirmation'])['state'])
        self.assertFalse(native_browser.request(key)['final_submission_allowed'])

    def test_go_explicit_attestation_requires_a_code_and_does_not_carry_over(self):
        folder = self.prepared()
        with patch.object(cli, '_publish_platform', return_value={'state': 'SUBMISSION_VERIFIED'}) as send:
            waiting = run('go', folder, '--person-approved')
            self.assertEqual('NEEDS_A_PERSON', waiting['state'])
            send.assert_not_called()
            result = run('go', folder, '--confirm', waiting['confirmation'], '--person-approved', '--workers', '2')
            self.assertTrue(result['all_submissions_verified'])
            self.assertEqual(1, send.call_count)
            words = core.read_json(folder/'copy.json')
            words['platforms']['youtube']['title'] = 'Changed boat'
            core.atomic_json(folder/'copy.json', words)
            with self.assertRaises(core.VpubError) as error:
                run('go', folder, '--confirm', waiting['confirmation'], '--person-approved')
            self.assertEqual('confirmation_required', error.exception.code)
            self.assertEqual(1, send.call_count)


if __name__ == '__main__':
    unittest.main()
