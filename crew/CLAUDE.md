# Rules for changing crew

crew (`crew.py`) drives the `claude` and `codex` CLIs across the owner's projects. It is one
stdlib-only Python file plus `crew.json` (a copy of `DEFAULTS`), `README.md` and `test_crew.py`.
These rules come from six rounds of adversarial review; each one records a defect that was
found and fixed. Do not relax them for convenience. `ASSURANCE.md` holds the evidence.

## Laws: invariants every change must keep

1. **The user's work is never touched.** crew never modifies the user's checkout, index,
   branches or uncommitted files, and never pushes. The only change it makes to a user's repo
   is a fast-forward of its own `crew/...` branch (`git fetch <clone> refs/heads/<b>:refs/heads/<b>`).
   Agents work in crew's own standalone clone under `~/.crew/worktrees/`, never in the user's
   checkout and never in a linked worktree: Claude's sandbox can write a linked worktree's
   shared `.git`. Claude workers get `sandbox.filesystem.denyWrite` for the clone's `.git` and
   the user's repo root; Codex's `workspace-write` keeps `.git` read-only on its own.
2. **Agent-modified code never runs outside a sandbox.** Setup (network, unsandboxed) runs only
   on the base commit in a fresh clone, before any agent has run there; in place (`--no-git`)
   only before the first task ever runs. Checks run inside `codex sandbox -P :workspace`, or
   not at all unless `verify.unsandboxed` is set. Claude gets `--allowedTools Bash` only after
   `claude_sandbox_ready()` proves the sandbox starts, with `allowUnsandboxedCommands: false`.
   crew's own git calls pass `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, commits use
   `--no-verify -c commit.gpgsign=false`, and the clone's `.git/config`, `info/attributes` and
   `info/exclude` are fingerprinted and checked before crew runs git there after an agent.
   The planner is read-only: Claude `--permission-mode plan --tools Read,Grep,Glob`, Codex
   `--sandbox read-only`. Every Claude call uses `--setting-sources user` so repo settings
   (hooks, `defaultMode`, allow rules) are ignored.
3. **Journal before you act.** Any step that changes a repo or a clone is written to the plan
   file first: `git.creating`, `task.untracked_before`, `task.status = running`,
   `task.committing`, `git.removing`. `_reconcile()` finishes or undoes what a crash cut off.
   A task counts as done only if its `crew(<id>):` commit is on the branch. Never add a
   mutating step without a journal key and a resume path, and add a `crash_point()` for it.
4. **Commits hold only the task's work.** New files are `untracked() - untracked_before`, minus
   anything that was ignored at snapshot time, generated files (`GENERATED_PARTS`, `.pyc`),
   sandbox stubs (`is_sandbox_stub`) and nested repos. Before committing, the clone must be on
   crew's branch at crew's recorded tip with no merge/rebase in progress; otherwise stop
   without running git.
5. **Nothing is "done" without evidence.** Task status comes from the agent's structured
   verdict (`WORKER_SCHEMA`); a CLI exit 0 is not success. Plan state is `done` only if every
   task is done, no check regressed on the verified tip (`checks.final_tip`), and setup
   succeeded. `run_plan` exits 0 only for `done`.
6. **One writer at a time.** `file_lock()` (fcntl + thread lock) guards each plan run, `queue
   run`, the queue, state, ledger, profiles and pid files. Writes go through `_write_json`
   (tmp + fsync + rename). `reap_orphans()` kills CLIs a dead crew left behind, matched by pid
   and start time.
7. **Nothing read from disk or a CLI is trusted.** Validate types (`num()`, `load_config`,
   `load_profile`, `validate_plan`, the plan `git` block), reject control characters in task
   fields, decode subprocess output with `errors="surrogateescape"` (git) or `"replace"`, treat
   a reply crew can't parse as a failed attempt (never a traceback), and detect usage limits
   only from the provider's own error message (`LIMIT_RE`), never from raw stdout.
8. **Codex `error` events are not failures.** Success is `turn.completed` and no `turn.failed`
   with exit 0; "Reconnecting..." notices are diagnostics.
9. **Every subprocess has a timeout, runs in its own session, and is killed as a tree.**
   `_exec` is the only way to start a CLI. SIGTERM/SIGHUP become `SystemExit` so children die
   with crew.
10. **Credentials stay with their provider.** `provider_env()` strips the other provider's keys;
    sandboxed checks get no `SECRET_ENV` variables at all; keys are never logged or put on a
    command line (`codex-login` pipes via stdin).

## Engineering rules

- Python 3.9+, standard library only. No new dependencies.
- `crew.json` must equal `crew.DEFAULTS` (a test enforces it). Change both.
- Every fixed defect gets a test in `test_crew.py` that reproduces it with the fake CLIs.
  Run `python3 -m unittest test_crew` before committing (about two minutes; it needs git and
  spawns real subprocesses, no model calls).
- CLI flags crew relies on are listed in `CONTRACT`; add any new flag there so `doctor` and
  `available_providers` catch a CLI that drops it.
- Prompts give agents paths relative to the repo; absolute paths into the user's checkout are
  remapped to the clone (`worker_prompt`). Never tell an agent the user's checkout path.
- Keep the README's "What crew guarantees" section true. If a change weakens a guarantee,
  change the README in the same commit.
- Real-CLI smoke test after engine changes: a throwaway repo with a dirty checkout, config
  `providers.claude.model = "haiku"`, `crew go` on a small goal; then confirm the checkout's
  file hashes are unchanged, the work is on a `crew/` branch, and the clone was removed.

## What is known not to be covered

- Codex as a worker has only been exercised with fake CLIs and mock servers (no Codex login
  was available); `codex sandbox` and the Codex event format were tested for real.
- Nothing has been run on macOS or Windows. On Windows `fcntl` is absent, so locks are
  per-process only, and `codex sandbox` is unavailable, so checks are skipped.
- Both CLI sandboxes allow writes under the temp directory. Repos kept under `/tmp` lose
  isolation; crew warns.
- The `tokens` metric is approximate: the two CLIs count differently.
