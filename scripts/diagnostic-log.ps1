function New-MoonlightLog {
    param([string]$Repository, [ValidateSet('setup','launch')][string]$Kind)
    $script:MoonlightUtf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $script:MoonlightUtf8
    $script:MoonlightLogDir = Join-Path $Repository 'output\logs'
    [void][System.IO.Directory]::CreateDirectory($script:MoonlightLogDir)
    $script:MoonlightSession = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'") + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    $script:MoonlightRawLog = Join-Path $script:MoonlightLogDir "$Kind-$script:MoonlightSession.log"
    $script:MoonlightEventLog = Join-Path $script:MoonlightLogDir "diagnostic-$Kind-$script:MoonlightSession.jsonl"
    Write-MoonlightLog "Log file: $script:MoonlightRawLog"
}

function Write-MoonlightLog {
    param([string]$Message)
    $line = '[' + [DateTime]::UtcNow.ToString('o') + '] ' + $Message
    [System.IO.File]::AppendAllText($script:MoonlightRawLog, $line + [Environment]::NewLine, $script:MoonlightUtf8)
    Write-Host $line
}

function Write-MoonlightEvent {
    param([string]$Event, [string]$Code, [int]$ExitCode)
    # Only pass fixed event/code names and a numeric native exit status to this file.
    $entry = [ordered]@{ at = [DateTime]::UtcNow.ToString('o'); event = $Event }
    if ($Code) { $entry['code'] = $Code }
    if ($PSBoundParameters.ContainsKey('ExitCode')) { $entry['exit_code'] = $ExitCode }
    $line = ConvertTo-Json -InputObject $entry -Compress
    [System.IO.File]::AppendAllText($script:MoonlightEventLog, $line + [Environment]::NewLine, $script:MoonlightUtf8)
}

function Invoke-MoonlightNative {
    param([string]$Executable, [string[]]$Arguments)
    # Windows PowerShell maps native stderr to ErrorRecord; a pip warning is not an exception.
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments 2>&1 | ForEach-Object {
        $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { [string]$_ }
        Write-MoonlightLog $line
    }
    return $LASTEXITCODE
}
