import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from video_publisher import cli, deletion, native_browser
from video_publisher.core import Ledger, VpubError, atomic_json, load_plan


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, VPUB_HOME=str(self.root/'runtime'))
        self.env.start()
        video = self.root/'video.mp4'
        video.write_bytes(b'synthetic offline fixture')
        self.manifest = self.root/'plan.json'
        self.value = cli.template(video, 'en')
        self.value['platforms'] = {'instagram': {'method':'native', 'visibility':'account',
                                                'ai_generated':True, 'expected_identity':'fixture_account'}}
        atomic_json(self.manifest, self.value)
        self.plan = load_plan(self.manifest)
        self.key = cli.publish_plan(self.plan,self.plan['confirmation'])['results']['instagram']['key']
        native_browser.begin(self.key, 'fixture_account')
        opts = native_browser.request(self.key)['options']
        proof = {k:opts[k] for k in ('title','description','tags','visibility','ai_generated','post_text')}
        proof.update(id='fixture_old', identity='fixture_account', outcome='verified',
                     page_url='https://www.instagram.com/reel/fixture_old/', observation='Synthetic publication fixture.')
        self.evidence = self.root/'evidence.json'
        atomic_json(self.evidence,proof)
        native_browser.record(self.key,self.evidence)
        self.proof = {'id':'fixture_old','identity':'fixture_account',
                      'page_url':'https://www.instagram.com/reel/fixture_old/',
                      'profile_url':'https://www.instagram.com/fixture_account/',
                      'delete_acknowledged':True,'work_absent':True,
                      'observation':'Synthetic successful UI deletion and refreshed absence.',
                      'recovery_note':'Synthetic recoverable deletion fixture.'}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def deleted(self):
        deletion.request(self.key,'fixture_account','fixture_old')
        atomic_json(self.evidence,self.proof)
        return deletion.record(self.key,self.evidence)

    def test_wrong_identity_id_and_missing_absence_fail_closed(self):
        for identity,work in [('other','fixture_old'),('fixture_account','other')]:
            with self.assertRaises(VpubError): deletion.request(self.key,identity,work)
        deletion.request(self.key,'fixture_account','fixture_old')
        for field,value in [('work_absent',False),('delete_acknowledged',False),
                            ('profile_url','https://www.instagram.com/other/')]:
            proof={**self.proof,field:value}
            atomic_json(self.evidence,proof)
            with self.assertRaises(VpubError): deletion.record(self.key,self.evidence)
        with self.assertRaises(VpubError):
            deletion.repost(self.key,self.manifest,self.plan['confirmation'],'fixture_account','fixture_old')
        self.assertFalse(native_browser.request(self.key)['can_begin'])
        self.assertFalse(native_browser.request(self.key)['final_submission_allowed'])

    def test_repost_preserves_history_and_blocks_duplicate_restart(self):
        self.assertEqual('AGENT_REPORTED_DELETED', self.deleted()['state'])
        self.assertEqual('SKIPPED_EXISTING',cli.publish_plan(self.plan,self.plan['confirmation'])['results']['instagram']['state'])
        cover=self.root/'portrait.jpg'
        cover.write_bytes(b'synthetic cover fixture')
        self.value['cover']=str(cover)
        atomic_json(self.manifest,self.value)
        plan=load_plan(self.manifest)
        result=deletion.repost(self.key,self.manifest,plan['confirmation'],'fixture_account','fixture_old')
        self.assertEqual('WAITING_FOR_BROWSER',result['state'])
        with self.assertRaises(VpubError):
            deletion.repost(self.key,self.manifest,plan['confirmation'],'fixture_account','fixture_old')
        job=native_browser.begin(self.key,'fixture_account')
        self.assertEqual(cover.read_bytes(),Path(job['plan']['cover']).read_bytes())
        ledger=Ledger()
        try:
            data=ledger.get(self.key)['data']
            self.assertNotIn('receipt',data)
            self.assertEqual('fixture_old',data['deleted_generations'][0]['publication_receipt']['id'])
            self.assertEqual('fixture_old',data['deleted_generations'][0]['deletion_receipt']['id'])
        finally: ledger.close()

    def test_repost_rejects_changed_audience_or_video(self):
        self.deleted()
        self.value['platforms']['instagram']['ai_generated']=False
        atomic_json(self.manifest,self.value)
        plan=load_plan(self.manifest)
        with self.assertRaises(VpubError):
            deletion.repost(self.key,self.manifest,plan['confirmation'],'fixture_account','fixture_old')
        self.value['platforms']['instagram']['ai_generated']=True
        Path(self.value['video']).write_bytes(b'different video')
        atomic_json(self.manifest,self.value)
        plan=load_plan(self.manifest)
        with self.assertRaises(VpubError):
            deletion.repost(self.key,self.manifest,plan['confirmation'],'fixture_account','fixture_old')
