# Remote updates from your MacBook

Run **`mellow-update`** in Mac Terminal to update the existing Windows installation.
Run **`mellow-update --check`** to validate it without pulling, installing packages,
or stopping/restarting the app.

## Existing configuration

The helper uses these existing values:

| Setting | Value |
| --- | --- |
| SSH destination | `tommy@100.81.130.10` |
| SSH identity | `~/.ssh/macromellow_windows` |
| Windows checkout | `C:\Users\tommy\MacroMellow` |
| Existing environment | `C:\Users\tommy\MacroMellow\.venv` |
| Scheduled task | `\MacroMellow Web` |
| Health endpoint | `http://127.0.0.1:8000/` on Windows |

The scheduled task still launches its existing `MacroMellowService` command file,
which selects the existing data directory and environment settings. The updater
does not replace that launcher, recreate the task, or change credentials. Your
current launcher uses `C:\Users\tommy\MacroMellowData`; the scripts never open,
copy, delete, or restore its database or menu files.

## One-time Mac setup

From your **Mac MacroMellow checkout**, run:

```bash
chmod +x scripts/mellow-update
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/scripts/mellow-update" "$HOME/.local/bin/mellow-update"
command -v mellow-update
mellow-update --check
```

The symlink command deliberately refuses to overwrite an existing command. If it
already exists, inspect it with `ls -l ~/.local/bin/mellow-update` before deciding
what to do. Keep this Mac checkout at the same location, or recreate the symlink
after moving it. The helper resolves symlinks and paths containing spaces.

If `command -v` cannot find the command, add this line **once** to `~/.zprofile`,
then open a new Terminal window:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

No Homebrew package is needed on the Mac: the helper uses Bash, SSH, `iconv`,
`base64`, and `readlink`. It requires the existing SSH key and trusted Windows
host key. It uses batch authentication and strict host-key checking, so it fails
instead of accepting a new/changed host key or prompting for credentials. Resolve
an SSH trust/authentication problem through your existing SSH setup; the updater
does not edit SSH or Tailscale configuration.

There is **no initial Windows script-copy step**. The helper streams the reviewed
local `scripts/Update-MacroMellow.ps1` over SSH, using a UTF-16LE encoded bootstrap
command to avoid shell quoting problems. Update this Mac checkout with
`git pull --ff-only origin main` when you want new deployment-script changes.
Application updates themselves always fetch GitHub main directly on Windows.

## Normal use

```bash
mellow-update --check  # optional: current installation, tests, and HTTP check
mellow-update         # pull, dependencies if declared, tests, restart, verify
```

A successful update ends with:

```text
SUCCESS: deployed <commit>; \MacroMellow Web is running; http://127.0.0.1:8000/ returned HTTP 200.
```

The command returns exit code **0** only after success. Failures, including a
successful recovery to the previous code, return a nonzero status. Check with
`echo $?` immediately afterward if using it in another script.

## What happens and what is protected

1. Acquire an exclusive, per-user deployment lock under
   `%LOCALAPPDATA%\MacroMellow\deployment.lock`. Concurrent runs refuse to start;
   the OS releases the handle when the updater exits. The file may remain safely.
2. Require a clean **main** checkout with the expected GitHub origin. Staged,
   unstaged, and nonignored untracked files cause refusal. Existing ignored venv
   and data directories are expected. Other branches are never switched.
3. Fetch only main and verify the current commit is its ancestor. Advance using
   `git merge --ff-only --no-autostash --no-overwrite-ignore` to the exact fetched commit. This
   is the controlled equivalent of a fast-forward-only pull. Local ahead/diverged
   commits cause refusal. Incoming database/data/venv paths and collisions with
   existing ignored files also cause refusal.
4. If `requirements.txt` exists, run the existing venv's
   `python -m pip install --no-input -r requirements.txt`, without a blanket
   upgrade. Already-satisfied dependencies stay installed. The current app has
   no requirements manifest, so this step currently skips installation. Future
   runtime dependencies must be declared there. A new unsupported packaging
   layout causes refusal rather than an improvised install. The venv is never
   created or replaced.
5. Run `pip check`, verify Python 3.10+, New York timezone availability and app
   imports, then run **all Python unit tests**, **all `test_*.cjs` Node tests**,
   and the isolated Windows deployment tests. Node 18+ and Windows PowerShell
   5.1+ must already be installed. Missing tools or any failed test prevent a stop.
6. Recheck the checkout, then stop only `\MacroMellow Web`. Wait for its task and
   child processes to exit. If a listener remains on port 8000, require the exact
   venv command prefix `-m macromellow web`, the expected Python executable/base
   interpreter, the current user's SID, and matching process creation times.
   Termination uses a pinned process handle to guard against PID reuse. Unknown
   or inaccessible listeners cause refusal; there is no broad Python kill or
   process-tree kill.
7. Start the existing task and poll for a **fresh verified process**, a running
   task, and two HTTP-200 responses containing the MacroMellow page title. The
   startup deadline is 45 seconds. Redirects, unrelated servers, and an old
   listener cannot satisfy the check. No model request or nutrition refresh runs.

Repeated runs are safe: tests rerun, already-satisfied requirements remain, and
the existing instance is stopped before another instance is started. A repeat
with no new commits still performs a tested restart.

## Failures and recovery

- Before a stop, the running service remains running. If this deployment advanced
  the checkout, it attempts to restore the previous commit using **`git reset
  --keep`**, only if main is still clean and HEAD is still the exact deployed
  commit. It never hard-resets, cleans, stashes, or overwrites user edits.
- After a stop/start failure, it attempts verified cleanup, the same guarded code
  restoration, and a restart with HTTP verification. It reports **RECOVERY** or
  **RECOVERY INCOMPLETE**, and still exits unsuccessfully.
- Installed dependencies are **not automatically downgraded** during code
  recovery. A failed/partial pip change or incompatible dependency may require
  manual repair of the existing venv. No rollback of application data is attempted.
- If another user changes the checkout, permissions prevent task/process control,
  the port belongs to something else, or recovery fails, the script stops and
  identifies the problem. Review the existing launcher's service logs and task
  state before retrying. It does not elevate privileges or alter task credentials.
- If SSH disconnects, do not assume success. Run `mellow-update --check` after
  reconnecting. A concurrent update will still hold its lock; otherwise a normal
  `mellow-update` can retry the clean checkout and restart a stopped task. Abrupt
  machine shutdowns cannot be made atomic by this script.

`--check` runs tests and verifies the current service; it does not check remote
GitHub availability or install missing requirements. Test fixtures and scratch
repositories use temporary directories, and the real scheduled task and its
listener are not stopped by the deployment test suite.

## Test the deployment safeguards separately on Windows

```powershell
powershell.exe -NoProfile -NonInteractive -File C:\Users\tommy\MacroMellow\tests\Test-Deployment.ps1
```

This needs Git and Node available on the Windows PATH. It covers dirty checkouts,
wrong branches/origins, divergence, protected data, ignored-file collisions,
rollback guards, ownership/PID checks, test/dependency failures, startup failure,
failed recovery, health checks, concurrent locking, and repeat deployments. It
does not need Pester or a real model connection.

The implementation checks native exit codes explicitly, as described by
[PowerShell's `$LASTEXITCODE` documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables?view=powershell-5.1#lastexitcode).
It uses the existing task's [stop](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/stop-scheduledtask)
and [asynchronous start](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/start-scheduledtask)
operations, followed by independent process and HTTP checks.
