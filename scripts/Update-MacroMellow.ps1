#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Repository = 'C:\Users\tommy\MacroMellow',
    [switch]$CheckOnly,
    [switch]$Automatic,
    [string]$AutomationDirectory = 'C:\Users\tommy\MacroMellowService\automation'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. "$PSScriptRoot\windows\Common.ps1"

function Get-NativeText {
    param([string]$Executable, [string[]]$Arguments)
    # Windows PowerShell can wrap native stderr in ErrorRecords, even on success.
    $ErrorActionPreference = 'Continue'
    $lines = @(& $Executable @Arguments 2>&1)
    $nativeExit = $LASTEXITCODE
    $text = ($lines | ForEach-Object {
        if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message }
        elseif ($null -ne $_) { $_.ToString() }
    } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join "`n"
    if ($nativeExit -ne 0) { throw "$Executable failed (exit $nativeExit): $text" }
    return $text.Trim()
}

function Invoke-Native {
    param([string]$Executable, [string[]]$Arguments)
    $result = Get-NativeText $Executable $Arguments
    if ($result) { [Console]::WriteLine($result) }
}

function Get-RepositoryState {
    param([string]$Root, [string]$Git)
    $top = Get-NativeText $Git @('-C', $Root, 'rev-parse', '--show-toplevel')
    if ([IO.Path]::GetFullPath($top).TrimEnd('\') -ine $Root.TrimEnd('\')) {
        throw 'The deployment directory must be the Git repository root.'
    }
    $dirty = Get-NativeText $Git @('-C', $Root, 'status', '--porcelain=v1', '--untracked-files=all')
    if ($dirty) { throw "Uncommitted or untracked changes found. Commit, stash, or review them yourself; nothing will be overwritten.`n$dirty" }
    if ((Get-NativeText $Git @('-C', $Root, 'branch', '--show-current')) -cne 'main') {
        throw 'Deployment requires the main branch. No branch will be switched automatically.'
    }
    $origin = Get-NativeText $Git @('-C', $Root, 'remote', 'get-url', 'origin')
    if ($origin -notmatch '^(https://github\.com/Salami2222/(?:Kippy|MacroMellow)(?:\.git)?/?|git@github\.com:Salami2222/(?:Kippy|MacroMellow)(?:\.git)?|ssh://git@github\.com/Salami2222/(?:Kippy|MacroMellow)(?:\.git)?)$') {
        throw 'origin must point to Salami2222/Kippy on GitHub (the previous URL is accepted during migration).'
    }
    return Get-NativeText $Git @('-C', $Root, 'rev-parse', 'HEAD')
}

function Assert-IncomingPathsSafe {
    param([string]$Root, [string]$Git, [string]$Previous, [string]$Target)
    $changes = Get-NativeText $Git @('-C', $Root, 'diff', '--name-only', $Previous, $Target)
    foreach ($relative in @($changes -split "`n" | Where-Object { $_ })) {
        if ($relative.StartsWith('"')) { throw 'An incoming path requires Git escaping; review this update manually.' }
        if ($relative -match '^(data|\.venv|venv)/|\.(sqlite3?|db)(-(wal|shm))?$') {
            throw "Incoming commit changes protected data/environment path: $relative"
        }
    }
    $added = Get-NativeText $Git @('-C', $Root, 'diff', '--name-only', '--diff-filter=A', $Previous, $Target)
    foreach ($relative in @($added -split "`n" | Where-Object { $_ })) {
        if (Test-Path -LiteralPath (Join-Path $Root $relative)) {
            throw "Incoming file would collide with an existing local file: $relative"
        }
    }
}

function Get-WebListeners {
    # Query all listeners so an empty port is not confused with an access/query error.
    @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 8000 })
}

function Assert-MellowProcess {
    param($Snapshot, [string]$Python, [string]$BasePython, [string]$OwnerSid)
    if ($null -eq $Snapshot -or -not $Snapshot.ExecutablePath -or -not $Snapshot.CommandLine -or -not $Snapshot.CreationDate) {
        throw 'Cannot prove the identity of the port-8000 listener; refusing to terminate it.'
    }
    if ($Snapshot.ExecutablePath -ine $Python -and $Snapshot.ExecutablePath -ine $BasePython) {
        throw "Port 8000 belongs to another executable (PID $($Snapshot.ProcessId)); refusing to terminate it."
    }
    # The Windows venv launcher spawns a base-python child but preserves this argv[0].
    $pattern = '^\s*"?' + [regex]::Escape($Python) + '"?\s+-m\s+(?:kippy|macromellow)\s+web(?:\s|$)'
    if (-not [regex]::IsMatch($Snapshot.CommandLine, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        throw "Port 8000 is not the expected venv's application web process (PID $($Snapshot.ProcessId))."
    }
    $owner = Invoke-CimMethod -InputObject $Snapshot -MethodName GetOwnerSid -ErrorAction Stop
    if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $OwnerSid) {
        throw 'The port-8000 listener is owned by another user or ownership could not be verified.'
    }
}

