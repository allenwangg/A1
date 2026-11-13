# crew: rules applied to facts

A record of what crew promises, what was established while building it, and whether each
promise holds on the evidence. Written on 2026-11-13 for crew 3.1 and updated the same day for 3.2 to 3.6. `CLAUDE.md` states the rules
for changing crew; `README.md` states the promises to users. This file joins them to the facts.

## 1. Facts

### What crew is

A single-file Python tool that plans a goal into tasks for three agents (Astra, Sol, Luna), runs
each task on either Claude Code or Codex, balances usage between the two across all of the
owner's projects, and keeps every run on its own branch in its own clone. Branch
`claude/crew-orchestrator` of `allenwangg/A1`, directory `crew/`.

### Environment the facts come from

- Linux container, Python 3.13, git 2.x, bubblewrap and socat installed during the session.
- `claude` 2.1.318, logged in with a Claude plan (OAuth). Real calls used `--model haiku`.
- `codex` 0.166.0, never logged in. `codex sandbox` works without a login and was used for
  real; `codex exec` as a worker was only ever exercised with fake CLIs and a mock server.
- No macOS or Windows machine was available.
- Antigravity's command-line tool and Gemini CLI were not available: this session could not run
  downloaded programs, so everything about them comes from their documentation.

### The owner's projects

`list_repos` showed about 60 repositories. The active ones are Vite + React + TypeScript apps
(Lovable-style, npm with a stray `bun.lockb`), one large JavaScript monorepo
(`Pathwise-Education-Platform-Private`), Python bots with `requirements.txt` (`pythia`,
`kalshi-bot`), Jupyter notebooks (`A1`), static HTML sites, and one SvelteKit app. Detection
and the test fixtures were built around these shapes.

### History of the build (2026-11-04 to 2026-11-06, plus this day)

| Version | What changed | Review round | Outcome |
|---|---|---|---|
| 1.0 | Planner, three agents, two providers, usage split, cooldowns | 4 reviewers + 4 verifiers | 13 confirmed defects, all fixed |
| 1.1 | Sandboxed Claude workers, read-only planner, user settings only | 2 reviewers + 2 verifiers | 10 confirmed, all fixed |
| 2.0 | Global install, per-project profiles, detected setup/checks, branch per run, queue across projects | 4 + 4 | 21 confirmed, all fixed |
| 2.1 | Locks, CLI contract check, journaled task state, `crew do` | fault-injection hunt, 4 + 4 | 30 confirmed, all fixed |
| 3.0 | Separate worktree per plan, journaled commits, orphan reaping, setup only on base commit | 4 + 4 | about 25 confirmed, all fixed |
| 3.1 | Standalone clone instead of linked worktree, sandbox denies git writes, journaled clone lifecycle, checks tied to the verified commit | 3 hunters + 3 verifiers (the first attempt, 2026-11-06, died on the weekly usage limit; rerun 2026-11-13) | 11 confirmed (10 distinct), 2 refuted; all confirmed fixed in 3.2 |
| 3.2 | Refused commits delivered on the next run, the user's branch re-read every run, one plan per clone, hashed branch names for `--out` plans, tracked git processes, refusal to run with crew's home in a temp directory | 3 hunters + 3 verifiers, 2026-11-13 | 16 confirmed, 2 refuted; 15 fixed in 3.3 and one fixed in part (see R2 gaps) |
| 3.3 | Submodule checkouts follow the user's commits, inherited git location variables dropped, the adoption of the user's commits journaled, a vanished clone's losses journaled with the rebuild, plan files moved only under their own lock, `git.owner`, stop signals held during spawn and kill, bounded JSON nesting | none yet | |
| 3.4 | Every submodule's `.git` checked before crew runs git in its copy; worker flags that keep a worker contained can no longer be dropped by a custom `args` | 3 hunters + 3 verifiers, 2026-11-13; the last verifier was run again after the machine restarted | 7 confirmed by two verifiers and fixed in 3.6; 11 more, 3 medium and 8 low, were still with the third verifier when 3.6 was cut |
| 3.5 | The `crew` skill for Claude Code, Codex and Antigravity, written by `crew install`; crew refuses to start inside its own task; a signal ignored at start stays ignored; unknown settings are reported; `crew plans` lists plans kept elsewhere while they have a copy | none yet | |
| 3.6 | Only the `--settings` Claude reads is passed on, with crew's rules in it; an adoption merge that stops part-way is taken back; submodules the user removed or hasn't pushed; `merge.autoStash` and `clone.defaultRemoteName` no longer change crew's git; every message names the plan file | none yet | |

