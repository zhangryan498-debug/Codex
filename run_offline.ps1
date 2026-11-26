$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$dtCandidates = @(
    $env:DT_PYTHON,
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$dtCommand = Get-Command python -ErrorAction SilentlyContinue
if ($dtCommand -and $dtCommand.Source -notmatch 'WindowsApps') { $dtCandidates += $dtCommand.Source }
$dtPython = $dtCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $dtPython) { throw 'Python 3.12+ required. Set DT_PYTHON to your Python executable.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $dtPython -X utf8 -u (Join-Path $PSScriptRoot 'scripts\offline_pipeline.py') @args
exit $LASTEXITCODE
