# First Kippy deployment: controlled transition

## Completed deployment — October 24, 2026

The transition below has now been executed and verified on the real Windows host. The existing HTTPS URL serves Kippy; `start-kippy.cmd` uses `C:\Users\tommy\KippyData`, port 8000, and the unchanged Ollama environment. The web task restarted successfully with persistent conversations, source selections and all four uploaded formats. Auto-deploy and model monitoring are enabled. Old launchers, nutrition data/backups (including the inspected OneDrive copies), cache-only package directory and nutrition backup task were removed. The four operational task identifiers and installation paths remain because they operate Kippy/Ollama and are referenced by the supervisors.

The remainder documents the **historical first transition**, not a required manual step for the deployed installation. Use the regular updater for future releases. The first attempt safely stopped at a PowerShell native-argument quoting bug before promotion; commit `bdad4ce` fixed it and added a real Python subprocess regression test. See [verification](verification.md) for live checks.

## Original inspected installation

This is a replacement application, not a second maintained deployment. **For a first transition, do not push until Prepare succeeds.** Development initially left production unchanged; the authorized migration has since completed as recorded above. The inventory below was read from the actual host over its existing SSH connection:

| Item | Inspected value |
| --- | --- |
| Checkout | `C:\Users\tommy\MacroMellow`, main, same repository now named `Salami2222/Kippy` |
| Web command | `.venv\Scripts\python.exe -m macromellow web --no-browser --port 8000 --data-dir "%DATA%"` |
| Web launcher | `C:\Users\tommy\MacroMellowService\start-macromellow.cmd` |
| Environment | `OLLAMA_BASE_URL=http://127.0.0.1:11434`, `OLLAMA_MODEL=qwen3.5:9b` |
| Configuration | `C:\Users\tommy\MacroMellowService\automation\config.json` |
| Web task | `\MacroMellow Web`, managed Python process supervisor |
| Deploy task | `\MacroMellow Auto Deploy`, checks main every two minutes |
| Ollama tasks | `\MacroMellow Ollama`, `\MacroMellow Ollama Monitor` |
| Obsolete backup task | `\MacroMellow Backup`, runs `MacroMellowService\backup-macromellow.py` |
| Old data / backups | `%USERPROFILE%\MacroMellowData`, `%USERPROFILE%\MacroMellowBackups` |
| Optional cloud backup | `MacroMellowBackups` under `OneDrive` or `OneDriveConsumer`, per the inspected backup script |
| Runtime | Python 3.13.15, Node 24.18.0 |
| Existing HTTPS | `https://tp-industry.tail4cab83.ts.net/` |

The old updater demands the removed module and mascot assets. Simply pushing would cause a failed first deployment. The conversion updates its checks to `kippy.web`, `/app.js`, and `/app.css`, but the first invocation must use the **new updater from outside the old checkout**. No application backup, new repository, or archive branch is needed.

## 1. Transfer the migration tools (before pushing)

In an **elevated Windows PowerShell session as tommy**, create a tools-only directory:

```powershell
$Migration = 'C:\Users\tommy\KippyMigration'
New-Item -ItemType Directory -Force "$Migration\windows" | Out-Null
```

From your Mac terminal in this completed repository:

```bash
scp -i ~/.ssh/macromellow_windows \
  scripts/Prepare-KippyMigration.ps1 scripts/Update-MacroMellow.ps1 requirements.txt \
  tommy@100.81.130.10:C:/Users/tommy/KippyMigration/
scp -i ~/.ssh/macromellow_windows \
  scripts/windows/Common.ps1 scripts/windows/Monitor-Ollama.ps1 \
  tommy@100.81.130.10:C:/Users/tommy/KippyMigration/windows/
```

This directory contains only new transition tools and requirements, not an old application copy. Review the transferred scripts before running them. No new SSH key, credential, model installation, network exposure, or execution-policy change is required.

## 2. Prepare Windows while the existing server remains available

In the same elevated PowerShell:

```powershell
$Migration = 'C:\Users\tommy\KippyMigration'
& "$Migration\Prepare-KippyMigration.ps1" -Action Check
& "$Migration\Prepare-KippyMigration.ps1" -Action Prepare
if ($LASTEXITCODE) { throw 'Inspect the preparation output before continuing.' }
(Get-ScheduledTask -TaskName 'MacroMellow Auto Deploy' -TaskPath '\').State
```

The last result must be **Disabled**. If deployment is currently running, Prepare refuses; wait for it to finish and rerun. Read any PowerShell error and stop—do not assume `$LASTEXITCODE` alone detects a terminating script exception.

Prepare:

- Verifies the current automation owner, configuration, launcher text, and known paths.
- Pauses only the automatic deployment task, without stopping a healthy web server or Ollama.
- Creates `MacroMellowService\start-kippy.cmd` from the inspected launcher's environment, changing only the module, app log name, and storage to `%USERPROFILE%\KippyData`.
- Creates a temporary startup bridge that runs the existing command **before** the conversion and Kippy **after** its source arrives. It copies no application code.
- Points the existing web supervisor configuration to that bridge. Credentials, triggers, task names, repository path, port and Ollama settings stay unchanged. Existing conflicting files are refused rather than overwritten.

Provision new Python dependencies explicitly before the deployment gate:

```powershell
& 'C:\Users\tommy\MacroMellow\.venv\Scripts\python.exe' -m pip install -r "$Migration\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; do not push yet.' }
& 'C:\Users\tommy\MacroMellow\.venv\Scripts\python.exe' -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Resolve dependency conflicts before pushing.' }
node.exe --version
npm.cmd --version
```

