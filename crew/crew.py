#!/usr/bin/env python3
"""crew: plan work for Astra, Sol and Luna in any of your projects, and split the
usage between Claude Code and Codex.

Standard library only. It drives the `claude` and `codex` CLIs, so each one runs
on the login it already has (a Claude or ChatGPT plan, or an API key). Keys are
never printed or stored; `codex-login` pipes OPENAI_API_KEY into Codex on stdin.

  crew install                  put `crew` on your PATH (in ~/.crew/bin)
  crew init                     detect this project's stack, setup and checks
  crew go "goal"                plan and run, on a crew/... branch of this project
  crew plan "goal"; crew run    the same in two steps; `crew run` resumes the latest plan
  crew queue add "goal" --workdir A --workdir B; crew queue run
                                work through several projects, Claude and Codex in parallel
  crew usage                    usage split, overall and per project
  crew projects | plans | config | doctor
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "2.0"
HERE = Path(__file__).resolve().parent
CREW_HOME = Path(os.environ.get("CREW_HOME") or Path.home() / ".crew")
PROVIDERS = ("claude", "codex")

DEFAULTS: dict = {
    "planner": {"provider": "claude", "max_tasks": 8, "model": {}},
    "split": {
        "mode": "by_role",
        "metric": "tasks",
        "target_share": {"claude": 0.5, "codex": 0.5},
        "tolerance": 0.15,
        "min_runs": 6,
        "window_hours": 168,
        "fallback_on_error": True,
        "cooldown_minutes": 60,
    },
    "providers": {
        "claude": {
            "enabled": True,
            "cmd": "claude",
            "model": None,
            "args": ["--permission-mode", "acceptEdits",
                     "--settings", '{"sandbox": {"enabled": true, "autoAllowBashIfSandboxed": true, '
                                   '"allowUnsandboxedCommands": false}}'],
            "sandboxed_bash": True,
            "timeout_sec": 1800,
            "max_parallel": 1,
        },
        "codex": {
            "enabled": True,
            "cmd": "codex",
            "model": None,
            "args": ["--sandbox", "workspace-write"],
            "timeout_sec": 1800,
            "max_parallel": 1,
        },
    },
    "agents": {
        "Astra": {"role": "Architect. Designs the approach, makes multi-file or cross-cutting changes, "
                          "untangles hard bugs.", "prefers": "claude"},
        "Sol": {"role": "Builder. Implements well-scoped features, scripts, and data pipelines from clear "
                        "instructions.", "prefers": "codex"},
        "Luna": {"role": "Verifier. Writes and runs tests, reviews the other agents' changes, fixes small "
                         "issues, updates docs.", "prefers": "any"},
    },
    "verify": {"enabled": True, "fixer": "Luna", "fix_rounds": 1, "timeout_sec": 900, "unsandboxed": False},
    "setup": {"enabled": True, "timeout_sec": 1800},
    "git": {"enabled": True, "branch_prefix": "crew/", "return_to_start": True},
}
REPLACE_KEYS = {"agents"}  # a config layer that sets these replaces them instead of merging

# Applied only to the provider's own error message, never to raw output.
LIMIT_RE = re.compile(
    r"usage[ _-]?limit|rate[ _-]?limit|limit reached|hit your \w* ?limit|quota|"
    r"(?:status|code|error)\W{0,3}429|429 too many requests|too many requests",
    re.I,
)
# Env vars that count as a login even when the CLI's own status check says no.
ENV_LOGIN = {"claude": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"), "codex": ("CODEX_API_KEY",)}
# Each CLI only gets its own provider's credentials.
FOREIGN_KEYS = {
    "claude": ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"),
    "codex": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
}
REQUIRED_TASK_FIELDS = ("id", "title", "agent", "instructions", "acceptance")
UNFINISHED = ("failed", "partial", "blocked")  # left alone by a plain re-run
WORKER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
        "summary": {"type": "string"},
    },
}
RESULT_KEEP = 4000  # chars of each agent's final message kept in the plan file
MANIFESTS = ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
             "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg",
             "Pipfile", "Pipfile.lock", "uv.lock", "poetry.lock", "Cargo.toml", "Cargo.lock",
             "go.mod", "go.sum")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "dist", "build", "__pycache__", ".next",
             "target", ".svelte-kit", ".turbo", "coverage"}

_TL = threading.local()
LOG_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
STOPPING = threading.Event()
ACTIVE: set = set()
ACTIVE_LOCK = threading.Lock()
SLOTS: dict[str, threading.Semaphore] = {}
SLOT_LIMIT: dict[str, int] = {}
BUSY: Counter = Counter()
SLOT_LOCK = threading.Lock()


class Interrupted(KeyboardInterrupt):
    """crew is stopping (Ctrl-C, SIGTERM): the current task stays pending."""


# --------------------------------------------------------------------------- small helpers

def log(msg: str) -> None:
    prefix = getattr(_TL, "prefix", "")
    text = "\n".join(prefix + line if line else line for line in msg.split("\n")) if prefix else msg
    with LOG_LOCK:
        print(text, flush=True)


def short(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def tail(s: str, n: int = 1500) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else "..." + s[-(n - 3):]


def slugify(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-") or "work"


def fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON ({e})") from None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# --------------------------------------------------------------------------- config

def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in REPLACE_KEYS:
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def global_config_path() -> Path:
    return CREW_HOME / "config.json"


def load_config(explicit: Path | None, overrides: dict | None = None) -> dict:
    """Built-in defaults <- ~/.crew/config.json (or --config) <- project overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    source = Path(explicit) if explicit else global_config_path()
    if explicit or source.exists():
        layer = _read_json(source)
        if not isinstance(layer, dict):
            raise ValueError(f"config {source}: not a JSON object")
        cfg = deep_merge(cfg, layer)
    if overrides:
        if not isinstance(overrides, dict):
            raise ValueError("project overrides must be a JSON object")
        cfg = deep_merge(cfg, overrides)
    cfg["_path"] = str(source.resolve()) if explicit or source.exists() else ""
    agents = cfg.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise ValueError("config: no agents defined")
    for name, a in agents.items():
        if not isinstance(a, dict) or not isinstance(a.get("role"), str) or not a["role"].strip():
            raise ValueError(f"config: agent {name}: needs a role")
        if a.get("prefers", "any") not in (*PROVIDERS, "any"):
            raise ValueError(f"config: agent {name}: prefers must be claude, codex or any")
        if not isinstance(a.get("model") or {}, dict):
            raise ValueError(f"config: agent {name}: model must be an object like {{\"claude\": \"sonnet\"}}")
    split = cfg["split"]
    if split.get("mode") not in ("by_role", "balanced", "fixed"):
        raise ValueError("config: split.mode must be by_role, balanced or fixed")
    if split.get("metric") not in ("tasks", "tokens", "seconds"):
        raise ValueError("config: split.metric must be tasks, tokens or seconds")
    if cfg["verify"].get("fixer") not in agents:
        cfg["verify"]["fixer"] = next(iter(agents))
    for p in PROVIDERS:
        pc = cfg["providers"].setdefault(p, {})
        cmd = os.path.expanduser(pc.get("cmd") or p)
        found = shutil.which(cmd)
        pc["cmd"] = os.path.abspath(found) if found else cmd  # cwd is the workdir at exec time
    return cfg


def provider_cfg(cfg: dict, p: str) -> dict:
    return cfg["providers"][p]


def provider_env(p: str) -> dict:
    env = dict(os.environ)
    for k in FOREIGN_KEYS[p]:
        env.pop(k, None)
    return env


def model_for(cfg: dict, p: str, agent: str | None) -> str | None:
    if agent == "planner":
        m = (cfg.get("planner", {}).get("model") or {}).get(p)
    else:
        m = ((cfg["agents"].get(agent or "", {}).get("model")) or {}).get(p)
    return m or provider_cfg(cfg, p).get("model")


# --------------------------------------------------------------------------- git

