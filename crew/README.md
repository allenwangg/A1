# crew

A planner breaks a goal into tasks and hands them to three agents, **Astra**, **Sol** and **Luna**. Each task runs on either **Claude Code** or **Codex**, and the router keeps usage split between the two.

`crew.py` uses only the Python 3 standard library. It calls the `claude` and `codex` command-line tools, so each one runs on the login it already has: your Claude plan or ChatGPT plan, or an API key.

## Crew

| Agent | Role | Default provider |
|---|---|---|
| Astra | Architect: design, multi-file changes, hard bugs | Claude Code |
| Sol | Builder: well-scoped features, scripts, pipelines | Codex |
| Luna | Verifier: tests, review, small fixes, docs | whichever is under its share |

Roles and preferences are in `crew.json`; edit them freely.

## Setup

1. Install both CLIs:
   ```
   npm install -g @anthropic-ai/claude-code @openai/codex
   ```
2. Log in to each one. Pick one of these for each tool.
   - **Plan login (no API key):** `claude auth login` and `codex login` (for a ChatGPT plan; add `--device-auth` on a headless machine).
   - **API keys:** set `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as environment variables, then run `python crew.py codex-login` once. That pipes `OPENAI_API_KEY` into `codex login --with-api-key` without printing it. `CODEX_API_KEY` also works for Codex without the login step.

   Don't commit keys or paste them into chat. In a Claude Code cloud environment, add them as environment variables in the environment settings (environment menu in the session title bar → Edit). New sessions pick them up. Each CLI gets only its own provider's key: OpenAI keys are removed from Claude's environment, and the Anthropic key from Codex's.
3. Check everything:
   ```
   python crew.py doctor
   ```

## Use

```
python crew.py plan "Add unit tests for the fraud notebook's feature code" --workdir ..
python crew.py run crew-plan.json --dry-run    # show who gets what, run nothing
python crew.py run crew-plan.json              # run it; re-run the same command to resume
python crew.py run crew-plan.json --retry-failed   # also re-run failed and skipped tasks
python crew.py go "goal"                       # plan + run in one step
python crew.py usage                           # split over the window, plus cooldowns
```

- Planning is read-only: Claude runs in plan mode with its edit tools disabled, and Codex runs with `--sandbox read-only`.
- Tasks run one at a time, in dependency order, in `--workdir`. Each agent gets its task, its role, and the summaries of the tasks it depends on.
- The plan file is updated after every task (`status`, `provider`, `result`), so re-running it picks up where it stopped. Re-running skips tasks that are `done` or `failed`; pass `--retry-failed` to try failed ones again. A task whose prerequisite didn't finish is marked `skipped`.
- If Claude was refused permission for something (for example a shell command), the task records it under `denied` and the run prints a note.
- Read the plan before you `run` it. Its instructions go straight to agents that can edit files. `go` skips that review step, so use it only for goals and repos you trust.
- Agents are told not to commit or push unless the task says to. Review the diff yourself.

## How usage is divided

Every call is logged to `~/.crew/usage.jsonl` (set `CREW_HOME` to move it). The router reads the last `window_hours` of that log and works out each provider's share.

`split.mode` in `crew.json`:

- `by_role` (default): use the agent's preferred provider unless that provider is already more than `tolerance` over its `target_share`; then use the other one. That check starts once the window holds `min_runs` calls, so a nearly empty log doesn't override preferences. Agents with `"prefers": "any"` always go to the less-used provider.
- `balanced`: ignore preferences and always use whichever provider is furthest under its target.
- `fixed`: always use the preferred provider.

`split.metric` sets what "usage" means: `tasks` (number of calls, the default), `tokens` (fresh input plus output tokens; Claude and Codex count differently, so this is approximate), or `seconds` (wall-clock time). The planner's calls count too. With `tokens` or `seconds`, `--dry-run` estimates each task from that provider's average call, so its routing is only a preview.

When a call fails, crew retries it once on the other provider (`fallback_on_error`). If the provider's error says it hit a usage or rate limit, that provider is also skipped for `cooldown_minutes`. Clear that early with `python crew.py usage --clear-cooldowns`. A provider that isn't installed or isn't logged in is skipped automatically, so crew still works with only one of them.

The `cost_usd` Claude reports is the API list-price equivalent. On a Claude plan you aren't billed that amount; it's only a size measure.

## Config reference

| Key | Meaning |
|---|---|
| `planner.provider` | Which CLI plans (falls back to the other one) |
| `planner.max_tasks` | Upper limit on tasks per plan; a longer plan is rejected and the other provider is asked |
| `providers.<p>.cmd` | Executable name or path (resolved to an absolute path at start-up) |
| `providers.<p>.model` | Model passed through with `--model` / `-m`; `null` keeps the CLI default |
| `providers.<p>.args` | Extra worker flags. Defaults: Claude `--permission-mode acceptEdits` plus its sandbox, Codex `--sandbox workspace-write` |
| `providers.<p>.timeout_sec` | Per-task time limit. On timeout crew stops the CLI and the processes it started |
| `split.min_runs` | Calls needed in the window before `by_role` starts rebalancing |
| `providers.<p>.enabled` | `false` takes a provider out of rotation |

### Sandboxing

Both providers run workers sandboxed by default, so they can run tests and scripts without asking:

- Codex: `--sandbox workspace-write`. Writes are limited to the workdir, and there's no network.
- Claude: `--permission-mode acceptEdits` plus Claude Code's sandbox (`sandbox.enabled`, `autoAllowBashIfSandboxed`). Shell commands run without prompts, writes are limited to the workdir and temp directories, and network access is blocked. On macOS the sandbox is built in. On Linux it needs `bubblewrap` and `socat` (`apt install bubblewrap socat`); `doctor` warns if they're missing. Without them, Claude's shell commands are refused, not run unsandboxed. File edits still work.

If a task needs network access (installing packages, for example), do that step yourself or loosen the sandbox in your own settings. For Codex, add `-c sandbox_workspace_write.network_access=true` to `providers.codex.args`. For Claude, allow the domains in the sandbox section of `~/.claude/settings.json` (see Claude Code's sandboxing docs).

Claude calls load only your user-level settings (`--setting-sources user`). A `.claude/settings.json` inside the workdir is ignored, because the other agent could write one that adds hooks or loosens permissions. Put any allow rules in `~/.claude/settings.json` or in `providers.claude.args`.
