"""Review is mandatory at the shared entry, with fake accounts and media only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, core, publication_review, quick


class ReviewFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.env = patch.dict(os.environ, VPUB_HOME=str(self.root/'home'), BILI_CLI_HOME=str(self.root/'bili'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = {'confirmation': 'fixture', 'video_sha256': 'synthetic',
                  'platforms': {'x': {'method': 'browser', 'account': 'default', 'title': 'A story'}}}
        self.dispatcher = patch.object(cli, '_publish_platform', return_value={'state': 'SUBMISSION_VERIFIED'})
        self.dispatch = self.dispatcher.start()
        self.addCleanup(self.dispatcher.stop)

    def history(self, state='SUBMISSION_VERIFIED', mode='publish'):
        ledger = core.Ledger()
        try:
            data = {'options': {'submission_mode': mode}}
            key = ledger.claim('x', 'default', 'prior-unrelated-video', data)
            ledger.save(key, state, data)
        finally:
            ledger.close()

class ReviewGateTests(ReviewFixture, unittest.TestCase):
    def test_first_publication_needs_review_even_when_preference_is_no(self):
        core.atomic_json(core.home()/'config.json', {'show_first': False})
        answer = cli.publish_plan(self.p, 'fixture')
        self.assertEqual('NEEDS_A_PERSON', answer['state'])
        self.assertEqual(['first_publication'], answer['review_reasons'])
        self.assertIn('--person-approved', answer['next'])
        self.assertEqual(0, answer['platform_writes'])
        self.assertFalse(cli.finished(answer))
        self.dispatch.assert_not_called()

    def test_review_attestation_requires_the_exact_current_code(self):
        with self.assertRaises(core.VpubError) as error:
            cli.publish_plan(self.p, 'old-code', reviewed=True)
        self.assertEqual('confirmation_required', error.exception.code)
        self.dispatch.assert_not_called()
        self.assertTrue(cli.publish_plan(self.p, 'fixture', reviewed=True)['all_submissions_verified'])
        self.dispatch.assert_called_once()

    def test_existing_customer_without_show_first_keeps_automatic_flow(self):
        self.history()
        self.assertTrue(cli.publish_plan(self.p, 'fixture')['all_submissions_verified'])

    def test_imported_work_promoted_to_verified_is_not_a_first_publication(self):
        ledger = core.Ledger()
        try:
            data = {'options': {'submission_mode': 'publish'}, 'association': 'Imported mapping'}
            key = ledger.claim('x', 'default', 'imported-video', data)
            ledger.save(key, 'SUBMISSION_VERIFIED', data)
        finally:
            ledger.close()
        self.assertEqual(['first_publication'], publication_review.requirements(self.p)['review_reasons'])
        self.assertEqual('NEEDS_A_PERSON', cli.publish_plan(self.p, 'fixture')['state'])
        self.dispatch.assert_not_called()

    def test_existing_customer_show_first_still_stops_direct_publish(self):
        self.history()
        core.atomic_json(core.home()/'config.json', {'show_first': True})
        answer = cli.publish_plan(self.p, 'fixture')
        self.assertEqual(['show_first'], answer['review_reasons'])
        self.dispatch.assert_not_called()

    def test_draft_does_not_count_as_first_publication(self):
        self.history('DRAFT_SAVED', 'draft_only')
        self.assertEqual('NEEDS_A_PERSON', cli.publish_plan(self.p, 'fixture')['state'])
        self.dispatch.assert_not_called()

    def test_adoption_and_unknown_attempt_do_not_waive_first_review(self):
        for state in ('EXISTING_VERIFIED', 'UNKNOWN', 'SUBMITTED_UNVERIFIED'):
            with self.subTest(state=state):
                ledger = core.Ledger()
                try:
                    ledger.db.execute('DELETE FROM publications')
                    ledger.db.commit()
                finally:
                    ledger.close()
                self.history(state)
                self.assertFalse(publication_review.has_published())

    def test_first_draft_is_not_a_publication_but_saved_review_still_applies(self):
        self.p['platforms']['x']['submission_mode'] = 'draft_only'
        self.assertFalse(publication_review.requirements(self.p)['review_required'])
        core.atomic_json(core.home()/'config.json', {'show_first': True})
        self.assertTrue(publication_review.requirements(self.p)['review_required'])

    def test_corrupt_history_is_not_permission(self):
        (core.home()/'publications.sqlite3').write_bytes(b'not a database')
        with self.assertRaises(core.VpubError) as error:
            cli.publish_plan(self.p, 'fixture', reviewed=True)
        self.assertEqual('review_history_unavailable', error.exception.code)
        self.dispatch.assert_not_called()

    def test_legacy_folder_choice_survives_direct_build_plan_and_pref_change(self):
        folder = self.root/'prepared'
        folder.mkdir()
        core.atomic_json(folder/'publish.json', {})
        core.atomic_json(folder/'copy.json', {})
        core.atomic_json(folder/'review.json', {'schema_version': 1, 'show_first': True})
        core.atomic_json(core.home()/'config.json', {'show_first': False})
        self.assertTrue(publication_review.manifest_choice(folder/'build'/'hash'/'ready.json', {}))
        self.history()
        self.p['review_before_publish'] = True
        self.assertEqual('NEEDS_A_PERSON', cli.publish_plan(self.p, 'fixture')['state'])
        self.dispatch.assert_not_called()

    def test_doctor_reports_invalid_preferences_without_rewriting_or_echoing_them(self):
        path = core.home()/'config.json'
        for value in ({'show_first': 'yes'}, [], '{broken secret-text'):
            with self.subTest(kind=type(value).__name__):
                if isinstance(value, str):
                    path.write_text(value, encoding='utf-8')
                else:
                    core.atomic_json(path, value)
                before = path.read_bytes()
                answer = cli.doctor()
                self.assertFalse(answer['ready'])
                self.assertIsNone(answer['preferences'])
                self.assertEqual('invalid_preferences', answer['issues'][0]['code'])
                self.assertNotIn('secret-text', json.dumps(answer))
                self.assertEqual(before, path.read_bytes())


class FirstGoTests(ReviewFixture, unittest.TestCase):
    def run_cli(self, *words):
        return cli.execute(cli.parser().parse_args([str(w) for w in words]))

    def test_first_go_stops_then_explicit_confirmation_reaches_shared_gate(self):
        video = self.root/'clip.mp4'
        video.write_bytes(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + bytes(64))
        answer = self.run_cli('prepare', video, '--platforms', 'youtube', '--language', 'en',
                              '--visibility', 'private', '--ai-generated', 'no')
        self.assertTrue(answer['review_required'])
        self.assertEqual(['first_publication'], answer['review_reasons'])
        folder = Path(answer['folder'])
        copy = core.read_json(folder/'copy.json')
        copy['platforms']['youtube'].update(title='A boat', description='A small boat.', category_id='1', tags=[])
        core.atomic_json(folder/'copy.json', copy)
        preview = self.run_cli('go', folder)
        self.assertEqual('NEEDS_A_PERSON', preview['state'])
        self.dispatch.assert_not_called()
        ready = quick.build(folder, copy)
        # The documented lower-level route cannot turn its known code into approval.
        lower = self.run_cli('publish', ready, '--confirm', preview['confirmation'])
        self.assertEqual('NEEDS_A_PERSON', lower['state'])
        self.dispatch.assert_not_called()
        self.run_cli('go', folder, '--confirm', preview['confirmation'])
        self.dispatch.assert_not_called()
        self.run_cli('go', folder, '--confirm', preview['confirmation'], '--person-approved')
        self.dispatch.assert_called_once()

    def test_legacy_review_survives_metadata_cover_and_instagram_exports(self):
        from video_publisher import covers, instagram_media, metadata
        from PIL import Image
        video = self.root/'clip.mp4'
        video.write_bytes(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + bytes(64))
        core.atomic_json(core.home()/'config.json', {'show_first': True})
        answer = self.run_cli('prepare', video, '--platforms', 'youtube', '--language', 'en',
                              '--visibility', 'private', '--ai-generated', 'no')
        folder = Path(answer['folder'])
        source = folder/'publish.json'
        legacy = core.read_json(source)
        legacy.pop('review_before_publish')
        core.atomic_json(source, legacy)
        core.atomic_json(core.home()/'config.json', {'show_first': False})
        self.history()
        job = metadata.request(source)
        response = {'request_id': job['request_id'], 'platforms': json.loads(json.dumps(job['response_template']['platforms']))}
        response['platforms']['youtube'].update(title='A boat', description='A small boat.', category_id='1', tags=[])
        core.atomic_json(self.root/'request.json', job)
        core.atomic_json(self.root/'response.json', response)
        for out in (self.root/'export.json', folder/'build'/'hash'/'localized.json'):
            result = metadata.apply(source, self.root/'request.json', self.root/'response.json', out)
            self.assertTrue(core.read_json(out)['review_before_publish'])
            plan = core.load_plan(out)
            self.assertEqual(result['confirmation'], plan['confirmation'])
            self.assertEqual('NEEDS_A_PERSON', cli.publish_plan(plan, plan['confirmation'])['state'])
        # Covers and Instagram prepare also export legacy sources directly.
        legacy['platforms']['youtube'].update(response['platforms']['youtube'])
        core.atomic_json(source, legacy)
        cover = self.root/'cover.jpg'
        Image.new('RGB', (1600, 900)).save(cover)
        covers.prepare(source, cover, self.root/'covers', self.root/'covered.json')
        self.assertTrue(core.read_json(self.root/'covered.json')['review_before_publish'])
        legacy['platforms'] = {'instagram': {'method':'browser', 'account':'default', 'visibility':'account',
                                            'ai_generated':False, 'title':'A boat', 'description':'A small boat.',
                                            'metadata_language':'en', 'metadata_mode':'target_only'}}
        core.atomic_json(source, legacy)
        instagram_media.prepare(source, self.root/'instagram.json')
        self.assertTrue(core.read_json(self.root/'instagram.json')['review_before_publish'])
        self.dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
