param(
    [string]$WebotsHome = $env:WEBOTS_HOME,
    [ValidateRange(1,65535)][int]$PanelPort = 1234,
    [ValidateRange(1,300)][int]$StartupTimeoutSeconds = 45,
    [switch]$NoBrowser,
    [switch]$Minimized,
    [switch]$AfterFailure
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$shell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop).Source
$launchArgs = @('-PanelPort',"$PanelPort",'-StartupTimeoutSeconds',"$StartupTimeoutSeconds",'-NoPause')
$checkArgs = @('--panel-port',"$PanelPort")
if ($WebotsHome) {
    $launchArgs += @('-WebotsHome',$WebotsHome)
    $checkArgs += @('--webots-home',$WebotsHome)
}
if ($NoBrowser) { $launchArgs += '-NoBrowser' }
if ($Minimized) { $launchArgs += '-Minimized' }

function Test-ProjectPython {
    if (Test-Path -LiteralPath $python -PathType Leaf) { return $true }
    Write-Host '请先选择「排障与接入 → 安装项目依赖」。'
    return $false
}

function Invoke-RouteCheck {
    Write-Host ''
    Write-Host '按接入方案自检'
    Write-Host '  1  手动探索 / Python 会员程序'
    Write-Host '  2  STM32 + OpenMV'
    Write-Host '  3  STM32 + 手机串流 + 电脑视觉'
    Write-Host '  0  返回'
    $route = switch (Read-Host '选择接入方案，回车选 1') {
        ''  { 'manual' }
        '1' { 'manual' }
        '2' { 'openmv' }
        '3' { 'phone' }
        '0' { return }
        default { Write-Host '请选择 0 至 3。'; return }
    }
    $routeArgs = @('--route',$route) + $checkArgs
    if ($route -ne 'manual' -and
        (Read-Host '运行独立 Renode 载入探针？它不会驱动车辆 [y/N]') -eq 'y') {
        $routeArgs += '--boot'
    }
    & (Join-Path $PSScriptRoot 'check-setup.bat') @routeArgs
}

function Invoke-OptionalSetupMenu {
    while ($true) {
        Write-Host ''
        Write-Host '安装可选组件（需要网络）'
        Write-Host '  1  STEP 模型转换组件'
        Write-Host '  2  STM32 固件执行组件（Renode）'
        Write-Host '  0  返回'
        $selection = Read-Host '输入编号，按回车确认'
        if ($null -eq $selection) { return }
        switch ($selection) {
            '1' {
                if (Test-ProjectPython) { & (Join-Path $PSScriptRoot 'setup-cad.bat') }
            }
            '2' {
                & (Join-Path $PSScriptRoot 'setup-firmware.bat')
            }
            '0' { return }
            default { Write-Host '请输入 0 至 2 中的一个编号。' }
        }
    }
}

function Invoke-TroubleshootingMenu {
    while ($true) {
        Write-Host ''
        Write-Host '排障与接入'
        Write-Host '  1  安装项目依赖（使用本机已有的 Python，需要网络）'
        Write-Host '  2  按接入方案自检'
        Write-Host '  3  列出实体 COM 口（不打开端口，不发指令）'
        Write-Host '  4  导出启动与运行诊断（网页打不开也可用）'
        Write-Host '  5  安装可选组件'
        Write-Host '  0  返回首页'
        $selection = Read-Host '输入编号，按回车确认'
        if ($null -eq $selection) { return }
        switch ($selection) {
            '1' {
                & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup.ps1') -NoPause
                if ($LASTEXITCODE -eq 0) { Write-Host '安装完成。返回首页可选择「开始练习」。' }
            }
            '2' {
                if (Test-ProjectPython) { Invoke-RouteCheck }
            }
            '3' {
                if (Test-ProjectPython) {
                    & (Join-Path $PSScriptRoot 'check-serial.bat') --list
                } else {
                    Write-Host '也可在 Windows 设备管理器查看端口。'
                }
            }
            '4' {
                & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'export-diagnostics.ps1') -NoPause
                if ($LASTEXITCODE -eq 0) { Write-Host '请检查项目 output 文件夹中的 ZIP，再自行交给负责人。' }
            }
            '5' { Invoke-OptionalSetupMenu }
            '0' { return }
            default { Write-Host '请输入 0 至 5 中的一个编号。' }
        }
    }
}

function Invoke-TeamProjectMenu {
    while ($true) {
        Write-Host ''
        Write-Host '队伍工程'
        Write-Host '  1  新建或检查队伍工程'
        Write-Host '  2  迁移旧队伍工程'
        Write-Host '  0  返回首页'
        $selection = Read-Host '输入编号，按回车确认'
        if ($null -eq $selection) { return }
        switch ($selection) {
            '1' {
                if (Test-ProjectPython) { & (Join-Path $PSScriptRoot 'team-project.bat') }
            }
            '2' { & (Join-Path $PSScriptRoot 'upgrade-project.bat') }
            '0' { return }
            default { Write-Host '请输入 0 至 2 中的一个编号。' }
        }
    }
}

function Show-LaunchFailureMenu {
    Write-Host ''
    Write-Host '本次启动没有完成。选 4 可直接导出诊断 ZIP，无需运行仿真。'
    Invoke-TroubleshootingMenu
}

if ($AfterFailure) { Show-LaunchFailureMenu }

while ($true) {
    Write-Host ''
    Write-Host '月光杯 · 启动器'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Write-Host '首次使用请选 4 → 1 安装项目依赖。'
    }
    Write-Host '  1  开始练习'
    Write-Host '  2  配置小车'
    Write-Host '  3  队伍工程'
    Write-Host '  4  排障与接入'
    Write-Host '  0  退出'
    $selection = Read-Host '输入编号，按回车确认'
    if ($null -eq $selection) { return }
    switch ($selection) {
        '1' {
            & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch.ps1') @launchArgs
            if ($LASTEXITCODE -eq 0) { return }
            Show-LaunchFailureMenu
        }
        '2' {
            if (Test-ProjectPython) { & (Join-Path $PSScriptRoot 'import-hardware.bat') }
        }
        '3' { Invoke-TeamProjectMenu }
        '4' { Invoke-TroubleshootingMenu }
        '0' { return }
        default { Write-Host '请输入 0 至 4 中的一个编号。' }
    }
}
