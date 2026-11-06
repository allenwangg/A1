"""Tests for crew.py. Run with:  python3 -m unittest -v test_crew   (from this folder)

They use fake `claude` and `codex` executables, so no real model is ever called.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crew  # noqa: E402

FAKE_COMMON = r'''
import json, os, re, sys, time
from pathlib import Path
LOG = os.environ["FAKE_LOG"]
def record(**kw):
    kw.update(t=time.time(), pid=os.getpid(), cwd=os.getcwd())
    with open(LOG, "a") as f:
        f.write(json.dumps(kw) + "\n")
def plan():
    p = os.environ.get("FAKE_PLAN")
    if p:
        return json.loads(Path(p).read_text())
    return {"summary": "two steps", "tasks": [
        {"id": "t1", "title": "first", "agent": "Sol", "instructions": "make t1.txt", "acceptance": "exists",
         "depends_on": []},
        {"id": "t2", "title": "second", "agent": "Luna", "instructions": "make t2.txt", "acceptance": "exists",
         "depends_on": ["t1"]}]}
def work(prompt):
    """Pretend to do the task: create <task>.txt, honour FAKE_* knobs. Returns (status, summary)."""
    m = re.search(r"Your task \((\w+)\)", prompt)
    tid = m.group(1) if m else "x"
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    if "Fix checks" in prompt:
        Path("broken").unlink(missing_ok=True)
        return "done", "fixed"
    Path(f"{tid}.txt").write_text("work\n")
    if os.environ.get("FAKE_BREAK") == tid:
        Path("broken").write_text("x")
    status = json.loads(os.environ.get("FAKE_STATUS", "{}")).get(tid, "done")
    return status, f"{tid} {status}"
'''

FAKE_CLAUDE = FAKE_COMMON + r'''
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    sys.exit(0)
if a and a[0] == "--version":
    print("fake-claude"); sys.exit(0)
prompt = sys.stdin.read()
record(p="claude", argv=a, planner="plan" in a)
if os.environ.get("FAKE_CLAUDE_LIMIT"):
    print(json.dumps({"type": "result", "is_error": True, "result": "Claude AI usage limit reached", "usage": {}}))
    sys.exit(1)
if "plan" in a:
    pl = plan()
    print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(pl), "structured_output": pl,
                      "usage": {"input_tokens": 10, "output_tokens": 10}}))
    sys.exit(0)
status, summary = work(prompt)
print(json.dumps({"type": "result", "is_error": False, "result": summary,
                  "structured_output": {"status": status, "summary": summary},
                  "usage": {"input_tokens": 10, "output_tokens": 10}}))
record(p="claude", end=True)
'''

FAKE_CODEX = FAKE_COMMON + r'''
import subprocess
a = sys.argv[1:]
if a[:2] == ["login", "status"]:
    sys.exit(0)
if a and a[0] == "--version":
    print("fake-codex"); sys.exit(0)
if a and a[0] == "sandbox":
    i = a.index("--")
    d = a[a.index("-C") + 1]
    record(p="codex", sandbox=True, cmd=a[i + 1:])
    sys.exit(subprocess.run(a[i + 1:], cwd=d).returncode)
prompt = sys.stdin.read()
record(p="codex", argv=a, planner="read-only" in a)
out = Path(a[a.index("-o") + 1])
print(json.dumps({"type": "thread.started"}))
if "read-only" in a:
    out.write_text(json.dumps(plan()))
else:
    status, summary = work(prompt)
    out.write_text(json.dumps({"status": status, "summary": summary}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0,
                                                     "output_tokens": 10}}))
record(p="codex", end=True)
'''


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


class CrewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crew-test-"))
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            f = self.bin / name
            f.write_text(f"#!{sys.executable}\n{body}")
            f.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        self.cfg = self.tmp / "cfg.json"
        self.write_cfg({})
        self.env = dict(os.environ, CREW_HOME=str(self.home), FAKE_LOG=str(self.log),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
        for k in ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers

    def write_cfg(self, extra: dict):
        base = {"providers": {"claude": {"cmd": str(self.bin / "claude")}, "codex": {"cmd": str(self.bin / "codex")}},
                "split": {"min_runs": 0}}
        self.cfg.write_text(json.dumps(crew.deep_merge(base, extra)))

    def repo(self, name="proj", files=None, git=True) -> Path:
        r = self.tmp / name
        r.mkdir()
        for path, text in (files or {"README.md": "hi\n"}).items():
            (r / path).parent.mkdir(parents=True, exist_ok=True)
            (r / path).write_text(text)
        if git:
            sh(r, "git", "init", "-q", "-b", "main")
            sh(r, "git", "add", "-A")
            sh(r, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        return r

    def crew(self, *args, env=None, check=True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), *args],
                           capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=120)
        if check and r.returncode not in (0, 1):
            self.fail(f"crew {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def latest_plan(self, root: Path) -> dict:
        key, _ = crew.project_identity(root)
        files = sorted((self.home / "plans" / key).glob("*.json"))
        return json.loads(files[-1].read_text())

    def set_profile(self, root: Path, **project_fields):
        old = crew.CREW_HOME
        crew.CREW_HOME = self.home
        try:
            key, prof = crew.ensure_profile(root, quiet=True)
            prof["project"].update(project_fields)
            crew._write_json(crew.profile_path(key), prof)
        finally:
            crew.CREW_HOME = old


class TestDetect(CrewTest):
    def detect(self, files, git=True):
        root = self.repo(files=files, git=git)
        return crew.detect_project(root, "x")

    def test_vite_typescript_app(self):
        p = self.detect({
            "package.json": json.dumps({"scripts": {"dev": "vite", "build": "vite build", "lint": "eslint .",
                                                    "test": "echo \"Error: no test specified\" && exit 1"},
                                        "dependencies": {"react": "18"},
                                        "devDependencies": {"vite": "5", "typescript": "5"}}),
            "package-lock.json": "{}", "tsconfig.json": "{}", "tsconfig.app.json": "{}", "index.html": "<html>"})
        self.assertEqual(p["stack"][:1], ["node/npm"])
        self.assertIn("vite", p["stack"])
        self.assertNotIn("static-html", p["stack"])
        self.assertEqual(p["setup"], [{"run": "npm ci", "unless_exists": "node_modules"}])
        self.assertNotIn("test", p["checks"])  # the npm placeholder is ignored
        self.assertEqual(p["checks"]["typecheck"], "npx tsc --noEmit -p tsconfig.app.json")
        self.assertEqual(p["checks"]["lint"], "npm run lint")
        self.assertEqual(p["checks"]["build"], "npm run build")

    def test_pnpm_svelte_monorepo(self):
        p = self.detect({"package.json": json.dumps({"scripts": {"check": "svelte-check", "test": "vitest"},
                                                     "devDependencies": {"svelte": "4"}, "workspaces": ["a"]}),
                         "pnpm-lock.yaml": ""})
        self.assertEqual(p["setup"][0]["run"], "pnpm install --frozen-lockfile")
        self.assertEqual(p["checks"]["typecheck"], "pnpm run check")
        self.assertEqual(p["checks"]["test"], "pnpm run test")
        self.assertTrue(any("Monorepo" in n for n in p["notes"]))

    def test_python_requirements_with_pytest(self):
        p = self.detect({"requirements.txt": "pytest\nrequests\n", "bot.py": "x = 1\n",
                         "tests/test_bot.py": "def test_x(): pass\n"})
        self.assertIn("python", p["stack"])
        self.assertEqual(p["setup"][0]["unless_exists"], ".venv")
        self.assertEqual(p["checks"]["test"], ".venv/bin/python -m pytest -q")
        self.assertIn("python-syntax", p["checks"])

    def test_python_unittest_without_manifest(self):
        p = self.detect({"main.py": "x = 1\n", "test_main.py": "import unittest\n"})
        self.assertEqual(p["checks"]["test"], "python3 -m unittest discover -q")
        self.assertEqual(p["setup"], [])

    def test_notebooks_and_static_site(self):
        p = self.detect({"analysis.ipynb": json.dumps({"cells": []}), "index.html": "<html>", "CLAUDE.md": "rules"})
        self.assertIn("notebooks", p["stack"])
        self.assertIn("static-html", p["stack"])
        self.assertIn("notebooks-valid", p["checks"])
        self.assertEqual(p["agent_docs"], ["CLAUDE.md"])

    def test_detected_checks_actually_run(self):
        root = self.repo(files={"a.py": "x = 1\n", "n.ipynb": json.dumps({"cells": []})})
        p = crew.detect_project(root, "x")
        for name in ("python-syntax", "notebooks-valid"):
            r = subprocess.run(["sh", "-c", p["checks"][name]], cwd=root)
            self.assertEqual(r.returncode, 0, name)
        (root / "bad.ipynb").write_text("{not json")
        (root / "b.py").write_text("def (:\n")
        for name in ("python-syntax", "notebooks-valid"):
            r = subprocess.run(["sh", "-c", p["checks"][name]], cwd=root, capture_output=True)
            self.assertNotEqual(r.returncode, 0, name)


class TestConfig(CrewTest):
    def test_layers_and_agent_replacement(self):
        old = crew.CREW_HOME
        crew.CREW_HOME = self.home
        try:
            self.home.mkdir(parents=True)
            crew.global_config_path().write_text(json.dumps({"split": {"tolerance": 0.3}}))
            cfg = crew.load_config(None, {"split": {"mode": "balanced"},
                                          "agents": {"Solo": {"role": "does everything", "prefers": "codex"}}})
            self.assertEqual(cfg["split"]["tolerance"], 0.3)
            self.assertEqual(cfg["split"]["mode"], "balanced")
            self.assertEqual(cfg["split"]["window_hours"], 168)  # from defaults
            self.assertEqual(list(cfg["agents"]), ["Solo"])  # agents replace, not merge
            self.assertEqual(cfg["verify"]["fixer"], "Solo")  # fixer falls back to an existing agent
        finally:
            crew.CREW_HOME = old

    def test_shipped_template_matches_defaults(self):
        self.assertEqual(json.loads((HERE / "crew.json").read_text()), crew.DEFAULTS)

    def test_agent_model_override(self):
        cfg = crew.load_config(self.cfg, {"agents": {"Luna": {"role": "r", "model": {"claude": "haiku"}}}})
        self.assertEqual(crew.model_for(cfg, "claude", "Luna"), "haiku")
        self.assertIsNone(crew.model_for(cfg, "codex", "Luna"))


class TestRun(CrewTest):
    def test_go_branch_commits_and_return(self):
        root = self.repo()
        r = self.crew("go", "make two files", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = self.latest_plan(root)
        self.assertEqual([t["status"] for t in plan["tasks"]], ["done", "done"])
        branch = plan["git"]["branch"]
        self.assertTrue(branch.startswith("crew/"))
        self.assertEqual(sh(root, "git", "branch", "--show-current").strip(), "main")  # switched back
        log = sh(root, "git", "log", "--format=%s", f"main..{branch}").splitlines()
        self.assertEqual(log, ["crew(t2): second", "crew(t1): first"])
        self.assertFalse((root / "t1.txt").exists())  # the work lives on the branch only
        # the ledger knows which project the calls were for
        ledger = [json.loads(line) for line in (self.home / "usage.jsonl").read_text().splitlines()]
        self.assertTrue(all(e["project"] == crew.project_identity(root)[1] for e in ledger))
        # the planner was told about the project, the workers got the brief too
        self.assertEqual(sum(1 for c in self.calls() if c.get("planner")), 1)

    def test_dirty_tree_is_refused_unless_no_git(self):
        root = self.repo()
        (root / "README.md").write_text("changed\n")
        r = self.crew("go", "x", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("uncommitted changes", r.stdout)
        r = self.crew("go", "x", "--workdir", str(root), "--no-git")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((root / "t2.txt").exists())

    def test_partial_blocks_dependents_until_retry(self):
        root = self.repo()
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t1": "partial"})})
        self.assertEqual(r.returncode, 1)
        self.assertEqual([t["status"] for t in self.latest_plan(root)["tasks"]], ["partial", "skipped"])
        n = len(self.calls())
        self.crew("run", "--workdir", str(root))  # plain re-run leaves partial alone
        self.assertEqual(len(self.calls()), n)
        r = self.crew("run", "--workdir", str(root), "--retry-failed")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([t["status"] for t in self.latest_plan(root)["tasks"]], ["done", "done"])

    def test_usage_limit_pauses_with_task_pending(self):
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False}}, "planner": {"provider": "claude"}})
        plan_env = {"FAKE_CLAUDE_LIMIT": ""}
        self.crew("plan", "x", "--workdir", str(root), env=plan_env)
        r = self.crew("run", "--workdir", str(root), env={"FAKE_CLAUDE_LIMIT": "1"})
        self.assertIn("usage limit", r.stdout)
        self.assertEqual([t["status"] for t in self.latest_plan(root)["tasks"]], ["pending", "pending"])
        state = json.loads((self.home / "state.json").read_text())
        self.assertGreater(state["cooldown_until"]["claude"], time.time())

    def test_verify_regression_triggers_fix_task(self):
        root = self.repo()
        self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_BREAK": "t1"})
        self.assertEqual(r.returncode, 0, r.stdout)
        plan = self.latest_plan(root)
        self.assertTrue(plan["checks"]["baseline"]["guard"]["ok"])
        self.assertEqual(plan["tasks"][-1]["id"], "fix1")
        self.assertEqual(plan["tasks"][-1]["status"], "done")
        self.assertEqual(plan["checks"]["regressions"], [])
        self.assertTrue(any(c.get("sandbox") for c in self.calls()))  # checks went through codex sandbox
        self.assertIn("fix1", sh(root, "git", "log", "--format=%s", plan["git"]["branch"]))

    def test_preexisting_failure_is_not_a_regression(self):
        root = self.repo(files={"README.md": "x", "broken": "already"})
        self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        plan = self.latest_plan(root)
        self.assertFalse(plan["checks"]["baseline"]["guard"]["ok"])
        self.assertEqual(plan["checks"]["regressions"], [])
        self.assertFalse(any(t["id"].startswith("fix") for t in plan["tasks"]))

    def test_setup_runs_once_when_marker_missing(self):
        root = self.repo(files={"package.json": "{}", ".gitignore": "deps/\n"})
        self.set_profile(root, setup=[{"run": "mkdir -p deps && echo ran >> setup.log", "unless_exists": "deps"}],
                         checks={})
        self.crew("go", "x", "--workdir", str(root))
        self.assertTrue((root / "deps").is_dir())
        self.crew("run", "--workdir", str(root), "--retry-failed")
        self.assertEqual((root / "setup.log").read_text().count("ran"), 1)

    def test_bad_inputs_give_clean_errors(self):
        r = self.crew("run", str(self.tmp / "missing.json"), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error:", r.stdout)
        bad = self.tmp / "bad.json"
        bad.write_text("{nope")
        r = self.crew("run", str(bad), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


class TestQueue(CrewTest):
    def test_two_projects_run_in_parallel_on_both_providers(self):
        a, b = self.repo("alpha"), self.repo("beta")
        plan = {"summary": "one", "tasks": [{"id": "t1", "title": "only", "agent": "Luna", "instructions": "i",
                                             "acceptance": "a", "depends_on": []}]}
        pf = self.tmp / "plan.json"
        pf.write_text(json.dumps(plan))
        env = {"FAKE_PLAN": str(pf), "FAKE_SLEEP": "2"}
        self.crew("queue", "add", "do it", "--workdir", str(a), "--workdir", str(b))
        r = self.crew("queue", "run", "--jobs", "2", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        items = json.loads((self.home / "queue.json").read_text())["items"]
        self.assertEqual([i["status"] for i in items], ["done", "done"])
        workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
        self.assertEqual({c["p"] for c in workers}, {"claude", "codex"})  # Luna went to the idle provider
        starts = sorted(c["t"] for c in workers)
        self.assertLess(starts[1] - starts[0], 1.5)  # the two workers overlapped
        usage = self.crew("usage").stdout
        self.assertIn("proj", usage.replace("alpha", "proj").replace("beta", "proj"))


class TestInstall(CrewTest):
    def test_install_copies_script_and_config(self):
        env = dict(self.env, HOME=str(self.tmp), PATH="/usr/bin:/bin")
        r = subprocess.run([sys.executable, str(HERE / "crew.py"), "install"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.access(self.home / "bin" / "crew", os.X_OK))
        self.assertEqual(json.loads((self.home / "config.json").read_text()), crew.DEFAULTS)
        self.assertIn("PATH", r.stdout)
        r = subprocess.run([str(self.home / "bin" / "crew"), "--version"], env=env, capture_output=True, text=True)
        self.assertIn(crew.VERSION, r.stdout)


if __name__ == "__main__":
    unittest.main()
