#!/usr/bin/env python3
"""crew: plan work, hand it to Astra, Sol and Luna, and split usage between
Claude Code and Codex.

Standard library only. It shells out to the `claude` and `codex` CLIs, so each
one runs on whatever login it already has: a Claude or ChatGPT plan, or an
ANTHROPIC_API_KEY / OPENAI_API_KEY. Keys are never read or printed here except
by `crew.py codex-login`, which pipes OPENAI_API_KEY straight into Codex.

  python crew.py doctor                 check both CLIs and their logins
  python crew.py plan "goal"            write a task plan (read-only run)
  python crew.py run crew-plan.json     execute the plan, resumable
  python crew.py go "goal"              plan + run
  python crew.py usage                  usage split and cooldowns
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
LIMIT_RE = re.compile(
    r"usage[ _-]?limit|rate[ _-]?limit|limit reached|hit your \w* ?limit|quota|"
    r"\b429\b|overloaded|too many requests",
    re.I,
)
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
        cfg.setdefault("providers", {}).setdefault(p, {}).setdefault("cmd", p)
    return cfg


def provider_cfg(cfg: dict, p: str) -> dict:
    return cfg["providers"][p]


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
        if e.get("ts", 0) >= cutoff:
            out.append(e)
    return out


def metric_value(entry: dict, metric: str) -> float:
    if metric == "tokens":
        return float(entry.get("tokens") or 0)
    if metric == "seconds":
        return float(entry.get("seconds") or 0)
    return 1.0


def usage_totals(cfg: dict) -> dict[str, float]:
    split = cfg["split"]
    totals = {p: 0.0 for p in PROVIDERS}
    for e in read_usage(split.get("window_hours", 168)):
        if e.get("provider") in totals:
            totals[e["provider"]] += metric_value(e, split.get("metric", "tasks"))
    return totals


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


_AUTH_CACHE: dict[str, bool] = {}


def logged_in(cfg: dict, p: str) -> bool:
    """True when the CLI reports a login (plan or API key). Cached per process."""
    if p == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if p not in _AUTH_CACHE:
        path = shutil.which(provider_cfg(cfg, p)["cmd"])
        sub = ["auth", "status"] if p == "claude" else ["login", "status"]
        try:
            r = subprocess.run([path, *sub], capture_output=True, text=True, timeout=60)
            _AUTH_CACHE[p] = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired, TypeError):
            _AUTH_CACHE[p] = False
    return _AUTH_CACHE[p]


def available_providers(cfg: dict) -> list[str]:
    now = time.time()
    return [
        p for p in PROVIDERS
        if provider_cfg(cfg, p).get("enabled", True)
        and installed(cfg, p)
        and cooldown_until(p) <= now
        and logged_in(cfg, p)
    ]


def choose_provider(cfg: dict, agent: str, totals: dict, avail: list[str]) -> str | None:
    """Pick the provider for one task.

    by_role:  the agent's preferred provider, unless that provider's share of
              usage is over its target by more than `tolerance`.
    balanced: whichever provider is furthest under its target share.
    fixed:    always the agent's preferred provider when it is available.
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
    if over[pref] > float(split.get("tolerance", 0.15)):
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
    extra: dict = field(default_factory=dict)


def is_git_repo(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _exec(cmd: list[str], prompt: str, cwd: Path, timeout: float) -> tuple[int, str, str, float]:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           cwd=str(cwd), timeout=timeout)
        return r.returncode, r.stdout, r.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out, f"timed out after {timeout:.0f}s", time.time() - t0
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}", time.time() - t0


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