Counts are the confirmed findings reported at the end of each round; some defects were found
by more than one reviewer, so the distinct total is somewhat lower than their sum (about 125). Every finding a verifier confirmed was fixed and got
a test. Findings the verifiers refuted were left alone, and are listed in section 4.

### Real runs (Claude haiku workers, Codex sandbox for checks)

- 2.0: a TypeScript app and a copy of `A1` through `crew queue run --jobs 2`; both planned,
  ran, committed and verified. Detected `npm test` broke on Node 22's `node --test dist/`
  (a project defect, not crew's) and was fixed by hand.
- 3.0: a Python bot with a dirty checkout; the checkout's files were byte-identical after the
  run, setup made the `.venv`, both checks ran in the sandbox, one commit landed on the
  `crew/` branch, and the worktree was removed.
- 3.1: the same bot and a static HTML site; both finished `done`. The user checkouts changed
  only in `.git` (the new `crew/...` ref). The static-site link check ran before and after.
- 3.2: a small Python project with a modified tracked file and an untracked file, through
  `crew do`. The checkout's files, HEAD and `git status` were identical afterwards, both checks
  passed in the sandbox before and after, one commit landed on the `crew/` branch, and the
  clone was removed.
- 3.3 and 3.4: the same kind of run through `crew do`, on a Python project with a modified tracked
  file and an untracked file. The checkout's files, HEAD and `git status` were identical
  afterwards, both checks passed in the sandbox before and after, one commit landed on the
  `crew/` branch, and the clone was removed.

### Direct probes of the two sandboxes (this machine)

- Claude, `acceptEdits` + sandbox + `allowUnsandboxedCommands: false`: writes to `/root`
  refused, network refused, writes under `/tmp` allowed. With `denyWrite` set to the clone's
  `.git` and the user's repo, `touch .git/PROBE`, `git commit`, `git config` and a write into
  the user's repo were all refused; a write to the working directory succeeded.
- Claude with bubblewrap removed: Bash ran **unsandboxed** and created `/root/NO_SANDBOX_PROOF.txt`
  while the CLI reported no denial. This is why `claude_sandbox_ready()` exists.
- Claude in plan mode with `--disallowedTools Edit,Write,NotebookEdit` and a user allow rule
  for `Bash(python3 *)`: it ran the shell command (reviewer-confirmed). This is why the planner
  uses `--tools Read,Grep,Glob`.
- Claude loading a repo's `.claude/settings.json`: `defaultMode: acceptEdits` and a
  `PreToolUse` hook were honoured (reviewer-confirmed). This is why every call uses
  `--setting-sources user`.
- Claude in a linked git worktree: it wrote the user's shared `.git` (HEAD, index, refs,
  config) and moved the user's `main` (reviewer-confirmed, 3.0). This is why 3.1 uses a
  standalone clone plus `denyWrite`.
- Codex `sandbox -P :workspace`: writes inside the workspace allowed, `.git` read-only,
  `/root` read-only, network refused, `/tmp` allowed, exit codes passed through.
- Codex `sandbox -P :workspace` with crew's home under `/tmp` (reviewer-confirmed, 3.1): crew's
  `config.json`, a project profile, another plan's clone and the `.plan` pointer were all
  writable, and a setup command written into the profile from inside the sandbox ran
  unsandboxed as root on the next run. The same layout under `/root` was read-only on every
  target. This is why 3.2 refuses a home in a temp directory.
