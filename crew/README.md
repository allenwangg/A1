# crew

A planner breaks a goal into tasks and hands them to three agents, **Astra**, **Sol** and **Luna**. Each task runs on either **Claude Code** or **Codex**, and the router keeps usage split between the two across all your projects.

`crew.py` is one file. It needs only Python 3.9+ and its standard library. It calls the `claude` and `codex` command-line tools, so each one runs on the login it already has: your Claude plan or ChatGPT plan, or an API key.

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
4. Check everything: `crew doctor`. It reports any CLI that is missing, logged out, or a version whose flags no longer match what crew uses.

## Use it in any project

```
cd ~/code/my-app
crew init                    # detect stack, setup and checks; saved in ~/.crew/projects/
crew go "Add dark mode to the settings page"
crew do "Fix the typo in the footer"     # one task, no planner call (cheaper)
crew run                     # resume this checkout's latest plan
crew run --retry-failed      # also re-run failed, partial, blocked and skipped tasks
crew go "..." --dry-run      # plan, then only show who would get each task
crew plans                   # this checkout's plans (crew plans --all for every project)
crew clean                   # remove worktrees of finished plans
```

When the run ends, crew prints the commands to review and merge:

```
git log --stat <base>..crew/<date>-<goal>
git merge crew/<date>-<goal>
```

What a run does, in order:

1. **Worktree.** crew creates a separate git worktree under `~/.crew/worktrees/` on a new branch, `crew/<date>-<goal>`. It starts from the commit you were on when the plan was made. Your own checkout is never switched, stashed or written to, so you can keep working while crew runs. Uncommitted changes in your checkout aren't part of the run (crew says so when it plans). Nothing is ever pushed. `--no-git` works in place instead.
2. **Setup.** crew installs dependencies in the new worktree (`npm ci`, `pnpm install`, a `.venv` with `pip install`...), because agents have no network. Setup runs outside any sandbox, so crew only runs it on the base commit, before any agent has touched the tree. If setup changes tracked files (a lockfile), crew restores them.
3. **Baseline checks.** crew runs the project's checks (tests, typecheck, lint, build...) inside Codex's sandbox: no network, and no writes outside the worktree.
4. **Tasks.** Tasks run one at a time, in dependency order. Each agent gets:
   - its role and the task;
   - a short project brief: stack, check commands, notes, and `CLAUDE.md`/`AGENTS.md` if present;
   - the summaries of the tasks it depends on.

   It ends by reporting `done`, `partial` or `blocked`; only `done` lets dependent tasks run. After each task crew commits that task's work as one commit (`crew(t1): ...`), without running your git hooks or signing. First it checks that the agent left the worktree on crew's branch, at crew's last commit, with no merge or rebase in progress. If not, crew stops without committing.
5. **Checks again.** A check that passed before and fails now is a regression, and crew gives Luna a fix task with the failing output (`verify.fix_rounds`, default 1). Checks that already failed before are reported, not counted.
6. **Finish.** If the plan is done, or needs your review, the worktree is removed (unless uncommitted changes are left in it); the branch keeps the work. If the plan is paused (usage limit, interrupt), the worktree stays so `crew run` can continue in it.

A plain re-run leaves `failed`, `partial` and `blocked` tasks alone. If every available provider hits its usage limit, the run stops with the current task `pending`; re-run later.

To add a dependency, install and commit it on your own branch, then start a new plan. crew never installs from a branch that agents have changed.

## Several projects at once

```
crew queue add "Add a GitHub Actions workflow that runs the checks" --workdir ~/code/a --workdir ~/code/b
crew queue add "Update the README setup steps" --all --direct   # every known project, one task each, no planner
crew queue list
crew queue run               # 2 projects at a time by default
crew queue rm q3 ; crew queue clear
```

Each project's items run in order; different projects run in parallel. By default crew runs at most one Claude task and one Codex task at the same time (`providers.<p>.max_parallel`). When the provider a task would use is busy and the other one is idle, crew hands it to the idle one. It won't if that would push the idle one past its share by more than `tolerance`, or if `split.mode` is `fixed`. That way both plans work at once.

A queue item ends as:

- `done`;
- `needs-review`: a task wasn't `done`, or a regression is left;
- `paused`: a usage limit, no provider available, or an interrupt. `crew queue run` picks it up again;
- `error`: `crew queue run --retry-failed` tries it again.

Only one `crew queue run` can work at a time.

## What crew guarantees, and what it can't

No tool driving language models can promise correct code. What crew does promise, and its test suite checks:

- **Your work is safe.** crew never modifies your checkout, your branches or your uncommitted files, and never pushes. Agents work in a separate worktree. They can't commit or switch its branch: its git metadata lives outside their sandbox.
- **Crashes don't lose or repeat work.** Every step that changes the repo is written to the plan file first. A crash, kill or power cut at any point resumes without repeating a committed task or losing an agent's files. Crash points tested: after the branch is recorded, before the commit, and after the commit. Agent processes left running by a crew that was killed are stopped before the next run continues.
- **One run per plan.** A second `crew run` of the same plan, or a second `crew queue run`, is refused. The queue, usage log and state files are locked between processes and written atomically.
- **Agent-modified code never runs outside a sandbox.** Setup only runs on the base commit. Checks run in Codex's sandbox, and are skipped, never run unsandboxed, if that sandbox doesn't work (unless you set `verify.unsandboxed`). Claude's shell access is granted only after its sandbox is confirmed to work.
- **Nothing is called done without evidence.** A task is `done` only if its agent says so in a structured reply. A plan is `done` only if every task is, no check regressed, and setup succeeded. Exit codes and queue states follow from that.
- **Commits contain only the task's work.** Generated files (`__pycache__`, caches, CLI state) are left out. So are files that existed before the task, even if an agent un-ignores them, and nested git repositories.
- **Bad input gives an error, not a mess.** Malformed CLI replies, plans, profiles, config or state files produce a clear error or a failed attempt, never a traceback or a wrong `done`. CLI updates that remove a flag crew relies on are caught up front by `crew doctor` and by every run.

