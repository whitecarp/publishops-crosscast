"""Offline checks for the package fingerprint and its installation evidence."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import audit


ROOT = Path(__file__).resolve().parents[1]


class StampTests(unittest.TestCase):
    def test_line_endings_path_order_and_changed_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            roots = []
            for tree, ending in (("a", b"\r\n"), ("b", b"\n")):
                packages = [Path(name, tree, pkg) for pkg in ("video_publisher", "bili_cli")]
                for pkg in packages:
                    (pkg / "nested").mkdir(parents=True)
                    (pkg / "nested" / "A.py").write_bytes(b"a" + ending + b"b\r")
                    (pkg / "z.py").write_bytes(b"c")
                roots.append(packages)
            original = audit.source_digest(roots[0])
            self.assertEqual(original, audit.source_digest(reversed(roots[1])))
            (roots[1][1] / "z.py").write_bytes(b"d")
            self.assertNotEqual(original, audit.source_digest(roots[1]))

    def test_running_packages_match_git_head_blobs(self):
        def git(*args, **kwargs):
            return subprocess.check_output(["git", "-C", str(ROOT), *args], **kwargs)
        if not (ROOT / ".git").exists():
            self.skipTest("Git checkout required for the independent HEAD comparison")
        listing = git("ls-tree", "-r", "-z", "HEAD", "--", "src/bili_cli", "src/video_publisher")
        files = []
        for entry in listing.split(b"\0"):
            if entry.endswith(b".py"):
                header, path = entry.split(b"\t", 1)
                files.append((path.decode("utf8").removeprefix("src/"), header.split()[-1]))
        self.assertTrue(files)
        files.sort()
        stream = io.BytesIO(git("cat-file", "--batch", input=b"".join(oid + b"\n" for _, oid in files)))
        records = []
        for path, oid in files:
            found, kind, size = stream.readline().split()
            self.assertEqual((oid, b"blob"), (found, kind))
            content = stream.read(int(size)).replace(b"\r\n", b"\n")
            self.assertEqual(b"\n", stream.read(1))
            file_hash = hashlib.sha256(content).hexdigest()
            records.append(f"{path}\0{file_hash}\n".encode("utf8"))
        self.assertEqual(hashlib.sha256(b"".join(records)).hexdigest(), audit.code_stamp()["source_digest"])

    def test_install_kinds_and_no_private_locations(self):
        sha = "ab" * 20
        private = "https://user:secret@host.example/private/"
        for info, kind, commit in [
            ({"archive_info": {}, "url": private + sha + ".zip?password=secret"}, "archive", sha),
            ({"vcs_info": {"commit_id": "abc123"}, "url": private}, "vcs", "abc123"),
            ({"dir_info": {}, "url": "file:///C:/private/path"}, "local", None),
            ({"vcs_info": {"commit_id": private}}, "vcs", None),
            ({"archive_info": {}, "url": private + "main.zip?token=" + sha}, "archive", None),
            (None, "unknown", None),
        ]:
            with self.subTest(kind=kind), patch.object(audit, "direct_url", return_value=info):
                audit._stamp.cache_clear()
                self.addCleanup(audit._stamp.cache_clear)
                with patch.object(audit, "source_digest", return_value="1" * 64) as hash_tree:
                    first = audit.code_stamp()
                    first["install"] = "tampered"
                    stamp = audit.code_stamp()
                    hash_tree.assert_called_once()
                self.assertEqual(set(stamp), {"version", "source_digest", "install", "commit", "python", "os"})
                self.assertEqual((kind, commit), (stamp["install"], stamp["commit"]))
                self.assertNotRegex(json.dumps(stamp), r"[/\\]|secret|private")

    def test_cli_startup_help_and_version_never_load_audit_or_hash_sources(self):
        script = """
import contextlib, hashlib, io, sys
from unittest.mock import patch
with patch.object(hashlib, 'sha256', side_effect=AssertionError('unexpected hash')):
    from video_publisher import cli
    for option in ('--help', '--version'):
        sys.argv = ['vpub', option]
        with contextlib.redirect_stdout(io.StringIO()):
            try: cli.main()
            except SystemExit as e: assert e.code == 0, e.code
    assert 'video_publisher.audit' not in sys.modules
"""
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True)
