param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$TargetDir,
    [Parameter(Mandatory=$true)][int]$WaitPid,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$target = [System.IO.Path]::GetFullPath($TargetDir)
$tempRoot = Join-Path $env:TEMP ("CarrierACUpdate-" + [guid]::NewGuid().ToString("N"))
$stage = Join-Path $tempRoot "stage"
$newVenv = Join-Path $tempRoot "venv-update"
$backupRoot = Join-Path $env:LOCALAPPDATA ("CarrierACSelector\update-backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$fileBackup = Join-Path $backupRoot "files"
$venvBackup = Join-Path $backupRoot ".venv"
$changed = New-Object System.Collections.Generic.List[string]
$newFiles = New-Object System.Collections.Generic.List[string]
$rollbackFailures = New-Object System.Collections.Generic.List[string]
$oldVenvMoved = $false
$newVenvMoved = $false

function Get-RelativeUpdatePath([System.IO.FileInfo]$File, [string]$Root) {
    return $File.FullName.Substring($Root.Length).TrimStart('\','/')
}

function Test-AllowedUpdatePath([string]$Relative) {
    $parts = $Relative -split '[\\/]'
    $allowedExtensions = @('.py', '.ps1', '.bat', '.txt', '.md', '.yml', '.xlsx', '.csv')
    if ($parts.Count -gt 2 -or ($parts.Count -eq 2 -and $parts[0] -ne 'data')) {
        throw "Unexpected path in update archive: $Relative"
    }
    if ($parts.Count -eq 1 -and [IO.Path]::GetExtension($Relative).ToLowerInvariant() -notin $allowedExtensions) {
        throw "Unexpected file type in update archive: $Relative"
    }
    if ($parts.Count -eq 2 -and ($parts[0] -ne 'data' -or [IO.Path]::GetExtension($Relative).ToLowerInvariant() -notin @('.xlsx', '.csv'))) {
        throw "Unexpected data path in update archive: $Relative"
    }
}

try {
    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 500
    }

    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $fileBackup -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $stage -Force

    $files = Get-ChildItem -LiteralPath $stage -File -Recurse
    if (-not $files) { throw 'The update archive contains no files.' }
    foreach ($file in $files) {
        $relative = Get-RelativeUpdatePath $file $stage
        Test-AllowedUpdatePath $relative
    }

    # Build and validate a replacement environment before touching the live .venv.
    $oldVenv = Join-Path $target '.venv'
    $venvPython = Join-Path $oldVenv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw 'The Windows virtual environment is missing. Run setup.ps1 before updating.'
    }
    & $venvPython -m venv $newVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the temporary update virtual environment.' }
    $newPython = Join-Path $newVenv 'Scripts\python.exe'
    & $newPython -m pip install -r (Join-Path $stage 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed in the temporary environment.' }
    & $newPython -c 'import PySide6, pandas, openpyxl, pytesseract, PIL, docx, pymupdf'
    if ($LASTEXITCODE -ne 0) { throw 'The temporary environment failed the runtime import check.' }

    foreach ($file in $files) {
        $relative = Get-RelativeUpdatePath $file $stage
        $destination = Join-Path $target $relative

        # data/ is shipped for fresh installations, but an existing user database
        # is never overwritten by an application update.
        if ($relative -match '^data[\\/]' -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
            continue
        }

        $destinationParent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $backupPath = Join-Path $fileBackup $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupPath -Force
            $changed.Add($relative)
        } else {
            $newFiles.Add($relative)
        }
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }

    # Keep the old environment outside the application directory and swap only
    # after the replacement environment has passed its import check.
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Move-Item -LiteralPath $oldVenv -Destination $venvBackup -Force
    $oldVenvMoved = $true
    Move-Item -LiteralPath $newVenv -Destination $oldVenv -Force
    $newVenvMoved = $true

    if ($Restart) {
        $pythonw = Join-Path $oldVenv 'Scripts\pythonw.exe'
        $python = Join-Path $oldVenv 'Scripts\python.exe'
        if (Test-Path -LiteralPath $pythonw -PathType Leaf) {
            Start-Process -FilePath $pythonw -ArgumentList @('main.py') -WorkingDirectory $target -ErrorAction Stop
        } elseif (Test-Path -LiteralPath $python -PathType Leaf) {
            Start-Process -FilePath $python -ArgumentList @('main.py') -WorkingDirectory $target -ErrorAction Stop
        } else {
            throw 'Python executable was not found after the environment swap.'
        }
    }

    Write-Host "Carrier AC Selector was updated successfully."
    Write-Host "Backup retained at: $backupRoot"
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Split-Path -Parent $Archive) -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
catch {
    $failureMessage = "Update failed: $($_.Exception.Message)"

    if ($newVenvMoved -and (Test-Path -LiteralPath (Join-Path $target '.venv'))) {
        try { Remove-Item -LiteralPath (Join-Path $target '.venv') -Recurse -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("remove replacement .venv: $($_.Exception.Message)") }
    }
    if ($oldVenvMoved -and (Test-Path -LiteralPath $venvBackup)) {
        try { Move-Item -LiteralPath $venvBackup -Destination (Join-Path $target '.venv') -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("restore .venv: $($_.Exception.Message)") }
    }

    foreach ($relative in $newFiles) {
        try { Remove-Item -LiteralPath (Join-Path $target $relative) -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("remove $relative: $($_.Exception.Message)") }
    }
    foreach ($relative in $changed) {
        $backupPath = Join-Path $fileBackup $relative
        if (Test-Path -LiteralPath $backupPath) {
            try { Copy-Item -LiteralPath $backupPath -Destination (Join-Path $target $relative) -Force -ErrorAction Stop }
            catch { $rollbackFailures.Add("restore $relative: $($_.Exception.Message)") }
        }
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $backupRoot) -Force -ErrorAction SilentlyContinue | Out-Null
    $logDir = Join-Path $env:LOCALAPPDATA 'CarrierACSelector'
    New-Item -ItemType Directory -Path $logDir -Force -ErrorAction SilentlyContinue | Out-Null
    $logPath = Join-Path $logDir 'update.log'
    $rollbackText = if ($rollbackFailures.Count) { ' Rollback problems: ' + ($rollbackFailures -join '; ') } else { ' Previous application files were restored.' }
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) $failureMessage$rollbackText Backup: $backupRoot" -ErrorAction SilentlyContinue
    $message = "$failureMessage$rollbackText`nBackup retained at: $backupRoot`nSee $logPath"
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($message, 'Carrier AC Selector Update', [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error) | Out-Null
    } catch {
        Write-Error $message
    }
    exit 1
}