What it can't promise: that the agents' code is right (that's what the checks and your review are for), that checks catch everything (they're only as good as the project's tests), or that the sandboxes of the two CLIs have no holes of their own.

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

- **Node** (npm, pnpm, yarn, bun):
  - The package manager comes from the `packageManager` field, else from the lockfile. `package-lock.json` wins over `bun.lockb`, as in Lovable repos.
  - Setup is a frozen-lockfile install.
  - Checks come from the `test`, `lint`, `build` and typecheck scripts. Without a typecheck script, TypeScript projects get `tsc --noEmit`: with `-p tsconfig.app.json` for Vite templates, and `tsc -b` for solution-style configs.
  - In monorepos, scripts that live only in the packages run across all workspaces.
  - npm's "no test specified" placeholder is ignored. If `test` runs Playwright or Cypress and a `test:unit` script exists, `test:unit` is used instead, because checks have no network or localhost.
- **Python:** `uv sync`, `poetry install`, or a `.venv` from `requirements*.txt`/`pyproject.toml` (kept out of git through `.git/info/exclude`):
  - dev and test requirements files and extras are installed;
  - plain `def test_...` files get pytest installed and used, otherwise unittest runs on the `tests` folder;
  - ruff runs if configured, plus a syntax check.
- **Notebooks:** a check that every `.ipynb` is valid JSON, plus a note telling agents to edit notebooks with nbformat, never as raw text.
- **Static sites:** a check that every relative `href`/`src` points at a file that exists. Flask/Django `templates/` folders don't count as a static site.
- **Rust, Go,** and `make test` / `make lint`.

`overrides` accepts any key from `~/.crew/config.json`. `agents` replaces the whole crew; other keys merge. `crew config` prints the merged result for the current project. If you add a git remote after `crew init`, crew moves the profile and plans to the new name.

## How usage is divided

Every call is logged to `~/.crew/usage.jsonl` with its project, including calls that were interrupted. Plan limits are per account, so the split is computed across all projects over the last `window_hours`. `crew usage` shows the totals and a per-project breakdown.

`split.mode`:

- `by_role` (default): use the agent's preferred provider unless it is more than `tolerance` over its `target_share`. That check starts after `min_runs` calls. Agents with `"prefers": "any"` always go to the less-used provider.
- `balanced`: always use whichever provider is furthest under its target.
- `fixed`: always use the preferred provider.

`split.metric` is `tasks` (calls, the default), `tokens` (approximate; the two CLIs count differently) or `seconds`. When a call fails, crew retries it once on the other provider. If the error says a usage or rate limit was hit, that provider is also skipped for `cooldown_minutes` (`crew usage --clear-cooldowns` to undo). A provider that isn't installed, logged in or compatible is skipped, so crew works with only one of them.

Ways to spend less:

- Use `crew do` and `queue add --direct` for small changes; they skip the planner call.
- The planner is told not to add tasks that only re-run checks, since crew runs them itself.
- Give agents cheaper models where it doesn't matter, for example `"Luna": {"model": {"claude": "sonnet"}}`; the planner has its own `planner.model`.

The `cost_usd` Claude reports is the API list-price equivalent; on a plan it's only a size measure.

## Sandboxing

- **Codex workers:** `--sandbox workspace-write`, so writes stay in the worktree and there's no network.
- **Claude workers:** `acceptEdits` plus Claude Code's sandbox, with its escape hatch turned off (`allowUnsandboxedCommands: false`). Writes stay in the worktree and temp directories, and there's no network. crew first checks that the sandbox can actually start (built in on macOS; bubblewrap and socat on Linux, tested once). Only then does it let Claude run shell commands without prompts (`--allowedTools Bash`). If the sandbox isn't available, Claude can still edit files, but its shell commands are refused rather than run unsandboxed.
- **Checks:** run by crew inside `codex sandbox`, with credentials removed from the environment. That needs only the `codex` binary, not a Codex login. crew probes once that the sandbox really runs commands. If it doesn't, checks are skipped, never run unsandboxed, unless you set `verify.unsandboxed`.
- **Planner:** read-only. Claude runs in plan mode with Read, Grep and Glob only; Codex runs `--sandbox read-only`. It reads your checkout as it is.
- **Repo settings ignored:** Claude calls load only your user-level settings, so a `.claude/settings.json` in the repo can't add hooks or loosen permissions. crew's own git commands never run repo hooks or an fsmonitor.

Read a plan before you `run` it. Its instructions go to agents that can edit files. `go`, `do` and `queue run` skip that review, so use them for goals and repos you trust.

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
| `setup.enabled` / `.timeout_sec` | Dependency install in each new worktree |
| `git.enabled` / `.branch_prefix` | Worktree-and-branch per plan |

## Tests

```
cd crew && python3 -m unittest -v test_crew
```

They use fake `claude`/`codex` executables and never call a model. They cover:

- stack detection;
- config layering;
- worktree isolation of your checkout;
- crash recovery at each journaled point, including SIGKILL with an orphaned agent;
- the setup and `.gitignore` attacks;
- locking;
- hostile CLI replies and plans;
- the queue.