def git(root: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def is_git_repo(path: Path) -> bool:
    r = git(path, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def git_root(path: Path) -> Path | None:
    r = git(path, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def resolve_workdir(arg: Path | None) -> Path:
    p = Path(arg or Path.cwd()).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"workdir not found: {p}")
    return git_root(p) or p


def tracked_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip())


def untracked(root: Path) -> set[str]:
    out = git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return {f for f in out.split("\0") if f}


def current_branch(root: Path) -> str:
    return git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()


def head_sha(root: Path) -> str:
    r = git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def git_identity_args(root: Path) -> list[str]:
    if git(root, "config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=crew", "-c", "user.email=crew@localhost"]


def git_commit(root: Path, message: str, new_files: list[str]) -> str:
    """Stage tracked changes plus the given new files and commit without running hooks."""
    git(root, "add", "-u")
    for i in range(0, len(new_files), 200):
        git(root, "add", "--", *new_files[i:i + 200])
    if git(root, "diff", "--cached", "--quiet").returncode == 0:
        return ""
    r = git(root, "-c", "core.hooksPath=/dev/null", *git_identity_args(root),
            "commit", "--no-verify", "-q", "-m", message)
    if r.returncode != 0:
        log(f"  warning: git commit failed: {short(r.stderr or r.stdout, 200)}")
        return ""
    return head_sha(root)


def exclude_locally(root: Path, pattern: str) -> None:
    """Add a pattern to .git/info/exclude (never committed) unless git already ignores it."""
    name = pattern.rstrip("/")
    if git(root, "check-ignore", "-q", name).returncode == 0:
        return
    gd = git(root, "rev-parse", "--git-dir").stdout.strip()
    if not gd:
        return
    excl = (root / gd if not Path(gd).is_absolute() else Path(gd)) / "info" / "exclude"
    excl.parent.mkdir(parents=True, exist_ok=True)
    existing = excl.read_text() if excl.exists() else ""
    if pattern not in existing.split("\n"):
        excl.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + pattern + "\n")


# --------------------------------------------------------------------------- projects

def project_identity(root: Path) -> tuple[str, str]:
    """(key for file names, display name). Uses the origin remote's owner/repo when there is one."""
    url = git(root, "config", "--get", "remote.origin.url").stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        name = f"{m.group(1)}/{m.group(2)}"
        key = f"{m.group(1)}__{m.group(2)}"
    else:
        name = root.name
        key = f"{root.name}-{hashlib.sha1(str(root).encode()).hexdigest()[:6]}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key), name


def profile_path(key: str) -> Path:
    return CREW_HOME / "projects" / f"{key}.json"


def load_profile(key: str) -> dict | None:
    p = profile_path(key)
    if not p.exists():
        return None
    prof = _read_json(p)
    if not isinstance(prof, dict) or not isinstance(prof.get("project"), dict):
        raise ValueError(f"{p}: not a crew project profile")
    proj = prof["project"]
    if not isinstance(proj.get("checks", {}), dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in proj.get("checks", {}).items()):
        raise ValueError(f"{p}: project.checks must map names to shell commands")
    if not isinstance(proj.get("setup", []), list) or not all(
            isinstance(s, dict) and isinstance(s.get("run"), str) for s in proj.get("setup", [])):
        raise ValueError(f"{p}: project.setup must be a list of {{\"run\": ...}} steps")
    return prof


def _find(root: Path, pattern: str, depth: int = 3, limit: int = 50) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        level = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")] if level < depth else []
        for f in filenames:
            if fnmatch.fnmatch(f, pattern):
                found.append(Path(dirpath, f))
                if len(found) >= limit:
                    return found
    return found


def _text(path: Path) -> str:
    try:
        return path.read_text(errors="replace") if path.is_file() else ""
    except OSError:
        return ""


def detect_project(root: Path, name: str) -> dict:
    """Work out the stack, how to install dependencies, and which checks to run."""
    stack, setup, checks, notes = [], [], {}, []
    has = lambda *names: any((root / n).exists() for n in names)  # noqa: E731

    # Node / TypeScript / Svelte
    pkg = None
    if (root / "package.json").is_file():
        try:
            pkg = json.loads(_text(root / "package.json"))
        except json.JSONDecodeError:
            notes.append("package.json is not valid JSON.")
    if isinstance(pkg, dict):
        pm = ("bun" if has("bun.lockb", "bun.lock") else "pnpm" if has("pnpm-lock.yaml")
              else "yarn" if has("yarn.lock") else "npm")
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        stack.append(f"node/{pm}")
        stack += [label for dep, label in (("next", "next"), ("vite", "vite"), ("svelte", "svelte"),
                                           ("react", "react"), ("vue", "vue"), ("astro", "astro"),
                                           ("typescript", "typescript")) if dep in deps]
        install = {"npm": "npm ci" if has("package-lock.json") else "npm install",
                   "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install", "bun": "bun install"}[pm]
        setup.append({"run": install, "unless_exists": "node_modules"})
        scripts = pkg.get("scripts") or {}
        run = (lambda s: f"{pm} run {s}")
        if scripts.get("test") and "no test specified" not in scripts["test"]:
            checks["test"] = run("test")
        for s in ("typecheck", "type-check", "check-types", "check", "tsc"):
            if s in scripts:
                checks["typecheck"] = run(s)
                break
        else:
            if "typescript" in deps and has("tsconfig.json"):
                exe = {"npm": "npx", "pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx"}[pm]
                proj = " -p tsconfig.app.json" if has("tsconfig.app.json") else ""
                checks["typecheck"] = f"{exe} tsc --noEmit{proj}"
        if "lint" in scripts:
            checks["lint"] = run("lint")
        if "build" in scripts:
            checks["build"] = run("build")
        if pkg.get("workspaces") or has("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json"):
            notes.append("Monorepo: name the package directory in every task and keep changes inside it.")

    # Python
    py_markers = [n for n in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile")
                  if (root / n).exists()]
    if py_markers or _find(root, "*.py", depth=2, limit=1):
        stack.append("python")
        meta = "\n".join(_text(root / n) for n in
                         ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "Pipfile"))
        if has("uv.lock"):
            setup.append({"run": "uv sync", "unless_exists": ".venv"})
            py = ".venv/bin/python"
        elif has("poetry.lock"):
            setup.append({"run": "poetry install", "unless_exists": ".venv"})
            py = "poetry run python"
        elif "requirements.txt" in py_markers:
            setup.append({"run": "python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt",
                          "unless_exists": ".venv"})
            py = ".venv/bin/python"
        elif "pyproject.toml" in py_markers or "setup.py" in py_markers:
            setup.append({"run": "python3 -m venv .venv && .venv/bin/python -m pip install -q -e .",
                          "unless_exists": ".venv"})
            py = ".venv/bin/python"
        else:
            py = "python3"
        uses_pytest = ("pytest" in meta or has("pytest.ini", "conftest.py", "tox.ini"))
        has_tests = (has("tests", "test") or bool(_find(root, "test_*.py", limit=1))
                     or bool(_find(root, "*_test.py", limit=1)))
        if uses_pytest or has_tests:
            checks.setdefault("test", f"{py} -m pytest -q" if uses_pytest else f"{py} -m unittest discover -q")
        if "[tool.ruff" in meta or has("ruff.toml", ".ruff.toml"):
            checks.setdefault("lint", f"{py} -m ruff check .")
        checks["python-syntax"] = (f"{py} -m compileall -q -x "
                                   "'(^|/)(\\.venv|venv|env|node_modules|\\.git|build|dist)(/|$)' .")

    # Notebooks
    if _find(root, "*.ipynb", limit=1):
        stack.append("notebooks")
        checks["notebooks-valid"] = (
            "python3 -c \"import json, pathlib; [json.loads(p.read_text(encoding='utf-8')) "
            "for p in pathlib.Path('.').rglob('*.ipynb') "
            "if not {'.venv', 'node_modules', '.ipynb_checkpoints'} & set(p.parts)]\"")
        notes.append("Notebooks (.ipynb) are JSON: change them with nbformat or a small Python script, "
                     "never by editing the JSON text by hand, and keep existing outputs unless asked.")

    # Rust, Go, Make
    if has("Cargo.toml"):
        stack.append("rust")
        setup.append({"run": "cargo fetch"})
        checks.setdefault("build", "cargo build -q")
        checks.setdefault("test", "cargo test -q")
    if has("go.mod"):
        stack.append("go")
        setup.append({"run": "go mod download"})
        checks.setdefault("test", "go test ./...")
        checks.setdefault("lint", "go vet ./...")
    make = _text(root / "Makefile")
    for target in ("test", "lint"):
        if target not in checks and re.search(rf"^{target}:", make, re.M):
            checks[target] = f"make {target}"

    # Static sites
    if not isinstance(pkg, dict) and (has("index.html") or _find(root, "*.html", depth=1, limit=1)):
        stack.append("static-html")
        notes.append("Static HTML/CSS/JS with no build step: keep relative asset paths working and "
                     "check pages still load.")

    docs = [d for d in ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md", ".cursorrules",
                        "CONTRIBUTING.md") if (root / d).exists()]
    files = len([f for f in git(root, "ls-files", "-z").stdout.split("\0") if f])
    if files > 3000:
        notes.append(f"Large repo ({files} tracked files): every task must name the paths it touches.")
    return {"name": name, "root": str(root), "stack": stack, "setup": setup, "checks": checks,
            "notes": notes, "agent_docs": docs, "tracked_files": files,
            "detected_at": time.strftime("%Y-%m-%d")}


