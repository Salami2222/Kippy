Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-AutomationEvent {
    param([string]$Directory, [string]$Stream, [string]$Event, $Details)
    $null = [IO.Directory]::CreateDirectory((Join-Path $Directory 'logs'))
    $entry = @{time = [DateTime]::UtcNow.ToString('o'); event = $Event; details = $Details}
    $file = Join-Path $Directory ('logs\' + $Stream + '-' + [DateTime]::UtcNow.ToString('yyyy-MM-dd') + '.jsonl')
    [IO.File]::AppendAllText($file, (($entry | ConvertTo-Json -Compress -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
    [Console]::WriteLine("[$Stream] $Event " + ($Details | ConvertTo-Json -Compress -Depth 5))
}
function Read-AutomationState {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @{} }
    $object = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
    $state = @{}; foreach ($property in $object.PSObject.Properties) { $state[$property.Name] = $property.Value }
    return $state
}
function Save-AutomationState {
    param([string]$Path, $State)
    $temp = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temp, ($State | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temp, $Path, [NullString]::Value) }
    else { [IO.File]::Move($temp, $Path) }
}
function Get-AutomationConfig {
    param([string]$Directory)
    return ([IO.File]::ReadAllText((Join-Path $Directory 'config.json')) | ConvertFrom-Json)
}
function Get-OllamaHealth {
    param([switch]$InferenceCheck)
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 -ErrorAction Stop
    if (-not ($tags.PSObject.Properties.Name -contains 'models')) { throw 'Invalid Ollama /api/tags response.' }
    $installed = @($tags.models | Where-Object { $_.name -ceq 'qwen3.5:9b' }).Count -gt 0
    $result = @{connected = $true; model_available = $installed; inference = 'not_requested'}
    if ($InferenceCheck -and $installed) {
        $body = @{model = 'qwen3.5:9b'; prompt = 'Reply with OK.'; stream = $false; think = $false;
                  options = @{num_predict = 8; temperature = 0}} | ConvertTo-Json -Depth 4
        $reply = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 60
        if (-not $reply.done -or [string]::IsNullOrWhiteSpace($reply.response)) { throw 'Qwen inference returned no completed text.' }
        $result.inference = 'passed'
    }
    return $result
}
function Stop-IdentityCheckedProcess {
    param($Snapshot, [string]$Executable, [string]$Pattern, [string]$OwnerSid)
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Snapshot.ProcessId)"
    if ($null -eq $current) { return }
    if ($current.CreationDate -ne $Snapshot.CreationDate -or $current.ExecutablePath -ine $Executable -or
        -not [regex]::IsMatch($current.CommandLine, $Pattern, 'IgnoreCase')) { throw 'Process identity changed; refusing termination.' }
    $owner = Invoke-CimMethod -InputObject $current -MethodName GetOwnerSid
    if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $OwnerSid) { throw 'Process owner could not be verified.' }
    $handle = [Diagnostics.Process]::GetProcessById($current.ProcessId)
    try {
        $null = $handle.Handle
        if ([Math]::Abs(($handle.StartTime.ToUniversalTime() - $current.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 10) { throw 'PID changed before handle capture.' }
        if (-not $handle.HasExited) { $handle.Kill(); if (-not $handle.WaitForExit(5000)) { throw 'Process did not exit.' } }
    } finally { $handle.Dispose() }
}