- Real Claude worker (haiku) and the real Codex sandbox against the 3.1 clone, home outside
  `/tmp` (reviewer, 3.1): neither could write the clone's `.git`, the user's repo, crew's home,
  a sibling clone or `$HOME`.
- `git clone --no-checkout` of a repo whose HEAD is on `crew/x` gives the clone a local branch
  `crew/x`, so `checkout -b crew/x` fails there (reviewer-confirmed). `git fetch -q` prints
  nothing when it rejects a non-fast-forward. A local clone copies unreachable objects too, so
  a deleted branch's commits still exist in a fresh clone.
- Codex `sandbox -P :workspace` in a repository with a submodule (probed 2026-11-13): a command
  inside the sandbox replaced the submodule's `.git` file with a directory. A 3.2 reviewer saw
  plain git 2.43 run a filter command from such a replaced repository during `git status` in the
  parent, with crew's own `-c` options. This is why 3.4 checks those files before running git.
- Claude 2.1.323 reads only the last `--settings` on its command line and ignores earlier ones
  whole (reviewer, 3.4, with a real haiku worker): with two, crew 3.4 put its sandbox rules in
  the first, reported the worker contained, and the worker wrote a marker file outside its
  working directory and into `.git`. With 3.6 the same run is refused both writes.
- `git merge --ff-only` that is interrupted while writing files leaves HEAD where it was, part
  of the new files in place and an index lock; git then refuses the same merge (reviewer, 3.4).
  `merge.autoStash=true` in the user's config makes a refused merge succeed and return the
  collision as conflict markers. `clone.defaultRemoteName` renames the clone's `origin`.
- With `providers.claude.args` set to `["--verbose"]`, crew 3.2 started the Claude worker with
  no permission mode, no sandbox settings and no write denials; with `providers.codex.args` set
  to `["--color", "never"]` it started Codex without `--sandbox workspace-write` (found with the
  fake CLIs while fact-checking documentation, 2026-11-13). This is why 3.4 adds back what a
  custom `args` leaves out.
- git exports an absolute `GIT_DIR` to shell aliases and hooks run in a linked worktree, and
  `GIT_INDEX_FILE` to hooks (reviewer-confirmed, 3.2): a crew started that way committed inside
  the user's repository. `git merge --ff-only` and `git checkout -B` move a gitlink in the index
  but not the submodule's checkout, and `git add -u` then stages the stale checkout.
- Codex `exec --json`: emits `{"type":"error","message":"Reconnecting..."}` on transient
  failures and then succeeds; `codex login status` exits 1 even when `CODEX_API_KEY` is set
  although `exec` authenticates with it.

## 2. Rules

The seven promises in README "What crew guarantees" (R1 to R7), plus the engineering laws in
`CLAUDE.md` that implement them. R1 your work is safe. R2 crashes don't lose or repeat work.
R3 one run per plan. R4 agent-modified code never runs outside a sandbox. R5 nothing is called
done without evidence. R6 commits contain only the task's work. R7 bad input gives an error,
not a mess.

## 3. Application: evidence for each rule

Evidence classes: **T** = a test in `test_crew.py` that reproduces the failure with fake CLIs;
**R** = a real run with the Claude CLI; **P** = a direct probe of a sandbox or CLI; **V** = an
independent reviewer reproduced the defect on the old code and confirmed the fix on a copy.

