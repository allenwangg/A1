# crew: rules applied to facts

A record of what crew promises, what was established while building it, and whether each
promise holds on the evidence. Written on 2026-11-13 for crew 3.1 and updated the same day for 3.2 to 3.9. `CLAUDE.md` states the rules
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
| 3.4 | Every submodule's `.git` checked before crew runs git in its copy; worker flags that keep a worker contained can no longer be dropped by a custom `args` | 3 hunters + 3 verifiers, 2026-11-13; the last verifier was run again after the machine restarted | 17 confirmed, 1 refuted; 7 fixed in 3.6 (2 of them high) and 10 in 3.7 (2 medium, 8 low) |
| 3.5 | The `crew` skill for Claude Code, Codex and Antigravity, written by `crew install`; crew refuses to start inside its own task; a signal ignored at start stays ignored; unknown settings are reported; `crew plans` lists plans kept elsewhere while they have a copy | reviewed together with 3.7 | see 3.7 |
| 3.6 | Only the `--settings` Claude reads is passed on, with crew's rules in it; an adoption merge that stops part-way is taken back; submodules the user removed or hasn't pushed; `merge.autoStash` and `clone.defaultRemoteName` no longer change crew's git; every message names the plan file | reviewed together with 3.7 | see 3.7 |
| 3.7 | A stop signal is acted on in `queue run` whichever thread the system hands it to; no process crew starts is given the terminal; a checkout's plans and profile are found after `origin` changes; a stale copy of a plan is refused; unusable numbers and wrong types in crew's own files are errors; a lost output reader is exit 141, not 0 | 4 hunters + 4 verifiers, 2026-11-13, of 3.5, 3.6 and 3.7 together (the machine restarted twice; the review resumed from its journal) | 48 confirmed (3 high, 15 medium, 30 low as the hunters rated them), 2 refuted; all 48 fixed in 3.8 |
| 3.8 | Codex is started without execpolicy rules and without trusting crew's copy; Claude gets no shell unless every command it could run is sandboxed; crew's flags come before yours; switches must be true or false; an interrupted adoption is undone without touching what a task deleted or what git ignores; one broken submodule no longer breaks the others; files left inside a submodule are reported; stops are acted on while `queue run` starts or watches its workers and while a CLI is probed; lost output is never exit 0; a plan whose last checks were stopped is `paused`; a different repository at a known path inherits nothing; a plan named through a link is locked as its file | 2 hunters + 2 verifiers, 2026-11-13, of what the current Claude and Codex CLIs offer crew's workers; and a rerun of each 3.7-review area's reproductions against the merge | 5 confirmed (1 high, 4 medium as the verifiers rated them), 1 refuted; all 5 fixed in 3.9. The rerun: 47 of 48 fixes hold on the merge; the 48th was a test count in this file. A review of the merge found nothing lost and two wrong sentences here; all fixed in 3.9 |
| 3.9 | Claude workers are offered only Read, Edit, Write, Glob, Grep, NotebookEdit and, when granted, Bash, and no MCP server; Codex's stored login is denied to Claude's file tools and to the planner; Codex runs without its sub-agent tools | none yet | |

