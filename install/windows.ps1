# sotto on Windows (preview). From the repo folder, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File install\windows.ps1             set up, start, and start at every login
#   powershell -ExecutionPolicy Bypass -File install\windows.ps1 -Plan       print what it would do; change nothing
#   powershell -ExecutionPolicy Bypass -File install\windows.ps1 -Uninstall  stop it and remove the login item
param([switch]$Plan, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Startup = [Environment]::GetFolderPath("Startup")
$Link = Join-Path $Startup "sotto.lnk"
$PyW = Join-Path $Repo ".venv\Scripts\pythonw.exe"
$Py = Join-Path $Repo ".venv\Scripts\python.exe"

function Step($what, [scriptblock]$do) {
    if ($Plan) { Write-Host "would: $what" } else { Write-Host $what; & $do }
}

if ($Uninstall) {
    if (Test-Path $Py) { & $Py -m sotto host --stop | Out-Null }
    if (Test-Path $Link) { Remove-Item $Link }
    Write-Host "sotto stopped and removed from login items. Config kept in $HOME\.config\sotto\."
    exit 0
}

# 1. uv (it brings its own Python 3.12)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is missing. Install it (https://docs.astral.sh/uv/), open a new PowerShell, and run this again:"
    Write-Host '    winget install --id=astral-sh.uv -e'
    exit 1
}

# 2. WebView2 draws the wallpaper. It ships with Windows 11 and current Windows 10.
$wv = Get-ItemProperty -ErrorAction SilentlyContinue "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
if (-not $wv) { $wv = Get-ItemProperty -ErrorAction SilentlyContinue "HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" }
if ($wv) { Write-Host "WebView2 $($wv.pv): ok" } else { Write-Host "WebView2 not found; if the wallpaper stays blank, install it: https://developer.microsoft.com/microsoft-edge/webview2/" }

# 3. the Python env, with pywebview
Step "uv sync --managed-python --python 3.12 --extra windows" { uv sync --managed-python --python 3.12 --extra windows }

# 4. a starter config, unless you have one
Step "python -m sotto config init" { & $Py -m sotto config init }

# 5. start at login (a shortcut in your Startup folder), and now
Step "shortcut $Link -> pythonw -m sotto host" {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($Link)
    $lnk.TargetPath = $PyW
    $lnk.Arguments = "-m sotto host"
    $lnk.WorkingDirectory = $Repo
    $lnk.Description = "sotto: the living wallpaper"
    $lnk.Save()
}
Step "start sotto host" { Start-Process -FilePath $PyW -ArgumentList "-m", "sotto", "host" -WorkingDirectory $Repo }

if (-not $Plan) {
    Write-Host ""
    Write-Host "sotto is on your desktop (give it a few seconds). Double-click empty wallpaper to ask it something."
    Write-Host "Customize it: .venv\Scripts\python -m sotto config check   (file: $HOME\.config\sotto\config.toml)"
    Write-Host "Stop it: .venv\Scripts\python -m sotto host --stop    Remove it: install\windows.ps1 -Uninstall"
}
