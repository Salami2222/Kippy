#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Repository = 'C:\Users\tommy\MacroMellow',
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

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
    if ($origin -notmatch '^(https://github\.com/Salami2222/MacroMellow(?:\.git)?/?|git@github\.com:Salami2222/MacroMellow(?:\.git)?|ssh://git@github\.com/Salami2222/MacroMellow(?:\.git)?)$') {
        throw 'origin must point to Salami2222/MacroMellow on GitHub.'
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
    $pattern = '^\s*"?' + [regex]::Escape($Python) + '"?\s+-m\s+macromellow\s+web(?:\s|$)'
    if (-not [regex]::IsMatch($Snapshot.CommandLine, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        throw "Port 8000 is not the expected venv's MacroMellow web process (PID $($Snapshot.ProcessId))."
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
            [Console]::WriteLine("Stopping verified orphaned MacroMellow listener PID $($current.ProcessId).")
            $processHandle.Kill()
            if (-not $processHandle.WaitForExit(5000)) { throw 'Verified orphan did not exit.' }
        }
    } finally {
        if ($null -ne $processHandle) { $processHandle.Dispose() }
    }
}

function Stop-MellowTask {
    param([string]$Python, [string]$BasePython, [string]$OwnerSid)
    $null = @(Get-VerifiedListeners $Python $BasePython $OwnerSid)
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
        if (@(Get-WebListeners).Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    foreach ($snapshot in @(Get-VerifiedListeners $Python $BasePython $OwnerSid)) {
        Stop-VerifiedProcess $snapshot $Python $BasePython $OwnerSid
    }
    if (@(Get-WebListeners).Count -ne 0) { throw 'Port 8000 is still occupied; scheduled task will not be started on top of it.' }
}

function Wait-MellowHealthy {
    param([string]$Python, [string]$BasePython, [string]$OwnerSid, [DateTime]$StartedAfter = [DateTime]::MinValue, [int]$TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $consecutive = 0
    do {
        $listeners = @(Get-VerifiedListeners $Python $BasePython $OwnerSid)
        $running = (Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop).State -eq 'Running'
        if ($listeners.Count -gt 0 -and $running -and @($listeners | Where-Object { $_.CreationDate.ToUniversalTime() -lt $StartedAfter }).Count -eq 0) {
            try {
                $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 3 -ErrorAction Stop
                if ($response.StatusCode -eq 200 -and $response.Content -match '<title>MacroMellow') {
                    $consecutive++
                    if ($consecutive -ge 2) { return }
                } else { $consecutive = 0 }
            } catch { $consecutive = 0 }
        } else { $consecutive = 0 }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'MacroMellow did not become healthy (new verified listener, running task, and HTTP 200) within 45 seconds.'
}

function Invoke-DeploymentTests {
    param([string]$Root, [string]$Python, [string]$Node)
    Invoke-Native $Python @('-m', 'pip', 'check')
    Invoke-Native $Python @('-c', "from zoneinfo import ZoneInfo; ZoneInfo('America/New_York'); import macromellow.web")
    Invoke-Native $Python @('-m', 'unittest', 'discover', '-s', 'tests', '-q')
    $javascriptTests = @(Get-ChildItem -LiteralPath (Join-Path $Root 'tests') -Filter 'test_*.cjs' -File | Sort-Object Name | ForEach-Object FullName)
    if ($javascriptTests.Count -eq 0) { throw 'JavaScript regression tests are missing.' }
    Invoke-Native $Node (@('--test') + $javascriptTests)
    $deploymentTests = Join-Path $Root 'tests\Test-Deployment.ps1'
    if (Test-Path -LiteralPath $deploymentTests) {
        Invoke-Native (Join-Path $PSHOME 'powershell.exe') @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $deploymentTests)
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

function Invoke-MacroMellowUpdate {
    param([string]$Root, [switch]$ValidateOnly,
          [string]$LockDirectory = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'MacroMellow'))
    $lock = $null
    $previousLocation = Get-Location
    $previousCommit = $null
    $targetCommit = $null
    $codeAdvanced = $false
    $serviceTouched = $false
    try {
        $Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
        $null = [IO.Directory]::CreateDirectory($LockDirectory)
        try {
            $lock = [IO.File]::Open((Join-Path $LockDirectory 'deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        } catch { throw 'Another deployment is running, or the deployment lock cannot be opened. No update started.' }
        Set-Location -LiteralPath $Root
        $git = (Get-Command git.exe -CommandType Application -ErrorAction Stop).Source
        $node = (Get-Command node.exe -CommandType Application -ErrorAction Stop).Source
        $python = Join-Path $Root '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Existing .venv\Scripts\python.exe is missing. The updater never creates or replaces the environment.' }
        $previousCommit = Get-RepositoryState $Root $git
        $environment = (Get-NativeText $python @('-c', 'import json,sys; print(json.dumps(dict(prefix=sys.prefix,base=sys._base_executable,version=list(sys.version_info[:2]))))')) | ConvertFrom-Json
        if ([IO.Path]::GetFullPath($environment.prefix).TrimEnd('\') -ine (Join-Path $Root '.venv')) { throw 'Python is not using the expected existing virtual environment.' }
        if ($environment.version[0] -lt 3 -or ($environment.version[0] -eq 3 -and $environment.version[1] -lt 10)) { throw 'Python 3.10+ is required.' }
        if ((Get-NativeText $node @('--version')) -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 18) { throw 'Node.js 18+ is required for the regression tests.' }
        $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $basePython = $environment.base
        $task = Get-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop
        if ($task.State -eq 'Disabled') { throw 'The existing scheduled task is disabled; it will not be modified or enabled.' }
        $null = @(Get-VerifiedListeners $python $basePython $ownerSid)
        [Console]::WriteLine("Preflight OK: clean main, existing venv, Node, task, and listener identity. HEAD $($previousCommit.Substring(0, 12)).")

        if (-not $ValidateOnly) {
            [Console]::WriteLine('Fetching GitHub main and pulling it with a fast-forward-only merge.')
            Invoke-Native $git @('-C', $Root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
            $targetCommit = Get-NativeText $git @('-C', $Root, 'rev-parse', 'refs/remotes/origin/main')
            Invoke-Native $git @('-C', $Root, 'merge-base', '--is-ancestor', $previousCommit, $targetCommit)
            Assert-IncomingPathsSafe $Root $git $previousCommit $targetCommit
            $null = Get-RepositoryState $Root $git
            Invoke-Native $git @('-C', $Root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', $targetCommit)
            $codeAdvanced = $previousCommit -ne $targetCommit
            if (Test-Path -LiteralPath (Join-Path $Root 'requirements.txt')) {
                [Console]::WriteLine('Checking/installing declared requirements in the existing venv (no blanket upgrade).')
                Invoke-Native $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-r', 'requirements.txt')
            } elseif ((Test-Path -LiteralPath 'pyproject.toml') -or (Test-Path -LiteralPath 'setup.py')) {
                throw 'This dependency layout is not supported by the updater yet. Add a reviewed requirements.txt before deploying.'
            }
        }
        [Console]::WriteLine('Running environment checks and regression tests before any service stop.')
        Invoke-DeploymentTests $Root $python $node
        if ($ValidateOnly) {
            Wait-MellowHealthy $python $basePython $ownerSid
            [Console]::WriteLine('CHECK PASSED: tests and HTTP 200 verified. No fetch, install, task stop/restart, or data changes performed.')
            return 0
        }
        if ((Get-RepositoryState $Root $git) -ne $targetCommit) { throw 'Repository changed during testing; refusing restart.' }
        [Console]::WriteLine('Stopping the existing scheduled task; checking for verified orphan listeners.')
        $serviceTouched = $true
        Stop-MellowTask $python $basePython $ownerSid
        $startedAfter = [DateTime]::UtcNow
        Start-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop
        Wait-MellowHealthy $python $basePython $ownerSid $startedAfter
        [Console]::WriteLine("SUCCESS: deployed $($targetCommit.Substring(0, 12)); \MacroMellow Web is running; http://127.0.0.1:8000/ returned HTTP 200.")
        return 0
    } catch {
        [Console]::WriteLine("FAILURE: $($_.Exception.Message)")
        if ($previousCommit -and -not $ValidateOnly) {
            try {
                if ($serviceTouched) { Stop-MellowTask $python $basePython $ownerSid }
                if ($codeAdvanced) { Restore-DeploymentCode $Root $git $previousCommit $targetCommit }
                if ($serviceTouched) {
                    $recoveryStart = [DateTime]::UtcNow
                    Start-ScheduledTask -TaskName 'MacroMellow Web' -TaskPath '\' -ErrorAction Stop
                    Wait-MellowHealthy $python $basePython $ownerSid $recoveryStart
                    [Console]::WriteLine('RECOVERY: previous code restarted and HTTP 200 verified. Deployment still failed (exit 1).')
                } else { [Console]::WriteLine('The existing service was not stopped. Previous code restored when safe.') }
            } catch { [Console]::WriteLine("RECOVERY INCOMPLETE: $($_.Exception.Message). Inspect the task and existing service logs before retrying.") }
        }
        [Console]::WriteLine('The updater never deletes or restores application data, or changes task credentials, SSH settings, or Tailscale settings.')
        return 1
    } finally {
        Set-Location $previousLocation
        if ($null -ne $lock) { $lock.Dispose() }
    }
}

# Dot-sourcing loads only functions for isolated tests; streamed SSH execution runs main.
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-MacroMellowUpdate -Root $Repository -ValidateOnly:$CheckOnly)
}
