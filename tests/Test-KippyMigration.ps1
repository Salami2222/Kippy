#requires -Version 5.1
# All task and HTTP operations are mocked. Files exist only under this test's unique TEMP directory.
$ErrorActionPreference='Stop'
$script:passed=0; $script:failed=0
$script:source=Join-Path $PSScriptRoot '..\scripts\Prepare-KippyMigration.ps1'
$script:scratch=Join-Path $env:TEMP ('kippy-migration-tests-'+[guid]::NewGuid().ToString('N'))
function Assert($value) { if(-not $value){throw 'Assertion failed'} }
function Case($name,$test) {
 try {& $test; $script:passed++;Write-Output "PASS: $name"}catch{$script:failed++;Write-Output "FAIL: $name -- $($_.Exception.Message)"}
}
function Setup {
 $root=Join-Path $script:scratch ([guid]::NewGuid().ToString('N'))
 $service=Join-Path $root 'service';$directory=Join-Path $service 'automation'
 $null=New-Item -ItemType Directory -Force $directory
 $launcher=Join-Path $service 'start-macromellow.cmd'
 [IO.File]::WriteAllText($launcher,"@echo off`r`nset `"DATA=%USERPROFILE%\MacroMellowData`"`r`nset `"CUSTOM_GPU_SETTING=preserve-me`"`r`npython -m macromellow web --port 8000 --data-dir `"%DATA%`"`r`n")
 $config=@{repository=$root;owner_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;web_launcher=$launcher}
 $config|ConvertTo-Json|Set-Content (Join-Path $directory 'config.json') -Encoding UTF8
 $global:MigrationTestState='Ready';$global:MigrationTestCalls=@()
 return @{root=$root;directory=$directory;service=$service}
}
function Get-ScheduledTask {
 param($TaskName,$TaskPath)
 if($TaskName -eq 'MacroMellow Auto Deploy'){return [pscustomobject]@{TaskName=$TaskName;State=$global:MigrationTestState}}
 return $null
}
function Disable-ScheduledTask {param($TaskName,$TaskPath);$global:MigrationTestCalls+=('disable:'+ $TaskName);$global:MigrationTestState='Disabled'}
function Enable-ScheduledTask {param($TaskName,$TaskPath);$global:MigrationTestCalls+=('enable:'+ $TaskName)}
function Invoke-RestMethod {param($Uri,$TimeoutSec);return @{application='Kippy';commit=('a'*40)}}
function Invoke-WebRequest {param($Uri,$TimeoutSec,[switch]$UseBasicParsing);return @{StatusCode=200;Content='<title>Kippy</title>'}}
function git.exe {$global:LASTEXITCODE=0;return ('a'*40)}
try {
 Case 'prepare preserves environment and is idempotent without restarting tasks' {
   $f=Setup
   & $script:source -Action Prepare -Repository $f.root -AutomationDirectory $f.directory
   $text=[IO.File]::ReadAllText((Join-Path $f.service 'start-kippy.cmd'))
   Assert ($text.Contains('CUSTOM_GPU_SETTING=preserve-me') -and $text.Contains('-m kippy web') -and $text.Contains('%USERPROFILE%\KippyData'))
   & $script:source -Action Prepare -Repository $f.root -AutomationDirectory $f.directory
   Assert (@($global:MigrationTestCalls|Where-Object {$_ -ne 'disable:MacroMellow Auto Deploy'}).Count -eq 0)
 }
 Case 'running deployment refuses preparation without changing tasks' {
   $f=Setup;$global:MigrationTestState='Running';$caught=$false
   try{& $script:source -Action Prepare -Repository $f.root -AutomationDirectory $f.directory}catch{$caught=$true}
   Assert ($caught -and $global:MigrationTestCalls.Count -eq 0)
 }
 Case 'unfamiliar launcher refuses preparation without changing tasks' {
   $f=Setup;$path=Join-Path $f.directory 'config.json';$c=Get-Content $path -Raw|ConvertFrom-Json;$c.web_launcher='C:\unrelated.cmd';$c|ConvertTo-Json|Set-Content $path -Encoding UTF8;$caught=$false
   try{& $script:source -Action Prepare -Repository $f.root -AutomationDirectory $f.directory}catch{$caught=$true}
   Assert ($caught -and $global:MigrationTestCalls.Count -eq 0)
 }
 Case 'activation requires preparation and leaves unprepared tasks alone' {
   $f=Setup;$caught=$false
   try{& $script:source -Action Activate -Repository $f.root -AutomationDirectory $f.directory}catch{$caught=$true}
   Assert ($caught -and $global:MigrationTestCalls.Count -eq 0)
 }
 Case 'finalization requires Kippy health and sets the direct launcher before enabling deployment' {
   $f=Setup
   & $script:source -Action Prepare -Repository $f.root -AutomationDirectory $f.directory
   & $script:source -Action Finalize -Repository $f.root -AutomationDirectory $f.directory
   $c=Get-Content (Join-Path $f.directory 'config.json') -Raw|ConvertFrom-Json
   Assert ($c.web_launcher -eq (Join-Path $f.service 'start-kippy.cmd'))
   Assert ($global:MigrationTestCalls[-1] -eq 'enable:MacroMellow Auto Deploy')
 }
} finally {if(Test-Path $script:scratch){Remove-Item -LiteralPath $script:scratch -Recurse -Force}}
Write-Output "Kippy migration tests: $script:passed passed, $script:failed failed (no real tasks changed)."
if($script:failed){exit 1}
