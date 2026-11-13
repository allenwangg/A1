# crew: rules applied to facts

A record of what crew promises, what was established while building it, and whether each
promise holds on the evidence. Written on 2026-11-13 for crew 3.1. `CLAUDE.md` states the rules
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
| 3.1 | Standalone clone instead of linked worktree, sandbox denies git writes, journaled clone lifecycle, checks tied to the verified commit | 3 hunters | **did not run**: weekly usage limit; relaunched 2026-11-13 |

Counts are the confirmed findings reported at the end of each round; some defects were found
by more than one reviewer, so the distinct total is somewhat lower than their sum (about 100). Every finding a verifier confirmed was fixed and got
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
| R1 Your work is safe | T: `test_users_checkout_is_never_touched`, `test_users_repo_gets_no_crew_or_cli_files`, `test_claude_gets_write_denied_on_crew_git_and_users_checkout`, `test_agent_switching_branch_stops_without_committing`, `test_plan_git_block_must_name_a_crew_branch`, `test_your_commits_on_the_crew_branch_are_built_on`. R: 3.0 and 3.1 runs with dirty checkouts, file hashes unchanged. P: `denyWrite` probe. V: the linked-worktree escape (3.0) was the reason for the clone; the clone itself has not been independently attacked yet. | Codex worker never run with a real login (its sandbox was probed directly). Repos under `/tmp` are writable by both sandboxes. 3.1 security hunt pending. |
| R2 Crashes don't lose or repeat work | T: `test_crash_points_resume_without_losing_or_repeating_work` (branch-recorded, before-commit, after-commit), `test_crash_while_making_the_copy_or_removing_it_resumes` (clone-made, removing), `test_sigkill_mid_task_kills_the_orphan_and_keeps_its_files`, `test_copy_removed_with_uncommitted_work_reruns_the_task`, `test_recovered_commit_is_verified_again`, `test_interrupted_task_resumes_with_a_note`, `test_usage_limit_pauses_with_task_pending`, `test_atomic_write_leaves_no_temp_files`. V: reviewers killed 3.0 with real SIGKILL at each of these points and confirmed the fixes on patched copies; the 3.1 clone lifecycle has not been independently killed yet. | Kill during `git clone` or during publish (fetch into the user's repo) is journaled but not covered by a test. Disk-full during a plan save was reproduced by a reviewer on 2.x and fixed (`_write_json` cleans up; `record_usage` is best effort) without a test. |
| R3 One run per plan | T: `test_second_run_of_the_same_plan_is_refused`, `test_clean_leaves_running_and_out_of_tree_plans_alone`, `test_two_projects_run_in_parallel_on_both_providers`. V: reviewers ran two `crew run`s and two `queue run`s concurrently on 2.x (double execution, commits on `main`) and confirmed the lock fix. | Locks use `fcntl`; on Windows they are per-process only. 4 simultaneous `queue add` processes lost items on 2.x; fixed with `file_lock("queue")`, covered only by review, not a test. |
| R4 Agent-modified code never runs outside a sandbox | T: `test_setup_runs_in_the_worktree_at_the_base_commit` (agent writes a `postinstall`, deletes `node_modules`; setup never runs on it), `test_in_place_setup_never_reruns_after_agents`, `test_tampered_git_config_stops_crew_before_it_runs_git`, `test_broken_codex_sandbox_skips_checks_instead_of_passing_them`, `test_incompatible_cli_is_taken_out_of_rotation`. P: both sandboxes, the no-bubblewrap case, plan-mode Bash, repo settings. V: nested-manifest and `.pth`-in-venv attacks reproduced on 2.x/3.0 and the fixes confirmed. | `~/.gitconfig` is under `/root` here, which both sandboxes keep read-only; on a machine where `HOME` is writable to a sandbox, a global git config could affect crew's own git calls. Not probed. Pending in the 3.1 security hunt. |
| R5 Nothing is called done without evidence | T: `test_partial_blocks_dependents_until_retry`, `test_verify_regression_triggers_fix_task`, `test_preexisting_failure_is_not_a_regression`, `test_needs_review_rerun_exits_nonzero_and_does_nothing`, `test_recovered_commit_is_verified_again`, `test_queue_planning_limit_pauses_the_item`. R: 3.1 runs reported `done` only after both check passes. | Verdicts come from the model; an agent can claim `done` falsely. The checks are the backstop, and they are only as strong as the project's tests. |
| R6 Commits contain only the task's work | T: `test_generated_and_previously_ignored_files_are_never_committed` (agent un-ignores `.gitignore`, writes `__pycache__`), `test_go_branch_commits_and_return` (one commit per task, work only on the branch), `test_commit_signing_config_does_not_break_commits`, `test_existing_crew_branch_name_falls_back`. V: `__pycache__` and sandbox-stub commits reproduced on 3.0 with real Claude and fixed. | Nested git repositories an agent creates are reported, not committed; the user has to handle them. |
| R7 Bad input gives an error, not a mess | T: `test_garbled_cli_reply_is_a_failed_attempt_not_a_crash`, `test_hostile_plans_and_goals_are_refused_before_any_agent_runs`, `test_bad_inputs_give_clean_errors`, `test_shipped_template_matches_defaults`, `test_layers_and_agent_replacement`, `test_two_checkouts_of_one_repo_keep_their_own_plans`, `test_adding_a_remote_keeps_the_profile_and_plans`. V: reviewers fed non-UTF-8 file names, NUL in task ids, 50 MB replies, wrong-typed state files, `CREW_HOME='~'`, a workdir with a trailing space, Python 3.8; each was fixed and confirmed. | Not every hostile input has its own test; the fixes for state-file typing and non-UTF-8 names are covered by review only. |

Stack detection (not a guarantee, but it decides what the checks are): 12 tests cover Vite +
TypeScript, pnpm + Svelte monorepos, Lovable's double lockfile, Python with pytest, unittest and
dev requirements, notebooks, static sites with the link check, and Flask templates. Reviewers ran
the detected commands for real inside `codex sandbox` on fixtures of each stack.

## 4. Conclusions

- **R1, R2, R3, R6, R7 hold on Linux on the evidence above**, with the gaps listed. Each has
  tests that reproduce the defect that once broke it, and real runs on 3.1 behaved as promised.
- **R4 holds on this machine.** The one unprobed path is a writable global git config, which
  would need a `HOME` the sandboxes can write; here they cannot.
- **R5 holds as stated**, and the statement is deliberately narrow: it is a promise about
  bookkeeping, not about correctness of the agents' code.
- **3.1 has not been independently attacked.** The sixth review round died on the weekly usage
  limit and was relaunched on 2026-11-13; its findings, if any, go into the next version and
  into this file.
- **Untested platforms:** macOS and Windows. Untested provider path: Codex as a worker with a
  real login.
- **Refuted claims, left as they are:** that `acceptEdits` understates what Claude can do
  (the README wording matches the behaviour); that `allowedTools Bash(pytest *)` is dangerous
  (it is documented as such); that check output in the fix prompt is a new injection surface
  (agents already read the repo); that `reap_orphans` can kill unrelated processes (pid plus
  start time, and the pid file is outside both sandboxes); that a non-exhaustive secret-name
  denylist leaks secrets (checks have no network and no writes outside the clone; the list was
  widened anyway).

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
