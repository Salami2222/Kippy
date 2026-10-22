#requires -Version 5.1
param([string]$AutomationDirectory = 'C:\Users\tommy\MacroMellowService\automation')
. "$PSScriptRoot\windows\Common.ps1"
$config = Get-AutomationConfig $AutomationDirectory
& "$PSScriptRoot\Update-MacroMellow.ps1" -Repository $config.repository -Automatic -AutomationDirectory $AutomationDirectory
exit $LASTEXITCODE
