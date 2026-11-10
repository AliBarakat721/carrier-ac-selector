$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Windows virtual environment not found. Run .\setup.ps1 first."
}

& $venvPython main.py @args
exit $LASTEXITCODE