def ensure_profile(root: Path, *, force: bool = False, quiet: bool = False) -> tuple[str, dict]:
    key, name = project_identity(root)
    prof = None if force else load_profile(key)
    if prof is None:
        old = load_profile(key) if profile_path(key).exists() else None
        prof = {"project": detect_project(root, name),
                "overrides": (old or {}).get("overrides") or {}}
        _write_json(profile_path(key), prof)
        if not quiet:
            p = prof["project"]
            log(f"profile for {name}: {', '.join(p['stack']) or 'no stack detected'}; "
                f"checks: {', '.join(p['checks']) or 'none'}\n  saved to {profile_path(key)} "
                "(edit it to change checks, setup or config overrides)")
    prof["project"]["root"] = str(root)
    prof["project"]["key"] = key
    return key, prof


def context_for(root: Path, explicit: Path | None) -> tuple[dict, dict]:
    key, prof = ensure_profile(root)
    return load_config(explicit, prof.get("overrides")), prof["project"]


def project_brief(project: dict, root: Path, *, tree: bool = False) -> str:
    lines = [f"Project: {project.get('name')} ({', '.join(project.get('stack') or []) or 'stack not detected'})"]
    checks = project.get("checks") or {}
    if checks:
        lines.append("Checks crew runs before and after the work (agents' shells have no network):")
        lines += [f"- {n}: `{c}`" for n, c in checks.items()]
    else:
        lines.append("No automated checks were detected for this project.")
    lines += [f"- note: {n}" for n in project.get("notes") or []]
    if project.get("agent_docs"):
        lines.append(f"Project instructions for agents: {', '.join(project['agent_docs'])}. "
                     "Read them before changing anything.")
    if tree:
        files = [f for f in git(root, "ls-files", "-z").stdout.split("\0") if f]
        if files:
            top = Counter(f.split("/", 1)[0] + ("/" if "/" in f else "") for f in files)
            lines.append("Tracked files by top-level entry: " + ", ".join(
                f"{k} ({v})" if k.endswith("/") else k for k, v in sorted(top.items())[:60]))
        recent = git(root, "log", "--oneline", "-8").stdout.strip()
        if recent:
            lines.append("Recent commits:\n" + recent)
    return "\n".join(lines)


# --------------------------------------------------------------------------- ledger

def _ledger() -> Path:
    return CREW_HOME / "usage.jsonl"


def _state() -> Path:
    return CREW_HOME / "state.json"


def record_usage(entry: dict) -> None:
    with STATE_LOCK:
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
    """Per-provider usage in the configured metric across all projects, plus the number of calls."""
    split = cfg["split"]
    totals = {p: 0.0 for p in PROVIDERS}
    runs = 0
    for e in read_usage(split.get("window_hours", 168)):
        if e.get("provider") in totals:
            totals[e["provider"]] += metric_value(e, split.get("metric", "tasks"))
            runs += 1
    return totals, runs


def per_call_estimate(cfg: dict) -> dict[str, float]:
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
    with STATE_LOCK:
        _write_json(_state(), state)


def set_cooldown(cfg: dict, provider: str) -> float:
    until = time.time() + cfg["split"].get("cooldown_minutes", 60) * 60
    with STATE_LOCK:
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
    return [p for p in PROVIDERS
            if provider_cfg(cfg, p).get("enabled", True) and installed(cfg, p)
            and cooldown_until(p) <= now and login_status(cfg, p)[0]]


def over_target(cfg: dict, totals: dict, avail: list[str]) -> dict[str, float]:
    """Each provider's usage share minus its target share (negative = under-used)."""
    raw = cfg["split"].get("target_share") or {}
    weights = {p: float(raw.get(p, 1.0)) for p in avail}
    wsum = sum(weights.values()) or 1.0
    used = sum(totals.get(p, 0.0) for p in avail)
    return {p: (totals.get(p, 0.0) / used if used else 0.0) - weights[p] / wsum for p in avail}


def choose_provider(cfg: dict, agent: str, totals: dict, avail: list[str], runs: int) -> str | None:
    """by_role: preferred provider unless it is more than `tolerance` over target (after
    `min_runs` calls); balanced: furthest under target; fixed: always preferred."""
    if not avail:
        return None
    if len(avail) == 1:
        return avail[0]
    split = cfg["split"]
    over = over_target(cfg, totals, avail)
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


def init_slots(cfg: dict) -> None:
    for p in PROVIDERS:
        n = max(1, int(provider_cfg(cfg, p).get("max_parallel", 1) or 1))
        SLOTS[p] = threading.BoundedSemaphore(n)
        SLOT_LIMIT[p] = n


def is_busy(p: str) -> bool:
    with SLOT_LOCK:
        return p in SLOT_LIMIT and BUSY[p] >= SLOT_LIMIT[p]


@contextlib.contextmanager
def provider_slot(p: str):
    sem = SLOTS.get(p)
    if sem is None:
        yield
        return
    while not sem.acquire(timeout=1):
        if STOPPING.is_set():
            raise Interrupted()
    with SLOT_LOCK:
        BUSY[p] += 1
    try:
        yield
    finally:
        with SLOT_LOCK:
            BUSY[p] -= 1
        sem.release()


# --------------------------------------------------------------------------- processes

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


def _descendants(pid: int) -> list[int]:
    """Every process below pid, from one `ps` snapshot. Catches tool processes the
    CLI started in their own session (e.g. Claude's bwrap sandbox)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [pid]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in found:
                found.append(c)
                stack.append(c)
    return found


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop a CLI and everything it started: SIGTERM, a grace period, then SIGKILL."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        with contextlib.suppress(OSError):
            proc.kill()
        return
    try:
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)  # don't abort the cleanup
    except ValueError:  # not the main thread
        old_int = None
    try:
        tree = _descendants(proc.pid)
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            for kill in [lambda: os.killpg(proc.pid, sig)] + [lambda p=p: os.kill(p, sig) for p in tree]:
                with contextlib.suppress(OSError):
                    kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=wait)
    finally:
        if old_int is not None:
            signal.signal(signal.SIGINT, old_int)


def stop_all() -> None:
    """Called from the main thread when crew is told to stop: kill every running CLI."""
    STOPPING.set()
    with ACTIVE_LOCK:
        procs = list(ACTIVE)
    threads = [threading.Thread(target=_kill_group, args=(p,)) for p in procs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _exec(cmd: list[str], stdin_text: str, cwd: Path, timeout: float, env: dict) -> tuple[int, str, str, float]:
    t0 = time.time()
    if STOPPING.is_set():
        raise Interrupted()
    if not Path(cwd).is_dir():
        return 127, "", f"workdir not found: {cwd}", 0.0
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, start_new_session=True)
    except OSError as e:
        return 127, "", f"cannot start {cmd[0]}: {e.strerror or e}", time.time() - t0
    with ACTIVE_LOCK:
        ACTIVE.add(proc)
    try:
        try:
            out, err = proc.communicate(stdin_text.encode(), timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            try:
                out, err = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:  # a surviving grandchild still holds the pipes
                for f in (proc.stdout, proc.stderr):
                    with contextlib.suppress(OSError):
                        f.close()
                out, err = b"", b""
            rc, err = 124, (err or b"") + f"\ntimed out after {timeout:.0f}s".encode()
        except BaseException:
            _kill_group(proc)  # Ctrl-C does not reach a child in its own session
            raise
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(proc)
    if STOPPING.is_set():
        raise Interrupted()
    dec = lambda b: (b or b"").decode("utf-8", "replace")  # noqa: E731
    return rc, dec(out), dec(err), time.time() - t0


_SANDBOX_READY: list[bool] = []


def claude_sandbox_ready() -> bool:
    """Can Claude Code's own sandbox actually start here? (Seatbelt on macOS; bubblewrap and
    socat on Linux, tested once.) Without it, granting Bash would mean unsandboxed shell access."""
    if not _SANDBOX_READY:
        ok = False
        if sys.platform == "darwin":
            ok = True
        elif sys.platform.startswith("linux") and shutil.which("bwrap") and shutil.which("socat"):
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                ok = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                                    capture_output=True, timeout=20).returncode == 0
        _SANDBOX_READY.append(ok)
    return _SANDBOX_READY[0]


def sandbox_mode(cfg: dict) -> str | None:
    """How crew runs setup-free shell checks: inside Codex's sandbox, directly, or not at all."""
    if os.name != "nt" and installed(cfg, "codex"):
        return "codex-sandbox"
    return "direct" if cfg["verify"].get("unsandboxed") else None


