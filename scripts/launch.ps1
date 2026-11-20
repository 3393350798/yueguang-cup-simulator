param(
    [string]$WebotsHome = $env:WEBOTS_HOME,
    [ValidateRange(1,65535)][int]$PanelPort = 1234,
    [ValidateRange(1,300)][int]$StartupTimeoutSeconds = 45,
    [switch]$NoBrowser,
    [switch]$Minimized,
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

function Test-CurrentControllerReady([string]$Session) {
    # A former launch in this or another project must not satisfy this check.
    $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $pattern = '^runtime-' + [regex]::Escape($Session) + '-[0-9a-f]{8}\.jsonl$'
    foreach ($file in (Get-ChildItem -LiteralPath $script:MoonlightLogDir -File -Filter "runtime-$Session-*.jsonl")) {
        if ($file.Name -cnotmatch $pattern -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        $stream = $null
        try {
            $stream = [IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
            if ($stream.Length -eq 0 -or $stream.Length -gt 256KB) { continue }
            $reader = [IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
            try {
                $ready = $false
                while ($null -ne ($row = $reader.ReadLine())) {
                    if ($row.Length -gt 4096) { continue }
                    try { $item = ConvertFrom-Json -InputObject $row -ErrorAction Stop } catch { continue }
                    if ($item.schema -ne 1 -or $item.source -cne 'runtime' -or $item.session -cne $Session) { continue }
                    if ($item.event -ceq 'controller_ready' -and $item.phase -ceq 'controller_ready') { $ready = $true }
                    if ($item.event -ceq 'shutdown') { $ready = $false }
                }
                if ($ready) { return $true }
            } finally { $reader.Dispose() }
        } catch [IO.IOException] {
            # The controller writes sparse lines concurrently; retry at the next poll.
        } catch [UnauthorizedAccessException] {
            # A log without read permission cannot prove readiness.
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
    return $false
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
        return Complete-LaunchFailure 'missing_python' '未找到项目 Python。请在启动与排障菜单选择「安装项目依赖」，或运行 setup.bat。'
    }
    $env:PATH = (Split-Path $env:YUEGUANG_PYTHON -Parent) + ';' + $env:PATH
    if (-not $WebotsHome) {
        $WebotsHome = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Webots_is1' -ErrorAction SilentlyContinue).InstallLocation
    }
    if (-not $WebotsHome) { $WebotsHome = 'C:\Program Files\Webots' }
    $executable = Join-Path $WebotsHome 'msys64\mingw64\bin\webots.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        return Complete-LaunchFailure 'missing_webots' "未找到 Webots：$executable。请安装 Webots R2025a，或设置 WEBOTS_HOME / -WebotsHome。"
    }
    $executable = (Resolve-Path -LiteralPath $executable -ErrorAction Stop).Path
    $portBusy = "面板端口 $PanelPort 已占用。请先关闭旧的 Webots 实例，或用 -PanelPort 选择其它端口。本次没有打开网页。"
    if (-not (Test-PanelPortFree)) { return Complete-LaunchFailure 'port_conflict' $portBusy }

    Set-Location -LiteralPath $project -ErrorAction Stop
    Write-MoonlightLog '[1/4] 正在检查 Python 环境、项目依赖和 Webots 程序。'
    $dependencyCode = Invoke-MoonlightNative $env:YUEGUANG_PYTHON @(
        (Join-Path $project 'tools\preflight.py'),'--dependencies-only','--webots-home',$WebotsHome)
    if ($dependencyCode -ne 0) {
        return Complete-LaunchFailure 'dependency_check_failed' '项目启动依赖未通过。请按上面对应项修复，再重试启动。' $dependencyCode
    }
    Write-MoonlightEvent 'dependencies_ready'
    Write-MoonlightLog '[1/4] 启动依赖已通过。正在生成场地和车模。'
    $buildCode = Invoke-MoonlightNative $env:YUEGUANG_PYTHON @((Join-Path $project 'tools\build_scene.py'))
    if ($buildCode -ne 0) {
        return Complete-LaunchFailure 'scene_generation_failed' "场地或车模生成失败（Python 退出码 $buildCode）。请核对车模配置，或导出诊断。" $buildCode
    }
    Write-MoonlightEvent 'scene_generated'
    if (-not (Test-PanelPortFree)) { return Complete-LaunchFailure 'port_conflict' $portBusy }

    $stdout = Join-Path $script:MoonlightLogDir "webots-out-$script:MoonlightSession.log"
    $stderr = Join-Path $script:MoonlightLogDir "webots-err-$script:MoonlightSession.log"
    Write-MoonlightLog "Webots executable: $executable"
    Write-MoonlightLog "Webots stdout: $stdout"
    Write-MoonlightLog "Webots stderr: $stderr"
    try {
        $webotsArgs = @('--batch','--mode=realtime','--stream',"--port=$PanelPort",'--stdout','--stderr')
        if ($Minimized) { $webotsArgs += '--minimize' }
        $webotsArgs += 'worlds/moonlight.wbt'
        $start = @{
            FilePath = $executable; WorkingDirectory = $project; PassThru = $true; ErrorAction = 'Stop'
            ArgumentList = $webotsArgs
            RedirectStandardOutput = $stdout; RedirectStandardError = $stderr
        }
        if ($Minimized) { $start.WindowStyle = 'Hidden' }
        $process = Start-Process @start
    } catch {
        return Complete-LaunchFailure 'webots_start_failed' "无法启动 Webots：$($_.Exception.Message)"
    }
    Write-MoonlightLog "Webots process ID: $($process.Id)"
    Write-MoonlightEvent 'webots_started'
    Write-MoonlightLog '[2/4] 已启动 Webots 进程。'
    $panel = "http://127.0.0.1:$PanelPort/robot_windows/moonlight/moonlight.html?name=Moonlight&v=$script:MoonlightSession"
    Write-MoonlightLog "[3/4] 等待练习网页响应：$panel"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $panelReady = $false
    do {
        $process.Refresh()
        if ($process.HasExited) {
            return Complete-LaunchFailure 'webots_exit' "Webots 在启动时退出（退出码 $($process.ExitCode)）。请导出诊断并查看 Webots 日志。" $process.ExitCode
        }
        if (-not $panelReady -and (Test-MoonlightPanel $panel)) {
            $panelReady = $true
            Write-MoonlightEvent 'panel_ready'
            Write-MoonlightLog '[3/4] 练习网页已响应。'
            Write-MoonlightLog '[4/4] 正在等待本次启动的控制器上送首个仿真状态。'
        }
        if ($panelReady -and (Test-CurrentControllerReady $script:MoonlightSession)) {
            $process.Refresh()
            if ($process.HasExited) {
                return Complete-LaunchFailure 'webots_exit' "Webots 在启动时退出（退出码 $($process.ExitCode)）。请导出诊断并查看 Webots 日志。" $process.ExitCode
            }
            Write-MoonlightEvent 'launch_ready'
            Write-MoonlightLog '[4/4] 本次控制器已上送首个状态。'
            Write-MoonlightLog "练习地址：$panel"
            Write-MoonlightLog '实体相机、串口和协议回包未在本次启动中检查；可在启动与排障菜单按方案自检。'
            if (-not $NoBrowser) {
                try { Start-Process $panel -ErrorAction Stop } catch {
                    Write-MoonlightEvent 'browser_open_failed' -Code 'browser_open_failed'
                    Write-MoonlightLog "未能自动打开浏览器，请手动打开上面的练习地址：$($_.Exception.Message)"
                }
            }
            return 0
        }
        Start-Sleep -Milliseconds 300
    } while ($timer.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds)
    if ($panelReady) {
        return Complete-LaunchFailure 'controller_timeout' "练习网页已响应，但 $StartupTimeoutSeconds 秒内没观察到本次控制器上送状态。Webots 保持打开，可查看其报错或导出诊断；若准备重启，请先关闭这个 Webots 窗口。"
    }
    return Complete-LaunchFailure 'panel_timeout' "$StartupTimeoutSeconds 秒内练习网页未响应。Webots 保持打开，可查看其报错或导出诊断；若准备重启，请先关闭这个 Webots 窗口。"
}

$code = 1
try {
    $code = Start-Moonlight
} catch {
    if ($script:MoonlightRawLog -and $script:MoonlightEventLog) {
        [void](Complete-LaunchFailure 'unexpected_failure' "启动脚本异常：$($_.Exception.Message)")
    } else {
        Write-Host "无法写入启动日志，请确认项目已解压到可写目录：$($_.Exception.Message)"
    }
}
if ($code -ne 0 -and -not $NoPause) {
    & (Join-Path $PSScriptRoot 'launcher-menu.ps1') -WebotsHome $WebotsHome -PanelPort $PanelPort `
        -StartupTimeoutSeconds $StartupTimeoutSeconds -NoBrowser:$NoBrowser -Minimized:$Minimized -AfterFailure
}
exit $code
