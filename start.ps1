param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Cree .venv e instale requirements.lock y el proyecto antes de iniciar; vea README.md.'
}
& $taskPython -m recepcion.doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $CheckOnly) {
    & $taskPython -m uvicorn recepcion.api:app --host 127.0.0.1 --port 8010 --workers 1 --no-access-log
    exit $LASTEXITCODE
}