def run_shell(cfg: dict, command: str, root: Path, timeout: float, *, sandboxed: bool) -> tuple[int, str, float]:
    env = dict(os.environ, CI="1")
    for keys in FOREIGN_KEYS.values():
        for k in keys:
            env.pop(k, None)
    if sandboxed:
        argv = [provider_cfg(cfg, "codex")["cmd"], "sandbox", "-P", ":workspace", "-C", str(root),
                "--", "sh", "-c", command]
    else:
        argv = ["sh", "-c", command] if os.name != "nt" else ["cmd", "/c", command]
    rc, out, err, secs = _exec(argv, "", root, timeout, env)
    return rc, (out + ("\n" + err if err.strip() else "")), secs


# --------------------------------------------------------------------------- providers

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
        with contextlib.suppress(json.JSONDecodeError):
            res.structured = json.loads(res.text)


def run_claude(cfg: dict, prompt: str, workdir: Path, *, schema: dict | None = None,
               read_only: bool = False, model: str | None = None) -> Result:
    pc = provider_cfg(cfg, "claude")
    # User-level settings only: project settings in the workdir (hooks, defaultMode, allow
    # rules) could be written by the other agent and would run unsandboxed.
    cmd = [pc["cmd"], "-p", "--output-format", "json", "--setting-sources", "user"]
    if model:
        cmd += ["--model", model]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if read_only:
        # Read-only tools only: plan mode alone still runs Bash that a user allow rule permits.
        cmd += ["--permission-mode", "plan", "--tools", "Read,Grep,Glob"]
    else:
        cmd += list(pc.get("args") or [])
        if pc.get("sandboxed_bash", True) and claude_sandbox_ready():
            # Every shell command then runs in the sandbox without a prompt (the settings above
            # turn off its escape hatch). Never granted when the sandbox can't start.
            cmd += ["--allowedTools", "Bash"]
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
              read_only: bool = False, model: str | None = None) -> Result:
    pc = provider_cfg(cfg, "codex")
    with tempfile.TemporaryDirectory(prefix="crew-codex-") as tmp:
        last = Path(tmp) / "last.txt"
        cmd = [pc["cmd"], "exec", "--json", "-C", str(workdir), "-o", str(last)]
        if model:
            cmd += ["-m", model]
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
            notices.append(ev.get("message") or json.dumps(ev))  # usually a retry notice
        elif kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                agent_msgs.append(item["text"])
    if not text and agent_msgs:
        text = agent_msgs[-1]
    ok = rc == 0 and completed and not failures
    res = Result("codex", ok, text=text, tokens=tokens, seconds=secs)
    if not ok:
        fatal = [n for n in notices if not str(n).startswith("Reconnecting")]
        msg = "\n".join(map(str, failures)) or (str(fatal[-1]) if fatal else "")
        res.error = (msg or err.strip() or short(out, 500) or f"exit {rc}")[-2000:]
        res.limited = bool(msg and LIMIT_RE.search(msg))  # retry notices never count
    if schema is not None:
        _parse_structured(res)
    return res


RUNNERS = {"claude": run_claude, "codex": run_codex}