Counts are the confirmed findings reported at the end of each round; some defects were found
by more than one reviewer, so the distinct total is somewhat lower than their sum (about 195). Every finding a verifier confirmed was fixed and got
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
| R1 Your work is safe | T: `test_users_checkout_is_never_touched`, `test_users_repo_gets_no_crew_or_cli_files`, `test_claude_gets_write_denied_on_crew_git_and_users_checkout`, `test_agent_switching_branch_stops_without_committing`, `test_plan_git_block_must_name_a_crew_branch`, `test_your_commits_on_the_crew_branch_are_built_on`. R: 3.0 and 3.1 runs with dirty checkouts, file hashes unchanged. P: `denyWrite` probe. `test_refused_publish_is_kept_and_delivered_on_the_next_run`, `test_deleted_crew_branch_is_put_back`, `test_non_fast_forward_refusal_gives_the_reason_then_reports_divergence`. 3.6: `test_merge_autostash_does_not_turn_a_collision_into_conflict_markers`, `test_clone_default_remote_name_does_not_stop_crew_reading_your_branch`, `test_a_submodule_commit_you_have_not_pushed_is_not_replaced_by_its_remotes`, `test_a_submodule_your_commit_removes_is_not_held_against_the_waiting_task`, `test_messages_about_an_older_plan_name_its_file`. 3.3: `test_git_env_vars_never_touch_the_users_repo`, `test_your_submodule_bump_on_the_crew_branch_is_not_reverted`, `test_a_file_the_rebuild_and_your_commit_both_add_is_not_a_dead_end`, `test_delivery_hints_name_the_plan_file`. V: the linked-worktree escape (3.0) was the reason for the clone; the 3.1 round attacked the clone with a real Claude worker and the real Codex sandbox and neither wrote outside it, and every lifecycle scenario left the user's index, working tree and other refs unchanged. 3.8: `test_merge_verify_signatures_does_not_refuse_your_own_unsigned_commit`, `test_one_submodule_that_cannot_be_fetched_does_not_break_the_others`, `test_your_commit_that_moves_one_submodule_and_updates_another_is_not_taken_for_tampering`, `test_an_untracked_file_git_would_not_write_over_is_not_forced_over_by_the_next_run`, `test_guard_a_cut_short_submodule_move_is_still_forced_past_generated_files`, `test_an_unpushed_submodule_commit_on_the_crew_branch_stops_with_the_real_reason`, `test_another_repository_at_a_known_path_inherits_neither_plans_nor_profile`. V: the 3.7 round reran the 3.2 delivery matrix (324 cases) on 3.7 and on the fix: no outcome changed and nothing in the user's repo moved. 3.9: `test_a_claude_worker_has_no_tool_that_makes_the_cli_write_git`. | Codex worker never run with a real login (its sandbox was probed directly). Repos under `/tmp` are writable by both sandboxes. The 3.2 round ran 2,862 lifecycle cases without a change to the user's HEAD, index, working tree or other refs, apart from the git-variable and submodule defects above. Submodules have twelve tests (`CLAUDE.md` lists what they cover). 3.8 has not been independently attacked. |
| R2 Crashes don't lose or repeat work | T: `test_crash_points_resume_without_losing_or_repeating_work` (branch-recorded, before-commit, after-commit), `test_crash_while_making_the_copy_or_removing_it_resumes` (clone-made, removing), `test_sigkill_mid_task_kills_the_orphan_and_keeps_its_files`, `test_copy_removed_with_uncommitted_work_reruns_the_task`, `test_recovered_commit_is_verified_again`, `test_interrupted_task_resumes_with_a_note`, `test_usage_limit_pauses_with_task_pending`, `test_atomic_write_leaves_no_temp_files`. V: reviewers killed 3.0 with real SIGKILL at each of these points and confirmed the fixes on patched copies; the 3.1 round killed crew at 13 points of the clone lifecycle, during setup and during `git clone`, and every resume finished without repeating a task, except that an orphaned `git clone` raced the next run (fixed in 3.2: `test_long_git_commands_are_recorded_and_reaped`). 3.2 also adds `test_refused_publish_is_kept_and_delivered_on_the_next_run` and `test_clean_all_says_when_it_discards_commits`. 3.6: `test_an_adoption_that_was_cut_short_is_taken_back_and_done_again`, `test_a_cut_short_adoption_of_a_commit_you_took_back_is_not_committed_as_a_task`, `test_an_adoption_git_gives_up_on_part_way_is_not_a_dead_end`, `test_guard_a_task_file_in_the_way_is_never_taken_back`, `test_a_submodule_move_that_was_cut_short_is_finished_by_the_next_run`; a reviewer restored 300 random half-merged states exactly. 3.3: `test_an_interrupted_rebuild_does_not_count_an_uncommitted_task_done`, `test_interrupted_commit_then_your_commit_still_lands_the_task`, `test_kill_between_adopt_merge_and_recording_resumes`, `test_a_second_stop_signal_does_not_abort_the_kill`; the 3.2 round found each of these on 3.2 with real kills and signals, including a real Claude worker left running after a terminal was closed. 3.7: `test_a_stop_signal_while_a_queue_worker_starts_an_agent_is_acted_on`, `test_a_stop_signal_that_a_queue_worker_thread_receives_is_acted_on`, `test_a_stop_signal_behind_an_ignored_one_is_not_lost`, `test_two_different_stop_signals_at_once_give_no_traceback`, `test_crew_in_the_background_of_a_terminal_is_not_stopped_by_its_probes`, `test_no_process_crew_starts_is_given_the_terminal`. R: with the real Claude CLI as a provider, `crew do ... &` typed into an interactive shell on a terminal was stopped at `claude auth status` on 3.6 and ran to `done` on 3.7. 3.8: `test_a_commit_cut_after_staging_does_not_switch_the_undo_off`, `test_a_commit_cut_after_staging_then_a_withdrawn_commit_is_not_delivered_as_the_tasks`, `test_your_change_to_a_file_a_task_deleted_is_a_collision_not_a_silent_restore`, `test_a_cut_short_adoption_never_puts_back_a_file_a_task_deleted`, `test_a_file_you_and_a_task_both_deleted_stays_deleted_when_the_adoption_is_cut`, `test_the_undo_leaves_an_ignored_file_that_was_there_before_the_merge`, `test_guard_taking_back_a_cut_short_adoption_never_deletes_through_a_symbolic_link`, `test_a_stop_signal_while_queue_run_starts_its_threads_is_acted_on`, `test_a_stop_signal_while_queue_run_looks_at_its_workers_does_not_hang_it`, `test_a_stop_while_a_queue_worker_probes_a_cli_is_acted_on`, `test_ctrl_c_with_the_output_reader_gone_is_still_a_clean_stop`, `test_queue_run_that_loses_its_reader_stops_its_agents_and_pauses_its_items`, `test_an_error_no_check_foresaw_in_queue_run_stops_the_agents_at_work`, `test_a_kill_right_after_a_regression_is_recorded_does_not_lose_the_fix_round`, `test_every_process_crew_waits_for_has_a_time_limit`. | A kill during the publish fetch has no test of its own; the fetch is tracked and retried. A SIGKILL within a few milliseconds of a tracked git command starting leaves that git unrecorded (the 3.2 finding fixed only in part: catchable signals are held, SIGKILL cannot be); the next run fails once with a clear message. A nested submodule whose own checkout is cut after its parent's finished is not covered. Disk-full during a plan save was reproduced by a reviewer on 2.x and fixed (`_write_json` cleans up; `record_usage` is best effort) without a test. The two `queue run` stop tests slow a process start or aim the signal with Linux's `tgkill`; with unmodified timing the window is about a millisecond and only a reviewer's aimed script hit it. The background-job test uses a fake CLI that reads the terminal, as the real Claude CLI does. The `queue run` stop fixes were checked with aimed signals and slowed thread starts; the window for the lock case is microseconds and was hit only by aiming. |
| R3 One run per plan | T: `test_second_run_of_the_same_plan_is_refused`, `test_clean_leaves_running_and_out_of_tree_plans_alone`, `test_two_projects_run_in_parallel_on_both_providers`. `test_rebuilt_copy_of_an_out_of_tree_plan_is_safe_from_clean`, `test_plans_with_the_same_file_name_never_share_a_copy`, `test_a_copy_that_belongs_to_another_plan_is_refused`. V: reviewers ran two `crew run`s and two `queue run`s concurrently on 2.x (double execution, commits on `main`) and confirmed the lock fix; on 3.1 they removed a running `--out` plan's clone with `clean --all` and made two same-named plans share a clone, both fixed in 3.2. 3.3: `test_adding_a_remote_mid_run_does_not_start_a_second_run`, `test_a_queued_plan_is_resumed_not_replanned_after_a_remote_is_added`, `test_clean_keeps_a_running_copy_even_with_a_stray_in_tree_plan`, `test_a_copied_plan_file_is_refused_even_after_clean_all`; on 3.2 a `git remote add` during a run let a second run start in the same clone. 3.7: `test_a_stale_copy_of_a_finished_plan_is_refused`, `test_plans_and_profile_are_still_found_after_the_remote_changes`, `test_run_by_path_follows_a_plan_that_moved_when_a_remote_was_added`. 3.8: `test_a_plan_run_through_a_symbolic_link_is_locked_like_the_file_itself`, `test_a_plan_whose_folder_was_moved_and_linked_back_is_not_a_copy_of_itself`, `test_a_paused_queue_item_is_not_planned_again_after_origin_changed_twice`, `test_queue_items_for_one_checkout_run_in_order_whatever_it_was_called_when_queued`, `test_a_second_checkout_keeps_the_shared_profile_when_its_origin_changes`, `test_origin_removed_and_added_back_keeps_the_profile_edited_in_between`. | Locks use `fcntl`; on Windows they are per-process only. 4 simultaneous `queue add` processes lost items on 2.x; fixed with `file_lock("queue")`, covered only by review, not a test. Two plan files from before 3.3 that already name the same branch are refused only while one has a live clone. Renaming a running plan's file, or deleting its `.plan` pointer by hand, while it runs lets a second run into the same copy and lets `crew clean --all` remove it (reproduced in the 3.4 round and not counted: the README says to leave a running plan's file alone). `test_two_projects_run_in_parallel_on_both_providers` failed once in a full run while other suites were running beside it, and passed 6 of 6 alone: two projects that start at the same moment can both see the same provider idle and both take it, and one then waits for the other. The idle-provider choice is best effort under load. |
| R4 Agent-modified code never runs outside a sandbox | T: `test_setup_runs_in_the_worktree_at_the_base_commit` (agent writes a `postinstall`, deletes `node_modules`; setup never runs on it), `test_in_place_setup_never_reruns_after_agents`, `test_tampered_git_config_stops_crew_before_it_runs_git`, `test_broken_codex_sandbox_skips_checks_instead_of_passing_them`, `test_incompatible_cli_is_taken_out_of_rotation`. P: both sandboxes, the no-bubblewrap case, plan-mode Bash, repo settings. `test_home_in_a_temp_directory_is_refused_without_the_opt_in`. 3.6: `test_a_second_settings_flag_cannot_drop_the_sandbox`. 3.4: `test_extra_worker_flags_keep_the_sandbox`, `test_a_sandbox_you_turn_off_yourself_gets_no_shell`, `test_a_submodule_git_file_replaced_by_an_agent_stops_crew`. V: nested-manifest and `.pth`-in-venv attacks reproduced on 2.x/3.0 and the fixes confirmed; on 3.1 a Codex sandbox with crew's home under `/tmp` rewrote a profile's setup command and crew ran it unsandboxed, which 3.2 refuses. 3.8: `test_codex_is_started_without_execpolicy_rules`, `test_codex_reads_no_settings_an_earlier_agent_left_in_the_copy`, `test_commands_your_claude_settings_take_out_of_the_sandbox_cost_the_shell`, `test_without_a_sandbox_claude_has_no_shell_at_all`, `test_sandboxed_bash_false_means_no_shell`, `test_a_flag_without_its_value_cannot_swallow_the_sandbox`, `test_codex_sandbox_written_as_one_word_is_kept`, `test_the_task_guard_variable_reaches_a_codex_workers_shell`, `test_guard_a_check_or_a_setup_command_cannot_start_crew`, `test_a_claude_worker_cannot_read_the_codex_login`, `test_doctor_and_codex_login_hand_each_cli_only_its_own_keys`, `test_the_skill_tells_a_tool_what_crew_really_does`. V: the 3.7 round's verifier ran each containment case with the real claude 2.1.323 and codex 0.166.0 CLIs and a scripted model endpoint, before and after the fix: a Codex `allow` rule, a `.codex/config.toml` planted in the copy, `sandbox.excludedCommands`, a flag without its value and a missing sandbox each let a command write outside the copy on 3.7, and none did on the fix. 3.9: `test_a_claude_worker_is_offered_no_tool_that_reaches_other_sessions`, `test_a_claude_call_gets_no_network_tool_and_no_mcp_server`, `test_the_codex_login_is_denied_to_claudes_file_tools_and_to_the_planner`, `test_codex_gets_no_sub_agent_tools`, `test_a_codex_that_cant_turn_off_its_sub_agents_is_incompatible`. V: the 3.9 round captured the tools the real claude 2.1.325 and codex 0.166.0 offered crew's workers through a scripted model endpoint. With 3.8's flags a Claude worker was offered Agent, Workflow, ListAgents, ScheduleWakeup, Skill and ToolSearch, which loaded SendMessage, WebFetch, WebSearch, Monitor, CronCreate and EnterWorktree; EnterWorktree wrote the copy's `.git` without asking, and the planner's Read returned a stand-in for Codex's stored login. A Codex worker and the read-only planner resumed another Codex session saved in `CODEX_HOME` and wrote a message into its history. On 3.9 a Claude worker is offered exactly the listed tools, and the Codex sub-agent calls are refused as unsupported. | Submodules: the swap of a submodule's `.git` is checked since 3.4 and tested with a fake agent; the whole chain (a real sandboxed worker making the swap and git obeying the swapped repository inside a crew run) was not run end to end. `$HOME` is trusted: the global git config, `~/.claude`, `~/.codex` and `~/.crew` live there and both sandboxes keep it read-only (probed on 3.1). A machine whose `HOME` a sandbox can write has no protection crew could add. With `CREW_ALLOW_TEMP_HOME=1` the temp-directory protection is off by the user's choice. Codex's `--sandbox workspace-write` and `-P :workspace` have no read denial, so a Codex worker or a check can read Claude's stored login; Claude workers are denied Codex's. `sandbox.excludedCommands` in Claude's managed settings file is read but was not tried with the real CLI. The trust override for crew's copy was checked on codex-cli 0.166.0 only. SendMessage and ListAgents were judged from the tool lists and the tools' own descriptions; under the review's safety rules no worker was allowed to message a real session. The tool lists were captured on claude 2.1.325 and codex 0.166.0 only: a later CLI can add tools, which the explicit list keeps away from Claude workers but nothing keeps away from Codex workers except crew's flags. |
| R5 Nothing is called done without evidence | T: `test_partial_blocks_dependents_until_retry`, `test_verify_regression_triggers_fix_task`, `test_preexisting_failure_is_not_a_regression`, `test_needs_review_rerun_exits_nonzero_and_does_nothing`, `test_recovered_commit_is_verified_again`, `test_queue_planning_limit_pauses_the_item`. `test_deleted_crew_branch_is_put_back` (3.1 called a plan done and removed the clone while the user's repo had no branch at all). R: 3.1 runs reported `done` only after both check passes. 3.7: `test_a_run_whose_output_reader_went_away_does_not_exit_0`. 3.8: `test_a_plan_stopped_during_its_last_checks_is_not_called_done`, `test_a_recorded_baseline_of_the_wrong_type_is_an_error_not_a_plan_called_done`, `test_queue_add_cut_short_by_its_reader_announces_nothing_it_did_not_do`, `test_output_that_cannot_be_written_is_no_traceback_and_no_exit_0`. | Verdicts come from the model; an agent can claim `done` falsely. The checks are the backstop, and they are only as strong as the project's tests. |
| R6 Commits contain only the task's work | T: `test_generated_and_previously_ignored_files_are_never_committed` (agent un-ignores `.gitignore`, writes `__pycache__`), `test_go_branch_commits_and_return` (one commit per task, work only on the branch), `test_commit_signing_config_does_not_break_commits`, `test_existing_crew_branch_name_falls_back`. V: `__pycache__` and sandbox-stub commits reproduced on 3.0 with real Claude and fixed. 3.8: `test_files_a_task_leaves_inside_a_submodule_are_reported_and_the_copy_is_kept`, `test_guard_what_setup_and_checks_leave_inside_a_submodule_is_not_held_against_a_task`. | Nested git repositories an agent creates are reported, not committed; the user has to handle them. |
| R7 Bad input gives an error, not a mess | T: `test_garbled_cli_reply_is_a_failed_attempt_not_a_crash`, `test_hostile_plans_and_goals_are_refused_before_any_agent_runs`, `test_bad_inputs_give_clean_errors`, `test_shipped_template_matches_defaults`, `test_layers_and_agent_replacement`, `test_two_checkouts_of_one_repo_keep_their_own_plans`, `test_adding_a_remote_keeps_the_profile_and_plans`, `test_profile_without_a_name_is_a_clear_error`, `test_deeply_nested_json_is_a_clean_error_not_a_traceback`, `test_a_corrupt_pointer_stops_neither_run_nor_clean`. V: reviewers fed non-UTF-8 file names, NUL in task ids, 50 MB replies, wrong-typed state files, `CREW_HOME='~'`, a workdir with a trailing space, Python 3.8; each was fixed and confirmed. 3.7: `test_out_of_range_and_wrong_typed_values_are_errors_not_tracebacks`, `test_a_queue_item_without_an_absolute_workdir_is_not_run`, `test_an_error_no_check_foresaw_is_one_line_and_stops_cleanly`. 3.8: `test_a_switch_in_quotes_is_an_error_not_true`, `test_a_switch_must_be_true_or_false`, `test_a_misspelt_setting_one_level_down_is_reported`, `test_unknown_settings_inside_a_section_are_reported_too`, `test_an_empty_branch_prefix_is_refused`, `test_an_empty_branch_prefix_is_refused_before_it_makes_a_plan_that_cannot_be_resumed`, `test_a_plan_with_a_relative_workdir_is_not_run_where_crew_was_started`, `test_a_profile_with_a_relative_root_is_a_clear_error`, `test_a_plan_whose_workdir_is_a_folder_inside_a_repository_is_refused_before_anything_is_made`, `test_an_error_after_the_reader_of_the_output_went_away_is_not_a_traceback`, `test_odd_values_in_a_repository_a_long_plan_and_the_usage_log_are_not_a_defect_in_crew`, `test_text_that_cannot_be_encoded_is_refused_by_name_or_shown_escaped`, `test_guard_every_timeout_is_capped_not_only_the_checks`. | Not every hostile input has its own test; the fixes for state-file typing and non-UTF-8 names are covered by review only. Since 3.7 an exception nothing foresaw becomes one line and exit 2 instead of a traceback. That hides no test failure (the harness fails any test that reaches it) but it is a last resort, not proof that every bad value is checked. Several reviewers' input sweeps were automated (every DEFAULTS key with odd values, 38 `package.json` shapes); each fix has one test, not one per shape. |

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
- **3.4 was independently attacked on 2026-11-13**: three hunters and a verifier per area.
  18 findings, 17 confirmed and 1 refuted. Two were high: a second `--settings` in the Claude
  worker flags switched the sandbox off while crew reported it on, and an adoption merge that
  was interrupted left half-written files that the next task committed as its own. 3.6 fixes
  those and five more with tests that fail on 3.5. The last verifier reported after 3.6 was
  cut and checked each finding on 3.5 and 3.6: 10 confirmed, none high. The two that matter
  most: `crew queue run` could miss a stop signal and then ignore every later one until an
  agent finished by itself, and crew started with `&` from a terminal was stopped by job
  control at `claude auth status`, holding its plan lock. 3.7 fixes all 10 with tests that
  fail on 3.6, and the verifier's own reproduction scripts, rerun on 3.7, all behave as fixed
  (the aimed stop signal 0 of 6, the same under load 0 of 18, 0 of 16 bad values ending in a
  traceback). The verifier rated one finding lower than its hunter did (a changed `origin`
  loses nothing, so low, not medium) and did not count half of another (two plan files that
  name each other as owner, reachable only by editing both by hand).
- **3.5, 3.6 and 3.7 were independently attacked on 2026-11-13**, as one review: four hunters
  (containment and the skill; the adoption of your commits and submodules; stops and exit
  codes; plan lookup, ownership, inputs and the documents) and a verifier per area. 50
  findings, 48 confirmed and 2 refuted. Three were high, all in containment: a Codex worker
  obeyed execpolicy `allow` rules (the README's own suggested rule for `crew` among them, and a
  rules file an earlier agent left in the copy) and ran the allowed command outside its
  sandbox; a `.codex/config.toml` that a Claude task left in the copy widened the next Codex
  worker's sandbox and started a program of its choosing outside it; and crew's blanket shell
  grant let commands the owner's Claude settings exclude from the sandbox run with no prompt.
  The verifier rated the last one medium. Each was shown with the real CLIs and a scripted
  model endpoint, and each verifier built its fixes into one patch with tests that fail on
  3.7. crew 3.8 merges the four patches; four findings were reported by two areas each
  (switches in quotes, an empty branch prefix, unknown keys one level down, `codex-login`
  without a time limit) and keep both areas' tests.
- **What crew's workers are offered was reviewed on 2026-11-13**, after a rerun of the 3.7
  review's reproductions showed that claude 2.1.325 offered a worker whose shell was refused
  tools such as Agent, Workflow, ListAgents and ToolSearch. crew 3.8 passed no tool list, so a
  worker got the CLI's whole default set, and each CLI release could add to it. Two hunters
  (Claude, Codex) and a verifier each, with the real CLIs on scripted model endpoints and fresh
  homes: 6 findings, 5 confirmed, 1 refuted. The high one: a Codex worker, and the read-only
  Codex planner, could resume any Codex session saved in the owner's `~/.codex` and write a
  message into its history, which the owner's next `codex resume` would send to the model as
  their own words. The four medium ones: Claude workers were offered ListAgents and SendMessage,
  an unprompted channel to the owner's other Claude sessions; EnterWorktree and the Agent tool's
  worktree isolation made the CLI write the copy's `.git` without asking; WebFetch, WebSearch,
  Monitor and the owner's MCP servers gave a worker network once an allow rule of the owner's
  permitted them; and the Claude planner, which plan mode lets read outside the checkout, could
  read Codex's stored login with its file tools (denyRead binds only the shell). The hunters
  rated two of the Claude ones high; the verifier rated them medium because harm needs another
  session to act on the message, or text in the checkout steering the planner. 3.9 fixes all
  five with tests that fail on 3.8.
- **The 3.7 review's fixes were checked again on the merge.** Each area's reproductions were
  rerun against 3.8 as merged, with the real CLIs on scripted endpoints where a verifier had used
  them: 47 of 48 behave as fixed; the other was a count in this file, fixed in 3.9. One of the
  rerun's agents found the same collision in the test fixtures that was fixed before 3.8 was
  pushed. A separate review of the merge itself compared every function two patches changed
  with both sides and reverted each hand-made resolution in a copy: an existing test failed
  every time, and nothing in crew.py was lost. It found two wrong sentences in this file (the
  submodule test count, and three findings reported twice where there were four), fixed in 3.9.
- **3.9 has not been independently attacked as a whole.** Its evidence is the 180 tests, the
  six new ones failing on 3.8; the two verifiers' runs with the real CLIs on scripted endpoints,
  including a normal task through each patched worker; and one real run with a Claude worker on a
  checkout with uncommitted changes, which came through byte-identical. 3.8's own evidence, before
  the rerun above, was its 174 tests, 53 of the 59 new ones failing on 3.7 (the six that pass
  there were written to: five guard earlier fixes that no test held, one pins behaviour 3.7
  already had). Merging the 3.7 review's four patches needed one change of its own, to the fake
  Codex's login in `test_crew.py`. Each of the ten rounds so far found defects in the version
  before it.
- **Untested platforms:** macOS and Windows. Untested provider path: Codex as a worker with a
  real login.
- **Refuted claims, left as they are:** that `acceptEdits` understates what Claude can do
  (the README wording matches the behaviour); that `allowedTools Bash(pytest *)` is dangerous
  (it is documented as such); that check output in the fix prompt is a new injection surface
  (agents already read the repo); that `reap_orphans` can kill unrelated processes (pid plus
  start time, and the pid file is outside both sandboxes); that a non-exhaustive secret-name
  denylist leaks secrets (checks have no network and no writes outside the clone; the list was
  widened anyway); that Codex workers get live web search when the owner's own
  `~/.codex/config.toml` sets `web_search = "live"` (the owner's explicit setting, as with the
  other owner grants below; by default the search is the CLI's cached one, and no agent can change
  the setting); that exit 0 and
  `done` while the last commits are still only in crew's copy breaks R5 (`done` is defined by
  tasks, checks and setup; the copy is kept, the next run delivers, and `crew plans` now says
  so); that crew's git should ignore global git config (no sandbox can write `$HOME`, and
  ignoring it would break `safe.directory` and hosted git settings); that putting a branch
  back without comment after the user moved it to an older crew commit breaks a promise (it is
  the documented handling of undelivered commits); that re-running tasks after the user
  rebases or amends a crew branch is a defect (a task is done only if its recorded commit is
  on the branch, and crew will not accept a commit by its subject line); that the owner's own
  tool settings (`permissions.additionalDirectories`, `sandbox.filesystem.allowWrite`, Codex's
  `writable_roots`) widen where a worker can write without crew reporting it (they are the
  owner's explicit grants in the trusted home directory and apply to the owner's own sessions
  the same way; crew adds nothing to them); that `crew install` replaces a `crew` skill the owner
  edited and re-points a `crew` link without saying so (the skill file says that `crew install`
  replaces it, the README promises only that a skill crew did not write is left alone, and the
  link change is printed); that nothing locks
  crew's copy itself, so renaming a running plan's file or deleting its `.plan` pointer lets a
  second run in (it reproduces, but both start with a manual step on a running plan that the
  README warns against; a copy or a hard link of the file is refused).

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
- The system hands a signal to whichever thread it likes, and Python acts on it only in the
  main one. A main thread that sleeps without a timeout while others work can miss a stop.
