$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python was not found on PATH. Install Python 3.10+ and enable 'Add Python to PATH'."
}

$version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) { throw "Unable to run Python." }
$parts = $version.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
    throw "Python 3.10 or newer is required. Found $version."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    if (Test-Path (Join-Path $PSScriptRoot ".venv")) {
        Write-Host "Replacing an incompatible .venv with a Windows virtual environment..."
        & python -m venv --clear .venv
    } else {
        Write-Host "Creating Windows virtual environment in .venv..."
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host ""
Write-Host "Setup complete. Run the application with .\run.ps1"
Write-Host "Run tests with .\test.ps1"
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Warning "Tesseract is not on PATH. Image and scanned-PDF OCR will be unavailable until Tesseract is installed."
}
