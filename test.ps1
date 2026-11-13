$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Windows virtual environment not found. Run .\setup.ps1 first."
}

$env:QT_QPA_PLATFORM = "offscreen"
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

$cadFiles = Get-ChildItem -Path cad -Filter *.py | ForEach-Object { $_.FullName }
$integrationFiles = Get-ChildItem -Path integration -Filter *.py | ForEach-Object { $_.FullName }
$uiFiles = Get-ChildItem -Path ui -Filter *.py | ForEach-Object { $_.FullName }
& $venvPython -m py_compile core.py main.py electrical_engine.py ocr_engine.py roi_tab.py updater.py version.py $cadFiles $integrationFiles $uiFiles
if ($LASTEXITCODE -ne 0) { throw "Python compilation check failed." }

Write-Host "All tests and syntax checks passed."