def call(cfg: dict, provider: str, prompt: str, workdir: Path, *, agent: str, task_id: str,
         project: str = "", schema: dict | None = None, read_only: bool = False) -> Result:
    with provider_slot(provider):
        res = RUNNERS[provider](cfg, prompt, workdir, schema=schema, read_only=read_only,
                                model=model_for(cfg, provider, agent))
    record_usage({
        "ts": time.time(), "provider": provider, "agent": agent, "task": task_id, "project": project,
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
    # Strict-mode compatible (Codex --output-schema): every property required, no extras.
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
    return {"type": "object", "additionalProperties": False, "required": ["summary", "tasks"],
            "properties": {"summary": {"type": "string"}, "tasks": {"type": "array", "items": task}}}


def roster(cfg: dict) -> str:
    return "\n".join(f"- {n}: {a['role']}" for n, a in cfg["agents"].items())


def planner_prompt(cfg: dict, project: dict, goal: str, workdir: Path) -> str:
    n = cfg.get("planner", {}).get("max_tasks", 8)
    return f"""You are the planner for a crew of AI coding agents working in {workdir}.

Goal:
{goal}

{project_brief(project, workdir, tree=True)}

Crew:
{roster(cfg)}

Look through the repository as much as you need, but do not change any files.
Break the goal into at most {n} concrete tasks and assign each one to the crew
member whose role fits it best. Each task runs in a separate non-interactive
session that will not see this message, so its instructions must stand on their
own: name the files, commands and expected outputs. Where a check above applies,
use its exact command in "acceptance". Agents cannot reach the network, so do not
plan tasks that need to download anything. Use ids t1, t2, ... and list
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


def plans_dir(key: str) -> Path:
    return CREW_HOME / "plans" / key


def new_plan_path(key: str, goal: str) -> Path:
    base = plans_dir(key) / f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(goal)}"
    path, n = base.with_suffix(".json"), 2
    while path.exists():
        path, n = Path(f"{base}-{n}.json"), n + 1
    return path


def latest_plan(key: str) -> Path | None:
    files = sorted(plans_dir(key).glob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def make_plan(cfg: dict, project: dict, goal: str, workdir: Path) -> dict:
    if not workdir.is_dir():
        raise ValueError(f"workdir not found: {workdir}")
    avail = available_providers(cfg)
    if not avail:
        raise ValueError("no provider available: run `crew doctor`")
    first = cfg.get("planner", {}).get("provider", "claude")
    order = [first] + [p for p in avail if p != first] if first in avail else avail
    if not cfg["split"].get("fallback_on_error", True):
        order = order[:1]
    max_tasks = cfg.get("planner", {}).get("max_tasks", 8)
    prompt = planner_prompt(cfg, project, goal, workdir)
    errors = []
    for i, p in enumerate(order):
        if i and p not in available_providers(cfg):
            continue
        log(f"planning with {p} ...")
        res = call(cfg, p, prompt, workdir, agent="planner", task_id="plan", project=project.get("name", ""),
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
        return {"goal": goal, "workdir": str(workdir),
                "project": {"key": project.get("key"), "name": project.get("name")},
                "planned_by": p, "created": time.strftime("%Y-%m-%d %H:%M"),
                "summary": plan.get("summary", ""), "tasks": plan["tasks"]}
    raise ValueError("planning failed\n  " + "\n  ".join(errors))


def print_plan(plan: dict, path: Path) -> None:
    log(f"\n{plan['summary']}\n")
    for t in plan["tasks"]:
        dep = f" (after {', '.join(t['depends_on'])})" if t["depends_on"] else ""
        log(f"  {t['id']} {t['agent']:<6} {t['title']}{dep}")
    log(f"\nplan written to {path}")


# --------------------------------------------------------------------------- running

def worker_prompt(cfg: dict, plan: dict, project: dict, task: dict, done: dict[str, dict]) -> str:
    agent = task["agent"]
    ctx = "\n".join(
        f"- {d} ({done[d]['agent']}, {done[d]['title']}): {short(str(done[d].get('result') or ''), 600)}"
        for d in task["depends_on"] if d in done
    ) or "- none"
    return f"""You are {agent}, one of the agents on a crew ({', '.join(cfg['agents'])}).
Your role: {cfg['agents'][agent]['role']}

Overall goal: {plan['goal']}

{project_brief(project, Path(plan['workdir']))}

Your task ({task['id']}): {task['title']}

Instructions:
{task['instructions']}

Done when:
{task['acceptance']}

Results of the tasks this one depends on:
{ctx}

Work directly in the repository at {plan['workdir']}. Do not commit, push or
switch branches: crew commits your work after this task. Your shell has no
network access; if something you need is missing, say so instead of working
around it.

When you stop, report status "done" only if the "Done when" criteria are met
and you checked them; "partial" if some of it is left; "blocked" if you could
not proceed. In summary, say what you changed, how you checked it, and what is
left undone or blocking you."""


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def own_files_in(cfg: dict, workdir: Path) -> dict[Path, str]:
    """crew.py and the config file, if they sit inside the workdir, with their hashes."""
    root = workdir.resolve()
    out = {}
    candidates = {Path(__file__).resolve()}
    if cfg.get("_path"):
        candidates.add(Path(cfg["_path"]).resolve())
    for f in candidates:
        try:
            f.relative_to(root)
        except ValueError:
            continue
        out[f] = _sha(f)
    return out


def plan_state(plan: dict) -> str:
    """Summary state of a plan: done, paused (pending tasks left), or needs-review."""
    statuses = [t.get("status", "pending") for t in plan.get("tasks", [])]
    regressions = (plan.get("checks") or {}).get("regressions") or []
    if any(s == "pending" for s in statuses):
        return "paused"
    if all(s == "done" for s in statuses) and not regressions:
        return "done"
    return "needs-review"


class Run:
    """One pass over a plan file: git branch, setup, baseline checks, tasks, verification."""

    def __init__(self, cfg: dict, plan_path: Path, *, dry_run: bool = False, retry_failed: bool = False,
                 use_git: bool = True):
        self.cfg, self.path, self.dry_run = cfg, plan_path, dry_run
        self.plan = _read_json(plan_path)
        plan = self.plan
        if not isinstance(plan, dict) or not isinstance(plan.get("goal"), str) \
                or not isinstance(plan.get("workdir"), str) or not plan["workdir"]:
            raise ValueError(f"{plan_path}: not a crew plan (needs goal, workdir, tasks)")
        self.order = validate_plan(cfg, plan)
        self.workdir = Path(plan["workdir"])
        if not self.workdir.is_dir():
            raise ValueError(f"workdir not found: {self.workdir}")
        key = plan["project"].get("key") if isinstance(plan.get("project"), dict) else None
        prof = load_profile(key) if isinstance(key, str) and key else None
        if prof is None:
            key, prof = ensure_profile(self.workdir, quiet=True)
        self.project = dict(prof["project"], key=key, root=str(self.workdir))
        self.by_id = {t["id"]: t for t in plan["tasks"]}
        if retry_failed:
            for t in plan["tasks"]:
                if t.get("status") in (*UNFINISHED, "skipped"):
                    t["status"] = "pending"
        self.use_git = use_git and cfg["git"].get("enabled", True) and is_git_repo(self.workdir)
        self.git = plan.get("git") if self.use_git else None
        self.totals, self.runs = usage_totals(cfg)
        self.estimate = per_call_estimate(cfg) if dry_run else {}
        self.wont_run: set[str] = set()
        self.failed = 0
        self.stopped = False
        self.guarded: dict[Path, str] = {}

    def save(self) -> None:
        if not self.dry_run:
            _write_json(self.path, self.plan)

    # ---- before the tasks

    def prepare(self) -> None:
        log(f"workdir: {self.workdir}")
        self.guarded = own_files_in(self.cfg, self.workdir)
        if self.guarded:
            log("note: crew's own files are inside the workdir; the run stops if an agent changes them")
        if self.dry_run:
            if self.use_git:
                log(f"would work on a branch named {self.cfg['git'].get('branch_prefix', 'crew/')}{self.path.stem}")
            for step in self.project.get("setup") or []:
                log(f"would run setup if needed: {step['run']}")
            for name, cmd in (self.project.get("checks") or {}).items():
                log(f"would check {name}: {cmd}")
            return
        if self.use_git:
            self._prepare_git()
        self._setup()
        checks = self.plan.setdefault("checks", {})
        if "baseline" not in checks:
            base = self._run_checks("baseline")
            if base is not None:
                checks["baseline"] = base
                self.save()

    def _prepare_git(self) -> None:
        root, g = self.workdir, self.git
        cur = current_branch(root)
        if g is None:
            if tracked_dirty(root):
                raise ValueError(f"{root} has uncommitted changes. Commit or stash them first, "
                                 "or pass --no-git to run without a crew branch.")
            base = head_sha(root)
            if not base:
                raise ValueError(f"{root} has no commits yet. Make an initial commit, or pass --no-git.")
            name = f"{self.cfg['git'].get('branch_prefix', 'crew/')}{self.path.stem}"
            branch, n = name, 2
            while git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode == 0:
                branch, n = f"{name}-{n}", n + 1
            r = git(root, "checkout", "-q", "-b", branch)
            if r.returncode != 0:
                raise ValueError(f"could not create branch {branch}: {short(r.stderr, 200)}")
            self.git = self.plan["git"] = {"branch": branch, "base": base, "start": cur or base}
            self.save()
            log(f"git: working on new branch {branch} (from {cur or base[:10]})")
        elif cur != g["branch"]:
            if tracked_dirty(root):
                raise ValueError(f"{root} has uncommitted changes; commit or stash them so crew can "
                                 f"switch to {g['branch']}.")
            r = git(root, "checkout", "-q", g["branch"])
            if r.returncode != 0:
                raise ValueError(f"could not switch to {g['branch']}: {short(r.stderr, 200)}")
            log(f"git: back on {g['branch']}")

    def _manifests_changed(self) -> bool:
        """Did agents touch dependency files since the plan started? (Unknown without git.)"""
        if not self.git:
            return bool(self.plan.get("setup_done"))
        present = [m for m in MANIFESTS if (self.workdir / m).exists()]
        if not present:
            return False
        # Base commit against the working tree, so uncommitted edits count too.
        return git(self.workdir, "diff", "--quiet", self.git["base"], "--", *present).returncode != 0

    def _setup(self) -> None:
        if not self.cfg["setup"].get("enabled", True):
            return
        for step in self.project.get("setup") or []:
            marker = step.get("unless_exists")
            if marker and (self.workdir / marker).exists():
                continue
            if not marker and self.plan.get("setup_done"):
                continue
            if self._manifests_changed():
                log(f"setup: skipped `{step['run']}`: dependency files may have changed since this plan "
                    "started; review them and run it yourself")
                continue
            log(f"setup: {step['run']}")
            rc, out, secs = run_shell(self.cfg, step["run"], self.workdir,
                                      self.cfg["setup"].get("timeout_sec", 1800), sandboxed=False)
            if rc != 0:
                log(f"  setup failed (exit {rc}) after {secs:.0f}s:\n{tail(out, 800)}")
            if marker and self.use_git and (self.workdir / marker).exists():
                exclude_locally(self.workdir, marker.rstrip("/") + "/")
        self.plan["setup_done"] = True
        self.save()

    def _run_checks(self, label: str) -> dict | None:
        checks = self.project.get("checks") or {}
        if not checks or not self.cfg["verify"].get("enabled", True):
            return None
        mode = sandbox_mode(self.cfg)
        if mode is None:
            if label == "baseline":
                log("checks: skipped; install codex so crew can run them in its sandbox "
                    "(or set verify.unsandboxed to run them directly)")
            return None
        log(f"checks ({label}, {'in codex sandbox' if mode == 'codex-sandbox' else 'unsandboxed'}):")
        results = {}
        for name, cmd in checks.items():
            rc, out, secs = run_shell(self.cfg, cmd, self.workdir, self.cfg["verify"].get("timeout_sec", 900),
                                      sandboxed=(mode == "codex-sandbox"))
            results[name] = {"ok": rc == 0, "exit": rc, "seconds": round(secs, 1), "output": tail(out)}
            log(f"  {name}: {'pass' if rc == 0 else f'FAIL (exit {rc})'} in {secs:.0f}s")
        return results

    # ---- tasks

    def execute(self, task: dict) -> None:
        tid = task["id"]
        avail = available_providers(self.cfg)
        provider = choose_provider(self.cfg, task["agent"], self.totals, avail, self.runs)
        if provider is None:
            log(f"[{tid}] no provider available (all disabled, missing, logged out, or cooling down)")
            self.failed += 1
            self.stopped = True
            return
        if not self.dry_run and len(avail) > 1 and is_busy(provider):
            other = next(p for p in avail if p != provider)
            pref = self.cfg["agents"][task["agent"]].get("prefers", "any")
            over = over_target(self.cfg, self.totals, avail)
            if (not is_busy(other) and self.cfg["split"].get("mode") != "fixed"
                    and (pref in ("any", other) or over[other] <= float(self.cfg["split"].get("tolerance", 0.15)))):
                provider = other  # use the idle plan instead of waiting
        log(f"[{tid}] {task['agent']} -> {provider}: {task['title']}")
        if self.dry_run:
            self.totals[provider] += self.estimate[provider]
            self.runs += 1
            return

        before = untracked(self.workdir) if self.git else set()
        done = {t["id"]: t for t in self.plan["tasks"] if t.get("status") == "done"}
        res = call_with_fallback(self.cfg, provider, worker_prompt(self.cfg, self.plan, self.project, task, done),
                                 self.workdir, agent=task["agent"], task_id=tid,
                                 project=self.project.get("name", ""), schema=WORKER_SCHEMA)
        self.totals, self.runs = usage_totals(self.cfg)
        if not res.ok and res.limited:
            log(f"  stopped: {res.provider} hit a usage limit; re-run this plan later to continue")
            self.failed += 1
            self.stopped = True
            return
        task["provider"] = res.provider
        if res.ok:
            verdict = res.structured if isinstance(res.structured, dict) else {}
            outcome = verdict.get("status")
            task["status"] = outcome if outcome in ("done", "partial", "blocked") else "partial"
            task["result"] = str(verdict.get("summary") or res.text)[-RESULT_KEEP:]
        else:
            task["status"] = "failed"
            task["result"] = res.error[-RESULT_KEEP:]
        task.pop("denied", None)
        if res.denied:
            task["denied"] = res.denied
        if self.git:
            new = sorted(f for f in untracked(self.workdir) - before
                         if not f.startswith((".claude/", ".codex/")))  # CLI state, not work
            sha = git_commit(self.workdir, f"crew({tid}): {task['title']}\n\n{task['agent']} via {res.provider}, "
                                           f"{task['status']}.", new)
            if sha:
                task["commit"] = sha
        self.save()
        if task["status"] == "done":
            log(f"  done on {res.provider} in {res.seconds:.0f}s")
        else:
            self.failed += 1
            log(f"  {task['status']} on {res.provider}: {short(task['result'])}")
        if res.denied:
            log(f"  note: {res.provider} was refused permission for: {', '.join(res.denied[:5])}")
        changed = [str(f) for f, h in self.guarded.items() if _sha(f) != h]
        if changed:
            raise ValueError(f"task {tid} changed crew's own files: {', '.join(changed)}. "
                             "Stopping; review that change before running again.")

    def run_tasks(self) -> None:
        for task in self.order:
            if self.stopped:
                break
            tid, status = task["id"], task.get("status", "pending")
            if status == "done":
                continue
            if status in UNFINISHED:
                log(f"[{tid}] {task['agent']}: {status} earlier; use --retry-failed to run it again")
                self.wont_run.add(tid)
                self.failed += 1
                continue
            if self.dry_run:
                blocked = [d for d in task["depends_on"] if d in self.wont_run]
            else:
                blocked = [d for d in task["depends_on"] if self.by_id[d].get("status") != "done"]
            if blocked:
                log(f"[{tid}] {task['agent']}: skipped ({', '.join(blocked)} not done)")
                self.wont_run.add(tid)
                self.failed += 1
                if not self.dry_run:
                    task["status"] = "skipped"
                    task["result"] = f"skipped: prerequisite(s) {', '.join(blocked)} not done"
                    self.save()
                continue
            self.execute(task)

    # ---- after the tasks

    def verify(self) -> None:
        checks = self.plan.setdefault("checks", {})
        baseline = checks.get("baseline")
        if self.dry_run or baseline is None:
            return
        rounds = sum(1 for t in self.plan["tasks"] if t.get("origin") == "verify")
        while True:
            final = self._run_checks("after")
            if final is None:
                return
            regs = [n for n, r in final.items() if not r["ok"] and baseline.get(n, {}).get("ok", True)]
            checks["final"], checks["regressions"] = final, regs
            self.save()
            if not regs:
                log("checks: no regressions")
                return
            if rounds >= int(self.cfg["verify"].get("fix_rounds", 1)) or self.stopped:
                log(f"checks: still failing after this run: {', '.join(regs)}")
                self.failed += 1
                return
            rounds += 1
            task = self._fix_task(rounds, regs, final)
            self.plan["tasks"].append(task)
            self.by_id[task["id"]] = task
            self.save()
            self.execute(task)

    def _fix_task(self, n: int, regs: list[str], final: dict) -> dict:
        checks = self.project.get("checks") or {}
        detail = "\n\n".join(f"`{checks.get(r, r)}` (exit {final[r]['exit']}):\n{tail(final[r]['output'], 1200)}"
                             for r in regs)
        return {"id": f"fix{n}", "title": f"Fix checks that started failing: {', '.join(regs)}",
                "agent": self.cfg["verify"]["fixer"], "origin": "verify", "depends_on": [], "status": "pending",
                "instructions": "These checks passed before this plan's changes and fail now. Find the cause "
                                "in the changes on this branch and fix it without undoing the intended work.\n\n"
                                + detail,
                "acceptance": "These commands exit 0: " + "; ".join(f"`{checks.get(r, r)}`" for r in regs)}

    def finish(self) -> None:
        if self.dry_run:
            log("dry run: nothing was executed")
            return
        g = self.git
        if g:
            stat = git(self.workdir, "diff", "--shortstat", g["base"], "HEAD").stdout.strip()
            log(f"git: {g['branch']} has {stat or 'no changes'} since {g['base'][:10]}")
            if self.cfg["git"].get("return_to_start", True) and not tracked_dirty(self.workdir):
                start = g.get("start") or g["base"]
                is_branch = git(self.workdir, "rev-parse", "--verify", "-q", f"refs/heads/{start}").returncode == 0
                r = git(self.workdir, "checkout", "-q", *([start] if is_branch else ["--detach", start]))
                if r.returncode == 0:
                    log(f"git: switched back to {start}. Review with: git diff {start}...{g['branch']}")
        log(f"plan saved to {self.path}")


def run_plan(cfg: dict, plan_path: Path, *, dry_run: bool = False, retry_failed: bool = False,
             use_git: bool = True, show_split: bool = True) -> int:
    run = Run(cfg, plan_path, dry_run=dry_run, retry_failed=retry_failed, use_git=use_git)
    run.prepare()
    run.run_tasks()
    if not run.stopped:
        run.verify()
    run.finish()
    if show_split:
        print_split(cfg)
    return 1 if run.failed else 0


# --------------------------------------------------------------------------- queue

QUEUE_LOCK = threading.RLock()


def _queue_path() -> Path:
    return CREW_HOME / "queue.json"


def load_queue() -> list[dict]:
    p = _queue_path()
    data = _read_json(p) if p.exists() else {}
    items = data.get("items", []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)]


def save_queue(items: list[dict]) -> None:
    _write_json(_queue_path(), {"items": items})


def queue_update(item_id: str, **fields) -> None:
    with QUEUE_LOCK:
        items = load_queue()
        for i in items:
            if i["id"] == item_id:
                i.update(fields, updated=time.strftime("%Y-%m-%d %H:%M"))
        save_queue(items)


def queue_add(goal: str, roots: list[Path]) -> None:
    with QUEUE_LOCK:
        items = load_queue()
        n = max([int(i["id"][1:]) for i in items if re.fullmatch(r"q\d+", i.get("id", ""))] + [0])
        for root in roots:
            key, prof = ensure_profile(root)
            n += 1
            items.append({"id": f"q{n}", "key": key, "name": prof["project"]["name"], "workdir": str(root),
                          "goal": goal, "status": "queued", "plan": None,
                          "added": time.strftime("%Y-%m-%d %H:%M")})
            log(f"queued q{n}: {prof['project']['name']}")
        save_queue(items)


def queue_list() -> None:
    items = load_queue()
    if not items:
        log("queue is empty")
        return
    for i in items:
        log(f"{i['id']:<5} {i.get('status', '?'):<13} {i.get('name', '?'):<30} {short(i.get('goal', ''), 60)}")
        if i.get("result"):
            log(f"      {short(i['result'], 120)}")


def _process_item(explicit: Path | None, item: dict, use_git: bool, retry_failed: bool) -> None:
    _TL.prefix = f"[{item.get('name', item['id'])}] "
    try:
        root = Path(item["workdir"])
        cfg, project = context_for(resolve_workdir(root), explicit)
        path = Path(item["plan"]) if item.get("plan") else None
        if path is None or not path.exists():
            queue_update(item["id"], status="planning")
            plan = make_plan(cfg, project, item["goal"], resolve_workdir(root))
            path = new_plan_path(project["key"], item["goal"])
            _write_json(path, plan)
            print_plan(plan, path)
            queue_update(item["id"], plan=str(path))
        queue_update(item["id"], status="running")
        run_plan(cfg, path, retry_failed=retry_failed, use_git=use_git, show_split=False)
        plan = _read_json(path)
        state = plan_state(plan)
        counts = Counter(t.get("status", "pending") for t in plan["tasks"])
        queue_update(item["id"], status=state,
                     result=", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    except KeyboardInterrupt:
        queue_update(item["id"], status="paused", result="stopped by user")
        raise
    except (ValueError, OSError) as e:
        log(f"error: {e}")
        queue_update(item["id"], status="error", result=str(e))
    finally:
        _TL.prefix = ""


def queue_run(explicit: Path | None, jobs: int, use_git: bool, retry_failed: bool) -> int:
    runnable = ("queued", "paused", "planning", "running") + (("needs-review",) if retry_failed else ())
    items = [i for i in load_queue() if i.get("status") in runnable]
    if not items:
        log("nothing to run in the queue")
        return 0
    by_project: dict[str, list[dict]] = {}
    for i in items:
        by_project.setdefault(i["key"], []).append(i)
    log(f"running {len(items)} queued item(s) across {len(by_project)} project(s), up to {jobs} at a time")

    def worker(group: list[dict]) -> None:
        for item in group:  # one project's items run one after another
            if STOPPING.is_set():
                return
            _process_item(explicit, item, use_git, retry_failed)

    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    futures = [pool.submit(worker, g) for g in by_project.values()]
    try:
        for f in as_completed(futures):
            with contextlib.suppress(Interrupted):  # a worker stopped because crew is stopping
                f.result()
    except BaseException:
        stop_all()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    queue_list()
    print_split(load_config(explicit), by_project=True)
    return 0 if all(i.get("status") == "done" for i in load_queue() if i["id"] in {x["id"] for x in items}) else 1


# --------------------------------------------------------------------------- reports

def print_split(cfg: dict, by_project: bool = False) -> None:
    split = cfg["split"]
    metric, window = split.get("metric", "tasks"), split.get("window_hours", 168)
    entries = read_usage(window)
    totals, _ = usage_totals(cfg)
    total = sum(totals.values())
    raw = split.get("target_share") or {}
    wsum = sum(float(raw.get(p, 1.0)) for p in PROVIDERS) or 1.0
    log(f"\nusage over the last {window}h, all projects (metric: {metric})")
    log(f"  {'provider':<8} {'runs':>5} {'failed':>6} {'tokens':>10} {'secs':>7} {'share':>6} {'target':>6}")
    for p in PROVIDERS:
        rows = [e for e in entries if e.get("provider") == p]
        share = totals[p] / total if total else 0.0
        log(f"  {p:<8} {len(rows):>5} {sum(not e.get('ok') for e in rows):>6} "
            f"{sum(e.get('tokens') or 0 for e in rows):>10} "
            f"{sum(e.get('seconds') or 0 for e in rows):>7.0f} {share:>6.0%} {float(raw.get(p, 1.0)) / wsum:>6.0%}")
        cd = cooldown_until(p)
        if cd > time.time():
            log(f"           cooling down until {fmt_time(cd)}")
    if by_project and entries:
        log(f"\n  {'project':<34} {'claude':>7} {'codex':>6} {'failed':>6} {'tokens':>10}")
        names = sorted({e.get("project") or "(unknown)" for e in entries})
        for name in names:
            rows = [e for e in entries if (e.get("project") or "(unknown)") == name]
            log(f"  {short(name, 34):<34} {sum(e.get('provider') == 'claude' for e in rows):>7} "
                f"{sum(e.get('provider') == 'codex' for e in rows):>6} {sum(not e.get('ok') for e in rows):>6} "
                f"{sum(e.get('tokens') or 0 for e in rows):>10}")


def list_projects() -> None:
    files = sorted((CREW_HOME / "projects").glob("*.json"))
    if not files:
        log("no projects yet: run `crew init` inside a project")
        return
    for f in files:
        try:
            prof = load_profile(f.stem)
        except ValueError as e:
            log(f"{f.stem}: {e}")
            continue
        p = prof["project"]
        last = latest_plan(f.stem)
        state = ""
        if last:
            with contextlib.suppress(ValueError, OSError, KeyError):
                state = f"; last plan {plan_state(_read_json(last))} ({last.name[:15]})"
        log(f"{p.get('name'):<34} {', '.join(p.get('stack') or []) or '-'}")
        log(f"    {p.get('root')}; checks: {', '.join(p.get('checks') or {}) or 'none'}{state}")


def list_plans(key: str | None) -> None:
    dirs = [plans_dir(key)] if key else sorted((CREW_HOME / "plans").glob("*"))
    shown = 0
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            with contextlib.suppress(ValueError, OSError):
                plan = _read_json(f)
                counts = Counter(t.get("status", "pending") for t in plan.get("tasks", []))
                log(f"{plan_state(plan):<13} {(plan.get('project') or {}).get('name', d.name):<30} "
                    f"{short(plan.get('goal', ''), 50):<50} {f}")
                log(f"              {', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}")
                shown += 1
    if not shown:
        log("no plans yet")


def doctor(cfg: dict, root: Path | None) -> int:
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
            ver = subprocess.run([pc["cmd"], "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            ver = f"version check failed: {e}"
        ok, detail = login_status(cfg, p)
        log(f"{p}: {ver} at {pc['cmd']}; {'logged in' if ok else 'NOT LOGGED IN'} ({detail})")
        if not ok:
            problems += 1
            log("   fix: " + ("claude auth login, or set ANTHROPIC_API_KEY" if p == "claude"
                              else "codex login (ChatGPT plan), or set OPENAI_API_KEY and run "
                                   "`crew codex-login`, or set CODEX_API_KEY"))
    if provider_cfg(cfg, "claude").get("enabled", True):
        if claude_sandbox_ready():
            log("claude sandbox: ready, so Claude workers run shell commands sandboxed without prompts")
        else:
            hint = " -> apt install bubblewrap socat" if sys.platform.startswith("linux") else ""
            log("claude sandbox: NOT available, so Claude workers' shell commands are refused "
                f"(never run unsandboxed){hint}")
    mode = sandbox_mode(cfg)
    log(f"checks: {'run in codex sandbox' if mode == 'codex-sandbox' else 'run unsandboxed' if mode else 'disabled (install codex)'}")
    if not shutil.which("git"):
        log("git: NOT INSTALLED, so crew can't keep each run on its own branch")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
        log(f"env {var}: {'set' if os.environ.get(var) else 'not set'}")
    log(f"crew {VERSION} at {Path(__file__).resolve()}; home {CREW_HOME}; "
        f"config {cfg.get('_path') or '(built-in defaults)'}")
    if root:
        key, name = project_identity(root)
        prof = load_profile(key)
        log(f"project: {name} at {root}; " + (f"profile {profile_path(key)}" if prof else "no profile yet (crew init)"))
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


def install() -> int:
    bin_dir = CREW_HOME / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "crew"
    src = Path(__file__).resolve()
    if src != target.resolve():
        shutil.copy2(src, target)
    target.chmod(0o755)
    log(f"installed {target}")
    cfg_path = global_config_path()
    if cfg_path.exists():
        log(f"kept your config at {cfg_path}")
    else:
        template = HERE / "crew.json"
        cfg_path.write_text(template.read_text() if template.is_file() else json.dumps(DEFAULTS, indent=2))
        log(f"wrote config {cfg_path} (edit it to change agents, models and the split)")
    local_bin = Path.home() / ".local" / "bin"
    on_path = [Path(p).expanduser().resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if local_bin.resolve() in on_path:
        link = local_bin / "crew"
        if link.is_symlink() or not link.exists():
            with contextlib.suppress(FileNotFoundError):
                link.unlink()
            link.symlink_to(target)
            log(f"linked {link} -> {target}")
            return 0
    if bin_dir.resolve() not in on_path:
        log(f"add crew to your PATH, e.g.: echo 'export PATH=\"{bin_dir}:$PATH\"' >> ~/.zshrc  (or ~/.bashrc)")
    return 0


# --------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crew", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="use this config instead of ~/.crew/config.json")
    ap.add_argument("--version", action="version", version=f"crew {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("install", help="install crew into ~/.crew/bin and write a global config")
    d = sub.add_parser("doctor", help="check CLIs, logins and sandboxes")
    d.add_argument("--workdir", type=Path)
    sub.add_parser("codex-login", help="log Codex in with OPENAI_API_KEY")
    i = sub.add_parser("init", help="detect this project's stack, setup and checks")
    i.add_argument("--workdir", type=Path)
    i.add_argument("--force", action="store_true", help="re-detect, keeping your overrides")
    c = sub.add_parser("config", help="show the effective config for a project")
    c.add_argument("--workdir", type=Path)
    u = sub.add_parser("usage", help="show the usage split, overall and per project")
    u.add_argument("--clear-cooldowns", action="store_true")
    sub.add_parser("projects", help="list projects crew knows about")
    pl = sub.add_parser("plans", help="list plans for this project (or --all)")
    pl.add_argument("--workdir", type=Path)
    pl.add_argument("--all", action="store_true")

    for name in ("plan", "go"):
        s = sub.add_parser(name, help="plan a goal" if name == "plan" else "plan and run a goal")
        s.add_argument("goal")
        s.add_argument("--workdir", type=Path, help="project directory (default: the git repo you are in)")
        s.add_argument("--out", type=Path, help="plan file (default: ~/.crew/plans/<project>/...)")
        if name == "go":
            s.add_argument("--dry-run", action="store_true", help="plan, then only show routing")
            s.add_argument("--no-git", action="store_true", help="don't use a crew branch or commits")
    r = sub.add_parser("run", help="run or resume a plan (default: this project's latest)")
    r.add_argument("plan", type=Path, nargs="?")
    r.add_argument("--workdir", type=Path)
    r.add_argument("--dry-run", action="store_true", help="show routing without running")
    r.add_argument("--retry-failed", action="store_true", help="also re-run failed, partial, blocked, skipped")
    r.add_argument("--no-git", action="store_true", help="don't use a crew branch or commits")

    q = sub.add_parser("queue", help="queue goals across projects and run them in parallel")
    qs = q.add_subparsers(dest="qcmd", required=True)
    qa = qs.add_parser("add", help="queue a goal for one or more projects")
    qa.add_argument("goal")
    qa.add_argument("--workdir", type=Path, action="append", help="project directory; repeat for several")
    qa.add_argument("--all", action="store_true", help="every project crew knows about")
    qs.add_parser("list", help="show the queue")
    qr = qs.add_parser("run", help="run queued items; each project's items in order")
    qr.add_argument("--jobs", type=int, default=2, help="projects at once (default 2: one per provider)")
    qr.add_argument("--retry-failed", action="store_true")
    qr.add_argument("--no-git", action="store_true")
    qd = qs.add_parser("rm", help="remove queue items")
    qd.add_argument("ids", nargs="+")
    qc = qs.add_parser("clear", help="remove finished items (or --all)")
    qc.add_argument("--all", action="store_true")

    args = ap.parse_args(argv)
    for sig in ("SIGTERM", "SIGHUP"):  # become SystemExit, so running agents are stopped with crew
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), lambda n, f: sys.exit(128 + n))

    try:
        if args.cmd == "install":
            return install()
        cfg = load_config(args.config)
        init_slots(cfg)
        if args.cmd == "doctor":
            root = None
            with contextlib.suppress(ValueError):
                root = resolve_workdir(args.workdir)
            return doctor(cfg, root)
        if args.cmd == "codex-login":
            return codex_login(cfg)
        if args.cmd == "init":
            key, prof = ensure_profile(resolve_workdir(args.workdir), force=args.force, quiet=True)
            log(json.dumps(prof, indent=2))
            log(f"\nprofile: {profile_path(key)}")
            return 0
        if args.cmd == "config":
            pcfg, project = context_for(resolve_workdir(args.workdir), args.config)
            log(json.dumps({"project": project, "config": pcfg}, indent=2))
            return 0
        if args.cmd == "usage":
            if args.clear_cooldowns:
                with STATE_LOCK:
                    state = load_state()
                    state["cooldown_until"] = {}
                    save_state(state)
            print_split(cfg, by_project=True)
            return 0
        if args.cmd == "projects":
            list_projects()
            return 0
        if args.cmd == "plans":
            list_plans(None if args.all else project_identity(resolve_workdir(args.workdir))[0])
            return 0
        if args.cmd in ("plan", "go"):
            root = resolve_workdir(args.workdir)
            pcfg, project = context_for(root, args.config)
            plan = make_plan(pcfg, project, args.goal, root)
            out = args.out or new_plan_path(project["key"], args.goal)
            _write_json(out, plan)
            print_plan(plan, out)
            if args.cmd == "plan":
                return 0
            return run_plan(pcfg, out, dry_run=args.dry_run, use_git=not args.no_git)
        if args.cmd == "run":
            if args.plan:
                plan_path = args.plan
                data = _read_json(plan_path)
                if not isinstance(data, dict) or not isinstance(data.get("workdir"), str):
                    raise ValueError(f"{plan_path}: not a crew plan (needs goal, workdir, tasks)")
                root = resolve_workdir(Path(data["workdir"]))
            else:
                root = resolve_workdir(args.workdir)
                plan_path = latest_plan(project_identity(root)[0])
                if plan_path is None:
                    raise ValueError(f"no plans for {root} yet: run `crew plan \"goal\"` first")
                log(f"resuming {plan_path}")
            pcfg, _ = context_for(root, args.config)
            return run_plan(pcfg, plan_path, dry_run=args.dry_run, retry_failed=args.retry_failed,
                            use_git=not args.no_git)
        if args.cmd == "queue":
            if args.qcmd == "add":
                roots = [resolve_workdir(w) for w in args.workdir or []]
                if args.all:
                    for f in sorted((CREW_HOME / "projects").glob("*.json")):
                        root = Path(load_profile(f.stem)["project"].get("root") or "")
                        if root.is_dir() and root not in roots:
                            roots.append(root)
                if not roots:
                    roots = [resolve_workdir(None)]
                queue_add(args.goal, roots)
                return 0
            if args.qcmd == "list":
                queue_list()
                return 0
            if args.qcmd == "run":
                return queue_run(args.config, args.jobs, not args.no_git, args.retry_failed)
            if args.qcmd in ("rm", "clear"):
                with QUEUE_LOCK:
                    items = load_queue()
                    if args.qcmd == "rm":
                        keep = [i for i in items if i["id"] not in set(args.ids)]
                    else:
                        keep = [] if args.all else [i for i in items if i.get("status") != "done"]
                    save_queue(keep)
                log(f"removed {len(items) - len(keep)} item(s)")
                return 0
    except BrokenPipeError:  # output piped into e.g. `head`
        sys.stderr.close()
        return 0
    except (ValueError, OSError) as e:
        log(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        stop_all()
        log("\ninterrupted; running tasks stay pending, so re-running resumes them")
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
