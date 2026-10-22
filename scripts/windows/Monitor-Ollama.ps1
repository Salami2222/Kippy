#requires -Version 5.1
param([string]$AutomationDirectory = 'C:\Users\tommy\MacroMellowService\automation',
      [switch]$EnsureStarted, [switch]$InferenceCheck)
. "$PSScriptRoot\Common.ps1"

function Invoke-OllamaMonitor {
    param([string]$Directory, [switch]$EnsureStarted, [switch]$InferenceCheck)
    $lock = $null
    try {
        $config = Get-AutomationConfig $Directory
        try { $lock = [IO.File]::Open((Join-Path $Directory 'ollama-monitor.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { return 0 } # An independent monitor or startup check already owns recovery.
        $statePath = Join-Path $Directory 'ollama-state.json'
        $state = Read-AutomationState $statePath
        try {
            $health = Get-OllamaHealth
            $state.failures = 0; $state.last_healthy = [DateTime]::UtcNow.ToString('o')
            Save-AutomationState $statePath $state
            Write-AutomationEvent $Directory 'ollama' 'health' $health
            if ($InferenceCheck -and $health.model_available) {
                try { $health = Get-OllamaHealth -InferenceCheck; Write-AutomationEvent $Directory 'ollama' 'inference_passed' $health }
                catch { Write-AutomationEvent $Directory 'ollama' 'inference_failed' @{error = $_.Exception.Message}; return 2 }
            }
            if (-not $health.model_available) { Write-AutomationEvent $Directory 'ollama' 'model_missing_no_download' @{model = 'qwen3.5:9b'}; return 2 }
            return 0
        } catch { Write-AutomationEvent $Directory 'ollama' 'connection_failed' @{error = $_.Exception.Message} }
        $failures = if ($state.ContainsKey('failures')) { [int]$state.failures + 1 } else { 1 }
        $state.failures = $failures
        Save-AutomationState $statePath $state
        $listeners = @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 11434)
        $pattern = '^\s*"?' + [regex]::Escape($config.ollama_executable) + '"?\s+serve\s*$'
        $servers = @(Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" | Where-Object {
            $_.ExecutablePath -ieq $config.ollama_executable -and [regex]::IsMatch($_.CommandLine, $pattern, 'IgnoreCase')
        })
        foreach ($listener in $listeners) {
            if ($listener.OwningProcess -notin @($servers | ForEach-Object ProcessId)) { throw 'Unknown process on port 11434; no process will be killed or new server launched.' }
        }
        if ($servers.Count -gt 1) { throw 'Multiple existing Ollama servers detected; refusing automatic termination.' }
        if ($servers.Count -and ([DateTime]::UtcNow - $servers[0].CreationDate.ToUniversalTime()).TotalSeconds -lt 60) {
            Write-AutomationEvent $Directory 'ollama' 'startup_grace' @{}; return 2
        }
        if ($servers.Count -and $failures -lt 3) { return 2 }
        if ($state.ContainsKey('last_recovery') -and ([DateTime]::UtcNow - [DateTime]::Parse($state.last_recovery).ToUniversalTime()).TotalMinutes -lt 10) {
            Write-AutomationEvent $Directory 'ollama' 'recovery_cooldown' @{}; return 2
        }
        $attempts = @()
        if ($state.ContainsKey('attempts')) { $attempts = @($state.attempts | Where-Object { [DateTime]::Parse($_).ToUniversalTime() -gt [DateTime]::UtcNow.AddHours(-1) }) }
        if ($attempts.Count -ge 3) { Write-AutomationEvent $Directory 'ollama' 'recovery_limit' @{}; return 2 }
        $state.last_recovery = [DateTime]::UtcNow.ToString('o'); $state.attempts = @($attempts) + @($state.last_recovery)
        Save-AutomationState $statePath $state
        Write-AutomationEvent $Directory 'ollama' 'recovery_attempt' @{attempts_this_hour = $state.attempts.Count}
        $task = Get-ScheduledTask -TaskName 'MacroMellow Ollama' -TaskPath '\'
        if ($task.State -in @('Running','Queued')) { Stop-ScheduledTask -InputObject $task; Start-Sleep -Seconds 2 }
        foreach ($server in $servers) {
            # Legacy cmd launchers may leave a runner behind. Only proven direct children qualify.
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($server.ProcessId)" | Where-Object {
                $_.ExecutablePath -ieq $config.ollama_executable -and $_.CommandLine -match '\srunner\b' -and $_.CreationDate -ge $server.CreationDate
            })
            foreach ($child in $children) { Stop-IdentityCheckedProcess $child $config.ollama_executable '\srunner\b' $config.owner_sid }
            Stop-IdentityCheckedProcess $server $config.ollama_executable $pattern $config.owner_sid
        }
        if (@(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 11434).Count) { throw 'Port 11434 is not free; duplicate launch refused.' }
        Start-ScheduledTask -TaskName 'MacroMellow Ollama' -TaskPath '\'
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            Start-Sleep -Seconds 2
            try {
                $health = Get-OllamaHealth
                Write-AutomationEvent $Directory 'ollama' 'recovery_responding' $health
                if (-not $health.model_available) { Write-AutomationEvent $Directory 'ollama' 'model_missing_no_download' @{model='qwen3.5:9b'}; return 2 }
                return 0
            } catch {}
        }
        Write-AutomationEvent $Directory 'ollama' 'recovery_not_ready' @{}
        return 2
    } catch { Write-AutomationEvent $Directory 'ollama' 'monitor_failed' @{error = $_.Exception.Message}; return 2 }
    finally { if ($null -ne $lock) { $lock.Dispose() } }
}
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-OllamaMonitor -Directory $AutomationDirectory -EnsureStarted:$EnsureStarted -InferenceCheck:$InferenceCheck)
}
