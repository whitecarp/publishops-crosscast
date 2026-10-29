param([string]$Python)
$ErrorActionPreference = 'Stop'

# Keep the historical data directory so upgrades retain sign-ins, jobs and settings.
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'WhiteCarpVideoPublisher'
$venvPath = Join-Path $runtimeRoot 'venv'
$venvPython = Join-Path $venvPath 'Scripts/python.exe'

function Test-CompatiblePython([string]$Executable, [string[]]$PrefixArgs = @()) {
    if (-not (Test-Path -LiteralPath $Executable)) { return $false }
    $probeArgs = @($PrefixArgs) + @('-c', 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,13) else 1)')
    & $Executable @probeArgs 2>$null
    return $LASTEXITCODE -eq 0
}

function New-VirtualEnvironment([string]$Path, [string]$Executable, [string[]]$PrefixArgs = @()) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    $venvArgs = @($PrefixArgs) + @('-m', 'venv', $Path)
    & $Executable @venvArgs
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed; existing runtime data was retained.' }
}

if (Test-Path -LiteralPath $venvPath) {
    if (-not (Test-Path -LiteralPath $venvPython) -or -not (Test-CompatiblePython $venvPython)) {
        # Leave the existing environment untouched; install into a separate supported interpreter.
        $venvPath = Join-Path $runtimeRoot 'venv-supported'
        $venvPython = Join-Path $venvPath 'Scripts/python.exe'
        if (Test-Path -LiteralPath $venvPath) {
            if (-not (Test-Path -LiteralPath $venvPython) -or -not (Test-CompatiblePython $venvPython)) {
                throw 'The existing fallback environment is incomplete or not Python 3.10-3.12. No runtime data was changed.'
            }
        }
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $interpreters = @()
    if ($Python) { $interpreters += @{ Executable = $Python; PrefixArgs = @() } }
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $interpreters += @{ Executable = $bundledPython; PrefixArgs = @() } }
    $foundPython = Get-Command python -ErrorAction SilentlyContinue
    if ($foundPython) { $interpreters += @{ Executable = $foundPython.Source; PrefixArgs = @() } }
    $foundPyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($foundPyLauncher) {
        foreach ($minor in 12..10) { $interpreters += @{ Executable = $foundPyLauncher.Source; PrefixArgs = @("-3.$minor") } }
    }

    $selected = $null
    foreach ($candidate in $interpreters) {
        if (Test-CompatiblePython $candidate.Executable $candidate.PrefixArgs) { $selected = $candidate; break }
    }
    if (-not $selected) { throw 'Python 3.10-3.12 is required. Install one, then pass -Python FULL_PATH.' }
    New-VirtualEnvironment $venvPath $selected.Executable $selected.PrefixArgs
}

# The distribution was renamed, but the venv and user-data directory stay in place.
& $venvPython -m pip uninstall -y whitecarp-video-publisher
if ($LASTEXITCODE -ne 0) { throw 'Legacy package upgrade failed; runtime data was retained.' }
& $venvPython -m pip install --upgrade $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. No login or publication was attempted.' }
& $venvPython -m video_publisher doctor
if ($LASTEXITCODE -ne 0) { throw 'Local dependency check failed.' }
Write-Output ('Command: ' + (Join-Path $venvPath 'Scripts/vpub.exe'))
