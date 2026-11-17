param([string]$WebotsHome = $env:WEBOTS_HOME)

$project = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $project
$env:PATH = (Join-Path $project '.venv\Scripts') + ';' + $env:PATH
$env:YUEGUANG_PYTHON = Join-Path $project '.venv\Scripts\python.exe'
if (-not $WebotsHome) {
    $WebotsHome = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Webots_is1' -ErrorAction SilentlyContinue).InstallLocation
}
if (-not $WebotsHome) { $WebotsHome = 'C:\Program Files\Webots' }
$executable = Join-Path $WebotsHome 'msys64\mingw64\bin\webots.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    Write-Host 'Install Webots R2025a first, or run: powershell -File tools\launch.ps1 -WebotsHome "D:\Webots"'
    Read-Host 'Press Enter to close'
    exit 1
}
& $env:YUEGUANG_PYTHON (Join-Path $project 'tools\build_scene.py')
if ($LASTEXITCODE -ne 0) {
    Read-Host 'Could not generate the robot. Press Enter to close'
    exit 1
}
Start-Process -FilePath $executable -WorkingDirectory $project -ArgumentList @('--batch', '--mode=realtime', '--stream', 'worlds/moonlight.wbt')
$panel = 'http://127.0.0.1:1234/robot_windows/moonlight/moonlight.html?name=Moonlight&v=' + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Write-Host "Opening the practice panel: $panel"
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $null = Invoke-WebRequest -Uri $panel -UseBasicParsing -TimeoutSec 1
        Start-Process $panel
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
Write-Host "Open this address after Webots finishes loading: $panel"
Read-Host 'Press Enter to close'
