#!/usr/bin/env python3
"""crew: plan work, hand it to Astra, Sol and Luna, and split usage between
Claude Code and Codex.

Standard library only. It shells out to the `claude` and `codex` CLIs, so each
one runs on whatever login it already has: a Claude or ChatGPT plan, or an API
key. Keys are never printed or written anywhere by this script; `codex-login`
pipes OPENAI_API_KEY into Codex on stdin.

  python crew.py doctor                 check both CLIs and their logins
  python crew.py plan "goal"            write a task plan (read-only run)
  python crew.py run crew-plan.json     execute the plan; re-run to resume
  python crew.py go "goal"              plan + run
  python crew.py usage                  usage split and cooldowns
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "crew.json"
PROVIDERS = ("claude", "codex")
CREW_HOME = Path(os.environ.get("CREW_HOME") or Path.home() / ".crew")
# Applied only to the provider's own error message, never to raw output.
LIMIT_RE = re.compile(
    r"usage[ _-]?limit|rate[ _-]?limit|limit reached|hit your \w* ?limit|quota|"
    r"(?:status|code|error)\W{0,3}429|429 too many requests|too many requests",
    re.I,
)
# Env vars that count as a login even when the CLI's own status check says no.
ENV_LOGIN = {"claude": ("ANTHROPIC_API_KEY",), "codex": ("CODEX_API_KEY",)}
# Each CLI only gets its own provider's keys.
FOREIGN_KEYS = {"claude": ("OPENAI_API_KEY", "CODEX_API_KEY"), "codex": ("ANTHROPIC_API_KEY",)}
REQUIRED_TASK_FIELDS = ("id", "title", "agent", "instructions", "acceptance")
RESULT_KEEP = 4000  # chars of each agent's final message kept in the plan file


# --------------------------------------------------------------------------- config

def load_config(path: Path) -> dict:
    cfg = json.loads(Path(path).read_text())
    agents = cfg.get("agents") or {}
    if not agents:
        sys.exit("config: no agents defined")
    for name, a in agents.items():
        if a.get("prefers", "any") not in (*PROVIDERS, "any"):
            sys.exit(f"config: agent {name}: prefers must be claude, codex or any")
    split = cfg.setdefault("split", {})
    if split.get("mode", "by_role") not in ("by_role", "balanced", "fixed"):
        sys.exit("config: split.mode must be by_role, balanced or fixed")
    if split.get("metric", "tasks") not in ("tasks", "tokens", "seconds"):
        sys.exit("config: split.metric must be tasks, tokens or seconds")
    for p in PROVIDERS:
        pc = cfg.setdefault("providers", {}).setdefault(p, {})
        cmd = os.path.expanduser(pc.get("cmd") or p)
        found = shutil.which(cmd)
        # Absolute path, because the CLI is launched with cwd set to the workdir.
        pc["cmd"] = os.path.abspath(found) if found else cmd
    return cfg


def provider_cfg(cfg: dict, p: str) -> dict:
    return cfg["providers"][p]


def provider_env(p: str) -> dict:
    env = dict(os.environ)
    for k in FOREIGN_KEYS[p]:
        env.pop(k, None)
    return env


# --------------------------------------------------------------------------- ledger

def _ledger() -> Path:
    return CREW_HOME / "usage.jsonl"


def _state() -> Path:
    return CREW_HOME / "state.json"


def record_usage(entry: dict) -> None:
    CREW_HOME.mkdir(parents=True, exist_ok=True)
    with _ledger().open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_usage(window_hours: float) -> list[dict]:
    if not _ledger().exists():
        return []
    cutoff = time.time() - window_hours * 3600
    out = []
    for line in _ledger().read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict) and e.get("ts", 0) >= cutoff:
            out.append(e)
    return out


def metric_value(entry: dict, metric: str) -> float:
    if metric == "tokens":
        return float(entry.get("tokens") or 0)
    if metric == "seconds":
        return float(entry.get("seconds") or 0)
    return 1.0


def usage_totals(cfg: dict) -> tuple[dict[str, float], int]:
    """Per-provider usage in the configured metric, plus the number of calls."""
    split = cfg["split"]
    totals = {p: 0.0 for p in PROVIDERS}
    runs = 0
    for e in read_usage(split.get("window_hours", 168)):
        if e.get("provider") in totals:
            totals[e["provider"]] += metric_value(e, split.get("metric", "tasks"))
            runs += 1
    return totals, runs


def per_call_estimate(cfg: dict) -> dict[str, float]:
    """Average usage of one call per provider, for dry-run projections."""
    split = cfg["split"]
    metric = split.get("metric", "tasks")
    if metric == "tasks":
        return {p: 1.0 for p in PROVIDERS}
    entries = read_usage(split.get("window_hours", 168))
    vals = {p: [metric_value(e, metric) for e in entries if e.get("provider") == p] for p in PROVIDERS}
    every = [v for vs in vals.values() for v in vs]
    overall = sum(every) / len(every) if every else 1.0
    return {p: (sum(v) / len(v) if v else overall) or 1.0 for p, v in vals.items()}


def load_state() -> dict:
    try:
        return json.loads(_state().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    CREW_HOME.mkdir(parents=True, exist_ok=True)
    _state().write_text(json.dumps(state, indent=2))


def set_cooldown(cfg: dict, provider: str) -> float:
    minutes = cfg["split"].get("cooldown_minutes", 60)
    until = time.time() + minutes * 60
    state = load_state()
    state.setdefault("cooldown_until", {})[provider] = until
    save_state(state)
    return until


def cooldown_until(provider: str) -> float:
    return float(load_state().get("cooldown_until", {}).get(provider, 0))


# --------------------------------------------------------------------------- routing

def installed(cfg: dict, p: str) -> bool:
    return shutil.which(provider_cfg(cfg, p)["cmd"]) is not None


_AUTH_CACHE: dict[str, tuple[bool, str]] = {}


def login_status(cfg: dict, p: str) -> tuple[bool, str]:
    """(logged in?, detail). Plan logins and API keys both count. Cached per process."""
    for var in ENV_LOGIN[p]:
        if os.environ.get(var):
            return True, f"using {var}"
    if p not in _AUTH_CACHE:
        sub = ["auth", "status"] if p == "claude" else ["login", "status"]
        try:
            r = subprocess.run([provider_cfg(cfg, p)["cmd"], *sub], capture_output=True,
                               text=True, timeout=60, env=provider_env(p))
            _AUTH_CACHE[p] = (r.returncode == 0, short((r.stdout or r.stderr).strip(), 100))
        except (OSError, subprocess.TimeoutExpired) as e:
            _AUTH_CACHE[p] = (False, str(e))
    return _AUTH_CACHE[p]


def available_providers(cfg: dict) -> list[str]:
    now = time.time()
    return [
        p for p in PROVIDERS
        if provider_cfg(cfg, p).get("enabled", True)
        and installed(cfg, p)
        and cooldown_until(p) <= now
        and login_status(cfg, p)[0]
    ]


def choose_provider(cfg: dict, agent: str, totals: dict, avail: list[str], runs: int) -> str | None:
    """Pick the provider for one task.

    by_role:  the agent's preferred provider, unless that provider's share of
              usage is over its target by more than `tolerance` (checked once the
              window holds at least `min_runs` calls).
    balanced: whichever provider is furthest under its target share.
    fixed:    always the agent's preferred provider when it is available.
    Agents that prefer "any" always get the provider furthest under target.
    """
    if not avail:
        return None
    if len(avail) == 1:
        return avail[0]
    split = cfg["split"]
    raw = split.get("target_share") or {}
    weights = {p: float(raw.get(p, 1.0)) for p in avail}
    wsum = sum(weights.values()) or 1.0
    target = {p: weights[p] / wsum for p in avail}
    used = sum(totals.get(p, 0.0) for p in avail)
    share = {p: (totals.get(p, 0.0) / used if used else 0.0) for p in avail}
    over = {p: share[p] - target[p] for p in avail}
    least_used = min(avail, key=lambda p: (over[p], PROVIDERS.index(p)))

    mode = split.get("mode", "by_role")
    pref = cfg["agents"][agent].get("prefers", "any")
    if pref not in avail or mode == "balanced":
        return least_used
    if mode == "fixed":
        return pref
    if runs >= int(split.get("min_runs", 6)) and over[pref] > float(split.get("tolerance", 0.15)):
        return least_used
    return pref


# --------------------------------------------------------------------------- providers

@dataclass
class Result:
    provider: str
    ok: bool
    text: str = ""
    structured: object = None
    tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    limited: bool = False
    error: str = ""
    denied: list = field(default_factory=list)


def is_git_repo(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop the CLI and the tool processes it started. SIGTERM first so the CLI
    can clean up its own children, then SIGKILL whatever is left."""
    if not hasattr(os, "killpg"):
        proc.kill()
        return
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # stragglers in the group
            except (ProcessLookupError, PermissionError):
                pass
        return