| Rule | Evidence | Gaps |
|---|---|---|
| R1 Your work is safe | T: `test_users_checkout_is_never_touched`, `test_users_repo_gets_no_crew_or_cli_files`, `test_claude_gets_write_denied_on_crew_git_and_users_checkout`, `test_agent_switching_branch_stops_without_committing`, `test_plan_git_block_must_name_a_crew_branch`, `test_your_commits_on_the_crew_branch_are_built_on`. R: 3.0 and 3.1 runs with dirty checkouts, file hashes unchanged. P: `denyWrite` probe. `test_refused_publish_is_kept_and_delivered_on_the_next_run`, `test_deleted_crew_branch_is_put_back`, `test_non_fast_forward_refusal_gives_the_reason_then_reports_divergence`. 3.6: `test_merge_autostash_does_not_turn_a_collision_into_conflict_markers`, `test_clone_default_remote_name_does_not_stop_crew_reading_your_branch`, `test_a_submodule_commit_you_have_not_pushed_is_not_replaced_by_its_remotes`, `test_a_submodule_your_commit_removes_is_not_held_against_the_waiting_task`, `test_messages_about_an_older_plan_name_its_file`. 3.3: `test_git_env_vars_never_touch_the_users_repo`, `test_your_submodule_bump_on_the_crew_branch_is_not_reverted`, `test_a_file_the_rebuild_and_your_commit_both_add_is_not_a_dead_end`, `test_delivery_hints_name_the_plan_file`. V: the linked-worktree escape (3.0) was the reason for the clone; the 3.1 round attacked the clone with a real Claude worker and the real Codex sandbox and neither wrote outside it, and every lifecycle scenario left the user's index, working tree and other refs unchanged. | Codex worker never run with a real login (its sandbox was probed directly). Repos under `/tmp` are writable by both sandboxes. The 3.2 round ran 2,862 lifecycle cases without a change to the user's HEAD, index, working tree or other refs, apart from the git-variable and submodule defects above. Submodules have two tests. 3.3 and 3.4 have not been independently attacked. |
| R2 Crashes don't lose or repeat work | T: `test_crash_points_resume_without_losing_or_repeating_work` (branch-recorded, before-commit, after-commit), `test_crash_while_making_the_copy_or_removing_it_resumes` (clone-made, removing), `test_sigkill_mid_task_kills_the_orphan_and_keeps_its_files`, `test_copy_removed_with_uncommitted_work_reruns_the_task`, `test_recovered_commit_is_verified_again`, `test_interrupted_task_resumes_with_a_note`, `test_usage_limit_pauses_with_task_pending`, `test_atomic_write_leaves_no_temp_files`. V: reviewers killed 3.0 with real SIGKILL at each of these points and confirmed the fixes on patched copies; the 3.1 round killed crew at 13 points of the clone lifecycle, during setup and during `git clone`, and every resume finished without repeating a task, except that an orphaned `git clone` raced the next run (fixed in 3.2: `test_long_git_commands_are_recorded_and_reaped`). 3.2 also adds `test_refused_publish_is_kept_and_delivered_on_the_next_run` and `test_clean_all_says_when_it_discards_commits`. 3.6: `test_an_adoption_that_was_cut_short_is_taken_back_and_done_again`, `test_a_cut_short_adoption_of_a_commit_you_took_back_is_not_committed_as_a_task`, `test_an_adoption_git_gives_up_on_part_way_is_not_a_dead_end`, `test_guard_a_task_file_in_the_way_is_never_taken_back`, `test_a_submodule_move_that_was_cut_short_is_finished_by_the_next_run`; a reviewer restored 300 random half-merged states exactly. 3.3: `test_an_interrupted_rebuild_does_not_count_an_uncommitted_task_done`, `test_interrupted_commit_then_your_commit_still_lands_the_task`, `test_kill_between_adopt_merge_and_recording_resumes`, `test_a_second_stop_signal_does_not_abort_the_kill`; the 3.2 round found each of these on 3.2 with real kills and signals, including a real Claude worker left running after a terminal was closed. | A kill during the publish fetch has no test of its own; the fetch is tracked and retried. A SIGKILL within a few milliseconds of a tracked git command starting leaves that git unrecorded (the 3.2 finding fixed only in part: catchable signals are held, SIGKILL cannot be); the next run fails once with a clear message. A nested submodule whose own checkout is cut after its parent's finished is not covered. Disk-full during a plan save was reproduced by a reviewer on 2.x and fixed (`_write_json` cleans up; `record_usage` is best effort) without a test. |
| R3 One run per plan | T: `test_second_run_of_the_same_plan_is_refused`, `test_clean_leaves_running_and_out_of_tree_plans_alone`, `test_two_projects_run_in_parallel_on_both_providers`. `test_rebuilt_copy_of_an_out_of_tree_plan_is_safe_from_clean`, `test_plans_with_the_same_file_name_never_share_a_copy`, `test_a_copy_that_belongs_to_another_plan_is_refused`. V: reviewers ran two `crew run`s and two `queue run`s concurrently on 2.x (double execution, commits on `main`) and confirmed the lock fix; on 3.1 they removed a running `--out` plan's clone with `clean --all` and made two same-named plans share a clone, both fixed in 3.2. 3.3: `test_adding_a_remote_mid_run_does_not_start_a_second_run`, `test_a_queued_plan_is_resumed_not_replanned_after_a_remote_is_added`, `test_clean_keeps_a_running_copy_even_with_a_stray_in_tree_plan`, `test_a_copied_plan_file_is_refused_even_after_clean_all`; on 3.2 a `git remote add` during a run let a second run start in the same clone. | Locks use `fcntl`; on Windows they are per-process only. 4 simultaneous `queue add` processes lost items on 2.x; fixed with `file_lock("queue")`, covered only by review, not a test. Two plan files from before 3.3 that already name the same branch are refused only while one has a live clone. |
| R4 Agent-modified code never runs outside a sandbox | T: `test_setup_runs_in_the_worktree_at_the_base_commit` (agent writes a `postinstall`, deletes `node_modules`; setup never runs on it), `test_in_place_setup_never_reruns_after_agents`, `test_tampered_git_config_stops_crew_before_it_runs_git`, `test_broken_codex_sandbox_skips_checks_instead_of_passing_them`, `test_incompatible_cli_is_taken_out_of_rotation`. P: both sandboxes, the no-bubblewrap case, plan-mode Bash, repo settings. `test_home_in_a_temp_directory_is_refused_without_the_opt_in`. 3.6: `test_a_second_settings_flag_cannot_drop_the_sandbox`. 3.4: `test_extra_worker_flags_keep_the_sandbox`, `test_a_sandbox_you_turn_off_yourself_gets_no_shell`, `test_a_submodule_git_file_replaced_by_an_agent_stops_crew`. V: nested-manifest and `.pth`-in-venv attacks reproduced on 2.x/3.0 and the fixes confirmed; on 3.1 a Codex sandbox with crew's home under `/tmp` rewrote a profile's setup command and crew ran it unsandboxed, which 3.2 refuses. | Submodules: the swap of a submodule's `.git` is checked since 3.4 and tested with a fake agent; the whole chain (a real sandboxed worker making the swap and git obeying the swapped repository inside a crew run) was not run end to end. `$HOME` is trusted: the global git config, `~/.claude`, `~/.codex` and `~/.crew` live there and both sandboxes keep it read-only (probed on 3.1). A machine whose `HOME` a sandbox can write has no protection crew could add. With `CREW_ALLOW_TEMP_HOME=1` the temp-directory protection is off by the user's choice. |
| R5 Nothing is called done without evidence | T: `test_partial_blocks_dependents_until_retry`, `test_verify_regression_triggers_fix_task`, `test_preexisting_failure_is_not_a_regression`, `test_needs_review_rerun_exits_nonzero_and_does_nothing`, `test_recovered_commit_is_verified_again`, `test_queue_planning_limit_pauses_the_item`. `test_deleted_crew_branch_is_put_back` (3.1 called a plan done and removed the clone while the user's repo had no branch at all). R: 3.1 runs reported `done` only after both check passes. | Verdicts come from the model; an agent can claim `done` falsely. The checks are the backstop, and they are only as strong as the project's tests. |
| R6 Commits contain only the task's work | T: `test_generated_and_previously_ignored_files_are_never_committed` (agent un-ignores `.gitignore`, writes `__pycache__`), `test_go_branch_commits_and_return` (one commit per task, work only on the branch), `test_commit_signing_config_does_not_break_commits`, `test_existing_crew_branch_name_falls_back`. V: `__pycache__` and sandbox-stub commits reproduced on 3.0 with real Claude and fixed. | Nested git repositories an agent creates are reported, not committed; the user has to handle them. |
| R7 Bad input gives an error, not a mess | T: `test_garbled_cli_reply_is_a_failed_attempt_not_a_crash`, `test_hostile_plans_and_goals_are_refused_before_any_agent_runs`, `test_bad_inputs_give_clean_errors`, `test_shipped_template_matches_defaults`, `test_layers_and_agent_replacement`, `test_two_checkouts_of_one_repo_keep_their_own_plans`, `test_adding_a_remote_keeps_the_profile_and_plans`, `test_profile_without_a_name_is_a_clear_error`, `test_deeply_nested_json_is_a_clean_error_not_a_traceback`, `test_a_corrupt_pointer_stops_neither_run_nor_clean`. V: reviewers fed non-UTF-8 file names, NUL in task ids, 50 MB replies, wrong-typed state files, `CREW_HOME='~'`, a workdir with a trailing space, Python 3.8; each was fixed and confirmed. | Not every hostile input has its own test; the fixes for state-file typing and non-UTF-8 names are covered by review only. |

Stack detection (not a guarantee, but it decides what the checks are): 12 tests cover Vite +
TypeScript, pnpm + Svelte monorepos, Lovable's double lockfile, Python with pytest, unittest and
dev requirements, notebooks, static sites with the link check, and Flask templates. Reviewers ran
the detected commands for real inside `codex sandbox` on fixtures of each stack.

## 4. Conclusions

- **R1, R2, R3, R6, R7 hold on Linux on the evidence above**, with the gaps listed. Each has
  tests that reproduce the defect that once broke it, and real runs on 3.1 behaved as promised.
- **R4 holds on this machine**, and since 3.2 crew refuses the one layout where a reviewer broke
  it (its own home in a temp directory). `$HOME` is inside the trust boundary by design.
- **R5 holds as stated**, and the statement is deliberately narrow: it is a promise about
  bookkeeping, not about correctness of the agents' code.
- **3.1 was independently attacked on 2026-11-13**: three hunters, and a verifier that re-tested
  each finding. 11 findings were confirmed (10 distinct) and 2 refuted. Every confirmed one is
  fixed in 3.2 with a test that fails on 3.1, and the reviewers' own reproduction scripts,
  rerun on 3.2, all behave as fixed. Three of the eleven broke R2 or R5 outright (commits that
  could never be delivered, `clean` deleting undelivered commits, `done` with no branch).
- **3.2 was independently attacked on 2026-11-13**: three hunters and a verifier per finding.
  16 findings were confirmed and 2 refuted. Three were high: a submodule update the user
  committed on a crew branch was reverted by crew's next commit; an interrupted rebuild of the
  clone counted a task done whose work was lost; and a git remote added during a run let a
  second run start in the same clone. 3.3 fixes 15 with a test that fails on 3.2, and the
  sixteenth in part.
- **3.4 closes two gaps that no review round reported.** A custom `providers.<p>.args` dropped
  the flags that turn each worker's sandbox on, and a sandboxed agent could replace a
  submodule's `.git` with a repository whose configuration crew's own git commands would obey.
  Both came out of side checks on 2026-11-13, not out of a review of crew's code.
- **Antigravity and Gemini are not providers, by decision.** The owner asked for them to be
  connected. Their command-line tools were checked against what law 2 needs from a provider:
  neither lets crew enforce or prove a sandbox for one run, and neither can be told to ignore
  hooks and tool servers left in the working directory. They hand work to crew through a skill
  instead, which needs no trust in their sandboxes. The skill itself is untested in any real
  tool: it was written from the three vendors' documentation.
- **3.4 was independently attacked on 2026-11-13.** Two of its three verifiers had reported
  when 3.6 was cut: 7 findings, all confirmed, 2 of them high. A second `--settings` in the
  Claude worker flags switched the sandbox off while crew reported it on, and an adoption
  merge that was interrupted left half-written files that the next task committed as its own.
  3.6 fixes all 7 with tests that fail on 3.5. Eleven more findings, none high, were still
  with the third verifier.
- **3.5 and 3.6 have not been independently attacked.** Their evidence is the 102 tests. Each of
  the eight rounds so far found defects in the version before it.
- **Untested platforms:** macOS and Windows. Untested provider path: Codex as a worker with a
  real login.
- **Refuted claims, left as they are:** that `acceptEdits` understates what Claude can do
  (the README wording matches the behaviour); that `allowedTools Bash(pytest *)` is dangerous
  (it is documented as such); that check output in the fix prompt is a new injection surface
  (agents already read the repo); that `reap_orphans` can kill unrelated processes (pid plus
  start time, and the pid file is outside both sandboxes); that a non-exhaustive secret-name
  denylist leaks secrets (checks have no network and no writes outside the clone; the list was
  widened anyway); that exit 0 and
  `done` while the last commits are still only in crew's copy breaks R5 (`done` is defined by
  tasks, checks and setup; the copy is kept, the next run delivers, and `crew plans` now says
  so); that crew's git should ignore global git config (no sandbox can write `$HOME`, and
  ignoring it would break `safe.directory` and hosted git settings); that putting a branch
  back without comment after the user moved it to an older crew commit breaks a promise (it is
  the documented handling of undelivered commits); that re-running tasks after the user
  rebases or amends a crew branch is a defect (a task is done only if its recorded commit is
  on the branch, and crew will not accept a commit by its subject line).

