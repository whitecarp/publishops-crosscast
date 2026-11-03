"""Independent consent probes: isolated homes, synthetic files, no account calls."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from video_publisher import cli, core, native_browser


class IndependentConsentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='crosscast-consent-independent-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, VPUB_HOME=str(self.root/'home'),
                         BILI_CLI_HOME=str(self.root/'bili'))
        env.start()
        self.addCleanup(env.stop)
        for name in ('browser_call', 'bili', 'auth_status'):
            guard = patch.object(cli.adapters, name, side_effect=AssertionError('No account calls'))
            guard.start()
            self.addCleanup(guard.stop)

    def plan(self, name, *, draft=False, method='native'):
        video = self.root/(name+'.mp4')
        video.write_bytes(b'\x00\x00\x00\x18ftypmp42'+bytes(32)+name.encode())
        manifest = self.root/(name+'.json')
        core.atomic_json(manifest, {'schema_version': 1, 'video': str(video),
            'title': 'Synthetic '+name, 'description': 'Offline fixture', 'tags': [],
            'platforms': {'instagram': {'method': method, 'visibility': 'account',
                'ai_generated': False, 'submission_mode': 'draft_only' if draft else 'publish'}}})
        return core.load_plan(manifest)

    def verified_prior(self):
        plan = self.plan('prior', method='browser')
        with patch.object(cli.adapters, 'preflight', return_value={'identity': 'fixture_owner'}), \
             patch.object(cli.adapters, 'publish', return_value={
                 'state': 'SUBMISSION_VERIFIED', 'method': 'browser', 'id': '123456789'}):
            self.assertTrue(cli.publish_plan(plan, plan['confirmation'], reviewed=True)
                            ['all_submissions_verified'])

    def draft(self):
        plan = self.plan('draft', draft=True)
        result = cli.publish_plan(plan, plan['confirmation'])
        key = result['results']['instagram']['key']
        native_browser.begin(key, 'fixture_owner')
        evidence = self.root/'draft-evidence.json'
        core.atomic_json(evidence, {'outcome': 'draft', 'identity': 'fixture_owner',
            'page_url': 'https://www.instagram.com/create', 'post_text': plan['platforms']['instagram']['post_text'],
            'media_preview': True, 'draft_saved': True, 'observation': 'Synthetic fixture only.'})
        native_browser.record(key, evidence)
        return key

    def saved(self, key):
        db = core.Ledger()
        try:
            return db.get(key), (core.home()/'browser-jobs'/f'{key}.json').read_bytes()
        finally:
            db.close()

    def test_returning_platform_draft_still_requires_person_attestation(self):
        self.verified_prior()
        key = self.draft()
        before = self.saved(key)
        waiting = native_browser.authorize_publish(key, 'fixture_owner')
        result = cli.execute(cli.parser().parse_args([
            'browser', 'authorize-publish', key, '--identity', 'fixture_owner',
            '--confirm', waiting['confirmation']]))
        self.assertEqual('NEEDS_A_PERSON', result['state'], result)
        self.assertEqual(before, self.saved(key))
        self.assertFalse(native_browser.request(key)['final_submission_allowed'])

    def test_first_platform_old_draft_code_and_one_flag_never_mutate(self):
        key = self.draft()
        before = self.saved(key)
        waiting = native_browser.authorize_publish(key, 'fixture_owner')
        for confirm, approved in ((waiting['confirmation'], False), (None, True)):
            result = native_browser.authorize_publish(key, 'fixture_owner', confirm,
                                                      person_approved=approved)
            self.assertEqual('NEEDS_A_PERSON', result['state'])
            self.assertEqual(before, self.saved(key))
        with self.assertRaises(core.VpubError) as caught:
            native_browser.authorize_publish(key, 'fixture_owner', before[0]['data']['plan_hash'],
                                             person_approved=True)
        self.assertEqual('confirmation_required', caught.exception.code)
        self.assertEqual(before, self.saved(key))

    def test_two_approved_promotions_have_one_owner(self):
        key = self.draft()
        waiting = native_browser.authorize_publish(key, 'fixture_owner')
        barrier = Barrier(2)

        def attempt():
            barrier.wait(timeout=5)
            try:
                return native_browser.authorize_publish(key, 'fixture_owner', waiting['confirmation'],
                                                        person_approved=True)['state']
            except core.VpubError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(1, results.count('BROWSER_IN_PROGRESS'), results)
        self.assertTrue(set(results) <= {'BROWSER_IN_PROGRESS', 'busy', 'authorization_not_allowed'}, results)
        self.assertEqual(1, len(self.saved(key)[0]['data']['submission_authorizations']))

    def test_worker_batch_code_alone_does_not_dispatch(self):
        plan = self.plan('new-browser', method='browser')
        with patch.object(cli, '_publish_platform') as send:
            answer = cli.publish_plan(plan, plan['confirmation'], workers=2)
        self.assertEqual('NEEDS_A_PERSON', answer['state'])
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