def _exec(cmd: list[str], prompt: str, cwd: Path, timeout: float, env: dict) -> tuple[int, str, str, float]:
    t0 = time.time()
    if not Path(cwd).is_dir():
        return 127, "", f"workdir not found: {cwd}", 0.0
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=str(cwd), env=env,
                                start_new_session=True)
    except OSError as e:
        return 127, "", f"cannot start {cmd[0]}: {e.strerror or e}", time.time() - t0
    try:
        out, err = proc.communicate(prompt.encode(), timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        rc, err = 124, (err or b"") + f"\ntimed out after {timeout:.0f}s".encode()
    except BaseException:
        _kill_group(proc)  # Ctrl-C does not reach a child in its own session
        raise
    dec = lambda b: (b or b"").decode("utf-8", "replace")  # noqa: E731
    return rc, dec(out), dec(err), time.time() - t0


def _last_json_object(stdout: str) -> dict | None:
    s = stdout.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(s.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def _parse_structured(res: Result) -> None:
    if res.ok and res.structured is None:
        try:
            res.structured = json.loads(res.text)
        except json.JSONDecodeError:
            res.ok, res.error = False, "planner did not return JSON"


def run_claude(cfg: dict, prompt: str, workdir: Path, *, schema: dict | None = None,
               read_only: bool = False) -> Result:
    pc = provider_cfg(cfg, "claude")
    # User-level settings only. Project settings in the workdir (hooks, defaultMode,
    # allow rules) could be written by the other agent and would run unsandboxed.
    cmd = [pc["cmd"], "-p", "--output-format", "json", "--setting-sources", "user"]
    if pc.get("model"):
        cmd += ["--model", pc["model"]]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if read_only:
        cmd += ["--permission-mode", "plan", "--disallowedTools", "Edit,Write,NotebookEdit"]
    else:
        cmd += list(pc.get("args") or [])
    rc, out, err, secs = _exec(cmd, prompt, workdir, pc.get("timeout_sec", 1800), provider_env("claude"))
    data = _last_json_object(out) or {}
    text = data.get("result") or ""
    ok = rc == 0 and bool(data) and not data.get("is_error")
    u = data.get("usage") or {}
    tokens = int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                 + (u.get("output_tokens") or 0))
    res = Result("claude", ok, text=text, structured=data.get("structured_output"),
                 tokens=tokens, cost_usd=data.get("total_cost_usd"), seconds=secs)
    for d in data.get("permission_denials") or []:
        if isinstance(d, dict):
            inp = d.get("tool_input") or {}
            detail = inp.get("command") or inp.get("file_path") or ""
            res.denied.append(f"{d.get('tool_name', '?')}" + (f"({short(str(detail), 80)})" if detail else ""))
    if not ok:
        msg = text or "; ".join(map(str, data.get("errors") or [])) or str(data.get("subtype") or "")
        res.error = (msg or err.strip() or short(out, 500) or f"exit {rc}")[-2000:]
        limit_text = msg if data else err
        res.limited = data.get("api_error_status") == 429 or bool(limit_text and LIMIT_RE.search(limit_text))
    if schema is not None:
        _parse_structured(res)
    return res


def run_codex(cfg: dict, prompt: str, workdir: Path, *, schema: dict | None = None,
              read_only: bool = False) -> Result:
    pc = provider_cfg(cfg, "codex")
    with tempfile.TemporaryDirectory(prefix="crew-codex-") as tmp:
        last = Path(tmp) / "last.txt"
        cmd = [pc["cmd"], "exec", "--json", "-C", str(workdir), "-o", str(last)]
        if pc.get("model"):
            cmd += ["-m", pc["model"]]
        if not is_git_repo(workdir):
            cmd += ["--skip-git-repo-check"]
        if schema is not None:
            sf = Path(tmp) / "schema.json"
            sf.write_text(json.dumps(schema))
            cmd += ["--output-schema", str(sf)]
        cmd += ["--sandbox", "read-only"] if read_only else list(pc.get("args") or [])
        cmd += ["-"]  # prompt on stdin
        rc, out, err, secs = _exec(cmd, prompt, workdir, pc.get("timeout_sec", 1800), provider_env("codex"))
        text = last.read_text(errors="replace").strip() if last.exists() else ""

    completed, tokens, failures, notices, agent_msgs = False, 0, [], [], []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "turn.completed":
            completed = True
            u = ev.get("usage") or {}
            fresh = (u.get("input_tokens") or 0) - (u.get("cached_input_tokens") or 0)
            tokens += int(max(fresh, 0) + (u.get("output_tokens") or 0))
        elif kind == "turn.failed":
            e = ev.get("error")
            failures.append((e.get("message") if isinstance(e, dict) else e) or json.dumps(ev))
        elif kind == "error":
            # Usually a retry notice ("Reconnecting... 1/5"); fatal only if no turn completes.
            notices.append(ev.get("message") or json.dumps(ev))
        elif kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                agent_msgs.append(item["text"])
    if not text and agent_msgs:
        text = agent_msgs[-1]
    ok = rc == 0 and completed and not failures
    res = Result("codex", ok, text=text, tokens=tokens, seconds=secs)
    if not ok:
        msg = "\n".join(map(str, failures)) or (notices[-1] if notices else "") or err.strip()
        res.error = (msg or short(out, 500) or f"exit {rc}")[-2000:]
        res.limited = bool(msg and LIMIT_RE.search(msg))
    if schema is not None:
        _parse_structured(res)
    return res


RUNNERS = {"claude": run_claude, "codex": run_codex}


def call(cfg: dict, provider: str, prompt: str, workdir: Path, *, agent: str, task_id: str,
         schema: dict | None = None, read_only: bool = False) -> Result:
    res = RUNNERS[provider](cfg, prompt, workdir, schema=schema, read_only=read_only)
    record_usage({
        "ts": time.time(), "provider": provider, "agent": agent, "task": task_id,
        "ok": res.ok, "limited": res.limited, "tokens": res.tokens,
        "seconds": round(res.seconds, 1), "cost_usd": res.cost_usd,
    })
    if res.limited:
        until = set_cooldown(cfg, provider)
        log(f"  {provider} hit a usage limit; skipping it until {fmt_time(until)}")
    return res


def call_with_fallback(cfg: dict, first: str, prompt: str, workdir: Path, **kw) -> Result:
    res = call(cfg, first, prompt, workdir, **kw)
    if res.ok or not cfg["split"].get("fallback_on_error", True):
        return res
    others = [p for p in available_providers(cfg) if p != first]
    if not others:
        return res
    log(f"  {first} failed ({short(res.error)}); retrying on {others[0]}")
    return call(cfg, others[0], prompt, workdir, **kw)


# --------------------------------------------------------------------------- planning

def plan_schema(agent_names: list[str]) -> dict:
    # Strict-mode compatible (Codex --output-schema): every property required,
    # no additional properties.
    task = {
        "type": "object",
        "additionalProperties": False,
        "required": [*REQUIRED_TASK_FIELDS, "depends_on"],
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "agent": {"type": "string", "enum": agent_names},
            "instructions": {"type": "string"},
            "acceptance": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "tasks"],
        "properties": {"summary": {"type": "string"}, "tasks": {"type": "array", "items": task}},
    }


