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
$backupRoot = Join-Path $env:LOCALAPPDATA ("CarrierACSelector\update-backups\" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N"))
$fileBackup = Join-Path $backupRoot "files"
$venvBackup = Join-Path $backupRoot ".venv"
$oldVenv = Join-Path $target ".venv"
$changed = New-Object System.Collections.Generic.List[string]
$newFiles = New-Object System.Collections.Generic.List[string]
$rollbackFailures = New-Object System.Collections.Generic.List[string]
$oldVenvMoved = $false
$newVenvCreated = $false
$startedProcess = $null
$healthFile = Join-Path $tempRoot "startup-ok.txt"
$logDir = Join-Path $env:LOCALAPPDATA "CarrierACSelector"
$logPath = Join-Path $logDir "update.log"

function Write-UpdateLog([string]$Message) {
    try {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
    } catch {
        Write-Warning "Could not write update log: $($_.Exception.Message)"
    }
}

function Get-RelativeUpdatePath([System.IO.FileInfo]$File, [string]$Root) {
    return $File.FullName.Substring($Root.Length).TrimStart('\','/')
}

function Test-UpdateArchive([string]$ZipPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $allowedRoot = @(
        "main.py", "core.py", "ocr_engine.py", "electrical_engine.py",
        "roi_tab.py", "updater.py", "version.py", "apply_update.ps1",
        "Start_Carrier.bat", "setup.ps1", "run.ps1", "test.ps1",
        "requirements.txt", "requirements-dev.txt", "README.md"
    )
    $allowedData = @("Carrier_AC_Database.xlsx", "Carrier_AC_Database.csv")
    $required = @("main.py", "core.py", "requirements.txt", "updater.py", "version.py", "apply_update.ps1")
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $found = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $total = [int64]0
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        if ($zip.Entries.Count -eq 0) { throw "The update archive is empty." }
        foreach ($entry in $zip.Entries) {
            $raw = $entry.FullName
            if ([string]::IsNullOrWhiteSpace($raw) -or $raw.Contains('\') -or $raw.Contains(':') -or $raw.StartsWith('/')) {
                throw "Unsafe archive path: $raw"
            }
            $isDirectory = $raw.EndsWith('/')
            $clean = $raw.TrimEnd('/')
            $parts = $clean.Split('/')
            if ($parts.Count -eq 0 -or ($parts | Where-Object { $_ -in @('', '.', '..') })) {
                throw "Unsafe archive path: $raw"
            }
            foreach ($part in $parts) {
                if ($part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$') {
                    throw "Invalid Windows archive path: $raw"
                }
            }
            if (-not $seen.Add($clean)) { throw "Duplicate archive path: $raw" }
            $kind = ([int]$entry.ExternalAttributes -band 0xF000)
            if ($kind -eq 0xA000 -or ($kind -ne 0 -and $kind -ne 0x8000 -and $kind -ne 0x4000)) {
                throw "Links and special files are not allowed: $raw"
            }
            if ($isDirectory) { continue }

            if ($parts.Count -eq 1 -and $allowedRoot -contains $parts[0]) {
                [void]$found.Add($parts[0])
            } elseif ($parts.Count -eq 2 -and $parts[0] -eq 'data' -and $allowedData -contains $parts[1]) {
                # The user database is preserved by the installer below.
            } else {
                throw "Unexpected file in update archive: $raw"
            }

            if ($entry.Length -gt 157286400) { throw "An archive file exceeds the size limit: $raw" }
            $total += $entry.Length
            if ($total -gt 314572800) { throw "Expanded update archive exceeds the size limit." }
            if ($entry.Length -gt 0 -and ($entry.CompressedLength -eq 0 -or $entry.Length / [double]$entry.CompressedLength -gt 250)) {
                throw "Unsafe compression ratio: $raw"
            }
        }
        foreach ($name in $required) {
            if (-not $found.Contains($name)) { throw "Required update file is missing: $name" }
        }
    } finally {
        $zip.Dispose()
    }
}

try {
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) { throw "Update archive was not found." }
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "Application directory was not found." }

    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 500
    }

    Test-UpdateArchive $Archive
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $fileBackup -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $stage -Force

    $files = @(Get-ChildItem -LiteralPath $stage -File -Recurse)
    if ($files.Count -eq 0) { throw "The update archive contains no files." }
    foreach ($file in $files) {
        $relative = Get-RelativeUpdatePath $file $stage
        $destination = Join-Path $target $relative

        # Preserve any existing user-managed database file.
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

    # Move the previous environment to a permanent backup. Create the replacement
    # directly at its final path: Python venvs must not be relocated after creation.
    if (-not (Test-Path -LiteralPath (Join-Path $oldVenv "Scripts\python.exe") -PathType Leaf)) {
        throw "The Windows virtual environment is missing. Run setup.ps1 before updating."
    }
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Move-Item -LiteralPath $oldVenv -Destination $venvBackup -Force
    $oldVenvMoved = $true

    $basePython = Join-Path $venvBackup "Scripts\python.exe"
    & $basePython -m venv $oldVenv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the replacement virtual environment." }
    $newVenvCreated = $true

    $newPython = Join-Path $oldVenv "Scripts\python.exe"
    & $newPython -m pip install --disable-pip-version-check -r (Join-Path $stage "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

    & $newPython -c "import PySide6, pandas, openpyxl, pytesseract, PIL, docx, pymupdf"
    if ($LASTEXITCODE -ne 0) { throw "The replacement environment failed the runtime import check." }

    if ($Restart) {
        $versionText = Get-Content -LiteralPath (Join-Path $stage "version.py") -Raw
        $versionMatch = [regex]::Match($versionText, 'APP_VERSION\s*=\s*["'']([^"'']+)["'']')
        if (-not $versionMatch.Success) { throw "Could not read APP_VERSION from the update package." }
        $expectedVersion = $versionMatch.Groups[1].Value

        $pythonw = Join-Path $oldVenv "Scripts\pythonw.exe"
        $python = Join-Path $oldVenv "Scripts\python.exe"
        $env:CARRIER_UPDATE_HEALTHCHECK_FILE = $healthFile
        if (Test-Path -LiteralPath $pythonw -PathType Leaf) {
            $startedProcess = Start-Process -FilePath $pythonw -ArgumentList @("main.py") -WorkingDirectory $target -PassThru -ErrorAction Stop
        } elseif (Test-Path -LiteralPath $python -PathType Leaf) {
            $startedProcess = Start-Process -FilePath $python -ArgumentList @("main.py") -WorkingDirectory $target -PassThru -ErrorAction Stop
        } else {
            throw "Python executable was not found after the environment swap."
        }

        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $healthFile)) {
            if ($startedProcess.HasExited) {
                throw "The updated application exited before completing its startup check (exit code $($startedProcess.ExitCode))."
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-Path -LiteralPath $healthFile)) {
            try { Stop-Process -Id $startedProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
            throw "The updated application did not complete its startup check within 45 seconds."
        }
        $reportedVersion = (Get-Content -LiteralPath $healthFile -Raw).Trim()
        if ($reportedVersion -ne $expectedVersion) {
            try { Stop-Process -Id $startedProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
            throw "Startup check version mismatch. Expected $expectedVersion, received $reportedVersion."
        }
        Start-Sleep -Seconds 2
        if ($startedProcess.HasExited) {
            throw "The updated application closed immediately after its startup check."
        }
        Remove-Item Env:CARRIER_UPDATE_HEALTHCHECK_FILE -ErrorAction SilentlyContinue
    }

    Write-UpdateLog "SUCCESS: updated application at '$target'. Backup retained at '$backupRoot'."
    Write-Host "Carrier AC Selector was updated successfully."
    Write-Host "Backup retained at: $backupRoot"
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Split-Path -Parent $Archive) -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
catch {
    $failureMessage = "Update failed: $($_.Exception.Message)"
    if ($startedProcess -and -not $startedProcess.HasExited) {
        try { Stop-Process -Id $startedProcess.Id -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("stop failed updated application: $($_.Exception.Message)") }
    }

    if ($newVenvCreated -and (Test-Path -LiteralPath $oldVenv)) {
        try { Remove-Item -LiteralPath $oldVenv -Recurse -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("remove replacement .venv: $($_.Exception.Message)") }
    }
    if ($oldVenvMoved -and (Test-Path -LiteralPath $venvBackup)) {
        try { Move-Item -LiteralPath $venvBackup -Destination $oldVenv -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("restore .venv: $($_.Exception.Message)") }
    }

    foreach ($relative in $newFiles) {
        try { Remove-Item -LiteralPath (Join-Path $target $relative) -Force -ErrorAction Stop }
        catch { $rollbackFailures.Add("remove $relative: $($_.Exception.Message)") }
    }
    foreach ($relative in $changed) {
        $backupPath = Join-Path $fileBackup $relative
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            try { Copy-Item -LiteralPath $backupPath -Destination (Join-Path $target $relative) -Force -ErrorAction Stop }
            catch { $rollbackFailures.Add("restore $relative: $($_.Exception.Message)") }
        }
    }

    $rollbackText = if ($rollbackFailures.Count) {
        "Rollback incomplete: " + ($rollbackFailures -join "; ")
    } else {
        "Previous application files and environment were restored where applicable."
    }
    Write-UpdateLog "$failureMessage $rollbackText Backup retained at '$backupRoot'."
    $message = $failureMessage + [Environment]::NewLine + $rollbackText + [Environment]::NewLine + "Backup retained at: $backupRoot" + [Environment]::NewLine + "See $logPath"
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($message, "Carrier AC Selector Update", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error) | Out-Null
    } catch {
        Write-Error $message
    }
    exit 1
}
