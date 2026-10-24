# Kippy remote deployment

For the first conversion, follow [Windows migration](windows-migration.md). The existing automatic task must be prepared before the first push; it cannot launch the removed package or validate removed artwork.

After finalization, the existing scheduler fetches **Salami2222/Kippy main every two minutes**, stages Python and frontend tests, fast-forwards only, restarts the identity-verified listener on port 8000, and checks HTTP/commit/MIME health. Dirty worktrees, divergence, protected data paths, dependency replacement and unknown listeners are refused. Failure recovery restores the previous code when safe. Persistent Kippy uploads live outside the checkout at `%USERPROFILE%\KippyData`.

Ollama is monitored independently every minute and at boot. The website stays available if Qwen is offline. No model downloads or public Ollama bind are introduced. Existing Windows task principals, GPU configuration, model environment, SSH and Tailscale access remain unchanged.

Run `kippy-update --check` from the optional Mac helper for a deployment test/health check; run `kippy-update` for a manual update after migration. Logs remain in the existing service automation directory. Do not rename tasks or directories independently of their configured launchers and supervisors. The remaining old names are operational bindings listed in the migration guide, not a second application.
