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

# Create a Windows desktop shortcut that launches the GUI without a console window.
$pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "pythonw.exe was not found in the virtual environment. Desktop shortcut was not created."
}

$desktop = [Environment]::GetFolderPath("DesktopDirectory")
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path $desktop)) {
    throw "Could not locate the current user's Desktop folder."
}

$shortcutPath = Join-Path $desktop "Carrier AC Selector.lnk"
try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"main.py"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = "Launch Carrier AC Selector"
    $shortcut.IconLocation = "$pythonw,0"
    $shortcut.Save()
} catch {
    throw "Could not create the desktop shortcut: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Desktop shortcut created: $shortcutPath"
Write-Host "Double-click 'Carrier AC Selector' on your Desktop to launch the application."
Write-Host "Run tests with .\test.ps1"
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Warning "Tesseract is not on PATH. Image and scanned-PDF OCR will be unavailable until Tesseract is installed."
}
