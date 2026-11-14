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
   This copies crew to `~/.crew/bin/crew` and writes `~/.crew/config.json` (agents, models, split), then tells you how to put it on your `PATH`. It also gives each coding tool it finds on the machine the `crew` skill (see "Hand work to crew from the tool you have open").
3. On Linux, install `bubblewrap` and `socat` so Claude's sandbox works (`apt install bubblewrap socat`). macOS has its sandbox built in.
4. Check everything: `crew doctor`. It reports any CLI that is missing, logged out, or a version whose flags no longer match what crew uses.

## Use it in any project

```
cd ~/code/my-app
crew init                    # detect stack, setup and checks; saved in ~/.crew/projects/
crew go "Add dark mode to the settings page"
crew do "Fix the typo in the footer"     # one task, no planner call (cheaper); goes to Sol
crew do "Document the API" --agent Luna  # pick who does a direct task
crew plan "Add dark mode"    # plan only: read the plan it saves, then `crew run`
crew run                     # resume this checkout's latest plan
crew run --retry-failed      # also re-run failed, partial, blocked and skipped tasks
crew go "..." --dry-run      # plan, then only show who would get each task
crew plans                   # this checkout's plans (crew plans --all for every project)
crew projects                # every project crew knows: path, stack, checks, last plan
crew clean                   # remove crew's copies of finished plans, in every project
```

Every command that works on a project takes `--workdir <path>`; without it crew uses the git repository your terminal is in. `--config <file>` before the command uses that config instead of `~/.crew/config.json`.

When the run ends, crew prints the commands to review and merge:

```
git log --stat <base>..crew/<date>-<goal>
git merge crew/<date>-<goal>
```

What a run does, in order:

1. **Its own copy.** crew makes a local clone of your repo under `~/.crew/worktrees/` and works there on a new branch, `crew/<date>-<goal>`. It starts from the commit you were on when the plan was made. Your own checkout is never switched, stashed or written to, so you can keep working while crew runs. Uncommitted changes in your checkout aren't part of the run (crew says so when it plans). After each commit, crew copies the branch into your repo (a fast-forward `git fetch`, so it only ever adds commits to its own `crew/...` branch). If git refuses that, because you have the branch checked out or have committed on it yourself, crew tells you git's reason, keeps the commits in its copy and delivers them on the next `crew run`. Nothing is ever pushed anywhere. `--no-git` works in place instead.
2. **Setup.** crew installs dependencies in its copy (`npm ci`, `pnpm install`, a `.venv` with `pip install`...), because agents have no network. Setup runs outside any sandbox, so crew only runs it on the base commit, before any agent has touched the copy. If setup fails, the copy is rebuilt and setup retried on the next run, as long as no agent has worked in it yet. If setup changes tracked files (a lockfile), crew restores them.
3. **Baseline checks.** crew runs the project's checks (tests, typecheck, lint, build...) inside Codex's sandbox: no network, and no writes outside crew's copy.
4. **Tasks.** Tasks run one at a time, in dependency order. Each agent gets:
   - its role and the task;
   - a short project brief: stack, check commands, notes, and `CLAUDE.md`/`AGENTS.md` if present;
   - the summaries of the tasks it depends on.

   It ends by reporting `done`, `partial` or `blocked`; only `done` lets dependent tasks run. After each task crew commits that task's work as one commit (`crew(t1): ...`), without running git hooks or signing. First it checks that the copy's git configuration is unchanged and that it is still on crew's branch, at crew's last commit, with no merge or rebase in progress. If not, crew stops without running git there.
5. **Checks again.** A check that passed before and fails now is a regression, and crew gives Luna a fix task with the failing output (`verify.fix_rounds`, default 1). Checks that already failed before are reported, not counted. Checks that couldn't run before the first task (the profile had none yet, verification was off, or Codex's sandbox didn't work) are never run later as if they had: with nothing from before the work to compare with, every check that fails after it counts. Results are tied to the commit they checked, so a resumed run re-checks anything new.
6. **Finish.** If the plan is done, or needs your review, crew's copy is removed (unless uncommitted changes are left in it, or the branch couldn't be copied into your repo); your repo's branch keeps the work. If the plan is paused (usage limit, interrupt), the copy stays so `crew run` can continue in it. `crew clean` leaves a copy alone while its plan is paused or while it holds commits your repo doesn't have yet; `crew clean --all` removes those too, and says so when it discards commits. Both look at the copies of every project, not only the one you are in, so before `--all` check `crew plans --all` for paused plans elsewhere: their interrupted work goes too. Re-running a finished plan does nothing and exits 0 if it's done, 1 if it needs review.

