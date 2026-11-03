"""Remembered choices and per-plan review, with no real accounts or browser."""
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, operations, preferences
from video_publisher.core import VpubError, atomic_json, config, read_json


def run(*words):
    return cli.execute(cli.parser().parse_args([str(word) for word in words]))


class LocalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.patched(patch.dict(os.environ, {'VPUB_HOME': str(self.home), 'BILI_CLI_HOME': str(self.root / 'bili')}))
        self.patched(patch('builtins.input', side_effect=AssertionError('The CLI must not prompt')))
        self.patched(patch.object(adapters, 'preflight', side_effect=AssertionError('No account calls')))
        self.patched(patch.object(adapters, 'publish', side_effect=AssertionError('No real publication')))
        self.video = self.root / 'clip.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + bytes(1024))
        self.folder = self.root / 'prepared'

    def patched(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def prepare(self, *extra):
        return run('prepare', self.video, '--folder', self.folder, '--language', 'en',
                   '--visibility', 'public', '--ai-generated', 'no', *extra)

    def write_copy(self, title='A film'):
        path = self.folder / 'copy.json'
        words = read_json(path)
        for text in words['platforms'].values():
            text.update(title=title, description='The story of a small boat.', tags=[])
            if 'category_id' in text:
                text['category_id'] = '1'
        atomic_json(path, words)

    def review_plan(self):
        run('preferences', '--platforms', 'youtube', '--show-first', 'yes')
        self.prepare()
        self.write_copy()


class PreferencesTests(LocalTests):
    def test_read_does_not_save_or_guess_unknown_choices(self):
        answer = run('preferences')
        self.assertEqual(('auto', None, None), (answer['report_language'], answer['preferred_platforms'], answer['show_first']))
        self.assertEqual(['preferred_platforms', 'show_first'], answer['missing'])
        self.assertFalse((self.home / 'config.json').exists())

    def test_set_choices_then_partial_update_keeps_unrelated_settings(self):
        atomic_json(self.home / 'config.json', {'external_stop_file': 'local-stop.json'})
        first = run('preferences', '--platforms', 'youtube,instagram', '--show-first', 'yes', '--language', 'zh')
        self.assertEqual([], first['missing'])
        second = run('preferences', '--language', 'en')
        self.assertEqual((['youtube', 'instagram'], True, 'en'),
                         (second['preferred_platforms'], second['show_first'], second['report_language']))
        self.assertEqual({'external_stop_file': 'local-stop.json', 'report_language': 'en',
                          'preferred_platforms': ['youtube', 'instagram'], 'show_first': True}, config())
        self.assertEqual('en', operations.reporting_language())
        self.assertNotIn('external_stop_file', second)

    def test_saving_no_is_a_known_choice_not_a_missing_answer(self):
        answer = run('preferences', '--show-first', 'no')
        self.assertIs(False, answer['show_first'])
        self.assertEqual(['preferred_platforms'], answer['missing'])
        self.assertIn('do not authorize', answer['note'])

    def test_invalid_platform_choices_never_replace_saved_choices(self):
        run('preferences', '--platforms', 'youtube')
        before = (self.home / 'config.json').read_bytes()
        for names in ('all', '', 'youtube,youtube', 'youtube,account-name', ['youtube', 3], {}):
            with self.subTest(names=names), self.assertRaises(VpubError):
                preferences.update(platforms=names)
            self.assertEqual(before, (self.home / 'config.json').read_bytes())

    def test_corrupt_show_first_cannot_be_treated_as_no(self):
        atomic_json(self.home / 'config.json', {'show_first': 'false'})
        with self.assertRaises(VpubError) as caught:
            run('preferences')
        self.assertEqual('invalid_preferences', caught.exception.code)

    def test_prepare_without_saved_or_explicit_platforms_refuses_without_folder(self):
        with self.assertRaises(VpubError) as caught:
            self.prepare()
        self.assertEqual('platforms_required', caught.exception.code)
        self.assertFalse(self.folder.exists())

    def test_prepare_uses_saved_platforms_only(self):
        run('preferences', '--platforms', 'youtube,instagram')
        self.assertEqual({'youtube', 'instagram'}, set(self.prepare()['publishing']))

    def test_explicit_platforms_override_without_changing_preferences(self):
        run('preferences', '--platforms', 'youtube')
        self.assertEqual({'instagram'}, set(self.prepare('--platforms', 'instagram')['publishing']))
        self.assertEqual(['youtube'], run('preferences')['preferred_platforms'])


class ReviewTests(LocalTests):
    def test_show_first_returns_exact_plan_without_account_access(self):
        self.review_plan()
        answer = run('go', self.folder)
        self.assertEqual(('NEEDS_A_PERSON', False, 0), (answer['state'], answer['ready'], answer['platform_writes']))
        self.assertTrue(answer['review_required'])
        self.assertEqual('A film', answer['review']['platforms']['youtube']['title'])
        self.assertEqual(answer['confirmation'], answer['review']['confirmation'])
        self.assertIn('--confirm ' + answer['confirmation'], answer['next'])
        self.assertFalse(cli.finished(answer))

    def test_preference_change_does_not_waive_existing_folder_review(self):
        self.review_plan()
        run('preferences', '--show-first', 'no')
        self.assertTrue(run('go', self.folder)['review_required'])

    def test_enabling_preference_also_protects_older_prepared_folders(self):
        self.prepare('--platforms', 'youtube')
        self.write_copy()
        # Older prepared folders had no review.json.
        (self.folder / 'review.json').unlink()
        run('preferences', '--show-first', 'yes')
        self.assertTrue(run('go', self.folder)['review_required'])

    def test_current_hash_passes_and_no_approval_is_saved_for_later_plans(self):
        self.review_plan()
        answer = run('go', self.folder)
        with patch.object(cli, 'publish_plan', return_value={'platform_writes': 0}) as publish:
            run('go', self.folder, '--confirm', answer['confirmation'], '--person-approved')
        self.assertEqual(answer['confirmation'], publish.call_args.args[1])
        self.write_copy('A different film')
        changed = run('go', self.folder)
        self.assertNotEqual(answer['confirmation'], changed['confirmation'])
        self.assertEqual('NEEDS_A_PERSON', changed['state'])

    def test_stale_hash_after_text_or_audience_change_fails_before_accounts(self):
        self.review_plan()
        original = run('go', self.folder)['confirmation']
        self.write_copy('A revised film')
        with self.assertRaises(VpubError) as caught:
            run('go', self.folder, '--confirm', original)
        self.assertEqual('confirmation_required', caught.exception.code)
        current = run('go', self.folder)['confirmation']
        path = self.folder / 'publish.json'
        manifest = read_json(path)
        manifest['platforms']['youtube']['visibility'] = 'private'
        atomic_json(path, manifest)
        with self.assertRaises(VpubError) as caught:
            run('go', self.folder, '--confirm', current)
        self.assertEqual('confirmation_required', caught.exception.code)

    def test_show_first_no_does_not_bypass_flagged_text(self):
        run('preferences', '--platforms', 'youtube', '--show-first', 'no')
        self.prepare()
        self.write_copy('Contact 13812345678')
        answer = run('go', self.folder)
        self.assertEqual('NEEDS_A_PERSON', answer['state'])
        self.assertTrue(answer['text_flags'])

    def test_invalid_review_record_stops_without_accounts(self):
        self.review_plan()
        atomic_json(self.folder / 'review.json', {'schema_version': 1, 'show_first': 'no'})
        with self.assertRaises(VpubError) as caught:
            run('go', self.folder)
        self.assertEqual('invalid_review', caught.exception.code)

    def test_dry_run_explains_confirmation_without_publishing(self):
        self.review_plan()
        with patch('video_publisher.rehearsal.sign_in', return_value={'saved_record_present': True}):
            answer = run('go', self.folder, '--dry-run')
        self.assertEqual(0, answer['platform_writes'])
        self.assertTrue(answer['review_required'])
        self.assertIn('not publishing authorization', answer['confirmation_note'])
        self.assertIn('--confirm ' + answer['confirmation'], answer['next'])


class FirstRunMessageTests(LocalTests):
    def test_doctor_shows_saved_choices_and_only_placeholder_examples(self):
        run('preferences', '--platforms', 'youtube', '--show-first', 'yes', '--language', 'en')
        with patch('video_publisher.overview.chrome_path', return_value='test-chrome'), \
                patch.object(cli, 'separate_bili_agent', return_value=None), \
                patch.object(adapters, 'addon_installed', return_value=False), \
                patch.object(cli.importlib.util, 'find_spec', return_value=object()):
            answer = cli.doctor()
        self.assertEqual(run('preferences'), answer['preferences'])
        self.assertIn('--platforms PLATFORMS', answer['next'])
        self.assertIn('--visibility VISIBILITY --ai-generated AI_STATUS', answer['next'])
        self.assertIn('VISIBILITY is public or private; AI_STATUS is yes or no.', answer['next'])
        self.assertNotIn('|', answer['next'])
        self.assertNotIn('--platforms bilibili', answer['next'])

    def test_prepare_explains_copy_shape_account_audience_cover_and_kids(self):
        answer = self.prepare('--platforms', 'youtube,instagram,x')
        self.assertEqual({'platforms'}, set(answer['copy_template']))
        instructions = ' '.join(answer['instructions'])
        self.assertNotIn('Return request_id plus', instructions)
        self.assertIn('go adds request_id', instructions)
        self.assertIn('public if the account is public', answer['publishing']['instagram']['visibility_note'])
        self.assertIn('public if the account is public', answer['publishing']['x']['visibility_note'])
        self.assertIn('phone after posting', answer['publishing']['instagram']['cover_note'])
        self.assertIs(False, answer['publishing']['youtube']['made_for_kids'])
        self.assertIn('defaults to false', answer['publishing']['youtube']['audience_note'])

    def test_plan_metavars_and_portrait_limits_are_in_help(self):
        for command in (('plan',), ('publish',), ('covers', 'prepare'), ('instagram', 'prepare'),
                        ('metadata', 'request'), ('metadata', 'apply')):
            output = io.StringIO()
            with self.subTest(command=command), redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                cli.parser().parse_args([*command, '--help'])
            self.assertEqual(0, exit.exception.code)
            self.assertIn('PLAN', output.getvalue())
            self.assertIn('ready.json', output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            cli.parser().parse_args(['covers', 'portrait', '--help'])
        self.assertIn('100', output.getvalue())
        self.assertIn('would_letterbox', output.getvalue())


if __name__ == '__main__':
    unittest.main()
