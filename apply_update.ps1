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
$backup = Join-Path $tempRoot "backup"
$changed = New-Object System.Collections.Generic.List[string]
$newFiles = New-Object System.Collections.Generic.List[string]

try {
    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 500
    }

    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $stage -Force

    $allowedExtensions = @(".py", ".ps1", ".bat", ".txt", ".md", ".yml", ".xlsx", ".csv")
    $files = Get-ChildItem -LiteralPath $stage -File -Recurse
    if (-not $files) { throw "The update archive contains no files." }

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($stage.Length).TrimStart('\','/')
        $parts = $relative -split '[\\/]'
        if ($parts.Count -gt 2 -or ($parts.Count -eq 2 -and $parts[0] -ne "data")) {
            throw "Unexpected path in update archive: $relative"
        }
        if ($parts.Count -eq 1 -and $file.Extension.ToLowerInvariant() -notin $allowedExtensions) {
            throw "Unexpected file type in update archive: $relative"
        }
        if ($parts.Count -eq 2 -and ($parts[0] -ne "data" -or $file.Extension.ToLowerInvariant() -notin @(".xlsx", ".csv"))) {
            throw "Unexpected data path in update archive: $relative"
        }
    }

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($stage.Length).TrimStart('\','/')
        $destination = Join-Path $target $relative
        $destinationParent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null

        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $backupPath = Join-Path $backup $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupPath -Force
            $changed.Add($relative)
        } else {
            $newFiles.Add($relative)
        }
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }

    # Refresh runtime dependencies in the existing Windows virtual environment.
    $venvPython = Join-Path $target ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "The Windows virtual environment is missing. Run setup.ps1 before updating."
    }
    & $venvPython -m pip install -r (Join-Path $target "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed. The application files will be rolled back."
    }

    if ($Restart) {
        $pythonw = Join-Path $target ".venv\Scripts\pythonw.exe"
        $python = Join-Path $target ".venv\Scripts\python.exe"
        if (Test-Path $pythonw) {
            Start-Process -FilePath $pythonw -ArgumentList '"main.py"' -WorkingDirectory $target
        } elseif (Test-Path $python) {
            Start-Process -FilePath $python -ArgumentList 'main.py' -WorkingDirectory $target
        }
    }

    Write-Host "Carrier AC Selector was updated successfully."
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Split-Path -Parent $Archive) -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
catch {
    Write-Error "Update failed: $($_.Exception.Message)"
    foreach ($relative in $newFiles) {
        Remove-Item -LiteralPath (Join-Path $target $relative) -Force -ErrorAction SilentlyContinue
    }
    foreach ($relative in $changed) {
        $backupPath = Join-Path $backup $relative
        if (Test-Path -LiteralPath $backupPath) {
            Copy-Item -LiteralPath $backupPath -Destination (Join-Path $target $relative) -Force
        }
    }
    Write-Error "The previous files were restored where possible."
    exit 1
}