def roster(cfg: dict) -> str:
    return "\n".join(f"- {n}: {a['role']}" for n, a in cfg["agents"].items())


def planner_prompt(cfg: dict, goal: str, workdir: Path) -> str:
    n = cfg.get("planner", {}).get("max_tasks", 8)
    return f"""You are the planner for a crew of AI coding agents working in {workdir}.

Goal:
{goal}

Crew:
{roster(cfg)}

Look through the repository as much as you need, but do not change any files.
Break the goal into at most {n} concrete tasks and assign each one to the crew
member whose role fits it best. Each task runs in a separate non-interactive
session that will not see this message, so its instructions must stand on their
own: name the files, commands and expected outputs. Use ids t1, t2, ... and list
prerequisite ids in depends_on. Keep tasks that edit the same files sequential.
Return only the JSON object."""


def validate_plan(cfg: dict, plan: dict, max_tasks: int | None = None) -> list[dict]:
    """Check a plan and return its tasks in dependency order. Raises ValueError."""
    if not isinstance(plan, dict):
        raise ValueError("plan is not a JSON object")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("plan has no tasks")
    if max_tasks and len(tasks) > max_tasks:
        raise ValueError(f"plan has {len(tasks)} tasks; planner.max_tasks is {max_tasks}")
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict):
            raise ValueError(f"task #{i} is not an object")
        name = t.get("id") if isinstance(t.get("id"), str) else f"#{i}"
        for k in REQUIRED_TASK_FIELDS:
            if not isinstance(t.get(k), str) or not t[k].strip():
                raise ValueError(f"task {name}: missing or empty {k!r}")
        deps = t.setdefault("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError(f"task {name}: depends_on must be a list of task ids")
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("task ids must be unique")
    for t in tasks:
        if t["agent"] not in cfg["agents"]:
            raise ValueError(f"task {t['id']}: unknown agent {t['agent']!r}")
        for d in t["depends_on"]:
            if d not in ids:
                raise ValueError(f"task {t['id']}: depends on unknown task {d!r}")
    return topo_order(tasks)


def topo_order(tasks: list[dict]) -> list[dict]:
    by_id = {t["id"]: t for t in tasks}
    order, state = [], {}

    def visit(tid: str, path: tuple) -> None:
        if state.get(tid) == "done":
            return
        if state.get(tid) == "active":
            raise ValueError("dependency cycle: " + " -> ".join((*path, tid)))
        state[tid] = "active"
        for d in by_id[tid]["depends_on"]:
            visit(d, (*path, tid))
        state[tid] = "done"
        order.append(by_id[tid])

    for t in tasks:
        visit(t["id"], ())
    return order


def check_workdir(workdir: Path) -> None:
    if not workdir.is_dir():
        raise ValueError(f"workdir not found: {workdir}")


def make_plan(cfg: dict, goal: str, workdir: Path) -> dict:
    check_workdir(workdir)
    avail = available_providers(cfg)
    if not avail:
        raise ValueError("no provider available: run `python crew.py doctor`")
    first = cfg.get("planner", {}).get("provider", "claude")
    order = [first] + [p for p in avail if p != first] if first in avail else avail
    if not cfg["split"].get("fallback_on_error", True):
        order = order[:1]
    max_tasks = cfg.get("planner", {}).get("max_tasks", 8)
    prompt = planner_prompt(cfg, goal, workdir)
    errors = []
    for i, p in enumerate(order):
        if i and p not in available_providers(cfg):
            continue
        log(f"planning with {p} ...")
        res = call(cfg, p, prompt, workdir, agent="planner", task_id="plan",
                   schema=plan_schema(list(cfg["agents"])), read_only=True)
        if not res.ok:
            errors.append(f"{p}: {short(res.error, 300)}")
            continue
        try:
            validate_plan(cfg, res.structured, max_tasks)
        except ValueError as e:
            errors.append(f"{p}: invalid plan: {e}")
            continue
        plan = res.structured
        for t in plan["tasks"]:
            t["status"] = "pending"
        return {"goal": goal, "workdir": str(workdir), "planned_by": p,
                "summary": plan.get("summary", ""), "tasks": plan["tasks"]}
    raise ValueError("planning failed\n  " + "\n  ".join(errors))


# --------------------------------------------------------------------------- running

def worker_prompt(cfg: dict, plan: dict, task: dict, done: dict[str, dict]) -> str:
    agent = task["agent"]
    names = ", ".join(cfg["agents"])
    ctx = "\n".join(
        f"- {d} ({done[d]['agent']}, {done[d]['title']}): {short(done[d].get('result', ''), 600)}"
        for d in task["depends_on"] if d in done
    ) or "- none"
    return f"""You are {agent}, one of the agents on a crew ({names}).
Your role: {cfg['agents'][agent]['role']}

Overall goal: {plan['goal']}

Your task ({task['id']}): {task['title']}

Instructions:
{task['instructions']}

Done when:
{task['acceptance']}

Results of the tasks this one depends on:
{ctx}

Work directly in the repository at {plan['workdir']}. Do not commit or push
unless the instructions say to. Finish with a short summary of what you changed,
how you checked it, and anything left undone."""


def run_plan(cfg: dict, plan_path: Path, *, dry_run: bool = False, retry_failed: bool = False) -> int:
    plan = json.loads(plan_path.read_text())
    if not isinstance(plan, dict) or not plan.get("goal") or not plan.get("workdir"):
        raise ValueError(f"{plan_path}: not a crew plan (needs goal, workdir, tasks)")
    order = validate_plan(cfg, plan)
    workdir = Path(plan["workdir"])
    check_workdir(workdir)
    by_id = {t["id"]: t for t in plan["tasks"]}
    if retry_failed:
        for t in plan["tasks"]:
            if t.get("status") in ("failed", "skipped"):
                t["status"] = "pending"
    totals, runs = usage_totals(cfg)
    estimate = per_call_estimate(cfg) if dry_run else {}
    wont_run: set[str] = set()  # dry run: tasks a real run would not complete
    failed = 0
    log(f"workdir: {workdir}")

    for task in order:
        tid, status = task["id"], task.get("status", "pending")
        if status == "done":
            continue
        if status == "failed":
            log(f"[{tid}] {task['agent']}: failed earlier; use --retry-failed to run it again")
            wont_run.add(tid)
            failed += 1
            continue
        if dry_run:
            blocked = [d for d in task["depends_on"] if d in wont_run]
        else:
            blocked = [d for d in task["depends_on"] if by_id[d].get("status") != "done"]
        if blocked:
            log(f"[{tid}] {task['agent']}: skipped ({', '.join(blocked)} not done)")
            wont_run.add(tid)
            failed += 1
            if not dry_run:
                task["status"] = "skipped"
                task["result"] = f"skipped: prerequisite(s) {', '.join(blocked)} not done"
                save_plan(plan_path, plan)
            continue

        provider = choose_provider(cfg, task["agent"], totals, available_providers(cfg), runs)
        if provider is None:
            log(f"[{tid}] no provider available (all disabled, missing, logged out, or cooling down)")
            failed += 1
            break
        log(f"[{tid}] {task['agent']} -> {provider}: {task['title']}")
        if dry_run:
            totals[provider] += estimate[provider]
            runs += 1
            continue

        done = {t["id"]: t for t in plan["tasks"] if t.get("status") == "done"}
        res = call_with_fallback(cfg, provider, worker_prompt(cfg, plan, task, done), workdir,
                                 agent=task["agent"], task_id=tid)
        task["provider"] = res.provider
        task["status"] = "done" if res.ok else "failed"
        task["result"] = (res.text if res.ok else res.error)[-RESULT_KEEP:]
        if res.denied:
            task["denied"] = res.denied
        save_plan(plan_path, plan)
        totals, runs = usage_totals(cfg)  # ledger includes failed first attempts
        if res.ok:
            log(f"  done on {res.provider} in {res.seconds:.0f}s")
        else:
            failed += 1
            log(f"  failed on {res.provider}: {short(res.error)}")
        if res.denied:
            log(f"  note: {res.provider} was refused permission for: {', '.join(res.denied[:5])}")

    if dry_run:
        log("dry run: nothing was executed")
    else:
        log(f"plan saved to {plan_path}")
    print_split(cfg)
    return 1 if failed else 0


def save_plan(path: Path, plan: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(plan, indent=2))
    tmp.replace(path)


# --------------------------------------------------------------------------- reports

def log(msg: str) -> None:
    print(msg, flush=True)


def short(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def print_split(cfg: dict) -> None:
    split = cfg["split"]
    metric = split.get("metric", "tasks")
    window = split.get("window_hours", 168)
    entries = read_usage(window)
    totals, _ = usage_totals(cfg)
    total = sum(totals.values())
    raw = split.get("target_share") or {}
    wsum = sum(float(raw.get(p, 1.0)) for p in PROVIDERS) or 1.0
    log(f"\nusage over the last {window}h (metric: {metric})")
    log(f"  {'provider':<8} {'runs':>5} {'failed':>6} {'tokens':>10} {'secs':>7} {'share':>6} {'target':>6}")
    for p in PROVIDERS:
        rows = [e for e in entries if e.get("provider") == p]
        share = totals[p] / total if total else 0.0
        target = float(raw.get(p, 1.0)) / wsum
        log(f"  {p:<8} {len(rows):>5} {sum(not e.get('ok') for e in rows):>6} "
            f"{sum(e.get('tokens') or 0 for e in rows):>10} "
            f"{sum(e.get('seconds') or 0 for e in rows):>7.0f} {share:>6.0%} {target:>6.0%}")
        cd = cooldown_until(p)
        if cd > time.time():
            log(f"           cooling down until {fmt_time(cd)}")


def doctor(cfg: dict) -> int:
    problems = 0
    for p in PROVIDERS:
        pc = provider_cfg(cfg, p)
        if not pc.get("enabled", True):
            log(f"{p}: disabled in config")
            continue
        if not installed(cfg, p):
            hint = "npm install -g @anthropic-ai/claude-code" if p == "claude" else "npm install -g @openai/codex"
            log(f"{p}: NOT INSTALLED ({pc['cmd']} not found) -> {hint}")
            problems += 1
            continue
        try:
            ver = subprocess.run([pc["cmd"], "--version"], capture_output=True, text=True,
                                 timeout=60).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            ver = f"version check failed: {e}"
        ok, detail = login_status(cfg, p)
        log(f"{p}: {ver} at {pc['cmd']}; {'logged in' if ok else 'NOT LOGGED IN'} ({detail})")
        if not ok:
            problems += 1
            log("   fix: " + ("claude auth login, or set ANTHROPIC_API_KEY" if p == "claude"
                              else "codex login (ChatGPT plan), or set OPENAI_API_KEY and run "
                                   "`python crew.py codex-login`, or set CODEX_API_KEY"))
    if sys.platform.startswith("linux") and provider_cfg(cfg, "claude").get("enabled", True):
        missing = [t for t in ("bwrap", "socat") if not shutil.which(t)]
        if missing:
            log(f"claude sandbox: missing {', '.join(missing)}, so Claude workers can't run shell "
                "commands (they are refused, not run unsandboxed) -> apt install bubblewrap socat")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
        log(f"env {var}: {'set' if os.environ.get(var) else 'not set'}")
    log(f"ledger: {_ledger()}")
    return 1 if problems else 0


def codex_login(cfg: dict) -> int:
    key = os.environ.get("OPENAI_API_KEY")
    if not installed(cfg, "codex"):
        sys.exit("codex is not installed: npm install -g @openai/codex")
    if not key:
        sys.exit("OPENAI_API_KEY is not set. For a ChatGPT plan, run `codex login` instead.")
    r = subprocess.run([provider_cfg(cfg, "codex")["cmd"], "login", "--with-api-key"],
                       input=key, text=True, capture_output=True)
    log(short((r.stdout or r.stderr).strip(), 300) or f"exit {r.returncode}")
    return r.returncode


# --------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check CLIs and logins")
    sub.add_parser("codex-login", help="log Codex in with OPENAI_API_KEY")
    u = sub.add_parser("usage", help="show the usage split")
    u.add_argument("--clear-cooldowns", action="store_true")

    for name in ("plan", "go"):
        s = sub.add_parser(name, help="plan a goal" if name == "plan" else "plan and run a goal")
        s.add_argument("goal")
        s.add_argument("--workdir", type=Path, default=Path.cwd())
        s.add_argument("--out", type=Path, default=Path("crew-plan.json"))
        if name == "go":
            s.add_argument("--dry-run", action="store_true", help="plan, then only show routing")
    r = sub.add_parser("run", help="run (or resume) a plan file")
    r.add_argument("plan", type=Path)
    r.add_argument("--dry-run", action="store_true", help="show routing without running")
    r.add_argument("--retry-failed", action="store_true", help="also re-run failed and skipped tasks")

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.cmd == "doctor":
            return doctor(cfg)
        if args.cmd == "codex-login":
            return codex_login(cfg)
        if args.cmd == "usage":
            if args.clear_cooldowns:
                state = load_state()
                state["cooldown_until"] = {}
                save_state(state)
            print_split(cfg)
            return 0
        if args.cmd in ("plan", "go"):
            args.out.parent.mkdir(parents=True, exist_ok=True)
            plan = make_plan(cfg, args.goal, args.workdir.resolve())
            save_plan(args.out, plan)
            log(f"\n{plan['summary']}\n")
            for t in plan["tasks"]:
                dep = f" (after {', '.join(t['depends_on'])})" if t["depends_on"] else ""
                log(f"  {t['id']} {t['agent']:<6} {t['title']}{dep}")
            log(f"\nplan written to {args.out}")
            if args.cmd == "plan":
                return 0
            return run_plan(cfg, args.out, dry_run=args.dry_run)
        if args.cmd == "run":
            return run_plan(cfg, args.plan, dry_run=args.dry_run, retry_failed=args.retry_failed)
    except (ValueError, FileNotFoundError) as e:
        log(f"error: {e}")
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