def run_claude(cfg: dict, prompt: str, workdir: Path, *, schema: dict | None = None,
               read_only: bool = False) -> Result:
    pc = provider_cfg(cfg, "claude")
    cmd = [pc["cmd"], "-p", "--output-format", "json"]
    if pc.get("model"):
        cmd += ["--model", pc["model"]]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if read_only:
        cmd += ["--disallowedTools", "Edit,Write,NotebookEdit"]
    else:
        cmd += list(pc.get("args") or [])
    rc, out, err, secs = _exec(cmd, prompt, workdir, pc.get("timeout_sec", 1800))
    data = _last_json_object(out) or {}
    text = data.get("result") or ""
    ok = rc == 0 and bool(data) and not data.get("is_error")
    u = data.get("usage") or {}
    tokens = int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                 + (u.get("output_tokens") or 0))
    res = Result("claude", ok, text=text, structured=data.get("structured_output"),
                 tokens=tokens, cost_usd=data.get("total_cost_usd"), seconds=secs)
    if not ok:
        res.error = (text or err or out or f"exit {rc}").strip()[-2000:]
        res.limited = data.get("api_error_status") == 429 or bool(LIMIT_RE.search(res.error))
    if schema is not None and ok and res.structured is None:
        try:
            res.structured = json.loads(text)
        except json.JSONDecodeError:
            res.ok, res.error = False, "planner did not return JSON"
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
        rc, out, err, secs = _exec(cmd, prompt, workdir, pc.get("timeout_sec", 1800))
        text = last.read_text().strip() if last.exists() else ""

    tokens, errors, agent_msgs = 0, [], []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "turn.completed":
            u = ev.get("usage") or {}
            fresh = (u.get("input_tokens") or 0) - (u.get("cached_input_tokens") or 0)
            tokens += int(max(fresh, 0) + (u.get("output_tokens") or 0))
        elif kind in ("turn.failed", "error"):
            e = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            errors.append(e.get("message") or ev.get("message") or json.dumps(ev))
        elif kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                agent_msgs.append(item["text"])
    if not text and agent_msgs:
        text = agent_msgs[-1]
    ok = rc == 0 and not errors
    res = Result("codex", ok, text=text, tokens=tokens, seconds=secs)
    if not ok:
        res.error = ("\n".join(errors) or err or out or f"exit {rc}").strip()[-2000:]
        res.limited = bool(LIMIT_RE.search(res.error))
    if schema is not None and ok:
        try:
            res.structured = json.loads(text)
        except json.JSONDecodeError:
            res.ok, res.error = False, "planner did not return JSON"
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
    task = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "title", "agent", "instructions", "acceptance", "depends_on"],
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


def validate_plan(cfg: dict, plan: dict) -> list[dict]:
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("plan has no tasks")
    ids = [t.get("id") for t in tasks]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        raise ValueError("task ids must be unique, non-empty strings")
    for t in tasks:
        if t.get("agent") not in cfg["agents"]:
            raise ValueError(f"task {t['id']}: unknown agent {t.get('agent')!r}")
        for d in t.get("depends_on") or []:
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
        for d in by_id[tid].get("depends_on") or []:
            visit(d, (*path, tid))
        state[tid] = "done"
        order.append(by_id[tid])

    for t in tasks:
        visit(t["id"], ())
    return order


def make_plan(cfg: dict, goal: str, workdir: Path) -> dict:
    avail = available_providers(cfg)
    if not avail:
        sys.exit("no provider available: run `python crew.py doctor`")
    first = cfg.get("planner", {}).get("provider", "claude")
    if first not in avail:
        first = avail[0]
    log(f"planning with {first} ...")
    res = call_with_fallback(cfg, first, planner_prompt(cfg, goal, workdir), workdir,
                             agent="planner", task_id="plan",
                             schema=plan_schema(list(cfg["agents"])), read_only=True)
    if not res.ok or not isinstance(res.structured, dict):
        sys.exit(f"planning failed on {res.provider}: {res.error or 'no plan returned'}")
    plan = res.structured
    validate_plan(cfg, plan)
    for t in plan["tasks"]:
        t["status"] = "pending"
    return {"goal": goal, "workdir": str(workdir), "planned_by": res.provider,
            "summary": plan.get("summary", ""), "tasks": plan["tasks"]}


# --------------------------------------------------------------------------- running

