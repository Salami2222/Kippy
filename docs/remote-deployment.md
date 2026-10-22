# Automatic Windows deployment and Ollama recovery

Windows checks GitHub `Salami2222/MacroMellow` **main every two minutes**. A new
commit is tested in a detached staging checkout, then deployed through the
existing `\MacroMellow Web` task. Your Mac can be off. No public webhook is used.

Ollama is monitored **independently every minute**. Both services have boot
triggers and run with the existing Windows user's noninteractive S4U identity.
The existing command files, credentials, model storage/environment/GPU settings,
SQLite/menu data, backup task, SSH, Tailscale Serve, and power settings are retained.

## One-time Windows installation

Prerequisites: existing working Web/Ollama tasks owned by `tommy` with S4U logon,
Windows PowerShell 5.1, Git, Node 18+, existing Python 3.10+ venv with New York
timezone data, existing Ollama and `qwen3.5:9b`. The installer inspects these
existing tasks; unfamiliar launchers/principals are refused rather than replaced.

After safely updating the Windows checkout to this release, run as the existing
Windows user in an **elevated PowerShell** (the configured SSH account already has
administrator access):

```powershell
& 'C:\Users\tommy\MacroMellow\scripts\Install-WindowsAutomation.ps1'
& 'C:\Users\tommy\MacroMellow\scripts\Update-MacroMellow.ps1'
```

The second command activates and verifies the new web process supervisor. The
installer itself does not stop a healthy Web or Ollama process. Original Web and
Ollama task XML is saved once under `MacroMellowService\automation\original-tasks`.
Rerunning installation updates the same managed tasks, without duplicating them.
Existing Web/Ollama task principals, credentials, triggers and settings remain;
only their actions are wrapped to supervise the **original** command files.

From Mac Terminal, the equivalent commands (using the existing trusted SSH host):

```bash
ssh -i ~/.ssh/macromellow_windows tommy@100.81.130.10 \
  'powershell.exe -NoProfile -NonInteractive -Command "& C:\Users\tommy\MacroMellow\scripts\Install-WindowsAutomation.ps1"'
mellow-update
```

No new passwords, model downloads, service manager installation or inbound ports
are required. If your task configuration differs, inspect the refusal and use an
administrator session under the existing task owner; do not change task credentials
just to get past a check. Installation does not weaken PowerShell execution policy.

### Optional Mac manual fallback

From your Mac MacroMellow checkout:

```bash
chmod +x scripts/mellow-update
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/scripts/mellow-update" "$HOME/.local/bin/mellow-update"
ln -s "$PWD/scripts/mellow-update" "$HOME/.local/bin/deploy-now"
```

These commands refuse existing destinations; inspect existing symlinks before
changing them. If necessary, add `export PATH="$HOME/.local/bin:$PATH"` once to
`~/.zprofile` and open a new Terminal. The checkout must stay at the symlink target.
The helper uses the **Windows checkout's installed updater**, requires the existing
`~/.ssh/macromellow_windows` key and trusted host key, and uses strict host checking
and batch authentication. No SSH/Tailscale settings are changed.

```bash
mellow-update --check  # tests + live commit/assets; no fetch, install or restart
mellow-update          # explicit retry, even for a previously failed commit
# deploy-now is the same manual fallback
```

## Safety and release identity

- An OS-held exclusive file lock prevents overlapping updates. The recurring task
  also uses `IgnoreNew`. Crashes release locks automatically; do not delete locks
  to force overlapping work.
- A clean main branch and expected GitHub origin are mandatory. Dirty/untracked
  files, divergence, protected data/venv paths and collisions with ignored local
  files are refused. There is no automatic stash, branch switch or hard reset.
- Fetch main, verify ancestry, stage its exact commit outside the live checkout,
  then run pip integrity/timezone/import checks, **all Python and Node tests**, and
  both isolated Windows safeguard suites. A failed candidate never advances live
  code or stops the website.
- Current runtime dependencies are Python stdlib plus the existing timezone data;
  no requirements installation is currently necessary. Future `requirements.txt`
  additions are dry-run checked, installed into a staging overlay, tested, then
  added to the existing venv. Replacement/upgrade/downgrade of any installed package
  is refused and requires a reviewed environment migration. Unsupported packaging
  layouts fail closed. New packages are retained on code rollback; partially failed
  installs may need manual venv repair. The serving environment is never recreated.
- Promote only with fast-forward merge, recheck for concurrent edits, then stop
  the Web task. A Windows **Job Object with kill-on-close** owns its full child
  tree (cmd, venv launcher, base Python). Stopping the supervisor closes the job.
  Legacy orphan listeners/venv parents must match exact executable, command line,
  owner SID and creation time, then are stopped through a pinned process handle.
  Unknown port occupants cause refusal. No hardcoded PIDs or broad Python kills.
- Wait for port 8000 to be empty, start the task and require a fresh verified
  listener plus two successful passes of `/`, `/api/status`, `/mascot.js` (JS MIME),
  `/assets/mellow/idle.png` (PNG MIME) and `/api/version`. The last endpoint freezes
  Git commit, PID and startup time **once at server startup**. Changing files under
  an old process cannot make it report the new commit. AI readiness is not a
  release gate.
- A failed restart attempts **one** cleanup/guarded `git reset --keep` to the
  previous clean commit and restart. Data is never rolled back. The failed target
  is held until a newer main commit or explicit manual retry, preventing loops.
  An unchanged healthy release skips tests and restart. An unchanged unhealthy
  release is reported/held for manual diagnosis, not restarted every two minutes.