A task is `pending`, `running`, `done`, `partial`, `blocked`, `failed` or `skipped`. A plan, as `crew plans` shows it, is `done`, `needs-review` or `paused`; `paused` means a task is still pending or running, or every task is done but the checks haven't been run on the result yet (a run that was stopped while they ran); a plan that has never run is `paused` too.

A plain re-run leaves `failed`, `partial` and `blocked` tasks alone. If every available provider hits its usage limit, the run stops with the current task `pending`; re-run later. If you commit on a `crew/...` branch yourself, the next run builds on your commits; if one of them moves a submodule, crew moves that submodule's checkout in its copy to match. If your commits change a file that an unfinished task has edited or deleted in the copy, crew stops before touching either and names the ways out. When the commit a plan starts from records a submodule commit that isn't on the submodule's remote yet, that submodule stays empty in crew's copy and the others are set up as usual. If a commit you add to a `crew/...` branch later records one, crew stops before the next task and gives git's reason; push the submodule's commit and run again. A submodule your commit removes isn't held against the task that was waiting. If your branch and crew's copy each have commits the other lacks, crew stops and gives you the two git commands that merge them. If you delete a `crew/...` branch and then continue its plan, crew puts the branch back.

To add a dependency, install and commit it on your own branch, then start a new plan. crew never installs from a branch that agents have changed.

## Writing a plan yourself

`crew run <file>` runs a plan you wrote, when you want to decide who does what:

```json
{
  "goal": "Move the repeated plotting code into one module",
  "workdir": "/home/you/code/notebooks",
  "tasks": [
    {"id": "t1", "title": "Design the module", "agent": "Astra",
     "instructions": "Read the notebooks and write DESIGN.md: the functions the module needs and their signatures.",
     "acceptance": "DESIGN.md exists and lists every function with its signature.", "depends_on": []},
    {"id": "t2", "title": "Build the module", "agent": "Sol",
     "instructions": "Implement plotting.py as DESIGN.md describes and use it in the notebooks.",
     "acceptance": "The checks listed in your prompt still pass.", "depends_on": ["t1"]}
  ]
}
```

- `goal`, `workdir` (an absolute path; for a git repository, its top folder) and `tasks` are required. Each task needs `id`, `title`, `agent`, `instructions` and `acceptance`; `depends_on` defaults to none.
- Every agent is shown the plan's `goal`, its own task, the project brief and a short summary (600 characters) of each task it depends on. Anything more that a later task needs must be a file an earlier task leaves in the repository.
- `crew run <file> --dry-run` checks the file and shows the branch, the setup and check commands, and which provider each task would go to. Nothing runs.
- crew writes its progress into the file and names the branch after it, so keep it somewhere stable outside the repository and outside `/tmp`. A plan saved outside `~/.crew/plans` (this one, or any saved with `--out`) is not picked up by a bare `crew run` or listed by `crew plans`: name the file every time, `crew run <file>`.

## Hand work to crew from the tool you have open

`crew install` writes one skill, `crew`, for each of these tools it finds on the machine, so you can say "crew: add dark mode to the settings page" in whichever one you are using:

| Tool | Where the skill goes |
|---|---|
| Claude Code | `~/.claude/skills/crew/SKILL.md` |
| Codex | `~/.agents/skills/crew/SKILL.md` |
| Antigravity (app, IDE and command line) | `~/.gemini/config/skills/crew/SKILL.md` |

It is the same text in all three; `crew skill` prints it. A skill named `crew` that crew didn't write is left alone. The skill tells the tool which crew command fits, to wait for the run, to report what crew printed, and then to read the finished branch itself and say what it would question. That last step is where a third model earns its place: it reviews work that neither it nor its vendor wrote.

