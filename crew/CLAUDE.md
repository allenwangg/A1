# Rules for changing crew

crew (`crew.py`) drives the `claude` and `codex` CLIs across the owner's projects. It is one
stdlib-only Python file plus `crew.json` (a copy of `DEFAULTS`), `README.md` and `test_crew.py`.
These rules come from eight rounds of adversarial review; each one records a defect that was
found and fixed. Do not relax them for convenience. `ASSURANCE.md` holds the evidence.

## Laws: invariants every change must keep

1. **The user's work is never touched.** crew never modifies the user's checkout, index,
   branches or uncommitted files, and never pushes. The only change it makes to a user's repo
   is a fast-forward of its own `crew/...` branch (`git fetch <clone> refs/heads/<b>:refs/heads/<b>`).
   Agents work in crew's own standalone clone under `~/.crew/worktrees/`, never in the user's
   checkout and never in a linked worktree: Claude's sandbox can write a linked worktree's
   shared `.git`. Claude workers get `sandbox.filesystem.denyWrite` for the clone's `.git` and
   the user's repo root; Codex's `workspace-write` keeps `.git` read-only on its own.
   The branch in the user's repo is the user's to move or delete: read it fresh on every run
   (`rb`), correct `git.published` from it, and compare ancestry inside the clone, which holds
   both sides once fetched. The user's repo doesn't have crew's commits until they are published.
   Commits that were refused stay in the clone; `clean` keeps such a clone unless `--all`.
   `main()` removes the git location variables crew inherits (`GIT_DIR`, `GIT_WORK_TREE`,
   `GIT_INDEX_FILE` and the rest): left in place they point every `git -C <dir>` at the
   caller's repository. The publish fetch passes `--no-recurse-submodules`. When the clone is
   put on commits its submodule checkouts weren't made for (the user's own commits, or the
   branch a rebuilt clone starts from), `_sync_submodules()` moves them, without `--init`;
   otherwise the next task's `git add -u` commits a pointer change that undoes the user's.
   A submodule that can't be put on the commit the base records when the clone is made (not
   pushed yet) is deinitialised, never left on its remote's HEAD. One the user's commits drop
   stays in the clone and goes into the snapshots (`_note_dropped_submodules`); deleting it
   would be an unjournaled removal. crew's git must not change with the user's git settings
   where crew depends on the result: the clone names its remote (`-o origin`) and the adoption
   merge pins `merge.autoStash=false`, as commits pin `commit.gpgsign`.
2. **Agent-modified code never runs outside a sandbox.** Setup (network, unsandboxed) runs only
   on the base commit in a fresh clone, before any agent has run there; in place (`--no-git`)
   only before the first task ever runs. Checks run inside `codex sandbox -P :workspace`, or
   not at all unless `verify.unsandboxed` is set. Claude gets `--allowedTools Bash` only after
   `claude_sandbox_ready()` proves the sandbox starts, with `allowUnsandboxedCommands: false`.
   crew's own git calls pass `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, commits use
   `--no-verify -c commit.gpgsign=false`, and the clone's `.git/config`, `info/attributes` and
   `info/exclude` are fingerprinted and checked before crew runs git there after an agent.
   So is what stands at every submodule's `.git` (`_gitlinks()`, recorded as `git.gitlinks`):
   that file is in the working tree, an agent can swap it for a repository, and `git status`,
   `git add -u` and `git submodule update` obey a submodule's own configuration. The check runs
   wherever agent-controlled code has run since crew last looked. The sandboxed checks count:
   `finish()` checks again before its `git status`.
   `providers.<p>.args` only adds flags. `_claude_worker_args()` and `run_codex()` put back the
   permission mode, sandbox settings and write denials that a custom `args` leaves out, keep a
   value the config sets itself, and give Claude Bash only when the resulting settings have the
   sandbox on and its escape hatch off. Claude reads only the last `--settings` on its command
   line; crew passes on only that one, with its rules in it.
   The planner is read-only: Claude `--permission-mode plan --tools Read,Grep,Glob`, Codex
   `--sandbox read-only`. Every Claude call uses `--setting-sources user` so repo settings
   (hooks, `defaultMode`, allow rules) are ignored.
   crew's own state drives unsandboxed execution (the config names the CLIs and their flags,
   profiles hold setup commands), so it must sit where no sandbox can write:
   `require_safe_home()` refuses to run agents with `CREW_HOME` or the config file in a temp
   directory unless `CREW_ALLOW_TEMP_HOME` is set. `$HOME` (global git config, `~/.claude`,
   `~/.codex`, `~/.crew`) is trusted; do not neutralise global git config, which would break
   `safe.directory` and hosted git settings.
3. **Journal before you act.** Any step that changes a repo or a clone is written to the plan
   file first: `git.creating`, `task.untracked_before`, `task.status = running`,
   `task.committing`, `git.adopting`, `git.adopting_clear`, `git.sync_submodules`,
   `git.sync_started`, `git.removing`. `_reconcile()`
   finishes or undoes what a crash cut off. A fact the resume depends on is saved in the same
   write as the step that creates it: when the clone has vanished, the tasks that lost
   uncommitted work are reset in the save that sets `git.creating`, and `_adopted()` moves
   `git.tip` and every `task.committing` marker together.
   A step git can leave half done needs a way back that can't touch a task's work. Before the
   adoption merge crew runs git's own test for it (`read-tree -n`) and journals that it passed
   (`git.adopting_clear`); only then may `_undo_half_merge()` put back a merge that stopped
   part-way, and only at the paths that merge writes. `_sync_submodules(journal=True)` records
   the clean checkouts it is about to move (`git.sync_started`), so a move that was cut short is
   forced through, and a checkout an agent edited never is.
   Long git commands (clone, checkout, fetch) run with `git(..., track=True)`, so they are in
   the pid file and `reap_orphans()` stops them before a resume touches the clone.
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
   and start time. Each clone belongs to one plan file, named by `<clone>.plan`:
   `_claim_copy()` writes it on every run and refuses a clone another live plan owns; `clean`
   reads it first to find a clone's plan. The plan also records its own path as `git.owner`, so
   a copied plan file is refused after the pointer is gone. Plans outside `~/.crew/plans` get a
   path hash in their branch name (`_branch_name`). A plan's lock and pid file are named after
   its path (`plan_lock_name`), so `_migrate_key` moves a plan file only while holding that
   lock, after reaping its pid file, and `plans_for` looks in the old folder too.
