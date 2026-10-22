#requires -Version 5.1
# Run once in an elevated, noninteractive PowerShell session as the existing task owner.
[CmdletBinding()]
param([string]$Repository = 'C:\Users\tommy\MacroMellow',
      [string]$AutomationDirectory = 'C:\Users\tommy\MacroMellowService\automation')
. "$PSScriptRoot\windows\Common.ps1"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run as the existing Windows user with administrator privileges to inspect and configure scheduled tasks.'
}
$web = Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\'
$ollama = Get-ScheduledTask -TaskName 'MacroMellow Ollama' -TaskPath '\'
foreach ($task in @($web,$ollama)) {
    $sid = ([Security.Principal.NTAccount]::new($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    if ($sid -ne $identity.User.Value -or $task.Principal.LogonType -ne 'S4U') {
        throw 'Expected existing tasks owned by this user with S4U noninteractive logon. Credentials will not be changed automatically.'
    }
    if ($task.State -eq 'Disabled' -or @($task.Actions).Count -ne 1) { throw 'Existing task is disabled or has multiple actions; review it manually.' }
    Write-AutomationEvent $AutomationDirectory 'install' 'existing_task' @{name=$task.TaskName;state=[string]$task.State;principal=$task.Principal.UserId;logon=[string]$task.Principal.LogonType;action=$task.Actions[0].Execute;arguments=$task.Actions[0].Arguments}
}
$null = [IO.Directory]::CreateDirectory($AutomationDirectory)
$configPath = Join-Path $AutomationDirectory 'config.json'
if (Test-Path $configPath) {
    $config = Get-AutomationConfig $AutomationDirectory
    if ($config.repository -ine $Repository -or $config.owner_sid -ne $identity.User.Value) { throw 'Existing automation configuration belongs to another checkout/user.' }
} else {
    function Get-OriginalLauncher($Task) {
        if ($Task.Actions[0].Execute -notmatch '(?i)(^|\\)cmd\.exe$' -or $Task.Actions[0].Arguments -notmatch '(?i)^/c\s+""([^"\r\n]+\.cmd)""$') {
            throw "Unrecognized existing launcher for $($Task.TaskName). Review instead of replacing it."
        }
        $path = $Matches[1]
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Launcher missing: $path" }
        return $path
    }
    $executable = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (-not (Test-Path -LiteralPath $executable)) { throw 'Existing Ollama installation not found; nothing will be downloaded.' }
    $config = @{repository=$Repository;owner_sid=$identity.User.Value;web_launcher=(Get-OriginalLauncher $web);ollama_launcher=(Get-OriginalLauncher $ollama);ollama_executable=$executable}
    Save-AutomationState $configPath $config
    $config = Get-AutomationConfig $AutomationDirectory
}
# Refuse collisions before changing any existing service action.
foreach ($name in @('MacroMellow Auto Deploy','MacroMellow Ollama Monitor')) {
    $existing = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing -and $existing.Description -ne 'MacroMellow managed automation v1') { throw "Task $name is not owned by this installer." }
}
$backup = Join-Path $AutomationDirectory 'original-tasks'
$null = [IO.Directory]::CreateDirectory($backup)
foreach ($task in @($web,$ollama)) {
    $path = Join-Path $backup ($task.TaskName + '.xml')
    if (-not (Test-Path $path)) { Export-ScheduledTask -InputObject $task | Set-Content -LiteralPath $path -Encoding Unicode }
}
$powerShell = Join-Path $PSHOME 'powershell.exe'
$supervisor = Join-Path $Repository 'scripts\windows\Run-ManagedProcess.ps1'
# Compile first, so a damaged/unsupported supervisor cannot replace working task actions.
if (-not ('MellowProcessJob' -as [type])) { Add-Type -Path "$PSScriptRoot\windows\ProcessJob.cs" }
foreach ($entry in @(@($web,'Web'),@($ollama,'Ollama'))) {
    $arguments = '-NoLogo -NoProfile -NonInteractive -File "' + $supervisor + '" -Role ' + $entry[1] + ' -AutomationDirectory "' + $AutomationDirectory + '"'
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $Repository
    # Update only the action: principal, credentials, existing triggers/settings remain intact.
    $null = Set-ScheduledTask -TaskName $entry[0].TaskName -TaskPath '\' -Action $action
}
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Highest
foreach ($spec in @(@('MacroMellow Auto Deploy',2,'Deploy'),@('MacroMellow Ollama Monitor',1,'Monitor'))) {
    $existing = Get-ScheduledTask -TaskName $spec[0] -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing -and $existing.Description -ne 'MacroMellow managed automation v1') { throw "Task $($spec[0]) already exists and is not owned by this installer." }
    if ($spec[2] -eq 'Deploy') {
        $args = '-NoLogo -NoProfile -NonInteractive -File "' + $supervisor + '" -Role Deploy -AutomationDirectory "' + $AutomationDirectory + '"'
    } else {
        $args = '-NoLogo -NoProfile -NonInteractive -File "' + (Join-Path $Repository 'scripts\windows\Monitor-Ollama.ps1') + '" -AutomationDirectory "' + $AutomationDirectory + '"'
    }
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $args -WorkingDirectory $Repository
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = 'PT45S'
    $poll = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $spec[1])
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    $null = Register-ScheduledTask -TaskName $spec[0] -TaskPath '\' -Action $action -Trigger @($boot,$poll) -Settings $settings -Principal $principal -Description 'MacroMellow managed automation v1' -Force
}
# Preserve an already healthy Ollama server. The independent monitor owns recovery.
Start-ScheduledTask -TaskName 'MacroMellow Ollama Monitor' -TaskPath '\'
Write-AutomationEvent $AutomationDirectory 'install' 'configured' @{auto_deploy_minutes=2;ollama_monitor_minutes=1;web_restarted=$false;ollama_restarted=$false}
[Console]::WriteLine('Installed. Run mellow-update once to activate the web process supervisor; verify task results and /api/version. No credentials, launchers, data, Ollama environment, SSH, or Tailscale settings were changed.')
