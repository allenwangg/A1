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
   - **API keys:** set `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as environment variables, then run `python crew.py codex-login` once. That pipes `OPENAI_API_KEY` into `codex login --with-api-key` without printing it.

   Don't commit keys or paste them into chat. In a Claude Code cloud environment, add them in the environment settings (environment menu → Edit → secrets / environment variables). New sessions pick them up.
3. Check everything:
   ```
   python crew.py doctor
   ```

## Use

```
python crew.py plan "Add unit tests for the fraud notebook's feature code" --workdir ..
python crew.py run crew-plan.json --dry-run    # show who gets what, run nothing
python crew.py run crew-plan.json              # run it; re-run the same command to resume
python crew.py run crew-plan.json --retry-failed
python crew.py go "goal"                       # plan + run in one step
python crew.py usage                           # split over the window, plus cooldowns
```

- Planning is read-only: Claude runs with file edits disabled and Codex with `--sandbox read-only`.
- Tasks run one at a time, in dependency order, in `--workdir`. Each agent gets its task, its role, and the summaries of the tasks it depends on.
- The plan file is updated after every task (`status`, `provider`, `result`), so an interrupted run picks up where it stopped. A task whose prerequisite failed is marked `skipped`.
- Agents are told not to commit or push unless the task says to. Review the diff yourself.

## How usage is divided

Every call is logged to `~/.crew/usage.jsonl` (set `CREW_HOME` to move it). The router reads the last `window_hours` of that log and works out each provider's share.

`split.mode` in `crew.json`:

- `by_role` (default): use the agent's preferred provider unless that provider is already more than `tolerance` over its `target_share`; then use the other one. Agents with `"prefers": "any"` always go to the less-used provider.
- `balanced`: ignore preferences and always use whichever provider is furthest under its target.
- `fixed`: always use the preferred provider.

`split.metric` sets what "usage" means: `tasks` (number of calls, the default), `tokens` (fresh input plus output tokens; Claude and Codex count differently, so this is approximate), or `seconds` (wall-clock time).

When a call fails, crew retries it once on the other provider (`fallback_on_error`). If the failure looks like a usage or rate limit, that provider is also skipped for `cooldown_minutes`. Clear that early with `python crew.py usage --clear-cooldowns`. A provider that isn't installed or isn't logged in is skipped automatically, so crew still works with only one of them.

The `cost_usd` Claude reports is the API list-price equivalent. On a Claude plan you aren't billed that amount; it's only a size measure.

## Config reference

| Key | Meaning |
|---|---|
| `planner.provider` | Which CLI plans (falls back to the other one) |
| `planner.max_tasks` | Upper limit on tasks per plan |
| `providers.<p>.cmd` | Executable name or path |
| `providers.<p>.model` | Model passed through with `--model` / `-m`; `null` keeps the CLI default |
| `providers.<p>.args` | Extra worker flags. Defaults: Claude `--permission-mode acceptEdits`, Codex `--sandbox workspace-write` |
| `providers.<p>.timeout_sec` | Per-task time limit |
| `providers.<p>.enabled` | `false` takes a provider out of rotation |

Claude's `acceptEdits` mode lets it edit files but denies shell commands that would need approval. If you want agents to run tests unattended, add specific tools, for example `"args": ["--permission-mode", "acceptEdits", "--allowedTools", "Bash(pytest *)"]`.