- A process in a background job that touches the terminal is stopped, and its parent with it.
  Give children nothing to read unless you mean to.
- Valid JSON is not valid input: infinity, NaN and numbers too large for a clock all parse.
- An exit code is a promise. Losing the reader of the output is not success.
- A project's name comes from its remote, and remotes change. Find a checkout's plans by the
  checkout.
- A handler that does nothing is safer than ignoring a signal: an ignored signal that is
  already waiting is an error in Python.
- A sandbox flag is not the whole sandbox: the tool's own rules, project settings and the
  owner's exclusions can each open a door. Turn off what the tool would read from the copy.
- A blanket permission turns every exception the owner made into an unprompted one.
- A setting written in quotes is a string, and every string is true.
- The output a command writes is part of what it does: say it after doing it, and never let a
  failed write hide a stop.
- A lock taken by library code can be left held by a signal handler that raises. Hold signals
  while touching what other threads wait on.
- A folder is not a repository. Recognise a repository by a commit it has.
- An undo must know what it is undoing: git's own test passes for a file a task deleted.
- A tool list that crew doesn't set is set by whoever ships the next CLI release. Name the tools.
- A sandbox binds the commands it runs, not the tools that run beside it in the CLI's own process.
- Another session is outside every sandbox. A tool that reaches one is a way out.
