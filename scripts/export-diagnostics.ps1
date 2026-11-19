param(
    [switch]$NoPause,
    [switch]$OpenFolder
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$output = Join-Path $root 'output'
$logs = Join-Path $output 'logs'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sessionPattern = '\d{8}T\d{6}Z-[0-9a-f]{8,32}'

# Export only enumerated fields. Raw process output may include private paths,
# code snippets, protocol bytes or exception messages supplied by member code.
$events = @{
    launch = @('launch_started','scene_generated','webots_started','panel_ready','launch_failed','browser_open_failed')
    setup = @('setup_started','python_found','environment_ready','setup_finished','setup_failed')
    runtime = @('startup_begin','config_ready','scene_loaded','controller_ready','mode_changed','algorithm_loaded',
        'vision_link_ready','run_started','run_paused','round_reset','round_finished','firmware_start_requested',
        'firmware_started','firmware_closed','gripper_holding','gripper_released','wall_contact','error','shutdown','log_limit')
}
$allowed = @{
    code = @('missing_python','missing_webots','port_conflict','scene_generation_failed','webots_start_failed',
        'webots_exit','panel_timeout','unexpected_failure','unsupported_python','venv_failed',
        'dependency_install_failed','browser_open_failed')
    mode = @('manual','member','firmware')
    stage = @('full','pickup','channel','parking')
    phase = @('dependency_load','scene_config','webots_setup','controller_ready','vision_link','algorithm_load',
        'panel_message','panel_start','panel_pause','panel_mode','panel_reload','panel_drive','round_reset','round_end',
        'gripper','contact','simulation_step','member_vision','member_step','firmware_start','firmware_step',
        'firmware_stop','actuator','shutdown')
    reason = @('reset','switch_mode','reload','manual_override','manual','time','shutdown','firmware_missing',
        'renode_missing','motor_pins_missing','renode_launch_failed','renode_connection_closed')
    category = @('dependency','configuration','communication','member_code','firmware','controller')
    error_class = @('FileNotFoundError','PermissionError','OSError','ImportError','ModuleNotFoundError',
        'ValueError','TypeError','KeyError','IndexError','AttributeError','NameError','SyntaxError',
        'IndentationError','UnicodeError','JSONDecodeError','RuntimeError','ConnectionError','TimeoutError',
        'BrokenPipeError','ConnectionResetError','ConnectionRefusedError','ZeroDivisionError','AssertionError',
        'OverflowError','ArithmeticError','MemoryError','SystemExit','KeyboardInterrupt','OtherError','OpenCVError')
    field_revision = @('rules-2000','practice-legacy','custom')
    robot_kind = @('parametric','urdf','mesh','unknown')
    gripper_kind = @('parametric','urdf','none','imported','unknown')
}

# Even error hints use fixed labels rather than copying the matching line.
$hints = [ordered]@{
    python_module_missing = '(?im)(?:ModuleNotFoundError|ImportError):'
    python_binary_incompatible = '(?im)(?:DLL load failed|numpy\.core\.multiarray failed to import)'
    webots_controller_error = '(?im)(?:controller .{0,140}(?:crashed|exited with status|failed to start|could not be started|terminated unexpectedly))'
    opengl_initialization_error = '(?im)(?:requires OpenGL|OpenGL .{0,80}(?:not supported|initialization failed)|failed to create .{0,40}OpenGL|failed to create context)'
    qt_platform_error = '(?im)(?:no Qt platform plugin could be initialized|could not (?:find|load) the Qt platform plugin)'
    dependency_download_error = '(?im)(?:Could not fetch URL|CERTIFICATE_VERIFY_FAILED|Temporary failure in name resolution|ProxyError|ReadTimeoutError)'
    dependency_no_compatible_package = '(?im)(?:Could not find a version that satisfies the requirement|No matching distribution found)'
    port_in_use = '(?im)(?:address already in use|Only one usage of each socket address|\u7aef\u53e3.{0,18}\u5360\u7528)'
    renode_handshake_failed = '(?im)(?:Renode.{0,80}(?:did not start|failed|exited|timed out|\u542f\u52a8\u5931\u8d25|\u542f\u52a8\u8d85\u65f6|\u63d0\u524d\u9000\u51fa))'
}
# Absolute paths, registry values, hardware identifiers and arbitrary lines are
# not part of the ZIP schema. Read a capped tail without locking active logs.
function Read-LogTail([string]$Path) {
    $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
    try {
        [int]$count = [Math]::Min($stream.Length, 131072)
        if ($count -eq 0) { return '' }
        $offset = $stream.Length - $count
        $null = $stream.Seek($offset,[IO.SeekOrigin]::Begin)
        $reader = New-Object IO.BinaryReader($stream)
        try { $bytes = $reader.ReadBytes($count) } finally { $reader.Dispose() }
        if ($offset -eq 0 -and $bytes.Length -ge 2 -and $bytes[0] -eq 255 -and $bytes[1] -eq 254) {
            return [Text.Encoding]::Unicode.GetString($bytes).TrimStart([char]0xfeff)
        }
        $content = $utf8.GetString($bytes).TrimStart([char]0xfeff)
        if ($offset -gt 0) {
            $firstNewline = $content.IndexOf([char]10)
            if ($firstNewline -lt 0) { return '' }
            $content = $content.Substring($firstNewline+1)
        }
        return $content
    } finally { $stream.Dispose() }
}

function Is-Int($value) {
    return ($value -is [int] -or $value -is [long])
}

function Select-SafeRow($row, [string]$source) {
    if ($null -eq $row -or $row.event -isnot [string] -or $events[$source] -cnotcontains $row.event) {
        return
    }
    $when = if ($source -eq 'runtime') { $row.timestamp } else { $row.at }
    if ($when -isnot [string] -or $when -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)$') { return }
    $safe = [ordered]@{at=$when; event=$row.event}
    foreach ($key in $allowed.Keys) {
        $val = $row.$key
        if ($val -is [string] -and $allowed[$key] -ccontains $val) { $safe[$key]=$val }
    }
    if ($row.exit_code -ne $null -and (Is-Int $row.exit_code) -and
        $row.exit_code -ge [int]::MinValue -and $row.exit_code -le [int]::MaxValue) {
        $safe.exit_code=$row.exit_code
    }
    foreach ($key in @('step_ms','physics_step_ms','sim_ms','round','ir_count','sonar_count','member_line')) {
        $val = $row.$key
        if ((Is-Int $val) -and $val -ge 0 -and $val -le [int]::MaxValue) { $safe[$key]=$val }
    }
    foreach ($key in @('vision_link','firmware_configured','firmware_active')) {
        if ($row.$key -is [bool]) { $safe[$key]=$row.$key }
    }
    if ($row.project_version -is [string] -and $row.project_version -cmatch '^v\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        $safe.project_version=$row.project_version
    }
    if ($row.python_version -is [string] -and $row.python_version -cmatch '^\d{1,2}\.\d{1,2}\.\d{1,3}$') {
        $safe.python_version=$row.python_version
    }
    return $safe
}

function Write-ZipEntry($archive,[string]$name,[string]$content) {
    $entry=$archive.CreateEntry($name,[IO.Compression.CompressionLevel]::Optimal)
    $writer=New-Object IO.StreamWriter($entry.Open(),$utf8)
    try { $writer.WriteLine($content) } finally { $writer.Dispose() }
}

$destination = $null
$finished = $false
$result = 0
try {
    $logSets=@()
    $files=@()
    if (Test-Path -LiteralPath $logs -PathType Container) {
        $files=@(Get-ChildItem -LiteralPath $logs -File |
            Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
            Sort-Object LastWriteTimeUtc -Descending)
    }
    foreach ($source in @('setup','launch','runtime')) {
        $pattern = if ($source -eq 'runtime') {
            '^runtime-(?<id>' + $sessionPattern + ')(?:-[0-9a-f]{4,32})?\.jsonl$'
        } else {
            '^diagnostic-' + $source + '-(?<id>' + $sessionPattern + ')\.jsonl$'
        }
        foreach ($file in @($files | Where-Object { $_.Name -cmatch $pattern } | Select-Object -First 4)) {
            $null = $file.Name -cmatch $pattern
            $session = $Matches['id']
            $rows=@()
            $unreadable=$false
            try {
                foreach ($line in ((Read-LogTail $file.FullName) -split '\r?\n')) {
                    if ($line.Length -eq 0 -or $line.Length -gt 8192) { continue }
                    try {
                        # PowerShell 7.5+ otherwise automatically parses ISO JSON times.
                        if ($PSVersionTable.PSVersion -ge [version]'7.5') {
                            $row=ConvertFrom-Json -InputObject $line -DateKind String
                        } else {
                            $row=ConvertFrom-Json -InputObject $line
                        }
                        $safe=Select-SafeRow $row $source
                        if ($null -ne $safe) { $rows+=@($safe) }
                    } catch { continue }
                }
            } catch { $unreadable=$true }
            $logSets+=@([ordered]@{source=$source; session=$session; unreadable=$unreadable; events=@($rows | Select-Object -Last 100)})
        }
    }

    $observations=@()
    foreach ($kind in @('setup','launch','webots-out','webots-err')) {
        $pattern='^'+$kind+'-(?<id>'+$sessionPattern+')\.log$'
        foreach ($file in @($files | Where-Object { $_.Name -cmatch $pattern } | Select-Object -First 4)) {
            $null=$file.Name -cmatch $pattern
            $session=$Matches['id']
            try { $tail=Read-LogTail $file.FullName } catch { continue }
            $found=@()
            foreach ($hint in $hints.Keys) { if ($tail -match $hints[$hint]) { $found+=@($hint) } }
            $observations+=@([ordered]@{source=$kind;session=$session;has_output=[bool]$tail;hints=$found})
        }
    }
    $projectVersion='unknown'
    $versionFile=Join-Path $root 'VERSION'
    if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
        $possible=([string](Get-Content -LiteralPath $versionFile -TotalCount 1 -ErrorAction SilentlyContinue)).Trim()
        if ($possible -cmatch '^v\d{1,3}\.\d{1,3}\.\d{1,3}$') { $projectVersion=$possible }
    }
    $windowsVersion=[Environment]::OSVersion.Version.ToString()
    $build=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name CurrentBuildNumber -ErrorAction SilentlyContinue).CurrentBuildNumber
    if ($build -isnot [string] -or $build -notmatch '^\d{4,6}$') { $build=$null }
    $report=[ordered]@{
        schema=1; created_at=[DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        project_version=$projectVersion; windows_version=$windowsVersion; windows_build=$build
        os_64_bit=[Environment]::Is64BitOperatingSystem
        venv_python_present=(Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe') -PathType Leaf)
        sessions=$logSets; process_hints=$observations
    }

    $null=New-Item -ItemType Directory -Path $output -Force
    $id=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    $destination=Join-Path $output ('moonlight-diagnostics-'+$id+'.zip')
    Add-Type -AssemblyName System.IO.Compression
    $zipFile=[IO.File]::Open($destination,[IO.FileMode]::CreateNew)
    try {
        $archive=New-Object IO.Compression.ZipArchive($zipFile,[IO.Compression.ZipArchiveMode]::Create)
        try {
            Write-ZipEntry $archive 'diagnostics.json' (ConvertTo-Json -InputObject $report -Depth 8)
            Write-ZipEntry $archive 'README.txt' @'
Moonlight Cup diagnostics
Read diagnostics.json. Events from the same run have the same session ID.
Recent setup, launch and controller events are included when present. Error
hints are based on recognized messages, and may need manual confirmation.
No raw console logs, source code, firmware, project settings, camera images
or UART payloads are included. Nothing was uploaded.
'@
        } finally { $archive.Dispose() }
    } finally { $zipFile.Dispose() }
    $finished=$true
    Write-Host 'Diagnostic ZIP created:'
    Write-Host $destination
    if (-not $logSets.Count) { Write-Host 'No runtime or setup session was found; the ZIP contains system details and any recognized hints.' }
    if ($OpenFolder) {
        try { Start-Process explorer.exe -ArgumentList ('/select,"'+$destination+'"') } catch {}
    }
} catch {
    $result=1
    Write-Host 'Could not create the diagnostic ZIP. Extract the project to a writable folder and retry.'
    if ($destination -and -not $finished -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
        Remove-Item -LiteralPath $destination -ErrorAction SilentlyContinue
    }
}
if (-not $NoPause) { [void](Read-Host 'Press Enter to close') }
exit $result
