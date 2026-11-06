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
def help_text(flags):
    drop = os.environ.get("FAKE_HELP_MISSING", "")
    print("\n".join(f for f in flags.split() if f != drop))
def work(prompt):
    """Pretend to do the task: create <task>.txt, honour FAKE_* knobs. Returns (status, summary)."""
    m = re.search(r"Your task \((\w+)\)", prompt)
    tid = m.group(1) if m else "x"
    if os.environ.get("FAKE_EARLY") == tid:
        Path("early.txt").write_text("written before the crash\n")
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    if "Fix checks" in prompt:
        Path("broken").unlink(missing_ok=True)
        return "done", "fixed"
    Path(f"{tid}.txt").write_text("work\n")
    for name, text in json.loads(os.environ.get("FAKE_EDIT", "{}")).items():
        Path(name).write_text(text)
    if os.environ.get("FAKE_DELETE"):
        import shutil as _sh
        _sh.rmtree(os.environ["FAKE_DELETE"], ignore_errors=True)
    if os.environ.get("FAKE_PYC") == tid:
        Path("__pycache__").mkdir(exist_ok=True)
        Path("__pycache__/m.cpython-313.pyc").write_bytes(b"x")
    if os.environ.get("FAKE_SWITCH") == tid:
        import subprocess
        subprocess.run(["git", "checkout", "-q", "-b", "rogue"], check=False)
    if os.environ.get("FAKE_BREAK") == tid:
        Path("broken").write_text("x")
    status = json.loads(os.environ.get("FAKE_STATUS", "{}")).get(tid, "done")
    return status, f"{tid} {status}"