function Get-VerifiedListeners {
    param([string]$Python, [string]$BasePython, [string]$OwnerSid)
    foreach ($listenerId in @(Get-WebListeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $snapshot = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerId" -ErrorAction Stop
        # A process that exited during the query is harmless; recheck the port later.
        if ($null -eq $snapshot) { continue }
        Assert-MellowProcess $snapshot $Python $BasePython $OwnerSid
        $snapshot
    }
}

function Stop-VerifiedProcess {
    param($Snapshot, [string]$Python, [string]$BasePython, [string]$OwnerSid)
    $processHandle = $null
    try {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Snapshot.ProcessId)" -ErrorAction Stop
        if ($null -eq $current) { return }
        if ($current.CreationDate -ne $Snapshot.CreationDate) { throw 'Listener PID was reused; refusing termination.' }
        Assert-MellowProcess $current $Python $BasePython $OwnerSid
        $processHandle = [Diagnostics.Process]::GetProcessById([int]$current.ProcessId)
        $null = $processHandle.Handle # Pin this process, rather than later killing by a reusable PID.
        if ([Math]::Abs(($processHandle.StartTime.ToUniversalTime() - $current.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 10) {
            throw 'Listener identity changed while opening its handle; refusing termination.'
        }
        if (-not $processHandle.HasExited) {
            [Console]::WriteLine("Stopping verified orphaned application listener PID $($current.ProcessId).")
            $processHandle.Kill()
            if (-not $processHandle.WaitForExit(5000)) { throw 'Verified orphan did not exit.' }
        }
    } finally {
        if ($null -ne $processHandle) { $processHandle.Dispose() }
    }
}

function Stop-MellowTask {
    param([string]$Python, [string]$BasePython, [string]$OwnerSid)
    $snapshots = @(Get-VerifiedListeners $Python $BasePython $OwnerSid)
    $parents = @()
    foreach ($snapshot in $snapshots) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($snapshot.ParentProcessId)"
        if ($parent -and $parent.CreationDate -le $snapshot.CreationDate -and $parent.ExecutablePath -ieq $Python) {
            Assert-MellowProcess $parent $Python $BasePython $OwnerSid
            $parents += $parent
        }
    }
    if ((Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop).State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $state = (Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop).State
        if ($state -notin @('Running', 'Queued')) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($state -in @('Running', 'Queued')) { throw 'Scheduled task did not stop in time.' }
    # Give children a short opportunity to exit before considering them orphaned.
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        if (@(Get-WebListeners).Count -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    foreach ($snapshot in @(Get-VerifiedListeners $Python $BasePython $OwnerSid)) {
        Stop-VerifiedProcess $snapshot $Python $BasePython $OwnerSid
    }
    foreach ($parent in $parents) { Stop-VerifiedProcess $parent $Python $BasePython $OwnerSid }
    if (@(Get-WebListeners).Count -ne 0) { throw 'Port 8000 is still occupied; scheduled task will not be started on top of it.' }
}

function Wait-MellowHealthy {
    param([string]$Python, [string]$BasePython, [string]$OwnerSid, [DateTime]$StartedAfter = [DateTime]::MinValue, [int]$TimeoutSeconds = 45, [string]$ExpectedCommit)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $consecutive = 0
    do {
        $listeners = @(Get-VerifiedListeners $Python $BasePython $OwnerSid)
        $running = (Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop).State -eq 'Running'
        if ($listeners.Count -gt 0 -and $running -and @($listeners | Where-Object { $_.CreationDate.ToUniversalTime() -lt $StartedAfter }).Count -eq 0) {
            try {
                $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 3 -ErrorAction Stop
                if ($response.StatusCode -eq 200 -and $response.Content -match '<title>(Kippy|MacroMellow)') {
                    if ($ExpectedCommit) { Assert-ReleaseEndpoints $ExpectedCommit $listeners }
                    $consecutive++
                    if ($consecutive -ge 2) { return }
                } else { $consecutive = 0 }
            } catch { $consecutive = 0 }
        } else { $consecutive = 0 }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Kippy did not become healthy (new verified listener, running task, and HTTP 200) within 45 seconds.'
}

function Invoke-DeploymentTests {
    param([string]$Root, [string]$Python, [string]$Node)
    Invoke-Native $Python @('-m', 'pip', 'check')
    Invoke-Native $Python @('-c', "import kippy.web")
    Invoke-Native $Python @('-m', 'unittest', 'discover', '-s', 'tests', '-q')
    if (Test-Path (Join-Path $Root 'package-lock.json')) {
        $npm = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop).Source
        Invoke-Native $npm @('ci','--prefix',$Root,'--ignore-scripts','--no-audit','--no-fund')
    }
    $javascriptTests = @(Get-ChildItem -LiteralPath (Join-Path $Root 'tests') -Filter 'test_*.cjs' -File | Sort-Object Name | ForEach-Object FullName)
    if ($javascriptTests.Count -eq 0) { throw 'JavaScript regression tests are missing.' }
    Invoke-Native $Node (@('--test') + $javascriptTests)
    foreach ($testName in @('Test-Deployment.ps1','Test-OllamaMonitor.ps1','Test-KippyMigration.ps1')) {
        $deploymentTests = Join-Path $Root ('tests\' + $testName)
        if (Test-Path -LiteralPath $deploymentTests) {
            Invoke-Native (Join-Path $PSHOME 'powershell.exe') @('-NoLogo','-NoProfile','-NonInteractive','-File',$deploymentTests)
        }
    }
}

function Restore-DeploymentCode {
    param([string]$Root, [string]$Git, [string]$Previous, [string]$Target)
    if (-not $Target -or $Previous -eq $Target) { return }
    $current = Get-RepositoryState $Root $Git
    if ($current -ne $Target) { throw 'HEAD changed during deployment; refusing automatic rollback.' }
    # --keep refuses conflicting edits. No hard reset, clean, stash, or data restore.
    Invoke-Native $Git @('-C', $Root, 'reset', '--keep', $Previous)
    [Console]::WriteLine("Restored previous code $($Previous.Substring(0, 12)). Installed dependencies are retained.")
}

function Assert-ReleaseEndpoints {
    param([string]$ExpectedCommit, $Listeners)
    $version = Invoke-RestMethod 'http://127.0.0.1:8000/api/version' -TimeoutSec 3
    if ($version.commit -cne $ExpectedCommit -or $version.pid -notin @($Listeners | ForEach-Object ProcessId)) {
        throw 'Stale or unexpected server: startup commit/PID does not match this deployment.'
    }
    foreach ($asset in @(@('/api/status','application/json'), @('/app.js','(application|text)/javascript'), @('/app.css','text/css'))) {
        $response = Invoke-WebRequest ('http://127.0.0.1:8000' + $asset[0]) -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 5
        if ($response.StatusCode -ne 200 -or $response.Headers['Content-Type'] -notmatch ('^' + $asset[1] + '(;|$)')) {
            throw "Release endpoint/MIME check failed: $($asset[0])"
        }
    }
}

function Get-InstalledPackages {
    param([string]$Python)
    # Windows PowerShell 5.1 removes embedded double quotes in native arguments.
    # Single quotes inside the Python program survive the native command line.
    return (Get-NativeText $Python @('-c',"import importlib.metadata,json; print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions()}))")) | ConvertFrom-Json
}

function Prepare-CandidateDependencies {
    param([string]$Candidate, [string]$Python, [string]$PreviousRoot)
    $requirements = Join-Path $Candidate 'requirements.txt'
    if (-not (Test-Path $requirements)) {
        if ((Test-Path (Join-Path $Candidate 'pyproject.toml')) -or (Test-Path (Join-Path $Candidate 'setup.py'))) {
            throw 'Unsupported dependency layout; review and supply requirements.txt.'
        }
        return $null
    }
    $report = Join-Path $Candidate 'dependency-plan.json'
    Invoke-Native $Python @('-m','pip','install','--dry-run','--report',$report,'--disable-pip-version-check','--no-input','-r',$requirements)
    $plan = Get-Content -Raw $report | ConvertFrom-Json
    $installed = Get-InstalledPackages $Python
    foreach ($package in $plan.install) {
        $name = $package.metadata.name.ToLower().Replace('_','-')
        if ($installed.PSObject.Properties.Name -contains $name) { throw "Dependency replacement ($name) requires a reviewed environment migration; existing packages are never upgraded automatically." }
    }
    if (@($plan.install).Count -eq 0) { return $null }
    # Isolate newly required packages from the serving process until tests pass.
    $overlay = Join-Path $Candidate 'candidate-packages'
    $packages = @($plan.install | ForEach-Object { $_.metadata.name + '==' + $_.metadata.version })
    Invoke-Native $Python (@('-m','pip','install','--target',$overlay,'--no-deps','--disable-pip-version-check','--no-input') + $packages)
    return @{overlay=$overlay;packages=$packages}
}

function Invoke-MacroMellowUpdate {
    param([string]$Root, [switch]$ValidateOnly, [switch]$Auto,
          [string]$LockDirectory = 'C:\Users\tommy\MacroMellowService\automation')
    $lock = $null; $candidate = $null; $git = $null
    $previousLocation = Get-Location
    $oldPythonPath = $env:PYTHONPATH
    $oldGitPrompt = $env:GIT_TERMINAL_PROMPT; $oldGcmInteractive = $env:GCM_INTERACTIVE
    $previousCommit = $null; $targetCommit = $null
    $codeAdvanced = $false; $serviceTouched = $false; $phase = 'preflight'
    $statePath = Join-Path $LockDirectory 'deployment-state.json'
    $state = @{}
    try {
        $Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
        $null = [IO.Directory]::CreateDirectory($LockDirectory)
        try { $lock = [IO.File]::Open((Join-Path $LockDirectory 'deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw 'Another deployment is running, or the lock cannot be opened.' }
        $state = Read-AutomationState $statePath
        Set-Location -LiteralPath $Root
        $git = (Get-Command git.exe -CommandType Application).Source
        $node = (Get-Command node.exe -CommandType Application).Source
        $python = Join-Path $Root '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Existing .venv\Scripts\python.exe is missing.' }
        $previousCommit = Get-RepositoryState $Root $git
        $environment = (Get-NativeText $python @('-c','import json,sys; print(json.dumps(dict(prefix=sys.prefix,base=sys._base_executable,version=list(sys.version_info[:2]))))')) | ConvertFrom-Json
        if ([IO.Path]::GetFullPath($environment.prefix).TrimEnd('\') -ine (Join-Path $Root '.venv')) { throw 'Unexpected virtual environment.' }
        if ($environment.version[0] -ne 3 -or $environment.version[1] -lt 10) { throw 'Python 3.10+ is required.' }
        if ((Get-NativeText $node @('--version')) -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 18) { throw 'Node.js 18+ is required.' }
        $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $basePython = $environment.base
        $task = Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\'
        if ($task.State -eq 'Disabled') { throw 'Web task is disabled; it will not be enabled automatically.' }
        $null = @(Get-VerifiedListeners $python $basePython $ownerSid)
        if ($ValidateOnly) {
            Invoke-DeploymentTests $Root $python $node
            Wait-MellowHealthy $python $basePython $ownerSid -ExpectedCommit $previousCommit
            Write-AutomationEvent $LockDirectory 'deploy' 'check_passed' @{commit=$previousCommit}
            return 0
        }
        $env:GIT_TERMINAL_PROMPT = '0'; $env:GCM_INTERACTIVE = 'Never'
        $phase = 'fetch'
        Invoke-Native $git @('-C',$Root,'fetch','--no-tags','origin','refs/heads/main:refs/remotes/origin/main')
        $targetCommit = Get-NativeText $git @('-C',$Root,'rev-parse','refs/remotes/origin/main')
        Write-AutomationEvent $LockDirectory 'deploy' 'commits' @{source=$previousCommit;target=$targetCommit}
        if ($Auto -and $state.ContainsKey('failed_commit') -and $state.failed_commit -eq $targetCommit) {
            Write-AutomationEvent $LockDirectory 'deploy' 'failed_release_held' @{target=$targetCommit;action='New commit or explicit mellow-update required'}
            return 1
        }
        if ($Auto -and $previousCommit -eq $targetCommit) {
            Wait-MellowHealthy $python $basePython $ownerSid -ExpectedCommit $targetCommit -TimeoutSeconds 5
            $state.last_good = $targetCommit; Save-AutomationState $statePath $state
            Write-AutomationEvent $LockDirectory 'deploy' 'unchanged_healthy' @{commit=$targetCommit}
            return 0
        }
        Invoke-Native $git @('-C',$Root,'merge-base','--is-ancestor',$previousCommit,$targetCommit)
        Assert-IncomingPathsSafe $Root $git $previousCommit $targetCommit
        $phase = 'staging_tests'
        $candidate = Join-Path $LockDirectory ('staging-' + [guid]::NewGuid().ToString('N'))
        Invoke-Native $git @('-C',$Root,'worktree','add','--detach',$candidate,$targetCommit)
        Set-Location $candidate
        $dependencies = Prepare-CandidateDependencies $candidate $python $Root
        $env:PYTHONPATH = if ($dependencies) { $candidate + ';' + $dependencies.overlay } else { $candidate }
        Invoke-DeploymentTests $candidate $python $node
        Write-AutomationEvent $LockDirectory 'deploy' 'tests_passed' @{target=$targetCommit}
        $env:PYTHONPATH = $oldPythonPath
        Set-Location $Root
        if ((Get-RepositoryState $Root $git) -ne $previousCommit) { throw 'Repository changed during staging.' }
        if ($dependencies) {
            Invoke-Native $python (@('-m','pip','install','--no-deps','--disable-pip-version-check','--no-input') + $dependencies.packages)
            Invoke-Native $python @('-m','pip','check')
        }
        $phase = 'promote'
        Invoke-Native $git @('-C',$Root,'merge','--ff-only','--no-autostash','--no-overwrite-ignore',$targetCommit)
        $codeAdvanced = $previousCommit -ne $targetCommit
        if ((Get-RepositoryState $Root $git) -ne $targetCommit) { throw 'Repository changed before restart.' }
        # Ollama diagnostics are independent of release availability.
        if (Test-Path (Join-Path $LockDirectory 'config.json')) {
            try {
                . "$PSScriptRoot\windows\Monitor-Ollama.ps1" -AutomationDirectory $LockDirectory
                $null = Invoke-OllamaMonitor -Directory $LockDirectory -EnsureStarted
            } catch { Write-AutomationEvent $LockDirectory 'deploy' 'ai_warning' @{error=$_.Exception.Message} }
        }
        $phase = 'restart'; $serviceTouched = $true
        Stop-MellowTask $python $basePython $ownerSid
        $startedAfter = [DateTime]::UtcNow
        Start-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\'
        Wait-MellowHealthy $python $basePython $ownerSid $startedAfter -ExpectedCommit $targetCommit
        Write-AutomationEvent $LockDirectory 'deploy' 'restart_health_passed' @{commit=$targetCommit;endpoints=@('/','/api/status','/api/version','/app.js','/app.css')}
        try {
            $ai = Invoke-RestMethod 'http://127.0.0.1:8000/api/ollama/status' -TimeoutSec 10
            Write-AutomationEvent $LockDirectory 'deploy' 'ai_status' $ai
        } catch { Write-AutomationEvent $LockDirectory 'deploy' 'ai_warning' @{error=$_.Exception.Message} }
        $state.last_good = $targetCommit; $state.failed_commit = $null
        Save-AutomationState $statePath $state
        Write-AutomationEvent $LockDirectory 'deploy' 'success' @{source=$previousCommit;target=$targetCommit;final=$targetCommit}
        return 0
    } catch {
        $failure = $_.Exception.Message
        Write-AutomationEvent $LockDirectory 'deploy' 'failed' @{phase=$phase;source=$previousCommit;target=$targetCommit;error=$failure;final=$(if ($serviceTouched) { 'unverified' } else { $previousCommit })}
        if ($lock -and $targetCommit -and -not $ValidateOnly) {
            $state.failed_commit = $targetCommit
            Save-AutomationState $statePath $state
        }
        if ($previousCommit -and -not $ValidateOnly -and ($codeAdvanced -or $serviceTouched)) {
            try {
                if ($serviceTouched) { Stop-MellowTask $python $basePython $ownerSid }
                if ($codeAdvanced) { Restore-DeploymentCode $Root $git $previousCommit $targetCommit }
                if ($serviceTouched) {
                    $recoveryStart = [DateTime]::UtcNow
                    Start-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\'
                    Wait-MellowHealthy $python $basePython $ownerSid $recoveryStart -ExpectedCommit $previousCommit
                    Write-AutomationEvent $LockDirectory 'deploy' 'recovered' @{final=$previousCommit}
                }
            } catch { Write-AutomationEvent $LockDirectory 'deploy' 'recovery_incomplete' @{error=$_.Exception.Message} }
        }
        return 1
    } finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:GIT_TERMINAL_PROMPT = $oldGitPrompt; $env:GCM_INTERACTIVE = $oldGcmInteractive
        Set-Location $previousLocation
        if ($candidate -and $git -and (Test-Path $candidate)) {
            try { Invoke-Native $git @('-C',$Root,'worktree','remove','--force',$candidate) }
            catch { Write-AutomationEvent $LockDirectory 'deploy' 'staging_cleanup_warning' @{path=$candidate;error=$_.Exception.Message} }
        }
        if ($null -ne $lock) { $lock.Dispose() }
    }
}
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-MacroMellowUpdate -Root $Repository -ValidateOnly:$CheckOnly -Auto:$Automatic -LockDirectory $AutomationDirectory)
}
