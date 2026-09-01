# Run the app locally in a sandbox. The equivalent of a dev server: same code
# the device runs, but pointed at throwaway data so every path is safe to poke.
#
#     .\dev.ps1           run the app
#     .\dev.ps1 -Test     run the test suite instead
#     .\dev.ps1 -Reset    delete the sandbox entries first
#
# Entries go to ~/journal-dev/, config to ~/.journal-config-dev.json, and both
# the shutdown and hotspot actions become no-ops -- so ^D, "Shut down" and
# "Hotspot" are all safe to exercise.
param(
    [switch]$Test,
    [switch]$Reset
)

# Not 'Stop': unittest and pip write progress to stderr, and under 'Stop'
# PowerShell 5.1 turns a native command's stderr into a terminating error, so a
# fully passing test run would report failure.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"

# Windows has no _curses in the standard library. The app stays dependency-free;
# the shim is a development-only tool, which is why it lives in a venv.
if (-not (Test-Path $py)) {
    Write-Host "creating .venv and installing windows-curses..." -ForegroundColor Cyan
    $sys = (Get-Command py -ErrorAction SilentlyContinue)
    if ($sys) { & py -3 -m venv .venv } else { & python -m venv .venv }
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet windows-curses
}

& $py -c "import curses" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "curses is missing from the venv - installing windows-curses" -ForegroundColor Yellow
    & $py -m pip install --quiet windows-curses
}

if ($Test) {
    & $py -m unittest discover -s tests -v
    exit $LASTEXITCODE
}

if ($Reset) {
    $sandbox = Join-Path $env:USERPROFILE "journal-dev"
    if (Test-Path $sandbox) {
        Remove-Item "$sandbox\*" -Force -ErrorAction SilentlyContinue
        Write-Host "cleared $sandbox" -ForegroundColor DarkGray
    }
    $cfg = Join-Path $env:USERPROFILE ".journal-config-dev.json"
    if (Test-Path $cfg) {
        Remove-Item $cfg -Force
        Write-Host "cleared $cfg" -ForegroundColor DarkGray
    }
}

Write-Host "sandbox: $(Join-Path $env:USERPROFILE 'journal-dev')" -ForegroundColor DarkGray
Write-Host "^X menu   ^D 'power off' (no-op here)" -ForegroundColor DarkGray
Write-Host ""

$env:JOURNAL_DEV = "1"
try {
    & $py journal.py
} finally {
    Remove-Item Env:\JOURNAL_DEV -ErrorAction SilentlyContinue
}