7. **Nothing read from disk or a CLI is trusted.** Validate types (`num()`, `load_config`,
   `load_profile`, `validate_plan`, the plan `git` block), reject control characters in task
   fields, decode subprocess output with `errors="surrogateescape"` (git) or `"replace"`, treat
   a reply crew can't parse as a failed attempt (never a traceback), and detect usage limits
   only from the provider's own error message (`LIMIT_RE`), never from raw stdout. `_read_json`
   is the only reader of crew's files and bounds their nesting (`MAX_JSON_DEPTH`), because
   `deepcopy` and `json.dumps` overflow on depth that `json.loads` accepts. Pointer and pid
   files are read with `errors="replace"` and checked before use.
8. **Codex `error` events are not failures.** Success is `turn.completed` and no `turn.failed`
   with exit 0; "Reconnecting..." notices are diagnostics.
9. **Every subprocess has a timeout, runs in its own session, and is killed as a tree.**
   `_exec` is the only way to start a CLI. SIGTERM/SIGHUP become `SystemExit` so children die
   with crew. A child is started and recorded inside `_signals_held()`, and killed inside it, so
   a stop signal can't land between a child's start and its record or cut a kill short; the
   first stop signal makes crew ignore later ones. `_exec` drops a child's record only once the
   child has exited. A signal that was ignored when crew started (nohup) stays ignored.
10. **Credentials stay with their provider.** `provider_env()` strips the other provider's keys;
    sandboxed checks get no `SECRET_ENV` variables at all; keys are never logged or put on a
    command line (`codex-login` pipes via stdin).
11. **A worker never starts crew.** Workers load the user's own skills and instructions, which
    include the `crew` skill that `crew install` writes. Every worker and every check runs with
    `CREW_TASK` set, and `main()` refuses `plan`, `go`, `do`, `run` and `queue run` when it is.

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
- A message that tells the user to run crew names the plan file (`crew run <file>`). A bare
  `crew run` resumes the checkout's latest plan, which may be a different one.
- Keep the README's "What crew guarantees" section true. If a change weakens a guarantee,
  change the README in the same commit.
- Real-CLI smoke test after engine changes: a throwaway repo with a dirty checkout, config
  `providers.claude.model = "haiku"`, `crew go` on a small goal; then confirm the checkout's
  file hashes are unchanged, the work is on a `crew/` branch, and the clone was removed.

## What a tool must offer before it can be a provider

A provider runs agent-written code, so adding one is a change to law 2. It needs all of:

- a non-interactive run that takes the prompt off the command line and returns a verdict crew
  can require by schema, where exit 0 alone is never read as success;
- a sandbox crew switches on for that run, that keeps writes inside the working directory, keeps
  `.git` read-only and has no network, and a probe that proves it works before crew trusts it;
- a way to load only the user's own configuration, because the working directory is a clone an
  earlier agent may have left hooks, tool servers or settings in;
- a login check that starts no session, and a usage-limit error crew can recognise.

Antigravity's `agy` 1.5 and Gemini CLI 0.68 were checked against this from their documentation
on 2026-11-13. Neither offers the second or third, and neither could be run on the build machine,
so they are not providers. They hand work to crew through the `crew` skill instead (`SKILL_MD`,
`install_skill()`); the skill's frontmatter is only `name` and `description`, which is all that
Claude Code, Codex and Antigravity have in common.

## What is known not to be covered

- Codex as a worker has only been exercised with fake CLIs and mock servers (no Codex login
  was available); `codex sandbox` and the Codex event format were tested for real.
- Nothing has been run on macOS or Windows. On Windows `fcntl` is absent, so locks are
  per-process only, and `codex sandbox` is unavailable, so checks are skipped.
- Both CLI sandboxes allow writes under the temp directory. Repos kept under `/tmp` lose
  isolation; crew warns. (crew's own home there is refused, see law 2.)
- Two plan files written before 3.3 (no `git.owner`) that already name the same branch are
  refused only while one of them has a live clone; after that they share the branch.
- SIGKILL cannot be held off. One that lands in the few milliseconds between a tracked git
  command starting and its pid being recorded leaves that git running unrecorded; the next run
  fails once ("another process may still be writing there") and succeeds when repeated.
- Submodules are tested for: an update, a removal and an unpushed commit of the user's, a move
  of a checkout that was cut short, and an agent swapping a submodule's `.git`. Nested
  submodules are untested: one whose checkout is cut after its parent's finished, one that
  fails to initialise inside one that did, one the user adds mid-plan. No real sandboxed worker
  was used to make the swap.
- The `crew` skill was written from each tool's documentation. No tool has been watched loading
  it or handing work to crew through it.
- The `tokens` metric is approximate: the two CLIs count differently.
