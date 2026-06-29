# Installs Canvas Sync as a normal Windows app:
#   - copies the app folder to %LOCALAPPDATA%\Programs\CanvasSync
#   - creates a Start Menu shortcut (searchable + pinnable)
# Run:  powershell -ExecutionPolicy Bypass -File app\install_windows.ps1

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo "dist\CanvasSync"
if (-not (Test-Path (Join-Path $source "CanvasSync.exe"))) {
  Write-Host "dist\CanvasSync\CanvasSync.exe not found. Build it first: python app\build_exe.py" -ForegroundColor Yellow
  exit 1
}

# --onedir ships a whole folder (CanvasSync.exe + an _internal\ folder); the exe
# will not run without its siblings, so copy the entire folder, not just the exe.
$installDir = Join-Path $env:LOCALAPPDATA "Programs\CanvasSync"
if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item (Join-Path $source "*") $installDir -Recurse -Force
$target = Join-Path $installDir "CanvasSync.exe"
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