Each tool asks before it runs crew, because crew needs the network and writes under `~/.crew`. To stop the question, add one allow rule yourself; crew never changes another tool's permissions:

| Tool | Rule |
|---|---|
| Claude Code | `"Bash(crew *)"` under `permissions.allow` in `~/.claude/settings.json` |
| Codex | `prefix_rule(pattern = ["crew"], decision = "allow")` in `~/.codex/rules/default.rules` |
| Antigravity | an Allow rule `command(crew)` in its permission settings |

These rules are for the tool you sit at. crew's own workers never run on them: Codex workers are started without your execpolicy rules, and a Claude worker has either a sandboxed shell or none.

crew refuses to start from inside one of its own tasks, so a worker that has the same skill can't start a second crew.

**Why Antigravity and Gemini are not workers.** A tool does tasks for crew only if crew can switch its sandbox on for that run and prove it works, stop it loading settings, hooks and tool servers from the repository an earlier agent worked in, and read a verdict it can check. Antigravity's command-line tool (1.5) takes its permission settings from your own settings file with no per-run override, has no way to show its sandbox is active, and always loads the project's hooks and tool servers. Gemini CLI (0.68) has the same gaps and no longer serves personal Google accounts. Both were checked against their documentation on 2026-11-13; neither could be run where crew was built. They fit the other end: the place you talk to crew from.

## Several projects at once

```
crew queue add "Add a GitHub Actions workflow that runs the checks" --workdir ~/code/a --workdir ~/code/b
crew queue add "Update the README setup steps" --all --direct   # every known project, one task each, no planner
crew queue list
crew queue run               # 2 projects at a time by default
crew queue rm q3 ; crew queue clear
```

Each project's items run in order; different projects run in parallel. A project here is a checkout: its items stay in one line whatever its `origin` was called when each was queued. By default crew runs at most one Claude task and one Codex task at the same time (`providers.<p>.max_parallel`). When the provider a task would use is busy and the other one is idle, crew hands it to the idle one. It won't if that would push the idle one past its share by more than `tolerance`, or if `split.mode` is `fixed`. That way both plans work at once.

A queue item ends as:

- `done`;
- `needs-review`: a task wasn't `done`, or a regression is left;
- `paused`: a usage limit, no provider available, an interrupt, or its plan already running in another crew. `crew queue run` picks it up again;
- `error`: `crew queue run --retry-failed` tries it again.

Only one `crew queue run` can work at a time. `--jobs N` sets how many projects run at once.

## What crew guarantees, and what it can't

No tool driving language models can promise correct code. What crew does promise, and its test suite checks (`ASSURANCE.md` records the evidence behind each promise, and `CLAUDE.md` the rules for changing crew):

