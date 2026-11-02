"""Local help and version must not load publication overview dependencies."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class StartupTests(unittest.TestCase):
    def run_fresh(self, args):
        script = """
import sys
from video_publisher import cli
assert 'video_publisher.overview' not in sys.modules
if len(sys.argv) > 1:
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0, exc.code
    else:
        raise AssertionError('help/version did not exit')
assert 'video_publisher.overview' not in sys.modules
"""
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, VPUB_HOME=folder, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-B", "-c", script, *args],
                                    capture_output=True, text=True, encoding="utf-8",
                                    env=env, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_import_does_not_load_overview(self):
        self.run_fresh([])

    def test_help_and_version_do_not_load_overview(self):
        for args in (["--help"], ["--version"], ["publish", "--help"]):
            with self.subTest(args=args):
                self.run_fresh(args)