## 5. What the facts taught, in one line each

- A sandbox that silently degrades to "no sandbox" must be probed, not assumed.
- A read-only tool list beats a permission mode: modes inherit the user's allow rules.
- Settings that live in the repo are agent-controlled; load only the user's.
- A linked worktree shares the user's `.git`; a clone does not.
- Journal before acting, snapshot before the agent runs, reconcile on resume.
- Unsandboxed steps may run only on commits the user made.
- A CLI's exit code is not a verdict; a transient "error" event is not a failure.
- Locks, atomic writes and start-time pid matching are what make SIGKILL survivable.
- Every value read from JSON is untrusted, including crew's own files.
- Each fixed defect gets a test, or it comes back.
- A branch in the user's repo is the user's to move or delete: read it every run, never trust
  a recorded copy of where it was.
- Compare commits where both sides exist. The clone has the user's commits once fetched; the
  user's repo lacks crew's until they are published.
- State that drives unsandboxed execution must live where no sandbox can write, and crew has
  to check that instead of assuming it.
- Everything long-running that crew starts needs a pid record, git included.
- Environment variables are input: git's own location variables redirect every git command.
- Moving HEAD does not move a submodule's checkout; whoever moves HEAD must move both.
- What a resume depends on must be saved in the same write as the step that creates it.
- A stop signal can arrive twice, and the cleanup it starts must not be interruptible by it.
- Ownership recorded only beside the thing owned disappears with it; the owner records it too.
- A name derived from a path (a lock, a pid file) breaks when the file is moved by someone else.
- A list of flags the user can replace is not a safeguard: add what is missing every time.
- Whatever tells git where a repository is, and sits in the working tree, belongs to the agent.
- A tool joins the crew only if its containment can be switched on and proven for each run;
  otherwise it can still be the place work is handed over from.
- Ask git whether a step will go through before taking it, and write the answer down: that
  record is what makes a half-finished step safe to undo.
- What a tool does with a repeated flag is part of its contract. Find out; don't assume.
- The user's git settings are input to crew's git commands. Pin the ones crew depends on.
- A worker inherits the user's own skills and instructions, so anything those tell a tool to
  do, a worker may try: crew must refuse itself.