def worker_prompt(cfg: dict, plan: dict, task: dict, done: dict[str, dict]) -> str:
    agent = task["agent"]
    names = ", ".join(cfg["agents"])
    ctx = "\n".join(
        f"- {d} ({done[d]['agent']}, {done[d].get('title', '')}): {short(done[d].get('result', ''), 600)}"
        for d in task.get("depends_on") or [] if d in done
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


def run_plan(cfg: dict, plan_path: Path, *, dry_run: bool = False) -> int:
    plan = json.loads(plan_path.read_text())
    order = validate_plan(cfg, plan)
    workdir = Path(plan["workdir"])
    totals = usage_totals(cfg)
    failed = 0

    for task in order:
        if task.get("status") == "done":
            continue
        deps = task.get("depends_on") or []
        by_id = {t["id"]: t for t in plan["tasks"]}
        blocked = [d for d in deps if by_id[d].get("status") != "done"]
        if blocked and not dry_run:
            task["status"] = "skipped"
            task["result"] = f"skipped: prerequisite(s) {', '.join(blocked)} not done"
            log(f"[{task['id']}] {task['agent']}: skipped ({', '.join(blocked)} not done)")
            save_plan(plan_path, plan)
            failed += 1
            continue

        avail = available_providers(cfg)
        provider = choose_provider(cfg, task["agent"], totals, avail)
        if provider is None:
            log(f"[{task['id']}] no provider available (all disabled, missing, or cooling down)")
            failed += 1
            break
        log(f"[{task['id']}] {task['agent']} -> {provider}: {task['title']}")
        if dry_run:
            totals[provider] += 1
            continue

        done = {t["id"]: t for t in plan["tasks"] if t.get("status") == "done"}
        res = call_with_fallback(cfg, provider, worker_prompt(cfg, plan, task, done), workdir,
                                 agent=task["agent"], task_id=task["id"])
        task["provider"] = res.provider
        task["status"] = "done" if res.ok else "failed"
        task["result"] = (res.text if res.ok else res.error)[-RESULT_KEEP:]
        totals[res.provider] += metric_value(
            {"tokens": res.tokens, "seconds": res.seconds}, cfg["split"].get("metric", "tasks"))
        save_plan(plan_path, plan)
        if res.ok:
            log(f"  done on {res.provider} in {res.seconds:.0f}s")
        else:
            failed += 1
            log(f"  failed on {res.provider}: {short(res.error)}")

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
    totals = usage_totals(cfg)
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
        path = shutil.which(pc["cmd"])
        if not pc.get("enabled", True):
            log(f"{p}: disabled in config")
            continue
        if not path:
            hint = "npm install -g @anthropic-ai/claude-code" if p == "claude" else "npm install -g @openai/codex"
            log(f"{p}: NOT INSTALLED ({pc['cmd']} not on PATH) -> {hint}")
            problems += 1
            continue
        ver = subprocess.run([path, "--version"], capture_output=True, text=True).stdout.strip()
        auth_cmd = [path, "auth", "status"] if p == "claude" else [path, "login", "status"]
        a = subprocess.run(auth_cmd, capture_output=True, text=True)
        status = "logged in" if a.returncode == 0 else "NOT LOGGED IN"
        detail = short((a.stdout or a.stderr).strip(), 100)
        log(f"{p}: {ver} at {path}; {status} ({detail})")
        if a.returncode != 0:
            problems += 1
            log("   fix: " + ("claude auth login, or set ANTHROPIC_API_KEY" if p == "claude"
                              else "codex login (ChatGPT plan), or set OPENAI_API_KEY and run "
                                   "`python crew.py codex-login`"))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
        log(f"env {var}: {'set' if os.environ.get(var) else 'not set'}")
    log(f"ledger: {_ledger()}")
    return 1 if problems else 0


def codex_login(cfg: dict) -> int:
    key = os.environ.get("OPENAI_API_KEY")
    path = shutil.which(provider_cfg(cfg, "codex")["cmd"])
    if not path:
        sys.exit("codex is not installed: npm install -g @openai/codex")
    if not key:
        sys.exit("OPENAI_API_KEY is not set. For a ChatGPT plan, run `codex login` instead.")
    r = subprocess.run([path, "login", "--with-api-key"], input=key, text=True,
                       capture_output=True)
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
    r.add_argument("--retry-failed", action="store_true", help="re-run failed and skipped tasks")

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

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
        workdir = args.workdir.resolve()
        plan = make_plan(cfg, args.goal, workdir)
        save_plan(args.out, plan)
        log(f"\n{plan['summary']}\n")
        for t in plan["tasks"]:
            dep = f" (after {', '.join(t['depends_on'])})" if t.get("depends_on") else ""
            log(f"  {t['id']} {t['agent']:<6} {t['title']}{dep}")
        log(f"\nplan written to {args.out}")
        if args.cmd == "plan":
            return 0
        return run_plan(cfg, args.out, dry_run=args.dry_run)
    if args.cmd == "run":
        if args.retry_failed and not args.dry_run:
            plan = json.loads(args.plan.read_text())
            for t in plan["tasks"]:
                if t.get("status") in ("failed", "skipped"):
                    t["status"] = "pending"
            save_plan(args.plan, plan)
        return run_plan(cfg, args.plan, dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
