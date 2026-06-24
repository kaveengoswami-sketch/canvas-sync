# Installs Canvas Sync as a normal Windows app:
#   - copies the exe to %LOCALAPPDATA%\Programs\CanvasSync
#   - creates a Start Menu shortcut (searchable + pinnable)
# Run:  powershell -ExecutionPolicy Bypass -File app\install_windows.ps1

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo "dist\CanvasSync.exe"
if (-not (Test-Path $source)) {
  Write-Host "dist\CanvasSync.exe not found. Build it first: python app\build_exe.py" -ForegroundColor Yellow
  exit 1
}

$installDir = Join-Path $env:LOCALAPPDATA "Programs\CanvasSync"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$target = Join-Path $installDir "CanvasSync.exe"
Copy-Item $source $target -Force
Write-Host "Installed to $target"

# Start Menu shortcut -> makes it searchable in Windows and pinnable to taskbar
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$lnk = Join-Path $startMenu "Canvas Sync.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $target
$sc.WorkingDirectory = $installDir
$sc.IconLocation     = $target
$sc.Description       = "Canvas Sync setup"
$sc.Save()
Write-Host "Created Start Menu shortcut: $lnk"
Write-Host ""
Write-Host "Done. Press the Windows key and type 'Canvas Sync' to launch it." -ForegroundColor Green
Write-Host "Right-click it in the Start results to pin to taskbar." -ForegroundColor Green