- The last-good commit stays in local Git and is recorded in deployment state;
  scripts do not prune it. Abrupt power loss is not an atomic deployment: after
  an interruption, inspect state and run an explicit retry. Never assume a failed
  SSH session completed successfully.

## Ollama/Qwen monitoring

The monitor checks `http://127.0.0.1:11434/api/tags` and exact installed model
`qwen3.5:9b`. It never pulls models or changes model files/environment variables.
It launches the existing `\MacroMellow Ollama` task only when needed. Its original
launcher retains `OLLAMA_HOST=127.0.0.1:11434`; no public exposure is introduced.
Healthy servers are preserved, including any existing tray application.

An absent server can be recovered immediately. A live but unresponsive verified
server gets a 60-second startup grace and three failed probes before recovery.
Attempts are limited to once per ten minutes and three per hour. The monitor
serializes recovery, checks server executable/command/owner/creation before
termination, refuses unknown port owners or multiple servers, and waits for port
11434 to become free. The task supervisor prevents duplicate managed launches
and contains Ollama descendants. A missing model or failed optional inference is
reported without restarting a responding server or downloading anything.

Web startup and deployment verify/start Ollama before checking app AI connectivity.
Ollama failure is a warning: the website still starts and deployment can succeed.
The browser refreshes its AI connection status every 30 seconds while visible,
without retrying or duplicating chat messages.

Optional inference check (may load the existing model into GPU/RAM normally):

```powershell
& 'C:\Users\tommy\MacroMellow\scripts\windows\Monitor-Ollama.ps1' -InferenceCheck
```

This sends one eight-token request, not a download. Monitor exit `0` means the
model is present/healthy (or another monitor owns the lock); `2` means degraded,
failed inference, refused/cooling-down recovery, or recovery not yet ready.
Deployment exit `0` means verified release success; `1` includes held/failed
releases even if recovery succeeded.

## Monitor, disable and recover

Run in Windows PowerShell, locally or over the existing SSH connection:

```powershell
Get-ScheduledTask -TaskName 'MacroMellow*' | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'MacroMellow Auto Deploy'
Get-ScheduledTaskInfo -TaskName 'MacroMellow Ollama Monitor'
Invoke-RestMethod http://127.0.0.1:8000/api/version
Invoke-RestMethod http://127.0.0.1:8000/api/ollama/status
Get-Content C:\Users\tommy\MacroMellowService\automation\deployment-state.json
Get-Content C:\Users\tommy\MacroMellowService\automation\logs\deploy-*.jsonl -Tail 15
Get-Content C:\Users\tommy\MacroMellowService\automation\logs\ollama-*.jsonl -Tail 15

Disable-ScheduledTask -TaskName 'MacroMellow Auto Deploy'
Disable-ScheduledTask -TaskName 'MacroMellow Ollama Monitor'
# Disable prevents future launches; it does not interrupt a deployment already running.
# Re-enable with Enable-ScheduledTask after diagnosing the problem.
```

Automation state/logs stay outside Git under
`C:\Users\tommy\MacroMellowService\automation`. Daily JSONL files record source,
target, test results/failures, restart/endpoint checks, final successful or recovered
commit, connection failures and recovery attempts. Existing application/Ollama
output remains in `MacroMellowService\logs`. Logs are not automatically deleted;
review disk usage periodically. Do not edit state while tasks are active.

For a failed release, fix main and push, or review the error and run
`mellow-update` once to retry explicitly. Do not discard local changes to bypass
the dirty-tree guard. If recovery is incomplete, inspect task/server logs and port
ownership first. Existing data/backups remain authoritative. S4U has no reusable
network credentials: verify Git fetch in the actual unattended task (this public
GitHub repository works anonymously). A future private repository may require a
separately reviewed Git authentication arrangement.

## Verification

```powershell
& C:\Users\tommy\MacroMellow\tests\Test-Deployment.ps1
& C:\Users\tommy\MacroMellow\tests\Test-OllamaMonitor.ps1
```

These tests use temporary Git repos and mocked task/process operations. Live
installation and recovery verification are separate; a configured boot trigger
alone does not prove a real reboot occurred. See the delivery report for actual
Windows checks performed.

Implementation references: [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[Task Scheduler S4U](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype),
[Ollama on Windows](https://docs.ollama.com/windows),
[Ollama tags](https://docs.ollama.com/api/tags) and
[generate](https://docs.ollama.com/api/generate).

### Setup verification (2026-10-22)

The actual Windows installation was inspected over the existing SSH connection.
Both services already used boot-triggered S4U tasks. Installation preserved the
healthy Ollama listener, and a real `qwen3.5:9b` inference completed successfully.
A staged release passed 100 Python tests, then the frontend and Windows suites;
its live startup commit/PID and all required endpoints/MIME types were verified.
The two-minute deployment task and independent one-minute monitor both completed
unattended runs with result `0`; the no-change poll left the web PID unchanged.
A separate real Windows supervisor test proved Python child/grandchild cleanup.

A full Windows reboot and a deliberately induced production Ollama outage were
**not** performed. Boot triggers/noninteractive execution are configured and
verified; recovery decisions are covered by isolated tests. No model was
redownloaded, and the already healthy production Ollama instance was preserved.
