# Rules for changing crew

crew (`crew.py`) drives the `claude` and `codex` CLIs across the owner's projects. It is one
stdlib-only Python file plus `crew.json` (a copy of `DEFAULTS`), `README.md` and `test_crew.py`.
These rules come from nine rounds of adversarial review; each one records a defect that was
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
   caller's repository. The publish fetch passes `--no-recurse-submodules`, and so does the fetch
   of the user's branch into the clone: a submodule fetch that fails there (a commit not pushed
   yet) makes git report the whole fetch as failed although the branch was read. When the clone is
   put on commits its submodule checkouts weren't made for (the user's own commits, or the
   branch a rebuilt clone starts from), `_sync_submodules()` moves them, without `--init`;
   otherwise the next task's `git add -u` commits a pointer change that undoes the user's.
   It names the checkouts that are behind (`submodule update -- <paths>`): without paths git
   also fills in every registered submodule that has no checkout (one the user's commit moved to
   another folder), and the check of each submodule's `.git` then takes crew's own git for tampering.
   A submodule that can't be put on the commit the base records when the clone is made (not
   pushed yet) is deinitialised, never left on its remote's HEAD. git stops at the first
   submodule that fails and leaves the ones after it cloned but empty, so after a failure each
   is updated on its own, with `--force` (the clone is new). One the user's commits drop
   stays in the clone and goes into the snapshots (`_note_dropped_submodules`); deleting it
   would be an unjournaled removal. crew's git must not change with the user's git settings
   where crew depends on the result: the clone names its remote (`-o origin`) and the adoption
   merge pins `merge.autoStash=false` and `merge.verifySignatures=false`, as commits pin
   `commit.gpgsign`.
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
   line; crew passes on only that one, with its rules in it. crew's own flags go before the
   configured ones: a flag there that lacks its value takes the next token for it.
   Bash is granted or taken away (`--disallowedTools Bash`), never just left ungranted: without
   the grant Claude still runs what the owner's own settings allow (`autoAllowBashIfSandboxed`,
   an allow rule such as `Bash(crew *)`), unsandboxed when the sandbox can't start.
   `claude_shell_refusal()` also reads the owner's own settings files: Claude merges
   `sandbox.excludedCommands` from them into crew's, crew can't empty that list from the command
   line, and such a command runs outside the sandbox once Bash is allowed.
   Codex obeys the directory it works in unless told not to. Every `codex exec` gets
   `--ignore-rules` (an execpolicy `allow` rule runs the command outside the sandbox; the owner's
   rules are for the tool they sit at, and a rules file in the clone is an agent's), and every
   `codex exec` and `codex sandbox` gets `-c projects={"<dir>"={trust_level="untrusted"}}`
   (`_codex_own_config_only()`): Codex loads `.codex/config.toml` from a directory it trusts,
   `codex exec` trusts the one it is started in, and a Claude worker can write that file in the
   clone. Its tool servers are started outside any sandbox.
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
   `task.committing`, `git.adopting`, `git.adopting_clear`, `git.adopting_gone`, `git.sync_submodules`,
   `git.sync_started`, `git.removing`. `_reconcile()`
   finishes or undoes what a crash cut off. A fact the resume depends on is saved in the same
   write as the step that creates it: when the clone has vanished, the tasks that lost
   uncommitted work are reset in the save that sets `git.creating`, and `_adopted()` moves
   `git.tip` and every `task.committing` marker together.
   A step git can leave half done needs a way back that can't touch a task's work. Before the
   adoption merge crew runs git's own test for it (`read-tree -n`) and journals that it passed
   (`git.adopting_clear`); only then may `_undo_half_merge()` put back a merge that stopped
   part-way, and only at the paths that merge writes. git's test is not the whole answer, so
   three things come first. What a commit of crew's that was cut after `git add` left staged is
   un-staged (`task.committing` is its journal; staged, it switches the undo off). A tracked
   file that is missing from the working tree counts as up to date for git: where the user's
   commits change one, crew refuses the merge as a collision, and where they delete it too, it
   is journaled (`git.adopting_gone`) and the undo leaves it deleted. And git lets a merge write
   over ignored files, so the undo never removes a file git ignores.
   `_sync_submodules(journal=True)` records the clean checkouts it is about to move
   (`git.sync_started`), so a move that was cut short is forced through, and a checkout an agent
   edited never is. Clean means no tracked change and no untracked file but generated ones: git
   refuses to write over an untracked file, and a forced retry would not.
   A regression and the fix task it calls for are one save (`verify()`): saved one after the other, a
   stop between them left a plan with the regression on record, its tip verified and nothing to run.
   Long git commands (clone, checkout, fetch) run with `git(..., track=True)`, so they are in
   the pid file and `reap_orphans()` stops them before a resume touches the clone.
   A task counts as done only if its `crew(<id>):` commit is on the branch. Never add a
   mutating step without a journal key and a resume path, and add a `crash_point()` for it.
4. **Commits hold only the task's work.** New files are `untracked() - untracked_before`, minus
   anything that was ignored at snapshot time, generated files (`GENERATED_PARTS`, `.pyc`),
   sandbox stubs (`is_sandbox_stub`) and nested repos. Before committing, the clone must be on
   crew's branch at crew's recorded tip with no merge/rebase in progress; otherwise stop
   without running git. Files inside a submodule's folder are another repository's, and git in
   the clone never lists them: `_inside_submodules()` does (snapshot key `inside`,
   `git.inside_base` after setup). A task that leaves new ones is marked partial and the clone
   is kept; unseen, they went with the clone while the task was called done.
5. **Nothing is "done" without evidence.** Task status comes from the agent's structured
   verdict (`WORKER_SCHEMA`); a CLI exit 0 is not success. Plan state is `done` only if every
   task is done, no check regressed on the verified tip (`checks.final_tip`), and setup
   succeeded. `run_plan` exits 0 only for `done`. `plan_state()` and `nothing_to_do()` both ask
   `checks_due()`: with a baseline on record, a plan whose last commit has no check results (a run
   stopped while they ran) is `paused`, never `done`. Without git `final_tip` is None before and
   after, so the results themselves must be there, and they are dropped in the save that starts a
   task in place. Checks that ran before the work and can't be run after it are recorded
   (`checks.skipped`) and said, not passed over in silence.
   A run that loses its output stops its agents and exits 128 + SIGPIPE. `log()` raises `OutputLost`
   for every way a write can fail (a closed pipe, a terminal that is gone, a full disk). Only a
   listing whose reader left exits 0; a command that changes something (`queue add`, `init`) never
   does, and it changes state before it announces it. A line printed while crew is stopping or
   reporting an error goes through `say()`: if it can't be printed, the stop, its exit code and the
   queue's bookkeeping must still happen (Ctrl-C on `crew ... | tee log` stops tee too).
6. **One writer at a time.** `file_lock()` (fcntl + thread lock) guards each plan run, `queue
   run`, the queue, state, ledger, profiles and pid files. Writes go through `_write_json`
   (tmp + fsync + rename). `reap_orphans()` kills CLIs a dead crew left behind, matched by pid
   and start time. Each clone belongs to one plan file, named by `<clone>.plan`:
   `_claim_copy()` writes it on every run and refuses a clone another live plan owns; `clean`
   reads it first to find a clone's plan. The plan also records its own path as `git.owner`, so
   a copied plan file is refused after the pointer is gone. Plans outside `~/.crew/plans` get a
   path hash in their branch name (`_branch_name`). A plan's lock and pid file are named after
   its path (`plan_lock_name`), so `_migrate_key` moves a plan file only while holding that
   lock, after reaping its pid file. A project's name comes from its `origin`, which the user
   can change at any time, so a checkout's plans are found by their `workdir` in every plans
   folder (`plans_for(key, root)`), its profile is carried to the new name
   (`_profile_of_checkout`), and `main()` follows a plan that was moved (`_where_plan_is`). A
   plan whose `git.owner` names another file that still exists and still records itself as
   owner is a copy of that file, and is refused whatever state the original is in.
   A plan is one file, whatever path names it. `run_plan` resolves the path before anything else:
   a save replaces a symbolic link with a file, and the lock taken under the link's target would
   not be the one later commands work out. `_claim_copy` compares recorded paths resolved, by
   path and never by inode: a folder that was moved with a link left behind is the same plan,
   a hard link is still a copy. A path is not a repository either: a plan under another name is
   a checkout's only if the repository has the commit it records (`other_repository`), and a
   profile is carried to a new name only then (`first_commit`, else the path's plans). A profile
   shared by several checkouts lists them in `roots`. `queue run` groups items by checkout (same
   key or same workdir), and follows a moved plan with `_where_plan_is` like `main()` does.
7. **Nothing read from disk or a CLI is trusted.** Validate types (`num()`, `load_config`,
   `load_profile`, `validate_plan`, the plan `git` block), reject control characters in task
   fields, decode subprocess output with `errors="surrogateescape"` (git) or `"replace"`, treat
   a reply crew can't parse as a failed attempt (never a traceback), and detect usage limits
   only from the provider's own error message (`LIMIT_RE`), never from raw stdout. `_read_json`
   is the only reader of crew's files and bounds their nesting (`MAX_JSON_DEPTH`), because
   `deepcopy` and `json.dumps` overflow on depth that `json.loads` accepts. Pointer and pid
   files are read with `errors="replace"` and checked before use. Valid JSON is not valid
   input: `num()` takes finite numbers only (`1e999`, `NaN` and an integer too large for a
   float all parse), timeouts are capped (`MAX_TIMEOUT_SEC`), a switch is a JSON boolean (the
   string `"false"` is true to Python), what crew itself records in a
   plan (`commit`, `committing`, `untracked_before`, `git.tip`, `git.untracked_base`) is
   type-checked when the plan is loaded, and a queue item needs an absolute `workdir`.
   So does a plan (`plan_workdir`), and a profile's `root`. A plan's `workdir` must also be the
   top folder of its repository (`git clone` of a folder inside one fails). A switch is a
   boolean and nothing else: settings are read by truth value and `"false"` is a true string,
   `verify.unsandboxed` included. `git.branch_prefix` can't be empty: the prefix is how a
   plan's branch is told from the user's. `checks.baseline`, `final` and `regressions` are
   what crew records and decide `done`, so they are type-checked with the rest. Unknown keys are
   reported one level into each section too (`_note_unknown_keys`).
   `main()` ends with a last resort that turns any other exception into one line and exit 2.
   It is never the fix for a known bad value: the test harness fails any test that reaches it.
   `main()`'s handlers print through `say()`: the reader of the output may be gone, and a
   `BrokenPipeError` raised inside an `except` clause is caught by none of the clauses beside it.
   The repository's own files are input as well: `detect_project` takes from `package.json` only
   values of the type npm gives them. Text is not always text: a JSON escape can hold half a
   surrogate pair, which can't be printed or put in a commit message, so `validate_plan` refuses it
   in task fields (`not_text()`; the halves Python itself uses for a byte that isn't UTF-8, in a goal
   typed on the command line, go back out as that byte and are left alone) and `log()` escapes what
   the output can't encode; a workdir or plan path that is
   not UTF-8 is refused by name (`need_utf8`), because it can't be written into the JSON that
   carries Claude's write denials. Numbers are bounded where they are added up
   (`MAX_USAGE_PER_CALL`): two finite token counts can sum to infinity. `topo_order` uses its own
   stack, so a long chain of dependencies is not a recursion error.
8. **Codex `error` events are not failures.** Success is `turn.completed` and no `turn.failed`
   with exit 0; "Reconnecting..." notices are diagnostics.
9. **Every subprocess has a timeout, runs in its own session, and is killed as a tree.**
   `_exec` is the only way to start a CLI; the short probes (`login_status`, `contract_problem`,
   the two sandbox tests, `doctor`'s version check, `codex-login`) go through it by way of
   `_probe()`, or a worker thread of `queue run` sits in one that no stop can reach.
   SIGTERM/SIGHUP become `SystemExit` so children die with crew, and `main()` calls `stop_all()`
   for a `SystemExit` wherever it was raised.
   A child is started and recorded inside `_signals_held()`, and killed inside it, so
   a stop signal can't land between a child's start and its record or cut a kill short; the
   first stop signal makes crew ignore later ones. `_exec` drops a child's record only once the
   child has exited. A signal that was ignored when crew started (nohup) stays ignored.
   Later stop signals get a handler that does nothing, not `SIG_IGN`: Python raises when a
   signal already waiting finds `SIG_IGN`. `_signals_held()` replays every signal it held, in
   order, so one that was being ignored hides none behind it. Worker threads (`queue run`)
   can't hold signals, so `_exec` checks `STOPPING` again once its child is on record, and the
   main thread looks at its workers a few times a second instead of waiting for them: Python
   acts on a signal only when the main thread next runs, whichever thread the system handed it
   to. It starts the worker threads inside the `try` that stops them, and touches the pool and
   its futures only inside `_signals_held()`: their locks are taken by Python code, and a handler
   that raises while one is held (inside `concurrent.futures.wait`, say) leaves it held for good,
   with a worker waiting for it and crew waiting for the worker. No child is given the
   terminal: every `subprocess.run` and `Popen` in `crew.py` passes `stdin=` or `input=` (a
   test reads the source). A background job that touches the terminal is stopped by job
   control, and crew with it, holding the plan lock.
10. **Credentials stay with their provider.** `provider_env()` strips the other provider's keys,
    for every start of a CLI (`doctor`'s version probe and `codex-login` included);
    sandboxed checks get no `SECRET_ENV` variables at all; keys are never logged or put on a
    command line (`codex-login` pipes via stdin). Claude workers get
    `sandbox.filesystem.denyRead` for Codex's login store; Codex has no such setting (see below).
11. **A worker never starts crew.** Workers load the user's own skills and instructions, which
    include the `crew` skill that `crew install` writes. Every worker and every check runs with
    `CREW_TASK` set, and `main()` refuses `plan`, `go`, `do`, `run` and `queue run` when it is.
    Codex passes its commands only the variables the owner's `shell_environment_policy` lets
    through, so crew also sets it with `-c shell_environment_policy.set.CREW_TASK`.

## Engineering rules

- Python 3.9+, standard library only. No new dependencies.
- `crew.json` must equal `crew.DEFAULTS` (a test enforces it). Change both.
- Every fixed defect gets a test in `test_crew.py` that reproduces it with the fake CLIs.
  Run `python3 -m unittest test_crew` before committing (about seven minutes; it needs git and
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
- A plan's lock is named after its file's path. Renaming a running plan's file, or deleting
  its `<clone>.plan` pointer by hand, while it runs lets a second run into the same clone and
  lets `crew clean --all` remove it. The README tells the user not to; nothing enforces it.
- The stop-signal tests for `queue run` slow a process start or aim a signal at a thread with
  Linux's `tgkill`; the real window is about a millisecond and was hit with unmodified timing
  only by a reviewer's aimed script. The two added after the 3.7 review send the signal from
  inside the call that starts a worker thread, and at a moment the main thread holds the lock of
  one of the pool's futures. An aimed script hit the first with unmodified timing in most tries,
  the second once in about fifty.
- Choosing the idle provider for an agent that prefers either is best effort: two projects that
  start at the same moment in `queue run` can both see the same provider idle and both take it;
  one then waits. `test_two_projects_run_in_parallel_on_both_providers` failed once that way
  under load.
- Two plan files written before 3.3 (no `git.owner`) that already name the same branch are
  refused only while one of them has a live clone; after that they share the branch.
- SIGKILL cannot be held off. One that lands in the few milliseconds between a tracked git
  command starting and its pid being recorded leaves that git running unrecorded; the next run
  fails once ("another process may still be writing there") and succeeds when repeated.
- Submodules are tested for: an update, a removal and an unpushed commit of the user's (alone
  and beside submodules that are fine), a submodule moved to another folder, a move of a
  checkout that was cut short, an agent's file in the way of one, files a task leaves inside a
  submodule, and an agent swapping a submodule's `.git`. A submodule commit that isn't pushed
  and is recorded by a commit the user adds to a crew branch later (not the one the plan starts
  from) stops the plan until it is pushed; so does a rebuilt clone of such a branch. Nested
  submodules are untested: one whose checkout is cut after its parent's finished, one that
  fails to initialise inside one that did, one the user adds mid-plan. No real sandboxed worker
  was used to make the swap.
- A file the stopped adoption merge wrote at a path git ignores stays in the clone after the
  undo. git never lists it, so it is never committed, and a repeated merge writes over it.
- The `crew` skill was written from each tool's documentation. No tool has been watched loading
  it or handing work to crew through it.
- Reads. Both sandboxes limit writes and the network, not reads. A Codex worker and a check can
  read the owner's home directory, Claude's stored login included; `--sandbox workspace-write`
  and `-P :workspace` offer no read denial. A failing check's output goes into the plan file and
  into the fix task's prompt as it is.
- `sandbox.excludedCommands` in Claude's managed settings file is read like the owner's own, but
  only the owner's own (`~/.claude/settings.json`, `$CLAUDE_CONFIG_DIR`) was tried with the real CLI.
- A different repository at a path crew knew is told apart by commits crew recorded there: a
  profile's `first_commit`, or the commits the path's plans started from. A profile written
  before `first_commit` existed, for a folder that never had a plan, has neither; after a
  change of `origin` it is copied to whatever repository is at that path, with a note. Plans in
  the folder of the checkout's current name are not compared at all, as before 3.7.
- The `tokens` metric is approximate: the two CLIs count differently.
