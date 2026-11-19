param([switch]$NoPause)

. (Join-Path $PSScriptRoot 'diagnostic-log.ps1')
$repository = Split-Path $PSScriptRoot -Parent

function Complete-SetupFailure {
    param([string]$Code, [string]$Message, [int]$ExitCode = 1)
    Write-MoonlightLog $Message
    Write-MoonlightEvent 'setup_failed' -Code $Code -ExitCode $ExitCode
    return $ExitCode
}

function Install-Moonlight {
    New-MoonlightLog $repository 'setup'
    Write-MoonlightEvent 'setup_started'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    Set-Location -LiteralPath $repository -ErrorAction Stop

    $selected = $null
    $lastProbe = $null
    $probe = 'import sys; print(sys.version); sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 2)'
    foreach ($name in 'python.exe','py.exe') {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        if ($name -eq 'python.exe' -and $command.Source -like '*\Microsoft\WindowsApps\python.exe') { continue }
        $prefix = @()
        if ($name -eq 'py.exe') { $prefix = @('-3') }
        Write-MoonlightLog "Checking Python via $($command.Source)"
        $probeCode = Invoke-MoonlightNative $command.Source ($prefix + @('-c', $probe))
        if ($probeCode -eq 0) {
            $selected = $command.Source
            $selectedPrefix = $prefix
            break
        }
        if ($probeCode -eq 2 -or $null -eq $lastProbe) { $lastProbe = $probeCode }
    }
    if (-not $selected) {
        if ($lastProbe -eq 2) {
            return Complete-SetupFailure 'unsupported_python' 'Use supported Python 3.11, 3.12, or 3.13.' 2
        }
        return Complete-SetupFailure 'missing_python' 'No usable Python found. Install Python 3.11 to 3.13 and add it to PATH.'
    }
    Write-MoonlightEvent 'python_found'

    Write-MoonlightLog 'Creating the project Python environment.'
    $venvCode = Invoke-MoonlightNative $selected ($selectedPrefix + @('-m','venv','.venv'))
    if ($venvCode -ne 0) { return Complete-SetupFailure 'venv_failed' "Virtual environment creation failed (Python exit $venvCode)." $venvCode }
    $venvExe = Join-Path $repository '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvExe -PathType Leaf)) {
        return Complete-SetupFailure 'venv_failed' 'The environment was created without its Python executable.'
    }
    Write-MoonlightEvent 'environment_ready'
    Write-MoonlightLog 'Installing project dependencies.'
    $installCode = Invoke-MoonlightNative $venvExe @('-m','pip','install','-r','requirements\core.txt')
    if ($installCode -ne 0) { return Complete-SetupFailure 'dependency_install_failed' "Dependency installation failed (pip exit $installCode)." $installCode }
    Write-MoonlightEvent 'setup_finished' -ExitCode 0
    Write-MoonlightLog 'Setup completed. Run start.bat to open the simulator.'
    return 0
}

$code = 1
try {
    $code = Install-Moonlight
} catch {
    if ($script:MoonlightRawLog -and $script:MoonlightEventLog) {
        [void](Complete-SetupFailure 'unexpected_failure' "Setup error: $($_.Exception.Message)")
    } else {
        Write-Host "Could not start the setup log: $($_.Exception.Message)"
    }
}
if (-not $NoPause) { [void](Read-Host 'Press Enter to close') }
exit $code
