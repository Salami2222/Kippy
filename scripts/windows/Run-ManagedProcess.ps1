#requires -Version 5.1
param([ValidateSet('Web','Ollama','Deploy')][string]$Role,
      [string]$AutomationDirectory = 'C:\Users\tommy\MacroMellowService\automation')
. "$PSScriptRoot\Common.ps1"
$lock = $null
try {
    $config = Get-AutomationConfig $AutomationDirectory
    if ($Role -eq 'Ollama') {
        $lock = [IO.File]::Open((Join-Path $AutomationDirectory 'ollama-server.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        try { $null = Get-OllamaHealth; Write-AutomationEvent $AutomationDirectory 'ollama' 'existing_server_preserved' @{}; exit 0 } catch {}
        $occupied = @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 11434)
        $existing = @(Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" | Where-Object {
            $_.ExecutablePath -ieq $config.ollama_executable -and $_.CommandLine -match '\sserve\s*$'
        })
        if ($occupied.Count -or $existing.Count) { throw 'Ollama is already starting or port 11434 is occupied; duplicate launch refused.' }
    }
    if ($Role -eq 'Web') {
        # AI startup failures never prevent the website from starting.
        try {
            . "$PSScriptRoot\Monitor-Ollama.ps1" -AutomationDirectory $AutomationDirectory
            $null = Invoke-OllamaMonitor -Directory $AutomationDirectory -EnsureStarted
        } catch {
            Write-AutomationEvent $AutomationDirectory 'ollama' 'web_start_ai_warning' @{error = $_.Exception.Message}
        }
    }
    if (-not ('MellowProcessJob' -as [type])) { Add-Type -Path "$PSScriptRoot\ProcessJob.cs" }
    if ($Role -eq 'Deploy') {
        $executable = Join-Path $PSHOME 'powershell.exe'
        $arguments = '-NoLogo -NoProfile -NonInteractive -File "' + (Join-Path $config.repository 'scripts\Invoke-AutoDeploy.ps1') + '" -AutomationDirectory "' + $AutomationDirectory + '"'
    } else {
        $executable = Join-Path $env:SystemRoot 'System32\cmd.exe'
        $launcher = if ($Role -eq 'Web') { $config.web_launcher } else { $config.ollama_launcher }
        $arguments = '/d /s /c ""' + $launcher + '""'
    }
    Write-AutomationEvent $AutomationDirectory $Role.ToLower() 'managed_process_start' @{supervisor_pid = $PID}
    $result = [MellowProcessJob]::Run($executable, $arguments, $config.repository)
    Write-AutomationEvent $AutomationDirectory $Role.ToLower() 'managed_process_exit' @{exit_code = $result}
    exit $result
} catch {
    Write-AutomationEvent $AutomationDirectory $Role.ToLower() 'supervisor_failed' @{error = $_.Exception.Message}
    exit 1
} finally { if ($null -ne $lock) { $lock.Dispose() } }
