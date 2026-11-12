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
        # Never delete the folder: move it aside so nothing is lost if .venv
        # was a non-Windows environment or contained user files.
        $backup = Join-Path $PSScriptRoot (".venv.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        Write-Host "Moving the incompatible .venv to $backup and creating a Windows virtual environment..."
        Move-Item -LiteralPath (Join-Path $PSScriptRoot ".venv") -Destination $backup
        & python -m venv .venv
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
    Write-Warning "pythonw.exe was not found. The application was installed, but the desktop shortcut was not created."
    $pythonw = $null
}

$desktop = [Environment]::GetFolderPath("DesktopDirectory")
$shortcutPath = $null
if ($pythonw -and -not [string]::IsNullOrWhiteSpace($desktop) -and (Test-Path $desktop)) {
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
        Write-Warning "The application was installed, but the desktop shortcut could not be created: $($_.Exception.Message)"
        $shortcutPath = $null
    }
} else {
    Write-Warning "The application was installed, but the current user's Desktop folder could not be located."
}

# This marker is written only after dependency installation completes. The launcher
# uses it to distinguish a complete setup from a partially-created virtualenv.
$setupMarker = Join-Path $PSScriptRoot ".venv\.carrier_setup_complete"
Set-Content -Path $setupMarker -Value "Carrier AC Selector setup completed on $(Get-Date -Format o)" -Encoding UTF8

Write-Host ""
Write-Host "Setup complete."
if ($shortcutPath) {
    Write-Host "Desktop shortcut created: $shortcutPath"
    Write-Host "Double-click 'Carrier AC Selector' on your Desktop to launch the application."
} else {
    Write-Host "The application can be started with .\run.ps1 or Start_Carrier.bat."
}
Write-Host "Run tests with .\test.ps1"
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Warning "Tesseract is not on PATH. Image and scanned-PDF OCR will be unavailable until Tesseract is installed."
}
