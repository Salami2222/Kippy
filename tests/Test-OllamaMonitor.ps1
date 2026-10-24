#requires -Version 5.1
# Isolated recovery decisions; no real server/task/model is changed.
$ErrorActionPreference='Stop'
$passed=0; $failed=0
$source=Join-Path $PSScriptRoot '..\scripts\windows\Monitor-Ollama.ps1'
$scratch=Join-Path ([IO.Path]::GetTempPath()) ('kippy-monitor-tests-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory $scratch
function Assert($Value) { if (-not $Value) { throw 'Assertion failed' } }
try {
    foreach ($scenario in @('healthy','missing-model','offline','unknown-listener','cooldown','hung-first-failure','inference-fails','lock-held')) {
        try {
            & {
                . $source
                $dir=Join-Path $scratch $scenario
                $null=New-Item -ItemType Directory $dir
                $script:starts=0; $script:stops=0; $script:probes=0
                function Get-AutomationConfig { @{ollama_executable='C:\existing\ollama.exe';owner_sid='owner'} }
                function Write-AutomationEvent {}
                function Get-OllamaHealth {
                    param([switch]$InferenceCheck)
                    $script:probes++
                    if ($scenario -eq 'inference-fails' -and $InferenceCheck) { throw 'inference unavailable' }
                    if ($scenario -in @('offline','unknown-listener','cooldown','hung-first-failure') -and $script:starts -eq 0) { throw 'offline' }
                    @{connected=$true;model_available=($scenario -ne 'missing-model')}
                }
                function Get-NetTCPConnection {
                    if ($scenario -eq 'unknown-listener') { @{LocalPort=11434;OwningProcess=99} }
                }
                function Get-CimInstance {
                    if ($scenario -eq 'hung-first-failure') { [pscustomobject]@{ExecutablePath='C:\existing\ollama.exe';CommandLine='"C:\existing\ollama.exe" serve';ProcessId=44;CreationDate=[DateTime]::Now.AddMinutes(-5)} }
                }
                function Get-ScheduledTask { @{State='Ready'} }
                function Start-ScheduledTask { $script:starts++ }
                function Stop-ScheduledTask { $script:stops++; throw 'Healthy instance must not be stopped' }
                function Start-Sleep {}
                if ($scenario -eq 'cooldown') { Save-AutomationState (Join-Path $dir 'ollama-state.json') @{last_recovery=[DateTime]::UtcNow.ToString('o')} }
                $held=$null
                if ($scenario -eq 'lock-held') { $held=[IO.File]::Open((Join-Path $dir 'ollama-monitor.lock'),'OpenOrCreate','ReadWrite','None') }
                try { $result=Invoke-OllamaMonitor -Directory $dir -InferenceCheck:($scenario -eq 'inference-fails') }
                finally { if($held){$held.Dispose()} }
                Assert ($script:stops -eq 0)
                if ($scenario -eq 'offline') { Assert ($script:starts -eq 1 -and $result -eq 0) }
                else {
                    Assert ($script:starts -eq 0)
                    Assert ($result -eq $(if($scenario -in @('healthy','lock-held')){0}else{2}))
                }
            }
            $passed++; [Console]::WriteLine("PASS: Ollama $scenario")
        } catch { $failed++; [Console]::WriteLine("FAIL: Ollama $scenario -- $($_.Exception.Message)") }
    }
} finally { Remove-Item $scratch -Recurse -Force }
[Console]::WriteLine("Ollama monitor tests: $passed passed, $failed failed")
if($failed){exit 1}