'''

FAKE_CLAUDE = FAKE_COMMON + r'''
a = sys.argv[1:]
if a == ["--help"]:
    help_text("--print --output-format --json-schema --setting-sources --permission-mode --tools "
              "--allowedTools --settings --model"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    sys.exit(0)
if a and a[0] == "--version":
    print("fake-claude"); sys.exit(0)
prompt = sys.stdin.read()
record(p="claude", argv=a, planner="plan" in a, resumed="earlier attempt" in prompt,
       lean="only\nruns checks" in prompt or "only runs checks" in prompt)
if os.environ.get("FAKE_CLAUDE_GARBAGE") and "plan" not in a:
    print(json.dumps({"type": "result", "is_error": False, "result": {"oops": 1}, "usage": [1, 2],
                      "permission_denials": ["x"], "errors": 5}))
    sys.exit(0)
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
if a == ["exec", "--help"]:
    help_text("--json --output-schema --output-last-message --sandbox --skip-git-repo-check --cd --model")
    sys.exit(0)
if a == ["sandbox", "--help"]:
    help_text("--permission-profile"); sys.exit(0)
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
record(p="codex", argv=a, planner="read-only" in a, resumed="earlier attempt" in prompt)
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

    def test_lovable_repo_with_bun_and_npm_lockfiles_uses_npm(self):
        p = self.detect({"package.json": json.dumps({"scripts": {"build": "vite build"}}),
                         "package-lock.json": "{}", "bun.lockb": "x"})
        self.assertEqual(p["setup"][0]["run"], "npm ci")
        self.assertEqual(p["checks"]["build"], "npm run build")

    def test_python_dev_requirements_and_pytest_style_tests(self):
        p = self.detect({"requirements.txt": "requests\n", "requirements-dev.txt": "black\n",
                         "tests/test_x.py": "def test_x():\n    assert True\n"})
        self.assertIn("-r requirements.txt -r requirements-dev.txt", p["setup"][0]["run"])
        self.assertTrue(p["setup"][0]["run"].endswith(" pytest"))  # plain test functions need pytest
        self.assertEqual(p["checks"]["test"], ".venv/bin/python -m pytest -q")

    def test_unittest_discovery_points_at_the_tests_folder(self):
        p = self.detect({"app.py": "x = 1\n", "tests/test_app.py": "import unittest\nclass T(unittest.TestCase): pass\n"})
        self.assertEqual(p["checks"]["test"], "python3 -m unittest discover -q -s tests")

    def test_monorepo_scripts_that_live_in_packages(self):
        p = self.detect({"package.json": json.dumps({"workspaces": ["packages/*"]}), "package-lock.json": "{}",
                         "packages/web/package.json": json.dumps({"scripts": {"build": "vite build", "lint": "x"}})})
        self.assertEqual(p["checks"]["build"], "npm run build --workspaces --if-present")
        self.assertEqual(p["checks"]["lint"], "npm run lint --workspaces --if-present")

    def test_static_link_check_catches_broken_links(self):
        root = self.repo(files={"index.html": '<link href="css/site.css"><a href="https://x.org">x</a>'
                                              '<a href="#top">t</a><img src="{{ logo }}">',
                                "css/site.css": "body{}"})
        p = crew.detect_project(root, "x")
        self.assertIn("static-html", p["stack"])
        self.assertEqual(subprocess.run(["sh", "-c", p["checks"]["links"]], cwd=root).returncode, 0)
        (root / "css/site.css").unlink()
        r = subprocess.run(["sh", "-c", p["checks"]["links"]], cwd=root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("css/site.css", r.stdout)

    def test_flask_templates_are_not_a_static_site(self):
        p = self.detect({"requirements.txt": "flask\n", "app.py": "x = 1\n", "templates/index.html": "<html>"})
        self.assertNotIn("static-html", p["stack"])


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

    def test_users_checkout_is_never_touched(self):
        root = self.repo(files={"README.md": "hi\n", ".gitignore": ".env\n"})
        (root / "README.md").write_text("my uncommitted edit\n")
        (root / "notes.txt").write_text("mine\n")
        (root / ".env").write_text("SECRET=1\n")
        before = {p.name: p.read_text() for p in root.iterdir() if p.is_file()}
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("not part of crew's work", r.stdout)
        self.assertEqual({p.name: p.read_text() for p in root.iterdir() if p.is_file()}, before)
        self.assertEqual(sh(root, "git", "branch", "--show-current").strip(), "main")
        workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
        self.assertTrue(all(c["cwd"] != str(root) for c in workers))  # agents ran in the worktree
        branch = self.latest_plan(root)["git"]["branch"]
        files = sh(root, "git", "ls-tree", "-r", "--name-only", branch).split()
        self.assertEqual(sorted(files), [".gitignore", "README.md", "t1.txt", "t2.txt"])
        self.assertFalse(Path(self.latest_plan(root)["git"]["worktree"]).exists())  # removed when done

    def test_no_git_works_in_place(self):
        root = self.repo()
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

    def test_setup_runs_in_the_worktree_at_the_base_commit(self):
        root = self.repo(files={"package.json": "{}", ".gitignore": "deps/\n"})
        log = self.tmp / "setup.log"
        self.set_profile(root, setup=[{"run": f"mkdir -p deps && cat package.json >> {log}",
                                       "unless_exists": "deps"}], checks={})
        evil = {"FAKE_STATUS": json.dumps({"t1": "partial"}), "FAKE_DELETE": "deps",
                "FAKE_EDIT": json.dumps({"package.json": '{"scripts": {"postinstall": "evil"}}'})}
        r = self.crew("go", "x", "--workdir", str(root), env=evil)
        self.assertIn("setup:", r.stdout)
        self.assertFalse((root / "deps").exists())  # never in the user's checkout
        plan = self.latest_plan(root)
        self.assertTrue(plan["git"]["setup_ok"])
        branch_pkg = sh(root, "git", "show", f"{plan['git']['branch']}:package.json")
        self.assertIn("evil", branch_pkg)  # the agent's edit is committed for review...
        self.crew("run", "--workdir", str(root), "--retry-failed", env=evil)
        self.assertGreaterEqual(log.read_text().count("{}"), 1)
        self.assertNotIn("evil", log.read_text())  # ...but setup never ran on it

    def test_bad_inputs_give_clean_errors(self):
        r = self.crew("run", str(self.tmp / "missing.json"), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error:", r.stdout)
        bad = self.tmp / "bad.json"
        bad.write_text("{nope")
        r = self.crew("run", str(bad), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


class TestHardening(CrewTest):
    def with_home(self):
        old = crew.CREW_HOME
        crew.CREW_HOME = self.home
        self.addCleanup(setattr, crew, "CREW_HOME", old)

    def test_incompatible_cli_is_taken_out_of_rotation(self):
        root = self.repo()
        r = self.crew("doctor", env={"FAKE_HELP_MISSING": "--json-schema"}, check=False)
        self.assertIn("INCOMPATIBLE", r.stdout)
        self.assertIn("--json-schema", r.stdout)
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_HELP_MISSING": "--output-schema"})
        workers = [c for c in self.calls() if "argv" in c]
        self.assertTrue(workers and all(c["p"] == "claude" for c in workers), r.stdout)

    def test_second_run_of_the_same_plan_is_refused(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        first = subprocess.Popen([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run",
                                  "--workdir", str(root)], env={**self.env, "FAKE_SLEEP": "3"},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("already running", r.stdout)
        out, _ = first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, out)
        workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
        self.assertEqual(len(workers), 2)  # each task ran exactly once

    def test_agent_switching_branch_stops_without_committing(self):
        root = self.repo()
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_SWITCH": "t1"}, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("switched the worktree to rogue", r.stdout)
        plan = self.latest_plan(root)
        self.assertEqual(plan["tasks"][0]["status"], "failed")
        self.assertEqual(sh(root, "git", "log", "--format=%s", "rogue").splitlines(), ["init"])
        self.assertEqual(len([c for c in self.calls() if "argv" in c and not c.get("planner")]), 1)

    def test_commit_signing_config_does_not_break_commits(self):
        root = self.repo()
        sh(root, "git", "config", "commit.gpgsign", "true")
        sh(root, "git", "config", "gpg.program", "false")
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        branch = self.latest_plan(root)["git"]["branch"]
        self.assertEqual(len(sh(root, "git", "log", "--format=%s", f"main..{branch}").splitlines()), 2)

    def test_existing_crew_branch_name_falls_back(self):
        root = self.repo()
        sh(root, "git", "branch", "crew")
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(self.latest_plan(root)["git"]["branch"].startswith("crew-"))

    def test_interrupted_task_resumes_with_a_note(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        key, _ = crew.project_identity(root)
        path = sorted((self.home / "plans" / key).glob("*.json"))[-1]
        plan = json.loads(path.read_text())
        plan["tasks"][0]["status"] = "running"  # as if crew was killed mid-task
        path.write_text(json.dumps(plan))
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
        self.assertTrue(workers[0]["resumed"])
        self.assertFalse(workers[1]["resumed"])

    def test_do_skips_the_planner(self):
        root = self.repo()
        r = self.crew("do", "fix the typo", "--workdir", str(root), "--agent", "Luna")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(any(c.get("planner") for c in self.calls()))
        plan = self.latest_plan(root)
        self.assertEqual((plan["planned_by"], len(plan["tasks"]), plan["tasks"][0]["status"]), ("direct", 1, "done"))
        self.crew("queue", "add", "same everywhere", "--workdir", str(root), "--direct")
        self.crew("queue", "run")
        self.assertFalse(any(c.get("planner") for c in self.calls()))

    def test_planner_is_told_not_to_add_check_only_tasks(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        self.assertTrue([c for c in self.calls() if c.get("planner")][0]["lean"])

    def test_exclude_locally_in_a_worktree(self):
        root = self.repo()
        wt = self.tmp / "wt"
        sh(root, "git", "worktree", "add", "-q", str(wt), "-b", "side")
        (wt / ".venv").mkdir()
        crew.exclude_locally(wt, ".venv/")
        self.assertEqual(subprocess.run(["git", "-C", str(wt), "check-ignore", "-q", ".venv"]).returncode, 0)

    def test_atomic_write_leaves_no_temp_files(self):
        target = self.tmp / "x" / "data.json"
        crew._write_json(target, {"a": 1})
        with self.assertRaises(TypeError):
            crew._write_json(target, {"a": object()})
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertEqual([f.name for f in target.parent.iterdir()], ["data.json"])


class TestFailSafe(CrewTest):
    """The invariants: the user's work is never touched, a crash anywhere resumes cleanly,
    and nothing is reported done without evidence."""

    def branch_log(self, root):
        return sh(root, "git", "log", "--format=%s", f"main..{self.latest_plan(root)['git']['branch']}").splitlines()

    def test_crash_points_resume_without_losing_or_repeating_work(self):
        for point in ("branch-recorded", "before-commit", "after-commit"):
            with self.subTest(point=point):
                root = self.repo(f"p-{point}")
                self.log.unlink(missing_ok=True)
                r = self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": point}, check=False)
                self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
                r = self.crew("run", "--workdir", str(root))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(self.branch_log(root), ["crew(t2): second", "crew(t1): first"])
                plan = self.latest_plan(root)
                self.assertEqual([t["status"] for t in plan["tasks"]], ["done", "done"])
                self.assertTrue(all("committing" not in t for t in plan["tasks"]))
                workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
                expected = 2 if point != "after-commit" and point != "before-commit" else 2
                self.assertEqual(len(workers), expected, point)  # no task ran twice
                self.assertEqual(sh(root, "git", "status", "--porcelain"), "")

    def test_sigkill_mid_task_kills_the_orphan_and_keeps_its_files(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        first = subprocess.Popen([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run",
                                  "--workdir", str(root)], env={**self.env, "FAKE_SLEEP": "60", "FAKE_EARLY": "t1"},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            workers = [c for c in self.calls() if "argv" in c and not c.get("planner")]
            if workers:
                break
            time.sleep(0.1)
        orphan = workers[0]["pid"]
        time.sleep(0.5)
        first.kill()  # SIGKILL: crew can't clean up; the agent CLI keeps running
        first.wait()
        os.kill(orphan, 0)  # still alive
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("left behind by an earlier crew run", r.stdout)
        with self.assertRaises(ProcessLookupError):
            for _ in range(50):
                os.kill(orphan, 0)
                time.sleep(0.1)
        files = sh(root, "git", "ls-tree", "-r", "--name-only", self.latest_plan(root)["git"]["branch"]).split()
        self.assertIn("early.txt", files)  # the interrupted attempt's file was committed with its task
        self.assertFalse((root / "early.txt").exists())

    def test_generated_and_previously_ignored_files_are_never_committed(self):
        root = self.repo(files={"README.md": "x\n", ".gitignore": "secret.cfg\n", "secret.cfg": "pw"},)
        self.set_profile(root, setup=[{"run": "echo pw > secret.cfg"}], checks={})  # exists, ignored, before tasks
        r = self.crew("go", "x", "--workdir", str(root),
                      env={"FAKE_PYC": "t1", "FAKE_EDIT": json.dumps({".gitignore": "# un-ignored by the agent\n"})})
        self.assertEqual(r.returncode, 0, r.stdout)
        files = sh(root, "git", "ls-tree", "-r", "--name-only", self.latest_plan(root)["git"]["branch"]).split()
        self.assertNotIn("secret.cfg", files)
        self.assertFalse(any("__pycache__" in f for f in files))

    def test_garbled_cli_reply_is_a_failed_attempt_not_a_crash(self):
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False}}})
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_CLAUDE_GARBAGE": "1"})
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.latest_plan(root)["tasks"][0]["status"], "failed")

    def test_queue_planning_limit_pauses_the_item(self):
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False}}})
        self.crew("queue", "add", "x", "--workdir", str(root))
        self.crew("queue", "run", env={"FAKE_CLAUDE_LIMIT": "1"})
        items = json.loads((self.home / "queue.json").read_text())["items"]
        self.assertEqual(items[0]["status"], "paused")
        self.crew("usage", "--clear-cooldowns")
        r = self.crew("queue", "run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads((self.home / "queue.json").read_text())["items"][0]["status"], "done")

    def test_hostile_plans_and_goals_are_refused_before_any_agent_runs(self):
        root = self.repo()
        r = self.crew("go", "   ", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 2)
        pf = self.tmp / "nul.json"
        pf.write_text(json.dumps({"summary": "s", "tasks": [{"id": "t1", "title": "a\u0000b", "agent": "Sol",
                                                             "instructions": "i", "acceptance": "a", "depends_on": []}]}))
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_PLAN": str(pf)}, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("control characters", r.stdout)
        self.assertEqual([c for c in self.calls() if "argv" in c and not c.get("planner")], [])

    def test_two_checkouts_of_one_repo_keep_their_own_plans(self):
        a = self.repo("a")
        sh(a, "git", "remote", "add", "origin", "https://github.com/me/app.git")
        b = self.tmp / "b"
        sh(self.tmp, "git", "clone", "-q", str(a), str(b))
        sh(b, "git", "remote", "set-url", "origin", "https://github.com/me/app.git")
        self.crew("plan", "for a", "--workdir", str(a))
        self.crew("plan", "for b", "--workdir", str(b))
        r = self.crew("run", "--workdir", str(a), "--dry-run")
        self.assertIn(f"workdir: {a}", r.stdout)

    def test_adding_a_remote_keeps_the_profile_and_plans(self):
        root = self.repo()
        self.set_profile(root, checks={"mine": "true"})
        self.crew("plan", "x", "--workdir", str(root))
        sh(root, "git", "remote", "add", "origin", "https://github.com/me/bot.git")
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("moved crew's profile", r.stdout)
        cfg = json.loads(self.crew("config", "--workdir", str(root)).stdout)
        self.assertEqual(cfg["project"]["checks"], {"mine": "true"})

    def test_broken_codex_sandbox_skips_checks_instead_of_passing_them(self):
        root = self.repo()
        self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
        broken = self.bin / "codex-nosandbox"
        broken.write_text(f"#!{sys.executable}\n" + FAKE_CODEX.replace('if a and a[0] == "sandbox":',
                                                                        'if a and a[0] == "sandbox" and False:'))
        broken.chmod(0o755)
        self.write_cfg({"providers": {"codex": {"cmd": str(broken)}}})
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_BREAK": "t1"})
        self.assertIn("checks: skipped", r.stdout)
        self.assertNotIn("no regressions", r.stdout)

    def test_clean_removes_kept_worktrees(self):
        root = self.repo()
        self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "after-commit"}, check=False)
        wt = Path(self.latest_plan(root)["git"]["worktree"])
        self.assertTrue(wt.exists())
        self.crew("clean")
        self.assertTrue(wt.exists())  # paused plan: kept
        self.crew("clean", "--all")
        self.assertFalse(wt.exists())
        self.assertNotIn(str(wt), sh(root, "git", "worktree", "list"))


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
