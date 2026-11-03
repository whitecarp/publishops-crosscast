"""Unsupported Bilibili deletion is a local refusal, never an account probe."""
import builtins
from copy import deepcopy
import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, core, work_delete_bili as bili, work_delete_state as state


class WorkDeleteBiliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'VPUB_HOME': str(self.root / 'vpub'), 'BILI_CLI_HOME': str(self.root / 'bili')})
        env.start()
        self.addCleanup(env.stop)
        self.target = {'key': 'a' * 64, 'platform': 'bilibili', 'id': 'BV0000000001', 'identity': '123',
                       'account': 'default', 'title': 'Synthetic video', 'generation': 'b' * 64,
                       'url': 'https://www.bilibili.com/video/BV0000000001', 'attempt': 'c' * 32}
        self.path = str(self.root / 'receipt.json')

    def refused(self, target, operation, path, code):
        before = deepcopy(target)
        with patch.object(adapters, 'bili') as account, patch.object(state, 'begin_press') as gate, \
                patch.object(core, 'atomic_json') as save, patch.object(subprocess, 'run') as process, \
                patch.object(Path, 'open') as opened:
            with self.assertRaises(core.VpubError) as caught:
                bili.execute(target, operation, path)
            self.assertEqual(code, caught.exception.code)
            for call in (account, gate, save, process, opened):
                call.assert_not_called()
        self.assertEqual(before, target)
        self.assertEqual([], list(self.root.iterdir()))

    def test_every_operation_refuses_before_any_account_call_or_attempt_file(self):
        for operation in ('inspect', 'delete', 'verify'):
            with self.subTest(operation=operation):
                self.refused(self.target, operation, self.path, 'unsupported_delete')

    def test_invalid_operations_are_local_validation_errors(self):
        for operation in ('publish', '', None, ['delete']):
            with self.subTest(operation=operation):
                self.refused(self.target, operation, self.path, 'invalid_operation')

    def test_invalid_targets_are_local_validation_errors(self):
        targets = [None, [], {}, *({**self.target, **change} for change in (
            {'platform': 'youtube'}, {'id': 'wrong'}, {'id': None}, {'identity': 'not-an-owner'},
            {'key': 'a' * 32}, {'key': None}, {'account': ' '}, {'generation': ''}, {'title': None}))]
        for target in targets:
            with self.subTest(target=target):
                self.refused(target, 'delete', self.path, 'invalid_deletion_target')

    def test_invalid_receipt_paths_are_not_opened(self):
        for path in ('', ' ', None, self.root / 'receipt.json'):
            with self.subTest(path=path):
                self.refused(self.target, 'delete', path, 'invalid_deletion_target')

    def test_parent_module_load_never_imports_bili_cli_or_gpl_library(self):
        original = builtins.__import__

        def checked(name, *args, **kwargs):
            if name.split('.')[0] in {'bili_cli', 'bilibili_api'}:
                self.fail('Unsupported route imported a platform library')
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=checked):
            importlib.reload(bili)
            self.refused(self.target, 'inspect', self.path, 'unsupported_delete')


if __name__ == '__main__':
    unittest.main()
