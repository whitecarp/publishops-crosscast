"""Offline interpreter selection: never run the installer's pip or doctor steps."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
PARSE = r"""
$ErrorActionPreference = 'Stop'
[Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal'
$source = Get-Content -LiteralPath $env:INSTALLER_TEST_SOURCE -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Installer did not parse' }
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell installer")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crosscast-installer-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, "VPUB_HOME": str(self.root / "vpub"),
                    "BILI_CLI_HOME": str(self.root / "bili"),
                    "LOCALAPPDATA": str(self.root / "appdata"),
                    "USERPROFILE": str(self.root / "user"),
                    "INSTALLER_TEST_SOURCE": str(INSTALLER), "PYTHONIOENCODING": "utf-8"}
        self.explicit = str(self.root / "explicit/python.exe")
        self.bundled = str(self.root / "user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe")
        self.on_path = str(self.root / "path/python.exe")
        self.launcher = str(self.root / "launcher/py.exe")
        self.venv = self.root / "appdata/WhiteCarpVideoPublisher/venv"
        self.fallback = self.venv.with_name("venv-supported")

    def powershell(self, script):
        encoded = base64.b64encode((PARSE + script).encode("utf-16le")).decode("ascii")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def select(self, compatible, existing=()):
        case = {"explicit": self.explicit,
                "paths": [self.explicit, self.bundled, self.on_path, self.launcher, *map(str, existing)],
                "commands": {"python": self.on_path, "py": self.launcher}, "compatible": compatible}
        self.env["INSTALLER_TEST_CASE"] = json.dumps(case)
        return self.powershell(r"""
$case = $env:INSTALLER_TEST_CASE | ConvertFrom-Json
$script:probed = [Collections.Generic.List[string]]::new()
$script:chosen = $null
$overrides = @'
function Test-Path([string]$LiteralPath) { return $case.paths -contains $LiteralPath }
function Get-Command([string]$Name, [string]$ErrorAction) {
    $found = $case.commands.PSObject.Properties[$Name]
    if ($found) { return [pscustomobject]@{ Source = $found.Value } }
}
function Test-CompatiblePython([string]$Executable, [string[]]$PrefixArgs = @()) {
    $key = $Executable + '|' + ($PrefixArgs -join ' ')
    $script:probed.Add($key)
    $found = $case.compatible.PSObject.Properties[$key]
    return $found -and $found.Value
}
function New-VirtualEnvironment([string]$Path, [string]$Executable, [string[]]$PrefixArgs = @()) {
    $script:chosen = @{ path = $Path; executable = $Executable; prefix = @($PrefixArgs) }
}
'@
$functions = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] })
$insert = $functions[-1].Extent.EndOffset
$end = $source.IndexOf('# The distribution was renamed')
if ($insert -ge $end -or $end -lt 0) { throw 'Cannot isolate interpreter selection from installation' }
# Execute the real selection control flow, replacing probes and venv creation.
$selection = $source.Substring(0, $insert) + "`n" + $overrides + "`n" + $source.Substring($insert, $end - $insert)
$failure = $null
try { & ([scriptblock]::Create($selection)) -Python $case.explicit | Out-Null }
catch { $failure = $_.Exception.Message }
@{ chosen = $script:chosen; probed = @($script:probed.ToArray()); error = $failure } | ConvertTo-Json -Compress -Depth 5
""")

    def test_supported_version_boundaries(self):
        # Evaluate the actual installer probe with synthetic version_info only.
        probe = re.search(r"'(import sys; sys\.exit\([^\r\n]+\))'", INSTALLER.read_text(encoding="utf-8")).group(1)
        for minor in (9, 10, 11, 12, 13, 14, 15):
            with self.subTest(minor=minor):
                code = f"import sys; sys.version_info = (3, {minor}, 0); " + probe
                result = subprocess.run([sys.executable, "-I", "-B", "-c", code], env=self.env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if 10 <= minor <= 14 else 1, result.stderr)

    def test_actual_interpreter_probe_and_missing_path(self):
        self.env["INSTALLER_TEST_PYTHON"] = sys.executable
        result = self.powershell(r"""
$definition = $ast.EndBlock.Statements | Where-Object { $_.Name -eq 'Test-CompatiblePython' }
. ([scriptblock]::Create($definition.Extent.Text))
@{ actual = (Test-CompatiblePython $env:INSTALLER_TEST_PYTHON);
   missing = (Test-CompatiblePython (Join-Path $env:VPUB_HOME 'missing-python.exe')) } | ConvertTo-Json -Compress
""")
        self.assertEqual(result, {"actual": True, "missing": False})

    def test_explicit_bundled_and_path_precedence(self):
        order = [self.explicit, self.bundled, self.on_path]
        for start in range(len(order)):
            with self.subTest(first=order[start]):
                result = self.select({**{path + "|": True for path in order[start:]}, self.launcher + "|-3.14": True})
                self.assertIsNone(result["error"])
                self.assertEqual(result["chosen"], {"path": str(self.venv), "executable": order[start], "prefix": []})
                self.assertEqual(result["probed"], [path + "|" for path in order[:start + 1]])

    def test_launcher_descends_from_314_to_310_without_315(self):
        for minor in (14, 13, 10):
            with self.subTest(minor=minor):
                result = self.select({self.launcher + f"|-3.{minor}": True})
                self.assertIsNone(result["error"])
                self.assertEqual(result["chosen"]["executable"], self.launcher)
                self.assertEqual(result["chosen"]["prefix"], [f"-3.{minor}"])
                self.assertEqual(result["probed"][3:], [self.launcher + f"|-3.{n}" for n in range(14, minor - 1, -1)])
        result = self.select({self.launcher + "|-3.15": True})
        self.assertIsNone(result["chosen"])
        self.assertIn("Python 3.10-3.14 is required", result["error"])
        self.assertEqual(result["probed"][3:], [self.launcher + f"|-3.{n}" for n in range(14, 9, -1)])

    def test_existing_supported_environment_precedes_explicit_python(self):
        python = str(self.venv / "Scripts/python.exe")
        result = self.select({python + "|": True, self.explicit + "|": True}, [self.venv, python])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["chosen"])
        self.assertEqual(result["probed"], [python + "|"])

    def test_unsupported_environment_uses_separate_fallback(self):
        python = str(self.venv / "Scripts/python.exe")
        result = self.select({self.explicit + "|": True}, [self.venv, python])
        self.assertIsNone(result["error"])
        self.assertEqual(result["chosen"]["path"], str(self.fallback))
        fallback_python = str(self.fallback / "Scripts/python.exe")
        result = self.select({self.explicit + "|": True}, [self.venv, python, self.fallback, fallback_python])
        self.assertIsNone(result["chosen"])
        self.assertIn("not Python 3.10-3.14", result["error"])
        self.assertEqual(result["probed"], [python + "|", fallback_python + "|"])


if __name__ == "__main__":
    unittest.main()
