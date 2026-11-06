# crew

A planner breaks a goal into tasks and hands them to three agents, **Astra**, **Sol** and **Luna**. Each task runs on either **Claude Code** or **Codex**, and the router keeps usage split between the two across all your projects.

`crew.py` is one file using only the Python 3 standard library. It calls the `claude` and `codex` command-line tools, so each one runs on the login it already has: your Claude plan or ChatGPT plan, or an API key.

## Crew

| Agent | Role | Default provider |
|---|---|---|
| Astra | Architect: design, multi-file changes, hard bugs | Claude Code |
| Sol | Builder: well-scoped features, scripts, pipelines | Codex |
| Luna | Verifier: tests, review, small fixes, docs | whichever is under its share |

## Setup (once)

1. Install both CLIs and log in to each:
   ```
   npm install -g @anthropic-ai/claude-code @openai/codex
   claude auth login
   codex login              # ChatGPT plan; add --device-auth on a headless machine
   ```
   With API keys instead, set `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as environment variables and run `crew codex-login` once (it pipes the key into `codex login --with-api-key`). `CODEX_API_KEY` also works for Codex without that step. Don't commit keys or paste them into chat. In a Claude Code cloud environment, add them as environment variables in the environment settings. Each CLI gets only its own provider's credentials.
2. Install crew so it works from any folder:
   ```
   python3 crew/crew.py install
   ```
   This copies crew to `~/.crew/bin/crew` and writes `~/.crew/config.json` (agents, models, split), then tells you how to put it on your `PATH`.
3. On Linux, install `bubblewrap` and `socat` so Claude's sandbox works (`apt install bubblewrap socat`). macOS has its sandbox built in.
4. Check everything: `crew doctor`

## Use it in any project

```
cd ~/code/my-app
crew init                    # detect stack, setup and checks; saved in ~/.crew/projects/
crew go "Add dark mode to the settings page"
crew run                     # resume this project's latest plan
crew run --retry-failed      # also re-run failed, partial, blocked and skipped tasks
crew go "..." --dry-run      # plan, then only show who would get each task
crew plans                   # this project's plans (crew plans --all for every project)
```

What a run does, in order:

1. **Branch.** In a git repo, crew refuses to start with uncommitted changes, then works on a new branch `crew/<date>-<goal>`. Each task becomes one commit (`crew(t1): ...`), made without running your git hooks. At the end crew switches back to the branch you started on and prints the `git diff` command to review. Nothing is pushed. `--no-git` skips all of this.
2. **Setup.** If dependencies are missing (no `node_modules` or `.venv`), crew installs them itself (`npm ci`, `pnpm install`, a `.venv` with `pip install`...), because agents have no network. It does this only while the dependency files are the ones you committed, never after an agent has changed them.
3. **Baseline checks.** crew runs the project's checks (tests, typecheck, lint, build...) inside Codex's sandbox: no network, and no writes outside the project.
4. **Tasks.** Tasks run one at a time, in dependency order. Each agent gets its role, the task, a short project brief (stack, check commands, notes, `CLAUDE.md`/`AGENTS.md` if present) and the summaries of the tasks it depends on. It ends by reporting `done`, `partial` or `blocked`; only `done` lets dependent tasks run.
5. **Checks again.** A check that passed before and fails now is a regression, and crew gives Luna a fix task with the failing output (`verify.fix_rounds`, default 1). Checks that already failed before are only reported.

The plan file (in `~/.crew/plans/<project>/`) is updated after every task, so re-running resumes. A plain re-run leaves `failed`, `partial` and `blocked` tasks alone. If every available provider hits its usage limit, the run stops with the current task `pending`; re-run later. Ctrl-C, SIGTERM and a closed terminal also stop the running agent and leave its task `pending`.

## Several projects at once

```
crew queue add "Add a GitHub Actions workflow that runs the checks" --workdir ~/code/a --workdir ~/code/b
crew queue add "Update the README setup steps" --all      # every project you've run `crew init` in
crew queue list
crew queue run               # 2 projects at a time by default
crew queue rm q3 ; crew queue clear
```

Each project's items run in order, and different projects run in parallel. By default crew runs at most one Claude task and one Codex task at the same time (`providers.<p>.max_parallel`). When the provider a task would use is busy and the other one is idle, crew hands it to the idle one, unless that would push the idle one past its share by more than `tolerance`, or `split.mode` is `fixed`. That way both plans work at once. A queue item ends as `done`, `needs-review` (some task wasn't `done`, or a regression is left), `paused` (usage limit or interrupted; `crew queue run` picks it up again) or `error`.

## Profiles: per-project settings

`crew init` writes `~/.crew/projects/<owner>__<repo>.json`. It lives outside the repo, so agents can't change it. Edit it to fix what was detected:

```json
{
  "project": {
    "name": "you/my-vite-app",
    "stack": ["node/npm", "vite", "react", "typescript"],
    "setup": [{"run": "npm ci", "unless_exists": "node_modules"}],
    "checks": {"typecheck": "npx tsc --noEmit -p tsconfig.app.json", "lint": "npm run lint", "build": "npm run build"},
    "notes": ["Pages live in src/pages; don't edit the generated components in src/components/ui."]
  },
  "overrides": {
    "agents": {"Astra": {"role": "...", "prefers": "claude", "model": {"claude": "opus"}}},
    "split": {"target_share": {"claude": 0.7, "codex": 0.3}},
    "verify": {"fix_rounds": 2}
  }
}
```

What gets detected:

- Node (npm, pnpm, yarn, bun): lockfile install; the `test`, `lint`, `build` and typecheck scripts. If there is no typecheck script, `tsc --noEmit` for TypeScript, using `tsconfig.app.json` when it exists, as Vite templates have. npm's "no test specified" placeholder is ignored. Monorepos get a note.
- Python: `uv sync`, `poetry install`, or a `.venv` from `requirements.txt`/`pyproject.toml` (kept out of git through `.git/info/exclude`). pytest or unittest, ruff if configured, and a syntax check.
- Notebooks: a check that every `.ipynb` is valid JSON, plus a note telling agents to edit notebooks with nbformat, never as raw text.
- Static sites, Rust, Go, and `make test` / `make lint`.

`overrides` accepts any key from `~/.crew/config.json`. `agents` replaces the whole crew; other keys merge. `crew config` prints the merged result for the current project.

## How usage is divided

Every call is logged to `~/.crew/usage.jsonl` with its project. Plan limits are per account, so the split is computed across all projects over the last `window_hours`. `crew usage` shows the totals and a per-project breakdown.

`split.mode`:

- `by_role` (default): use the agent's preferred provider unless it is more than `tolerance` over its `target_share`. That check starts after `min_runs` calls. Agents with `"prefers": "any"` always go to the less-used provider.
- `balanced`: always use whichever provider is furthest under its target.
- `fixed`: always use the preferred provider.

`split.metric` is `tasks` (calls, the default), `tokens` (approximate; the two CLIs count differently) or `seconds`. When a call fails, crew retries it once on the other provider. If the error says a usage or rate limit was hit, that provider is also skipped for `cooldown_minutes` (`crew usage --clear-cooldowns` to undo). A provider that isn't installed or logged in is skipped, so crew works with only one of them.

To save usage, give agents cheaper models where it doesn't matter, for example `"Luna": {"model": {"claude": "sonnet"}}`. The planner has its own `planner.model`. The `cost_usd` Claude reports is the API list-price equivalent; on a plan it's only a size measure.

## Sandboxing

- **Codex workers:** `--sandbox workspace-write`, so writes stay in the project and there's no network.
- **Claude workers:** `acceptEdits` plus Claude Code's sandbox, with its escape hatch turned off (`allowUnsandboxedCommands: false`). Writes stay in the project and temp directories, and there's no network. crew first checks that the sandbox can actually start (built in on macOS; bubblewrap and socat on Linux, tested once). Only then does it let Claude run shell commands without prompts (`--allowedTools Bash`). If the sandbox isn't available, Claude can still edit files, but its shell commands are refused rather than run unsandboxed.
- **Checks:** run by crew inside `codex sandbox`. That needs only the `codex` binary, not a Codex login. Without it, checks are skipped unless you set `verify.unsandboxed`.
- **Planner:** read-only. Claude runs in plan mode with Read, Grep and Glob only; Codex runs `--sandbox read-only`.
- **Repo settings ignored:** Claude calls load only your user-level settings, so a `.claude/settings.json` that an agent writes into the repo can't add hooks or loosen permissions.
- **Self-edit guard:** if `crew.py` or the config sits inside the project and an agent changes it, the run stops.
- **Setup runs unsandboxed.** It needs the network, so crew runs only the commands from your profile, and only on dependency files you committed.

Read a plan before you `run` it. Its instructions go to agents that can edit files. `go` and `queue run` skip that review, so use them for goals and repos you trust.

If a task needs the network (adding a package, say), do that step yourself: change the dependency file, commit, and `crew run` again. Setup then installs it.

## Config reference

| Key | Meaning |
|---|---|
| `planner.provider`, `planner.max_tasks`, `planner.model` | Who plans, the task limit, per-provider planner model |
| `agents.<name>.role` / `.prefers` / `.model` | The crew; `model` is per provider, e.g. `{"claude": "sonnet"}` |
| `providers.<p>.cmd` / `.model` / `.args` / `.timeout_sec` | CLI path, default model, extra worker flags, per-task time limit |
| `providers.<p>.max_parallel` | Concurrent calls to this provider in `queue run` (global setting) |
| `providers.claude.sandboxed_bash` | Let Claude run shell commands without prompts when its sandbox works (default true) |
| `providers.<p>.enabled` | `false` takes a provider out of rotation |
| `split.*` | See "How usage is divided" |
| `verify.enabled` / `.fix_rounds` / `.fixer` / `.timeout_sec` / `.unsandboxed` | Checks and the fix loop |
| `setup.enabled` / `.timeout_sec` | Dependency install before a run |
| `git.enabled` / `.branch_prefix` / `.return_to_start` | Branch-per-run behaviour |

## Tests

```
cd crew && python3 -m unittest -v test_crew
```

They use fake `claude`/`codex` executables and never call a model.
