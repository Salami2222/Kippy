#requires -Version 5.1
# No Pester dependency. All service/process operations are mocked; Git uses temp repos.
$ErrorActionPreference = 'Stop'
$script:Updater = Join-Path $PSScriptRoot '..\scripts\Update-MacroMellow.ps1'
$script:Passed = 0
$script:Failed = 0
$script:Scratch = Join-Path ([IO.Path]::GetTempPath()) ('kippy-deploy-tests-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $script:Scratch

function Assert-True($Value, [string]$Message = 'Assertion failed') {
    if (-not $Value) { throw $Message }
}
function Assert-Throws([scriptblock]$Body) {
    $threw = $false
    try { & $Body } catch { $threw = $true }
    Assert-True $threw 'Expected operation to refuse/fail.'
}
function Case([string]$Name, [scriptblock]$Body) {
    $console = [Console]::Out
    $captured = New-Object IO.StringWriter
    $succeeded = $false
    $failure = ''
    try {
        [Console]::SetOut($captured)
        . $script:Updater # Fresh functions in this case scope; never invokes updater main.
        & $Body
        $script:Passed++
        $succeeded = $true
    } catch {
        $script:Failed++
        $failure = $_.Exception.Message
    } finally {
        [Console]::SetOut($console)
    }
    if ($succeeded) { [Console]::WriteLine("PASS: $Name") }
    else {
        [Console]::WriteLine("FAIL: $Name -- $failure")
        [Console]::WriteLine($captured.ToString())
    }
    $captured.Dispose()
}
function New-TestRepository {
    $root = Join-Path $script:Scratch ([guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $root
    $git = (Get-Command git.exe -CommandType Application).Source
    $null = Get-NativeText $git @('-C', $root, 'init', '-b', 'main')
    $null = Get-NativeText $git @('-C', $root, 'config', 'user.name', 'Deployment Tests')
    $null = Get-NativeText $git @('-C', $root, 'config', 'user.email', 'deployment@example.invalid')
    $null = Get-NativeText $git @('-C', $root, 'remote', 'add', 'origin', 'https://github.com/Salami2222/Kippy.git')
    [IO.File]::WriteAllText((Join-Path $root 'app.txt'), 'initial')
    $null = Get-NativeText $git @('-C', $root, 'add', 'app.txt')
    $null = Get-NativeText $git @('-C', $root, 'commit', '-m', 'initial')
    return @{Root = $root; Git = $git}
}

try {
    Case 'clean repository accepted, tracked and untracked edits refused without modification' {
        $repo = New-TestRepository
        Assert-True ((Get-RepositoryState $repo.Root $repo.Git).Length -eq 40)
        [IO.File]::WriteAllText((Join-Path $repo.Root 'app.txt'), 'local edit')
        Assert-Throws { Get-RepositoryState $repo.Root $repo.Git }
        Assert-True ([IO.File]::ReadAllText((Join-Path $repo.Root 'app.txt')) -eq 'local edit')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'restore', 'app.txt')
        [IO.File]::WriteAllText((Join-Path $repo.Root 'untracked.txt'), 'keep')
        Assert-Throws { Get-RepositoryState $repo.Root $repo.Git }
    }
    Case 'other branch and other origin refused' {
        $repo = New-TestRepository
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', '-b', 'work')
        Assert-Throws { Get-RepositoryState $repo.Root $repo.Git }
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', 'main')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'remote', 'set-url', 'origin', 'https://example.invalid/repo')
        Assert-Throws { Get-RepositoryState $repo.Root $repo.Git }
    }
    Case 'divergent commits cannot be deployed with a fast-forward merge' {
        $repo = New-TestRepository
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', '-b', 'incoming')
        [IO.File]::WriteAllText((Join-Path $repo.Root 'incoming.txt'), 'incoming')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'incoming.txt')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'incoming')
        $target = Get-NativeText $repo.Git @('-C', $repo.Root, 'rev-parse', 'HEAD')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', 'main')
        [IO.File]::WriteAllText((Join-Path $repo.Root 'local.txt'), 'local')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'local.txt')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'local')
        $before = Get-RepositoryState $repo.Root $repo.Git
        Assert-Throws { Invoke-Native $repo.Git @('-C', $repo.Root, 'merge', '--ff-only', '--no-overwrite-ignore', $target) }
        Assert-True ((Get-RepositoryState $repo.Root $repo.Git) -eq $before)
    }
    Case 'fast-forward and guarded rollback preserve ignored database bytes' {
        $repo = New-TestRepository
        $before = Get-RepositoryState $repo.Root $repo.Git
        [IO.File]::WriteAllText((Join-Path $repo.Root '.git\info\exclude'), 'data/')
        $null = New-Item -ItemType Directory -Path (Join-Path $repo.Root 'data')
        $database = Join-Path $repo.Root 'data\kippy.sqlite3'
        [IO.File]::WriteAllText($database, 'local database bytes')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', '-b', 'incoming')
        [IO.File]::WriteAllText((Join-Path $repo.Root 'app.txt'), 'updated code')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'app.txt')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'updated code')
        $target = Get-NativeText $repo.Git @('-C', $repo.Root, 'rev-parse', 'HEAD')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'checkout', 'main')
        Assert-IncomingPathsSafe $repo.Root $repo.Git $before $target
        Invoke-Native $repo.Git @('-C', $repo.Root, 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', $target)
        Assert-True ((Get-RepositoryState $repo.Root $repo.Git) -eq $target)
        Restore-DeploymentCode $repo.Root $repo.Git $before $target
        Assert-True ((Get-RepositoryState $repo.Root $repo.Git) -eq $before)
        Assert-True ([IO.File]::ReadAllText($database) -eq 'local database bytes')
    }
    Case 'incoming data/database paths and ignored-file collisions refused' {
        $repo = New-TestRepository
        $before = Get-RepositoryState $repo.Root $repo.Git
        [IO.File]::WriteAllText((Join-Path $repo.Root 'example.sqlite3'), 'database fixture')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'example.sqlite3')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'protected path')
        $target = Get-RepositoryState $repo.Root $repo.Git
        Assert-Throws { Assert-IncomingPathsSafe $repo.Root $repo.Git $before $target }
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'reset', '--keep', $before)
        [IO.File]::WriteAllText((Join-Path $repo.Root 'collision.txt'), 'incoming')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'collision.txt')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'new file')
        $target = Get-RepositoryState $repo.Root $repo.Git
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'reset', '--keep', $before)
        [IO.File]::WriteAllText((Join-Path $repo.Root '.git\info\exclude'), 'collision.txt')
        [IO.File]::WriteAllText((Join-Path $repo.Root 'collision.txt'), 'local ignored file')
        Assert-Throws { Assert-IncomingPathsSafe $repo.Root $repo.Git $before $target }
        Assert-True ([IO.File]::ReadAllText((Join-Path $repo.Root 'collision.txt')) -eq 'local ignored file')
    }
    Case 'rollback refuses concurrent local edits' {
        $repo = New-TestRepository
        $before = Get-RepositoryState $repo.Root $repo.Git
        [IO.File]::WriteAllText((Join-Path $repo.Root 'app.txt'), 'incoming')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'add', 'app.txt')
        $null = Get-NativeText $repo.Git @('-C', $repo.Root, 'commit', '-m', 'incoming')
        $target = Get-RepositoryState $repo.Root $repo.Git
        [IO.File]::WriteAllText((Join-Path $repo.Root 'app.txt'), 'concurrent edit')
        Assert-Throws { Restore-DeploymentCode $repo.Root $repo.Git $before $target }
        Assert-True ([IO.File]::ReadAllText((Join-Path $repo.Root 'app.txt')) -eq 'concurrent edit')
        Assert-True ((Get-NativeText $repo.Git @('-C', $repo.Root, 'rev-parse', 'HEAD')) -eq $target)
    }
    Case 'orphan proof accepts venv base child but refuses wrong module, executable, and owner' {
        $python = 'C:\app\.venv\Scripts\python.exe'
        $base = 'C:\Python313\python.exe'
        $snapshot = [pscustomobject]@{ProcessId = 123; ExecutablePath = $base; CommandLine = '"C:\app\.venv\Scripts\python.exe" -m macromellow web --port 8000'; CreationDate = [DateTime]::Now}
        function Invoke-CimMethod { [pscustomobject]@{ReturnValue = 0; Sid = 'expected'} }
        Assert-MellowProcess $snapshot $python $base 'expected'
        $snapshot.CommandLine = '"C:\app\.venv\Scripts\python.exe" -m kippy web --port 8000'
        Assert-MellowProcess $snapshot $python $base 'expected'
        $snapshot.CommandLine = '"C:\app\.venv\Scripts\python.exe" -m unrelated web --port 8000'
        Assert-Throws { Assert-MellowProcess $snapshot $python $base 'expected' }
        $snapshot.CommandLine = '"C:\app\.venv\Scripts\python.exe" -m macromellow web --port 8000'
        $snapshot.ExecutablePath = 'C:\other\python.exe'
        Assert-Throws { Assert-MellowProcess $snapshot $python $base 'expected' }
        $snapshot.ExecutablePath = $base
        Assert-Throws { Assert-MellowProcess $snapshot $python $base 'different-owner' }
    }
    Case 'reused PID is refused before opening or killing a process' {
        $old = [pscustomobject]@{ProcessId = 123; CreationDate = [DateTime]::Now.AddHours(-1)}
        function Get-CimInstance { [pscustomobject]@{CreationDate = [DateTime]::Now} }
        Assert-Throws { Stop-VerifiedProcess $old 'unused' 'unused' 'unused' }
    }
    Case 'unknown listener aborts before scheduled task stop' {
        function Get-VerifiedListeners { throw 'unrelated listener' }
        function Stop-ScheduledTask { throw 'Must never be reached' }
        Assert-Throws { Stop-MellowTask 'unused' 'unused' 'unused' }
    }
    Case 'already stopped task and empty port need no kill or stop command' {
        function Get-VerifiedListeners { @() }
        function Get-WebListeners { @() }
        function Get-ScheduledTask { [pscustomobject]@{State = 'Ready'} }
        function Stop-ScheduledTask { throw 'Not idempotent' }
        function Stop-VerifiedProcess { throw 'Must never kill' }
        Stop-MellowTask 'unused' 'unused' 'unused'
    }
    Case 'health requires running task, fresh verified listener and two HTTP-200 responses' {
        $script:HttpCalls = 0
        function Get-VerifiedListeners { [pscustomobject]@{CreationDate = [DateTime]::Now} }
        function Get-ScheduledTask { [pscustomobject]@{State = 'Running'} }
        function Invoke-WebRequest { $script:HttpCalls++; [pscustomobject]@{StatusCode = 200; Content = '<title>Kippy</title>'} }
        function Start-Sleep {}
        Wait-MellowHealthy 'unused' 'unused' 'unused'
        Assert-True ($script:HttpCalls -eq 2)
        function Get-VerifiedListeners { [pscustomobject]@{CreationDate = [DateTime]::Now.AddHours(-1)} }
        Assert-Throws { Wait-MellowHealthy 'unused' 'unused' 'unused' -StartedAfter ([DateTime]::UtcNow) -TimeoutSeconds 0 }
        Assert-True ($script:HttpCalls -eq 2) 'Old listener must not satisfy a new deployment health check.'
    }
    Case 'HTTP errors and redirects cannot report success' {
        function Get-VerifiedListeners { [pscustomobject]@{CreationDate = [DateTime]::Now} }
        function Get-ScheduledTask { [pscustomobject]@{State = 'Running'} }
        function Invoke-WebRequest { [pscustomobject]@{StatusCode = 302; Content = 'redirect'} }
        function Start-Sleep {}
        Assert-Throws { Wait-MellowHealthy 'unused' 'unused' 'unused' -TimeoutSeconds 0 }
        function Invoke-WebRequest { throw 'Connection refused' }
        Assert-Throws { Wait-MellowHealthy 'unused' 'unused' 'unused' -TimeoutSeconds 0 }
    }

    foreach ($scenario in @('dirty-checkout', 'deps-fail', 'tests-fail', 'success', 'startup-fails', 'recovery-fails', 'check-only', 'unchanged', 'held-release')) {
        Case "deployment orchestration: $scenario" {
            $root = Join-Path $script:Scratch ([guid]::NewGuid().ToString('N'))
            $null = New-Item -ItemType Directory -Path (Join-Path $root '.venv\Scripts') -Force
            $null = New-Item -ItemType File -Path (Join-Path $root '.venv\Scripts\python.exe')
            if ($scenario -eq 'deps-fail') { [IO.File]::WriteAllText((Join-Path $root 'requirements.txt'), 'test-only') }
            $lockDirectory = Join-Path $root 'lock'
            if ($scenario -eq 'held-release') {
                $null = New-Item -ItemType Directory -Path $lockDirectory
                Save-AutomationState (Join-Path $lockDirectory 'deployment-state.json') @{failed_commit=('b' * 40)}
            }
            $script:FakeRoot = $root
            $script:Commit = ('a' * 40)
            $script:Stops = 0; $script:Starts = 0; $script:Restores = 0; $script:HealthChecks = 0
            $script:Events = @()
            function Get-RepositoryState {
                if ($scenario -eq 'dirty-checkout') { throw 'Uncommitted changes' }
                $script:Commit
            }
            function Get-NativeText {
                param($Executable, $Arguments)
                if ($Arguments[0] -ceq '-c') { return (@{prefix = (Join-Path $script:FakeRoot '.venv'); base = 'C:\Python313\python.exe'; version = @(3,13)} | ConvertTo-Json -Compress) }
                if ($Arguments[0] -eq '--version') { return 'v22.0.0' }
                if ($scenario -eq 'unchanged') { return ('a' * 40) }; return ('b' * 40)
            }
            function Invoke-Native {
                param($Executable, $Arguments)
                $script:Events += $Arguments[2]
                if ($Arguments[2] -eq 'worktree' -and $Arguments[3] -eq 'add') { $null = New-Item -ItemType Directory -Path $Arguments[5] }
                if ($Arguments[2] -eq 'merge') { $script:Commit = ('b' * 40) }
                if ($scenario -eq 'deps-fail' -and $Arguments[2] -eq 'install') { throw 'Dependency installation failed' }
            }
            function Prepare-CandidateDependencies {
                if ($scenario -eq 'deps-fail') { throw 'Dependency preparation failed' }
                return $null
            }
            function Assert-IncomingPathsSafe {}
            function Invoke-RestMethod { @{connected=$false;model_available=$false} }
            function Get-ScheduledTask { [pscustomobject]@{State = 'Running'} }
            function Get-VerifiedListeners { @() }
            function Invoke-DeploymentTests {
                $script:Events += 'tests'
                if ($scenario -eq 'tests-fail') { throw 'Test gate failed' }
            }
            function Stop-MellowTask { $script:Stops++; $script:Events += 'stop' }
            function Start-ScheduledTask { $script:Starts++; $script:Events += 'start' }
            function Wait-MellowHealthy {
                $script:HealthChecks++
                if ($scenario -eq 'recovery-fails') { throw 'Still unhealthy' }
                if ($scenario -eq 'startup-fails' -and $script:HealthChecks -eq 1) { throw 'New version unhealthy' }
            }
            function Restore-DeploymentCode { $script:Restores++; $script:Commit = ('a' * 40) }
            $result = Invoke-MacroMellowUpdate -Root $root -ValidateOnly:($scenario -eq 'check-only') -LockDirectory $lockDirectory -Auto:($scenario -in @('unchanged','held-release'))
            if ($scenario -in @('unchanged','held-release')) {
                Assert-True ($script:Stops -eq 0 -and $script:Starts -eq 0)
                Assert-True ('tests' -notin $script:Events)
                Assert-True ($result -eq $(if ($scenario -eq 'held-release') { 1 } else { 0 }))
            } elseif ($scenario -eq 'dirty-checkout') {
                Assert-True ($result -eq 1 -and $script:Stops -eq 0 -and $script:Starts -eq 0 -and $script:Restores -eq 0 -and $script:Events.Count -eq 0)
            } elseif ($scenario -in @('tests-fail', 'deps-fail')) {
                Assert-True ($result -eq 1 -and $script:Stops -eq 0 -and $script:Starts -eq 0 -and $script:Restores -eq 0)
            } elseif ($scenario -in @('startup-fails', 'recovery-fails')) {
                Assert-True ($result -eq 1 -and $script:Stops -eq 2 -and $script:Starts -eq 2 -and $script:Restores -eq 1 -and $script:HealthChecks -eq 2)
            } elseif ($scenario -eq 'check-only') {
                Assert-True ($result -eq 0 -and $script:Stops -eq 0 -and $script:Starts -eq 0 -and $script:Restores -eq 0)
                Assert-True (($script:Events -join ',') -eq 'tests')
            } else {
                Assert-True ($result -eq 0 -and $script:Stops -eq 1 -and $script:Starts -eq 1)
                Assert-True ([array]::IndexOf($script:Events, 'tests') -lt [array]::IndexOf($script:Events, 'stop'))
                # A repeated deployment follows the same tested path, without extra process starts.
                $result = Invoke-MacroMellowUpdate -Root $root -LockDirectory (Join-Path $root 'lock')
                Assert-True ($result -eq 0 -and $script:Stops -eq 2 -and $script:Starts -eq 2)
            }
        }
    }
    Case 'dependency staging pins only new packages and refuses installed-package replacement' {
        $dir=Join-Path $script:Scratch 'dependency-plan'
        $null=New-Item -ItemType Directory $dir
        [IO.File]::WriteAllText((Join-Path $dir 'requirements.txt'),'added>=2')
        $script:Replacing=$false; $script:InstallCalls=0
        function Get-NativeText { '{"present":"1.0"}' }
        function Invoke-Native {
            param($Executable,$Arguments)
            if ($Arguments -contains '--dry-run') {
                $report=$Arguments[[array]::IndexOf($Arguments,'--report')+1]
                $name=if($script:Replacing){'present'}else{'added'}
                @{install=@(@{metadata=@{name=$name;version='2.0'}})} | ConvertTo-Json -Depth 5 | Set-Content $report
            } else {
                $script:InstallCalls++
                Assert-True ($Arguments -contains '--no-deps' -and $Arguments -contains 'added==2.0')
            }
        }
        $result=Prepare-CandidateDependencies $dir 'unused' 'unused'
        Assert-True ($result.packages[0] -eq 'added==2.0' -and $script:InstallCalls -eq 1)
        $script:Replacing=$true
        Assert-Throws { Prepare-CandidateDependencies $dir 'unused' 'unused' }
        Assert-True ($script:InstallCalls -eq 1)
    }
    Case 'release identity and all asset MIME types are required; AI is not a release gate' {
        $script:Expected = 'a' * 40; $script:WrongCommit = $false; $script:WrongMime = $false
        function Invoke-RestMethod { @{commit=$(if ($script:WrongCommit) {'b' * 40} else {$script:Expected});pid=123} }
        function Invoke-WebRequest {
            param($Uri)
            if ($Uri -match 'ollama') { throw 'AI must not be checked here' }
            $mime = if ($Uri -match 'app.css') {'text/css'} elseif ($Uri -match 'app.js') {'text/javascript'} else {'application/json'}
            if ($script:WrongMime) {$mime='text/html'}
            @{StatusCode=200;Headers=@{'Content-Type'=$mime}}
        }
        Assert-ReleaseEndpoints $script:Expected @(@{ProcessId=123})
        $script:WrongCommit=$true
        Assert-Throws { Assert-ReleaseEndpoints $script:Expected @(@{ProcessId=123}) }
        $script:WrongCommit=$false
        Assert-Throws { Assert-ReleaseEndpoints $script:Expected @(@{ProcessId=999}) }
        $script:WrongMime=$true
        Assert-Throws { Assert-ReleaseEndpoints $script:Expected @(@{ProcessId=123}) }
    }
    Case 'concurrent deployment lock refuses work' {
        $lockDir = Join-Path $script:Scratch 'lock-check'
        $null = New-Item -ItemType Directory -Path $lockDir
        $held = [IO.File]::Open((Join-Path $lockDir 'deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        try {
            $result = Invoke-MacroMellowUpdate -Root $script:Scratch -LockDirectory $lockDir
            Assert-True ($result -eq 1)
        } finally { $held.Dispose() }
    }
} finally {
    Remove-Item -LiteralPath $script:Scratch -Recurse -Force
}
[Console]::WriteLine("Deployment tests: $script:Passed passed, $script:Failed failed. No real service or process was stopped.")
if ($script:Failed -gt 0) { exit 1 }
exit 0