The pinned extraction libraries include pypdf, python-pptx, python-docx, lxml, Pillow, XlsxWriter and typing-extensions. The updater refuses automatically replacing already installed versions, so this reviewed one-time installation handles those conflicts explicitly. It does not touch model storage, GPU configuration or Ollama environment. Node 24.14+ is needed for jsdom tests; the inspected host satisfies it. `npm ci --ignore-scripts` runs in the deployment candidate to provision test dependencies; application JS is already vendored.

## 3. Publish the one complete commit and activate

Only after Prepare and dependency checks pass, push the completed local main commit from the Mac:

```bash
git status --short
git push origin main
```

Automatic deployment is intentionally still paused. In elevated Windows PowerShell:

```powershell
& 'C:\Users\tommy\KippyMigration\Prepare-KippyMigration.ps1' -Action Activate
```

The **new** updater fetches main, checks for dirty files/divergence, tests in isolated staging, fast-forwards the existing checkout, verifies the old listener's identity before stopping it, and restarts the existing web task into Kippy. It checks the new startup commit/PID, HTTP 200 and release asset MIME types. Ollama availability is diagnostic, not a gate that takes the website down.

On failure, inspect `MacroMellowService\automation\logs\deploy-*.jsonl` before retrying. The existing updater can restore the previous Git commit and launcher through the temporary bridge after a failed promotion; this is failure recovery, not an archive branch or parallel application. Automatic deployment remains paused during troubleshooting. Do not delete operational locks or force-reset local edits.

## 4. Verify Kippy, then finalize

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/version
Invoke-RestMethod http://127.0.0.1:8000/api/ollama/status
(Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing).StatusCode
```

Check `application: Kippy`, the expected commit, and HTTP 200. Open the existing Tailscale HTTPS address. Test a new conversation, upload a small lecture, ask a sourced question, and refresh to confirm history and selections persist. Qwen should report connected; if it is offline, the library and conversation history must still work.

Then:

```powershell
& 'C:\Users\tommy\KippyMigration\Prepare-KippyMigration.ps1' -Action Finalize
```

Finalize verifies Kippy at the checked-out commit, points the supervisor directly to `start-kippy.cmd`, disables only the specifically inspected nutrition backup task, and reenables the same automatic deploy task. Rerunning is safe. It does not delete data or rename tasks. There is one web backend, one model service, one deploy workflow, and the existing independent model monitor.

## 5. Remove obsolete files only after verification

The application no longer uses the old data. Confirm that Kippy is using `C:\Users\tommy\KippyData`, not either old data directory. Inspect the following **known legacy targets** before removing them:

- `\MacroMellow Backup`, already disabled; its action was verified as `MacroMellowService\backup-macromellow.py`.
- `MacroMellowService\backup-macromellow.py`, `start-macromellow.cmd`, and the temporary `kippy-transition.cmd` (configuration must now reference `start-kippy.cmd`).
- `%USERPROFILE%\MacroMellowData` and `%USERPROFILE%\MacroMellowBackups`, as identified in the old launcher/backup script.
- The optional OneDrive/OneDriveConsumer `MacroMellowBackups` destination identified by that script. Inspect archive contents and ownership; do not delete other cloud files merely because their names look similar.
- The checkout's old `macromellow` **cache-only** directory if Git left ignored `.pyc` files after source removal; inspect its contents first. Remove old `data/umd-*.json` / `data/macromellow.sqlite3` files only if they actually exist in the checkout and are confirmed legacy data.

Example commands, after reviewing those exact targets:

```powershell
Get-ScheduledTask -TaskName 'MacroMellow Backup' -TaskPath '\' | Select-Object TaskName, State, Actions
Unregister-ScheduledTask -TaskName 'MacroMellow Backup' -TaskPath '\' -Confirm:$true
Get-ChildItem "$env:USERPROFILE\MacroMellowData" -Force
Get-ChildItem "$env:USERPROFILE\MacroMellowBackups" -Force
# After confirming these are only the obsolete data identified above:
Remove-Item -LiteralPath "$env:USERPROFILE\MacroMellowData" -Recurse -Confirm:$true
Remove-Item -LiteralPath "$env:USERPROFILE\MacroMellowBackups" -Recurse -Confirm:$true
```

Do not delete the checkout, `.venv`, the service directory, automation configuration/logs, original Ollama task settings, `start-ollama.cmd`, model files, KippyData, or unrelated personal files. No cleanup commands were executed on production during development.

## Retained operational names

`C:\Users\tommy\MacroMellow`, `MacroMellowService`, the four operational Web/Deploy/Ollama/Monitor task identifiers, `Update-MacroMellow.ps1`, internal process-check function/class names, and `~/.ssh/macromellow_windows` are existing installation bindings. They run **Kippy only after conversion**. Renaming them is not required to replace the application and is deferred to avoid breaking supervisors, principal settings, paths or monitoring. The updater temporarily recognizes the old process/title/origin only for the transition/rollback; there is no old Python application, hidden route or feature flag in the release.

The Mac helper is now `scripts/kippy-update`. After finalization, optionally install it:

```bash
chmod +x scripts/kippy-update
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/scripts/kippy-update" "$HOME/.local/bin/kippy-update"
# Add ~/.local/bin to PATH if necessary, then:
kippy-update --check
```

Inspect any existing destination before replacing a symlink. Remove a stale `mellow-update` symlink only after confirming it points to the discontinued helper. Neither helper performs the first migration; Activate above does.
