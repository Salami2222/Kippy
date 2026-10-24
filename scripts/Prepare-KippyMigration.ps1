#requires -Version 5.1
<#[Single application transition; run only after reviewing docs/windows-migration.md.
This script never changes credentials, model files, network settings, or task names.
Prepare keeps the current app running; Activate requires the completed Kippy commit on main.]#>
[CmdletBinding()]
param([ValidateSet('Check','Prepare','Activate','Finalize')][string]$Action='Check',
      [string]$Repository='C:\Users\tommy\MacroMellow',
      [string]$AutomationDirectory='C:\Users\tommy\MacroMellowService\automation')
$ErrorActionPreference='Stop'
. "$PSScriptRoot\windows\Common.ps1"
$config=Get-AutomationConfig $AutomationDirectory
if ($config.repository -ine $Repository) { throw 'Repository does not match the existing automation configuration.' }
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if ($config.owner_sid -ne $identity.User.Value) { throw 'Run as the existing automation owner. Credentials will not be changed.' }
$service=Split-Path $AutomationDirectory -Parent
$original=Join-Path $service 'start-macromellow.cmd'
$launcher=Join-Path $service 'start-kippy.cmd'
$bridge=Join-Path $service 'kippy-transition.cmd'
$auto=Get-ScheduledTask -TaskName 'MacroMellow Auto Deploy' -TaskPath '\'
if ($Action -eq 'Check') {
    [pscustomobject]@{repository=$Repository;launcher=$config.web_launcher;automatic_deployment=$auto.State;
        storage=(Join-Path $env:USERPROFILE 'KippyData');model='qwen3.5:9b'} | Format-List
    return
}
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'An elevated PowerShell under the existing task owner is required.'
}
if ($Action -eq 'Prepare') {
    if ($auto.State -in @('Running','Queued')) { throw 'Automatic deployment is in progress. Wait for it to finish before preparing migration.' }
    if ($config.web_launcher -ine $original -and $config.web_launcher -ine $bridge -and $config.web_launcher -ine $launcher) {
        throw 'Unrecognized launcher. Inspect it; this script will not overwrite custom configuration.'
    }
    if ($config.web_launcher -ieq $launcher) { Write-Host 'Kippy launcher already active; no preparation needed.'; return }
    $source=[IO.File]::ReadAllText($original)
    if (([regex]::Matches($source,'-m macromellow web')).Count -ne 1 -or
        -not $source.Contains('set "DATA=%USERPROFILE%\MacroMellowData"')) {
        throw 'Launcher differs from the inspected installation. Review manually before migration.'
    }
    $updated=$source.Replace('-m macromellow web','-m kippy web').Replace('set "DATA=%USERPROFILE%\MacroMellowData"','set "DATA=%USERPROFILE%\KippyData"').Replace('macromellow.log','kippy.log').Replace('MacroMellow stopped unexpectedly','Kippy stopped unexpectedly')
    # Do not overwrite unrelated files that happen to use these names.
    if ((Test-Path $launcher) -and [IO.File]::ReadAllText($launcher) -cne $updated) { throw 'start-kippy.cmd already exists with different contents; review it.' }
    $bridgeText="@echo off`r`nif exist `"$Repository\kippy\__main__.py`" (`r`n  call `"$launcher`"`r`n) else (`r`n  call `"$original`"`r`n)`r`n"
    if ((Test-Path $bridge) -and [IO.File]::ReadAllText($bridge) -cne $bridgeText) { throw 'Transition launcher already exists with different contents; review it.' }
    $null=Disable-ScheduledTask -TaskName $auto.TaskName -TaskPath '\'
    [IO.File]::WriteAllText($launcher,$updated,[Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($bridge,$bridgeText,[Text.UTF8Encoding]::new($false))
    $config.web_launcher=$bridge
    Save-AutomationState (Join-Path $AutomationDirectory 'config.json') $config
    Write-Host 'Prepared. Existing server continues running. Automatic deployment is paused. Publish only the completed, tested Kippy commit, then run Activate from this migration directory.'
    return
}
if ($Action -eq 'Activate') {
    if ($auto.State -ne 'Disabled' -or $config.web_launcher -ine $bridge) { throw 'Run Prepare first and keep automatic deployment paused.' }
    # The NEW updater is run from this directory, not from the old checkout.
    & "$PSScriptRoot\Update-MacroMellow.ps1" -Repository $Repository -AutomationDirectory $AutomationDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Activation failed; inspect deployment logs. Automatic deployment remains paused.' }
    Write-Host 'Activation passed. Verify Kippy through Tailscale, then run Finalize.'
    return
}
$version=Invoke-RestMethod 'http://127.0.0.1:8000/api/version' -TimeoutSec 5
$page=Invoke-WebRequest 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 5
$expected=(& git.exe -C $Repository rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $version.application -cne 'Kippy' -or $version.commit -cne $expected -or $page.StatusCode -ne 200 -or $page.Content -notmatch '<title>Kippy') {
    throw 'Kippy is not verified at the current commit. Nothing will be finalized.'
}
if ($config.web_launcher -ine $bridge -and $config.web_launcher -ine $launcher) { throw 'Unexpected launcher; refusing finalization.' }
$config.web_launcher=$launcher
Save-AutomationState (Join-Path $AutomationDirectory 'config.json') $config
# Only the specifically inspected nutrition backup task is obsolete.
$backup=Get-ScheduledTask -TaskName 'MacroMellow Backup' -TaskPath '\' -ErrorAction SilentlyContinue
if ($backup) {
    if (@($backup.Actions).Count -ne 1 -or $backup.Actions[0].Arguments -notmatch ('^"?' + [regex]::Escape((Join-Path $service 'backup-macromellow.py')) + '"?$')) {
        throw 'Backup task differs from the inspected task; review it manually. No task was disabled.'
    }
    $null=Disable-ScheduledTask -TaskName $backup.TaskName -TaskPath '\'
}
$null=Enable-ScheduledTask -TaskName $auto.TaskName -TaskPath '\'
Write-Host 'Kippy is active. Automatic deployment is enabled; the obsolete nutrition backup task is disabled. Review documented cleanup before deleting old files.'
