"""Exercise the actual workflow's PowerShell blocks with offline command stand-ins."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/tests.yml'


def block(name):
    text = WORKFLOW.read_text(encoding='utf-8')
    step = text.split('      - name: ' + name + '\n', 1)[1].split('\n      - ', 1)[0]
    return '\n'.join(line[10:] for line in step.split('        run: |\n', 1)[1].splitlines())


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell required for Windows workflow')
class WheelWorkflowTests(unittest.TestCase):
    def run_block(self, name, wheel_names=(), fail_at=0):
        with tempfile.TemporaryDirectory(prefix='ci-wheel-offline-') as tmp:
            root = Path(tmp)
            (root/'dist').mkdir()
            for wheel in wheel_names:
                (root/'dist'/wheel).write_bytes(b'offline fixture, never installed')
            script = r'''
$ErrorActionPreference = 'Stop'
$global:ordinal = 0
function Invoke-FakeNative($kind, $arguments) {
    $global:ordinal++
    Add-Content -LiteralPath calls.jsonl -Value (ConvertTo-Json -Compress -InputObject @{kind=$kind; arguments=@($arguments)})
    $global:LASTEXITCODE = if ($global:ordinal -eq [int]$env:FAIL_AT) { 19 } else { 0 }
    if ($kind -eq 'python' -and $arguments[0] -eq '-c' -and $arguments[1].Contains('__version__')) {
        Write-Output 'publishops_crosscast-0.16.0-py3-none-any.whl'
    }
}
function python { Invoke-FakeNative 'python' $args }
function vpub { Invoke-FakeNative 'vpub' $args }
function bili { Invoke-FakeNative 'bili' $args }
'''
            script += block(name) + '\nWrite-Output "BLOCK_FINISHED"\n'
            (root/'step.ps1').write_text(script, encoding='utf-8')
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(root/'step.ps1')], cwd=root,
                                    env={**os.environ, 'FAIL_AT':str(fail_at), 'VPUB_HOME':str(root/'vpub'), 'BILI_CLI_HOME':str(root/'bili')},
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            calls = [json.loads(line) for line in (root/'calls.jsonl').read_text(encoding='utf-8-sig').splitlines()] if (root/'calls.jsonl').exists() else []
            return result, calls

    def test_dependency_block_stops_at_each_native_failure(self):
        for n in range(1,4):
            with self.subTest(command=n):
                result, calls = self.run_block('Install package and build tools', fail_at=n)
                self.assertEqual(19, result.returncode, result.stderr)
                self.assertEqual(n, len(calls))
                self.assertNotIn('BLOCK_FINISHED', result.stdout)

    def test_zero_multiple_or_wrong_wheels_never_install(self):
        for names in [[], ['first.whl','second.whl'], ['wrong.whl']]:
            with self.subTest(wheels=names):
                result, calls = self.run_block('Install built wheel and smoke-test CLI', names)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(any('--force-reinstall' in c['arguments'] for c in calls))

    def test_concrete_wheel_and_provenance_and_native_failures(self):
        names=['publishops_crosscast-0.16.0-py3-none-any.whl']
        result, calls = self.run_block('Install built wheel and smoke-test CLI', names)
        self.assertEqual(0, result.returncode, result.stderr)
        install=next(c for c in calls if '--force-reinstall' in c['arguments'])
        self.assertTrue(Path(install['arguments'][-1]).is_absolute())
        self.assertEqual(names[0], Path(install['arguments'][-1]).name)
        self.assertNotIn('*', install['arguments'][-1])
        self.assertIn('BUILT_WHEEL_PROVENANCE_OK', calls[2]['arguments'][1])
        for n in range(1,len(calls)+1):
            with self.subTest(command=n):
                failed, seen=self.run_block('Install built wheel and smoke-test CLI', names, fail_at=n)
                self.assertEqual(19, failed.returncode, failed.stderr)
                self.assertEqual(n,len(seen))
                self.assertNotIn('BLOCK_FINISHED', failed.stdout)