- **Your work is safe.** crew never modifies your checkout, your branches or your uncommitted files, and never pushes. The only change it makes to your repo is adding commits to its own `crew/...` branches. Agents work in crew's own clone. Your checkout and the clone's `.git` are read-only to them: Codex's sandbox does this by itself, and crew tells Claude's sandbox to deny writes to both, because otherwise Claude can write a repo's git directory. Before running git in the clone after an agent, crew checks that the clone's git configuration is unchanged. crew drops the git location variables it inherits (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and the like), so starting it from a git alias or hook can't point its git commands at your repository, and copying a branch into your repo never fetches into your submodules. crew reads your checkout without taking git's lock on its index, so a crew that is stopped can't leave a `.git/index.lock` there that blocks your own git commands.
- **Crashes don't lose or repeat work.** Every step that changes anything is written to the plan file first: making the copy, each commit, removing the copy (at the end of a run, or by `crew clean`). A crash, kill or power cut at any point resumes without repeating a committed task or losing an agent's files. Crash points tested: while making the copy, after the branch is recorded, before a commit, after a commit, while bringing your own commits into the copy, while removing the copy (a `crew clean` stopped part-way included: the next run finishes the removal and starts the copy over, never works in what is left), and SIGKILL mid-task. Agent processes and long git commands left running by a crew that was killed are stopped before the next run continues; a planner such a crew left at work is stopped by the next `crew plan`, `go`, `do`, `run` or `queue run`. Commits your repo hasn't accepted yet stay in crew's copy until a later run delivers them. If crew's copy is deleted while a task's work is uncommitted, that task runs again instead of being counted as done, even when the rebuild of the copy is itself interrupted. A task whose commit was made just before a crash or a stop is still checked for what the commit left out (a file inside a submodule, a nested repository), and is `partial` for it as it would be without the crash. A merge of your own commits into the copy that is stopped part-way is taken back on the next run, without touching a task's uncommitted work: not a file the task changed, not one it deleted, and not a file git ignores that was there before the merge. What the merge itself wrote is removed, ignored or not, so it can't end up in a task's commit later (of a merge an older crew started, a file written at an ignored path stays). A file a task deleted that your commits change stays deleted even when crew 3.6 or 3.7 started the merge, and crew names it. A second Ctrl-C, SIGTERM or hang-up, such as closing the terminal, doesn't cut the stopping short: crew finishes killing its agents first. A stop is acted on whenever it arrives, including the moments `crew queue run` is starting its threads or an agent, looking at its workers, or asking a CLI whether it is logged in. A check that starts failing is recorded together with the task that will fix it, so a kill between the two can't lose the fix. crew can run as a background job (`crew run plan.json > crew.log 2>&1 &`): nothing it starts is given the terminal, so job control doesn't stop it.
- **One run per plan.** A second `crew run` of the same plan, or a second `crew queue run`, is refused, and `crew clean` never removes the copy of a running plan. Each copy belongs to one plan file: a second plan that names the same copy is refused, a copied plan file stays refused after `crew clean --all` and after the original has finished (for as long as the original file exists), and a plan saved with `--out` gets a branch name that includes a hash of its path, so two files with the same name can't collide. If you add a git remote while a plan is running, crew leaves that plan's file where it is until the run ends, and a path crew printed before the move still works with `crew run`. If you rename, re-point or remove `origin`, `crew run` and `crew plans` still find the checkout's earlier plans, and a paused queue item is continued, not planned again. The same folder is not always the same repository: a plan kept under another name counts as this checkout's only if the repository has the commit that plan started from, so a plan of a project that used to be in the folder is never continued in the one that replaced it, by `crew run` or by `crew queue run` (`crew plans --all` still lists it, and its queue item ends as `error`). That holds for a project that had no remote, whose plans are kept under the folder's name, when the one that replaced it has one. A plan named through a symbolic link is the file the link leads to: one lock, one branch, one copy, whichever way you name it. A plan whose folder you moved, leaving a link at the old place, is still itself, not a copy. All of this assumes you don't rename a plan's file, or delete the `.plan` file beside its copy, while that plan is running: the lock that keeps a second run out is named after the file's path. The queue, usage log and state files are locked between processes and written atomically.
- **Agent-modified code never runs outside a sandbox.** Setup only runs on the base commit. Checks run in Codex's sandbox, and are skipped, never run unsandboxed, if that sandbox doesn't work (unless you set `verify.unsandboxed`). Claude's shell access is granted only after its sandbox is confirmed to work. Flags you add in `providers.<p>.args` are added to the ones that keep a worker contained; leaving those out of your list doesn't remove them. If you pass `--settings` more than once, crew keeps the one Claude would read, the last, and adds its rules to that one. crew's own flags come before yours, so a flag of yours that is missing its value can't take one of crew's for it; a `--settings` with no value is an error. Codex is started without execpolicy rules (an `allow` rule there runs a command outside the sandbox) and is told not to trust crew's copy, so a `.codex/config.toml` or a rules file that an earlier agent left in the copy is not read. Codex's sub-agent tools are turned off, whatever your own settings say: they can resume any Codex session saved on your machine, yours included, and Codex writes what they send into that session's history as if you had typed it. Your own hooks, Claude's and Codex's, are off for crew's workers and planner, and so are the MCP servers your Codex configuration names: each CLI runs them itself, outside its sandbox, in the folder it works in, so a formatter hook or a server started with `npx` would run what an agent left in crew's copy, and the planner's formatter would write your checkout. Claude gets a shell only while every command it could run is sandboxed: none when its sandbox can't start, and none while your own Claude settings take commands out of the sandbox (`sandbox.excludedCommands`). Before running git in its copy, crew checks that every submodule's `.git` is still the file it was: it sits where an agent can replace it with a repository of its own, whose settings git would obey. crew refuses to run agents when its own home (`CREW_HOME`, default `~/.crew`) or its config file is inside a temp directory: agents can write there, so one could rewrite crew's config or a project's setup commands, which crew runs unsandboxed. `CREW_ALLOW_TEMP_HOME=1` overrides this; the test suite sets it.
- **Nothing is called done without evidence.** A task is `done` only if its agent says so in a structured reply. A plan is `done` only if every task is, no check regressed, and setup succeeded. Exit codes and queue states follow from that. A plan that was stopped while its last checks ran is `paused`, not `done`, until a re-run has checked it. A plan whose checks couldn't run before its first task never takes its own work for the state before it. A run whose output is piped into something that stops reading (`crew run plan.json | head`) stops there and exits 141, not 0; so does one whose output is lost any other way (a full disk, a terminal closed under `setsid`). Only a listing whose reader left exits 0. A command that changes something says so only once it is done (`crew queue add` saves the queue before it prints `queued ...`), and exits 141 if it finds its reader gone. Ctrl-C exits 130 and leaves queue items `paused` even when the line that says so can no longer be printed (`crew queue run | tee log`).
- **Commits contain only the task's work.** Generated files (`__pycache__`, caches, CLI state, the empty placeholders Claude's sandbox can leave) are left out. So are files that existed before the task, even if an agent un-ignores them, and nested git repositories. A file a task leaves inside a submodule's folder belongs to another repository, so crew can't commit it: it says so, marks the task `partial` and keeps its copy, where the file is.
- **Bad input gives an error, not a mess.** Malformed CLI replies, plans, profiles, config or state files produce a clear error or a failed attempt, never a traceback or a wrong `done`. That includes a file nested absurdly deep or holding a number too long to read, a copy's `.plan` pointer that isn't valid text, numbers that are valid JSON but unusable (`1e999`, `NaN`, a timeout of years, or of no time at all), a switch written in quotes (`"unsandboxed": "false"` is an error, not true), values of the wrong type in what crew itself records in a plan (the baseline of the checks included), a queue item whose folder or plan file isn't an absolute path (it is ignored, not run or written in the folder crew was started from), a plan whose `workdir` or a profile whose `root` isn't an absolute path, a plan whose `workdir` is a folder inside a repository instead of the repository (refused by `--dry-run` too), and an empty `git.branch_prefix`. A setting crew doesn't know, at the top of the config or inside one of its sections, is named in a note instead of being silently ignored. An error stays an error, exit 2 and one line, when nothing reads crew's output any more. It also includes a `package.json` whose fields aren't what npm expects (what can be read is used), half of a surrogate pair in a task's text (a JSON escape such as `\ud83d`: refused in a plan, shown escaped in an agent's reply), and a project folder or plan file whose name isn't valid UTF-8 (refused by name; a goal typed with such a byte still works). An error no check foresaw is reported in one line that names where in crew it happened and exits 2; `CREW_TRACEBACK=1` shows the full trace. CLI updates that remove a flag crew relies on are caught up front by `crew doctor` and by every run.

What it can't promise: that the agents' code is right (that's what the checks and your review are for), that checks catch everything (they're only as good as the project's tests), or that the sandboxes of the two CLIs have no holes of their own. Both sandboxes let agents write to temp directories, so keep your repos outside `/tmp`. Both limit what an agent can write and reach, not what it can read: a Codex worker, and any check, can read the files in your home directory, the other tool's stored login among them (crew hides Codex's from Claude's workers and planner; Codex's sandbox has no setting for the reverse). Nothing in a sandbox can send that anywhere, but what a worker reads goes to its own model provider, and a failing check's output is kept in the plan file and shown to the agent that fixes it. A SIGKILL that lands within a few milliseconds of crew starting a git command can leave that one git process running with no record; the next run then fails once with a clear message and works when run again. Repositories with submodules are covered by a handful of tests, not by every case: a submodule update, removal or unpushed commit of yours (alone, and beside submodules that are fine), a submodule you move to another folder, an interrupted move of a submodule's checkout, files a task leaves inside one, and an agent replacing a submodule's `.git`. Nested submodules are untested. crew trusts your home directory: your global git config, `~/.claude`, `~/.codex` and `~/.crew` are yours, and anything that can write there can already run code as you.

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
- **Python:** `uv sync`, `poetry install`, or a `.venv` from `requirements*.txt`/`pyproject.toml`, kept out of commits through the clone's own `.git/info/exclude`:
  - dev and test requirements files and extras are installed;
  - plain `def test_...` files get pytest installed and used, otherwise unittest runs on the `tests` folder, or on each folder that holds test files;
  - ruff runs if configured, plus a syntax check.
- **Notebooks:** a check that every `.ipynb` is valid JSON, plus a note telling agents to edit notebooks with nbformat, never as raw text.
- **Static sites:** a check that every relative `href`/`src` points at a file that exists. Flask/Django `templates/` folders don't count as a static site.
- **Rust, Go,** and `make test` / `make lint`.

`overrides` accepts any key from `~/.crew/config.json`. `agents` replaces the whole crew wherever it is set, in `~/.crew/config.json` too, so always list every agent with its `role`; other keys merge. A project's own check and setup commands belong under `project`, as above, not under `overrides`. `crew config` prints the merged result for the current project; `crew usage` shows the targets from `~/.crew/config.json` only. If you add a git remote after `crew init`, crew moves the profile and plans to the new name (not those of a different project that used to be in the folder: see below); if that name already has another checkout's profile, both are kept and crew says which one is in use and where the other went. If you rename, re-point or remove `origin`, crew copies the checkout's profile to the new name and says so. That holds for each checkout that uses the profile, not only the one it was detected in: crew notes the others in the profile as `roots` (one that last ran under an earlier crew is on record once it runs crew again), and `crew init --force` keeps them on record. If `origin` goes back to a name the checkout had before, the profile it has used since is copied back over the older one there, and crew says so, unless other checkouts share that one. The old profile stays, because another checkout of the old remote may use it. If a different project now sits in that folder, its repository has none of the commits crew recorded for the first one (the profile's `first_commit`, or the commits the folder's plans started from): crew then leaves the old profile alone, says so and detects the new project afresh. Where nothing was recorded (a profile written by an earlier crew, for a folder that never had a plan) crew can't tell, copies the profile and says so; `crew init --force` detects afresh.

## How usage is divided

Every call is logged to `~/.crew/usage.jsonl` with its project, including calls that were interrupted (Ctrl-C, SIGTERM or a hang-up). Plan limits are per account, so the split is computed across all projects over the last `window_hours`. `crew usage` shows the totals and a per-project breakdown.

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

- **Codex workers:** `--sandbox workspace-write`, so writes stay in crew's copy (minus its `.git`) and temp directories, and there's no network. Every Codex call also gets `--ignore-rules` and is told that the directory it works in is untrusted: your execpolicy rules, and any `.codex/` settings or rules in crew's copy, are not loaded (your own `~/.codex/config.toml` is). Codex's sub-agent tools are off (`--disable multi_agent --disable multi_agent_v2`, so also when your own `config.toml` turns the second version on): they can resume any session saved in `~/.codex`, and what they send it is written into that session's history outside the sandbox, where your next `codex resume` would read it as your own words. Your Codex hooks are off (`--disable hooks`), and so is every MCP server your Codex configuration names: crew asks `codex mcp list --json` in its copy, with your `-c` and `-p` flags, and passes `-c mcp_servers.<name>.enabled=false` for each. Codex runs both outside its sandbox, in crew's copy, so a formatter hook would run a script an agent edited and `npx -y <server>` a package an agent left in `node_modules`. If crew can't list your servers, or one has a name `-c` can't give (anything but letters, digits, `-` and `_`), Codex isn't run. Web search is the CLI's cached search unless your own `~/.codex/config.toml` sets `web_search = "live"`; that setting is yours, and crew leaves it as it is.
- **Claude workers:** `acceptEdits` plus Claude Code's sandbox, with its escape hatch turned off (`allowUnsandboxedCommands: false`) and writes denied to the copy's `.git` and to your checkout. Writes stay in crew's copy and temp directories, and there's no network. crew first checks that the sandbox can actually start (built in on macOS; bubblewrap and socat on Linux, tested once). Only then does it let Claude run shell commands without prompts (`--allowedTools Bash`). If the sandbox isn't available, or your own Claude settings exclude commands from it (`sandbox.excludedCommands`), Claude can still edit files but has no shell (`--disallowedTools Bash`): a command that an allow rule of yours permits would otherwise run unsandboxed. crew says so in the run's output and in `crew doctor`. Claude workers are offered only the tools a task needs (`--tools`: Read, Edit, Write, Glob, Grep, NotebookEdit and, when granted, Bash) and none of your MCP servers (`--strict-mcp-config`). The CLI's other tools work outside its sandbox: EnterWorktree and the Agent tool's worktree isolation make it write the copy's `.git`, ListAgents and SendMessage reach your other Claude sessions, and WebFetch, WebSearch and Monitor reach the network once an allow rule of yours permits them. A `--tools` in `providers.claude.args` can narrow that list, not add to it. Your hooks are off (`disableAllHooks` in the settings crew passes, whatever a `--settings` of yours says): Claude runs them itself, outside its sandbox, in crew's copy, so a hook that formats edited files would run what an agent left in `node_modules`. Codex's stored login is hidden from Claude's shell and from its file tools.
- **Checks:** run by crew inside `codex sandbox`, with credentials removed from the environment. That needs only the `codex` binary, not a Codex login. crew probes once that the sandbox really runs commands. If it doesn't, checks are skipped, never run unsandboxed, unless you set `verify.unsandboxed`. Like the workers, it reads no `.codex/` settings from crew's copy.
- **Planner:** read-only. Claude runs in plan mode with Read, Grep and Glob only, none of your MCP servers or hooks, and Codex's stored login denied to it (plan mode lets those tools read outside the checkout without asking); Codex runs `--sandbox read-only`, without your execpolicy rules, sub-agent tools, hooks or MCP servers. A hook runs outside the sandbox, and a formatter hook in the planner wrote the checkout it reads. It reads your checkout as it is and is told to give paths relative to the repo; any absolute path into your checkout is rewritten to point at crew's copy.
- **Repo settings ignored:** Claude calls load only your user-level settings, so a `.claude/settings.json` in the repo can't add hooks or loosen permissions. crew's own git commands never run repo hooks or an fsmonitor.

Read a plan before you `run` it. Its instructions go to agents that can edit files. `go`, `do` and `queue run` skip that review, so use them for goals and repos you trust.

## Config reference

| Key | Meaning |
|---|---|
| `planner.provider`, `planner.max_tasks`, `planner.model` | Who plans, the task limit, per-provider planner model |
| `agents.<name>.role` / `.prefers` / `.model` | The crew; `model` is per provider, e.g. `{"claude": "sonnet"}` |
| `providers.<p>.cmd` / `.model` / `.args` / `.timeout_sec` | CLI path, default model, extra worker flags, per-task time limit |
| `providers.<p>.max_parallel` | Concurrent calls to this provider in `queue run` (global setting) |
| `providers.claude.sandboxed_bash` | Give Claude a shell, sandboxed and without prompts, when its sandbox works (default true); `false` gives it none |
| `providers.<p>.enabled` | `false` takes a provider out of rotation |
| `split.*` | See "How usage is divided" |
| `verify.enabled` / `.fix_rounds` / `.fixer` / `.timeout_sec` / `.unsandboxed` | Checks and the fix loop |
| `setup.enabled` / `.timeout_sec` | Dependency install in each new copy |
| `git.enabled` / `.branch_prefix` | Separate copy and branch per plan; the prefix can't be empty, crew knows its own branches by it |

Switches are JSON booleans: `false`, not `"false"`. A setting crew doesn't know, at either level, is reported and ignored.

## Tests

```
cd crew && python3 -m unittest -v test_crew
```

They use fake `claude`/`codex` executables and never call a model. They cover:

- stack detection;
- config layering;
- isolation of your checkout and your `.git`;
- crash recovery at each journaled point, including SIGKILL with an orphaned agent;
- tampered git configuration in crew's copy;
- the setup and `.gitignore` attacks;
- locking;
- hostile CLI replies and plans;
- delivering commits after git refused them, a deleted or diverged `crew/...` branch, and `crew clean` with undelivered commits;
- one plan per copy, and the refusal to run with crew's home in a temp directory;
- your own commits on a `crew/...` branch: a submodule update, a commit made while a task was mid-commit, a file that setup also generates, and a kill while crew adopts them;
- git location variables in crew's environment, a remote added while a plan runs, a second stop signal, and deeply nested or corrupt state files;
- your own worker flags not dropping the sandbox, and a submodule's `.git` replaced by an agent;
- a merge of your commits that is interrupted, a submodule you removed or haven't pushed, git settings of yours that change how git behaves (`merge.autoStash`, `clone.defaultRemoteName`), and a second `--settings`;
- a merge of your commits that is interrupted after a commit of crew's own was, a file a task deleted that your commits change or delete, an ignored file in the merge's way, `merge.verifySignatures`, one submodule that can't be fetched beside others that can, a submodule you move while you update another, an agent's file in the way of a submodule update, files a task leaves inside a submodule, a plan whose `workdir` is a folder inside a repository, and an empty branch prefix;
- a stop signal while `queue run` starts an agent or handed to a worker thread, two stop signals at once, crew as a background job of a terminal, a closed output pipe, a changed `origin`, a stale copy of a plan, and unusable numbers or wrong types in crew's own files;
- the tools each CLI offers a worker: none that reaches your other sessions or the network, none that makes the CLI write crew's `.git`, Codex's login denied to Claude's file tools and to the planner, and Codex's sub-agent tools off;
- your own Claude and Codex hooks and your Codex MCP servers, which the CLIs run outside their sandboxes: off for every worker and the planner;
- the skill `crew install` writes, crew refusing to start inside its own task, a hang-up ignored at start, settings crew doesn't know, and plans kept outside crew's folder;
- Codex settings and rules left in crew's copy, your own Claude settings that take commands out of the sandbox, a worker flag without its value, switches written in quotes, and which keys each CLI is started with;
- a different repository at a path crew knew, a second checkout whose `origin` changes, `origin` removed and added back, a plan run through a symbolic link or moved behind one, a queue item after `origin` changed twice, switches that aren't true or false, a relative `workdir` or profile `root`, a recorded baseline of the wrong type, and an error with nobody reading;
- a stop signal while `queue run` starts its threads, holds a lock of theirs or waits for a CLI's login check, Ctrl-C with the output's reader gone, `queue run` losing its reader or hitting an error of crew's own while agents are at work, output to a full disk, `queue add` into a reader that has left, a kill between a regression and its fix task, a plan stopped during its last checks, odd `package.json` shapes, a 1500-task dependency chain, absurd token counts, text that can't be encoded, and a time limit on every process crew waits for;
- a kill right after a commit of a task that left files inside a submodule or a nested repository, a file an interrupted merge of your commits wrote at a path that stops being ignored, checks that couldn't run before the first task, and a merge of your commits that crew 3.6 or 3.7 started;
- a `crew clean` stopped part-way, two checkouts of one remote planned in the same second by `queue run`, a queue item whose plan runs in another crew, a planner left at work by a killed crew, a stop while crew reads your checkout, and the usage of a call cut short by SIGTERM or a hang-up;
- a project without a remote replaced by one with a remote, for a bare `crew run` and for the queue, `origin` changed and put back, `crew init --force` in one of two checkouts, `queue run` losing its reader as it finds a moved plan, a timeout of no time at all, a relative plan path in the queue, a number too long to read, and Codex's second set of sub-agent tools;
- the queue.
