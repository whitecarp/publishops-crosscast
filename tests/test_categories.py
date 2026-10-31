"""Category omission, reclassification and upload/receipt regression tests."""
import unittest
from unittest.mock import MagicMock, patch
from video_publisher import adapters, metadata, native_browser
from video_publisher.core import atomic_json, load_plan, read_json, VpubError
import test_metadata_cover_playlist as fixtures


class CategoryTests(unittest.TestCase):
    setUp = fixtures.MetadataTests.setUp
    tearDown = fixtures.MetadataTests.tearDown
    prepared_response = fixtures.MetadataTests.prepared_response
    def test_category_must_be_explicit_and_host_can_supply_it(self):
        del self.value['platforms']['youtube']['category_id']
        atomic_json(self.path, self.value)
        with self.assertRaises(VpubError) as error:
            load_plan(self.path, ['youtube'])
        self.assertEqual('category_required', error.exception.code)
        req, response, _ = self.prepared_response()
        result = metadata.apply(self.path, req, response, self.root / 'out.json')
        self.assertEqual('1', read_json(result['manifest'])['platforms']['youtube']['category_id'])

    def test_explicit_selection_cannot_be_replaced_by_copywriting(self):
        req, path, response = self.prepared_response()
        response['platforms']['youtube']['category_id'] = '22'
        atomic_json(path, response)
        with self.assertRaises(VpubError) as error:
            metadata.apply(self.path, req, path, self.root / 'out.json')
        self.assertEqual('category_changed', error.exception.code)

    def test_native_category_revision_requires_fresh_matching_evidence(self):
        from video_publisher import cli
        plan = load_plan(self.path, ['youtube'])
        key = cli.publish_plan(plan, plan['confirmation'])['results']['youtube']['key']
        identity = 'UC' + 'a' * 22
        native_browser.begin(key, identity)
        opts = plan['platforms']['youtube']
        evidence = {k: opts[k] for k in ('title','description','tags','visibility','ai_generated','made_for_kids','category_id')}
        evidence.update(id='abcdefghijk',identity=identity,page_url='https://studio.youtube.com/video/abcdefghijk/edit',outcome='verified',observation='Synthetic saved category fixture.')
        proof = self.root / 'proof.json'
        atomic_json(proof, evidence)
        native_browser.record(key, proof)
        self.value['platforms']['youtube']['category_id'] = '24'
        atomic_json(self.path, self.value)
        changed = load_plan(self.path, ['youtube'])
        native_browser.revise(key, self.path, changed['confirmation'], identity)
        with self.assertRaises(VpubError):
            native_browser.record(key, proof)
        evidence['category_id'] = '24'
        atomic_json(proof, evidence)
        result = native_browser.record(key, proof)
        self.assertEqual('abcdefghijk', result['id'])
        self.assertEqual('AGENT_REPORTED_VERIFIED', result['state'])



