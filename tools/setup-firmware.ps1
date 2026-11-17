$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$project = Split-Path $PSScriptRoot -Parent
$folder = Join-Path $project '.local\renode'
$archive = Join-Path $project '.local\renode-1.17.0.zip'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
Write-Host 'Downloading official Renode 1.17.0 portable (about 107 MB)...'
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/renode/renode/releases/download/v1.17.0/renode-1.17.0.windows-portable.zip' -OutFile $archive
Expand-Archive -LiteralPath $archive -DestinationPath $folder -Force
Write-Host 'Renode is ready. Open import-hardware.bat to select your firmware and configure pins.'
