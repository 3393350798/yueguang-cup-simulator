param(
    [string]$WebotsHome = $env:WEBOTS_HOME,
    [ValidateRange(1,65535)][int]$PanelPort = 1234,
    [ValidateRange(1,300)][int]$StartupTimeoutSeconds = 45,
    [switch]$NoBrowser,
    [switch]$NoPause
)

. (Join-Path $PSScriptRoot 'diagnostic-log.ps1')
$repository = Split-Path $PSScriptRoot -Parent
$project = Join-Path $repository 'simulator'

function Test-PanelPortFree {
    # Webots listens on a wildcard address; Windows may still allow a loopback bind.
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ($listeners | Where-Object Port -eq $PanelPort | Select-Object -First 1) { return $false }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $PanelPort)
    try {
        $listener.ExclusiveAddressUse = $true
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

function Test-MoonlightPanel([string]$Url) {
    try {
        $request = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($Url)
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 900
        $request.ReadWriteTimeout = 900
        $response = $request.GetResponse()
        try {
            if ([int]$response.StatusCode -ne 200) { return $false }
            $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
            try {
                $buffer = New-Object 'char[]' 131072
                $count = $reader.ReadBlock($buffer, 0, $buffer.Length)
                $body = [string]::new($buffer, 0, $count)
                return $body.Contains('id="mode"') -and $body.Contains('moonlight.js')
            } finally {
                $reader.Dispose()
            }
        } finally {
            $response.Dispose()
        }
    } catch {
        return $false
    }
}

function Complete-LaunchFailure {
    param([string]$Code, [string]$Message, [int]$ExitCode = 1)
    Write-MoonlightLog $Message
    Write-MoonlightEvent 'launch_failed' -Code $Code -ExitCode $ExitCode
    return 1
}

function Start-Moonlight {
    New-MoonlightLog $repository 'launch'
    Write-MoonlightEvent 'launch_started'
    $env:YUEGUANG_LOG_SESSION = $script:MoonlightSession
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    $env:YUEGUANG_PYTHON = Join-Path $repository '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $env:YUEGUANG_PYTHON -PathType Leaf)) {
        return Complete-LaunchFailure 'missing_python' 'Project Python is missing. Run setup.bat first.'
    }
    $env:PATH = (Split-Path $env:YUEGUANG_PYTHON -Parent) + ';' + $env:PATH
    if (-not $WebotsHome) {
        $WebotsHome = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Webots_is1' -ErrorAction SilentlyContinue).InstallLocation
    }
    if (-not $WebotsHome) { $WebotsHome = 'C:\Program Files\Webots' }
    $executable = Join-Path $WebotsHome 'msys64\mingw64\bin\webots.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        return Complete-LaunchFailure 'missing_webots' "Webots not found at $executable. Install Webots R2025a, set WEBOTS_HOME, or use -WebotsHome."
    }
    $executable = (Resolve-Path -LiteralPath $executable -ErrorAction Stop).Path
    $portBusy = "Panel port $PanelPort is already in use. Close the other process or select -PanelPort. No panel was opened."
    if (-not (Test-PanelPortFree)) { return Complete-LaunchFailure 'port_conflict' $portBusy }

    Set-Location -LiteralPath $project -ErrorAction Stop
    Write-MoonlightLog 'Generating the arena and robot.'
    $buildCode = Invoke-MoonlightNative $env:YUEGUANG_PYTHON @((Join-Path $project 'tools\build_scene.py'))
    if ($buildCode -ne 0) {
        return Complete-LaunchFailure 'scene_generation_failed' "Scene generation failed (Python exit $buildCode)." $buildCode
    }
    Write-MoonlightEvent 'scene_generated'
    if (-not (Test-PanelPortFree)) { return Complete-LaunchFailure 'port_conflict' $portBusy }

    $stdout = Join-Path $script:MoonlightLogDir "webots-out-$script:MoonlightSession.log"
    $stderr = Join-Path $script:MoonlightLogDir "webots-err-$script:MoonlightSession.log"
    Write-MoonlightLog "Webots executable: $executable"
    Write-MoonlightLog "Webots stdout: $stdout"
    Write-MoonlightLog "Webots stderr: $stderr"
    try {
        $start = @{
            FilePath = $executable; WorkingDirectory = $project; PassThru = $true; ErrorAction = 'Stop'
            ArgumentList = @('--batch','--mode=realtime','--stream',"--port=$PanelPort",'--stdout','--stderr','worlds/moonlight.wbt')
            RedirectStandardOutput = $stdout; RedirectStandardError = $stderr
        }
        $process = Start-Process @start
    } catch {
        return Complete-LaunchFailure 'webots_start_failed' "Webots could not start: $($_.Exception.Message)"
    }
    Write-MoonlightLog "Webots process ID: $($process.Id)"
    Write-MoonlightEvent 'webots_started'
    $panel = "http://127.0.0.1:$PanelPort/robot_windows/moonlight/moonlight.html?name=Moonlight&v=$script:MoonlightSession"
    Write-MoonlightLog "Waiting for the practice panel at $panel"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $process.Refresh()
        if ($process.HasExited) {
            return Complete-LaunchFailure 'webots_exit' "Webots exited during startup (exit $($process.ExitCode)). See the two Webots logs." $process.ExitCode
        }
        if (Test-MoonlightPanel $panel) {
            $process.Refresh()
            if ($process.HasExited) {
                return Complete-LaunchFailure 'webots_exit' "Webots exited during startup (exit $($process.ExitCode)). See the two Webots logs." $process.ExitCode
            }
            Write-MoonlightEvent 'panel_ready'
            Write-MoonlightLog "Practice panel ready: $panel"
            if (-not $NoBrowser) {
                try { Start-Process $panel -ErrorAction Stop } catch {
                    Write-MoonlightEvent 'browser_open_failed' -Code 'browser_open_failed'
                    Write-MoonlightLog "Could not open the browser. Open the address manually: $($_.Exception.Message)"
                }
            }
            return 0
        }
        Start-Sleep -Milliseconds 300
    } while ($timer.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds)
    return Complete-LaunchFailure 'panel_timeout' "Panel not ready after $StartupTimeoutSeconds seconds. Webots is left open for inspection. Panel: $panel"
}

$code = 1
try {
    $code = Start-Moonlight
} catch {
    if ($script:MoonlightRawLog -and $script:MoonlightEventLog) {
        [void](Complete-LaunchFailure 'unexpected_failure' "Launcher error: $($_.Exception.Message)")
    } else {
        Write-Host "Could not start the launcher log: $($_.Exception.Message)"
    }
}
if ($code -ne 0 -and -not $NoPause) { [void](Read-Host 'Press Enter to close') }
exit $code
