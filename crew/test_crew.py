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
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crew  # noqa: E402

FAKE_COMMON = r'''
import json, os, re, sys, time, signal as _signal
from pathlib import Path
if os.environ.get("FAKE_IGNORE_TERM"):
    _signal.signal(_signal.SIGTERM, _signal.SIG_IGN)  # an agent that won't stop on a polite signal
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
    Path(".claude").mkdir(exist_ok=True)  # the real CLI leaves this behind
    if os.environ.get("FAKE_TAMPER") == tid:
        with open(".git/config", "a") as f:
            f.write("[core]\n\tfsmonitor = touch /tmp/crew-test-pwned\n")
    if os.environ.get("FAKE_SWITCH") == tid:
        import subprocess
        subprocess.run(["git", "checkout", "-q", "-b", "rogue"], check=False)
    if os.environ.get("FAKE_BREAK") == tid:
        Path("broken").write_text("x")
    if os.environ.get("FAKE_NESTED") == tid:
        import subprocess
        r = subprocess.run([sys.executable, os.environ["FAKE_NESTED_CREW"], "--config", os.environ["FAKE_NESTED_CFG"],
                            "do", "inner goal", "--workdir", os.getcwd()], capture_output=True, text=True)
        Path(os.environ["FAKE_NESTED_OUT"]).write_text(f"{r.returncode}\n{r.stdout}{r.stderr}")
    if os.environ.get("FAKE_SUBGIT") == tid:
        import subprocess
        os.remove("lib/.git")  # the submodule's one-line git file ...
        subprocess.run(["git", "init", "-q", "lib"], check=False)  # ... replaced by a repository of the agent's own
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
record(p="claude", argv=a, planner="plan" in a, resumed="earlier attempt" in prompt, prompt=prompt[-3000:],
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
        self.env = dict(os.environ, CREW_HOME=str(self.home), FAKE_LOG=str(self.log), CREW_ALLOW_TEMP_HOME="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
        for k in ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CREW_TASK"):
            self.env.pop(k, None)  # CREW_TASK: this suite may itself be running as a check inside a crew task

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
        self.assertNotIn("rogue", sh(root, "git", "branch", "--list"))  # only ever in crew's own copy
        self.assertEqual(sh(root, "git", "log", "--format=%s", "main").splitlines(), ["init"])
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


class TestCopyEngine(CrewTest):
    """crew's own clone: journaled creation and removal, publishing, and the isolation of the user's repo."""

    def plan_of(self, root):
        return self.latest_plan(root)

    def test_crash_while_making_the_copy_or_removing_it_resumes(self):
        for point in ("clone-made", "removing"):
            with self.subTest(point=point):
                root = self.repo(f"c-{point}")
                log = self.tmp / f"setup-{point}.log"
                self.set_profile(root, setup=[{"run": f"echo ran >> {log}", "unless_exists": "deps"}], checks={})
                r = self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": point}, check=False)
                self.assertEqual(r.returncode, -9, r.stdout)
                r = self.crew("run", "--workdir", str(root))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                plan = self.plan_of(root)
                self.assertEqual([t["status"] for t in plan["tasks"]], ["done", "done"])
                self.assertNotIn("creating", plan["git"])
                self.assertNotIn("removing", plan["git"])
                self.assertFalse(Path(plan["git"]["worktree"]).exists())
                files = sh(root, "git", "ls-tree", "-r", "--name-only", plan["git"]["branch"]).split()
                self.assertEqual(sorted(files), ["README.md", "t1.txt", "t2.txt"])
                self.assertGreaterEqual(log.read_text().count("ran"), 1)

    def test_recovered_commit_is_verified_again(self):
        root = self.repo()
        self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_BREAK": "t2", "CREW_CRASH_AT": "after-commit"},
                      check=False)
        self.assertEqual(r.returncode, -9)
        r = self.crew("run", "--workdir", str(root), env={"FAKE_BREAK": "t2"})
        plan = self.plan_of(root)
        self.assertIn("checks (after", r.stdout)
        self.assertTrue(any(t["id"].startswith("fix") for t in plan["tasks"]))  # the regression was caught
        self.assertEqual(plan["checks"]["regressions"], [])

    def test_needs_review_rerun_exits_nonzero_and_does_nothing(self):
        root = self.repo()
        self.set_profile(root, checks={"guard": "test ! -f t1.txt"}, setup=[])  # unfixable on purpose
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 1)
        n = len(self.calls())
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 1)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(len(self.calls()), n)  # no copy rebuilt, no setup, no model calls

    def test_copy_removed_with_uncommitted_work_reruns_the_task(self):
        root = self.repo()
        self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False)
        self.crew("clean", "--all")
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("runs again", r.stdout)
        files = sh(root, "git", "ls-tree", "-r", "--name-only", self.plan_of(root)["git"]["branch"]).split()
        self.assertIn("t1.txt", files)

    def test_clean_leaves_running_and_out_of_tree_plans_alone(self):
        root = self.repo()
        out = self.tmp / "myplans" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(out), env={"CREW_CRASH_AT": "before-commit"},
                  check=False)
        wt = Path(json.loads(out.read_text())["git"]["worktree"])
        self.crew("clean")
        self.assertTrue(wt.exists())  # paused --out plan: kept
        run = subprocess.Popen([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run", str(out)],
                               env={**self.env, "FAKE_SLEEP": "3"}, stdout=subprocess.PIPE, text=True)
        time.sleep(1.5)
        r = self.crew("clean", "--all")
        self.assertIn("its plan is running", r.stdout)
        run.communicate(timeout=60)
        self.assertEqual(run.returncode, 0)

    def test_users_repo_gets_no_crew_or_cli_files(self):
        root = self.repo()
        excl = root / ".git" / "info" / "exclude"
        before = excl.read_text() if excl.exists() else ""
        self.set_profile(root, setup=[{"run": "mkdir -p deps", "unless_exists": "deps"}], checks={})
        self.crew("go", "x", "--workdir", str(root))
        self.assertFalse((root / ".claude").exists())
        self.assertEqual(excl.read_text() if excl.exists() else "", before)
        self.assertEqual(sh(root, "git", "status", "--porcelain"), "")
        self.assertEqual(sh(root, "git", "worktree", "list").count("\n"), 1)

    def test_claude_gets_write_denied_on_crew_git_and_users_checkout(self):
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False}}})
        self.crew("go", "x", "--workdir", str(root))
        worker = [c for c in self.calls() if c.get("p") == "claude" and "argv" in c and not c.get("planner")][0]
        settings = json.loads(worker["argv"][worker["argv"].index("--settings") + 1])
        deny = settings["sandbox"]["filesystem"]["denyWrite"]
        self.assertIn(str(root), deny)
        self.assertTrue(any(d.endswith("/.git") and "worktrees" in d for d in deny))
        self.assertNotIn(str(root), worker["prompt"])  # paths point at crew's copy

    def test_tampered_git_config_stops_crew_before_it_runs_git(self):
        root = self.repo()
        pwned = Path("/tmp/crew-test-pwned")
        pwned.unlink(missing_ok=True)
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_TAMPER": "t1"}, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("git configuration of crew's clone", r.stdout)
        self.assertFalse(pwned.exists())

    def test_your_commits_on_the_crew_branch_are_built_on(self):
        root = self.repo()
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t2": "partial"})})
        branch = self.plan_of(root)["git"]["branch"]
        sh(root, "git", "checkout", "-q", branch)
        (root / "mine.txt").write_text("by hand\n")
        sh(root, "git", "add", "mine.txt")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "my fix")
        sh(root, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(root), "--retry-failed")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("continuing from your own commits", r.stdout)
        log = sh(root, "git", "log", "--format=%s", f"main..{branch}").splitlines()
        self.assertEqual(log[0], "my fix")  # t2's retry found nothing new to commit; your commit is kept on top
        self.assertEqual(self.plan_of(root)["git"]["tip"], sh(root, "git", "rev-parse", branch).strip())

    def test_plan_git_block_must_name_a_crew_branch(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        key, _ = crew.project_identity(root)
        path = sorted((self.home / "plans" / key).glob("*.json"))[-1]
        plan = json.loads(path.read_text())
        plan["git"] = {"branch": "main", "base": sh(root, "git", "rev-parse", "HEAD").strip(), "start": "dev",
                       "worktree": str(self.home / "worktrees" / key / "x")}
        path.write_text(json.dumps(plan))
        r = self.crew("run", str(path), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid git block", r.stdout)

    def test_unittest_discovery_per_folder(self):
        root = self.repo(files={"crew/tool.py": "x = 1\n",
                                "crew/test_tool.py": "import unittest\nclass T(unittest.TestCase):\n"
                                                     "    def test_a(self): pass\n"})
        p = crew.detect_project(root, "x")
        self.assertEqual(p["checks"]["test"], "python3 -m unittest discover -q -s crew -t crew")
        self.assertEqual(subprocess.run(["sh", "-c", p["checks"]["test"]], cwd=root).returncode, 0)

    def test_in_place_setup_never_reruns_after_agents(self):
        root = self.repo(files={"package.json": "{}"})
        log = self.tmp / "setup.log"
        self.set_profile(root, setup=[{"run": f"cat package.json >> {log}"}], checks={})
        self.crew("go", "x", "--workdir", str(root), "--no-git",
                  env={"FAKE_STATUS": json.dumps({"t1": "partial"}), "FAKE_EDIT": json.dumps({"package.json": "EVIL"})})
        self.crew("run", "--workdir", str(root), "--no-git", "--retry-failed")
        self.crew("do", "y", "--workdir", str(root), "--no-git")
        self.assertNotIn("EVIL", log.read_text())


class TestPublishAndOwnership(CrewTest):
    """Getting crew's commits into the user's repo when git refuses at first, and keeping each
    copy with the one plan that owns it (the 3.1 review's findings)."""

    def plan_of(self, root):
        return self.latest_plan(root)

    def workers(self):
        return [c for c in self.calls() if "argv" in c and not c.get("planner")]

    def files_on(self, root, branch):
        return sh(root, "git", "ls-tree", "-r", "--name-only", branch).split()

    def refused_publish(self, name="proj"):
        """t1 done and published, the clone gone; then the user checks the crew branch out and
        retries t2, which commits a new file that git refuses to copy into a checked-out branch."""
        root = self.repo(name)
        self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t2": "partial"})})
        plan = self.plan_of(root)
        branch, wt = plan["git"]["branch"], Path(plan["git"]["worktree"])
        self.assertFalse(wt.exists())
        sh(root, "git", "checkout", "-q", branch)  # reviewing crew's branch
        r = self.crew("run", "--workdir", str(root), "--retry-failed",
                      env={"FAKE_EDIT": json.dumps({"extra.txt": "more\n"})})
        return root, branch, wt, r

    def test_refused_publish_is_kept_and_delivered_on_the_next_run(self):
        root, branch, wt, r = self.refused_publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the copy was rebuilt with the branch checked out
        self.assertIn(f"you have {branch} checked out", r.stdout)
        self.assertIn("couldn't update", r.stdout)
        self.assertIn("refusing to fetch", r.stdout)  # git's own reason, not an empty one
        self.assertTrue(wt.exists())  # the only place the new commit lives
        self.assertNotIn("extra.txt", self.files_on(root, branch))
        self.assertEqual(sh(root, "git", "status", "--porcelain"), "")  # the checkout itself was left alone
        self.assertIn("only in crew's copy", self.crew("plans", "--workdir", str(root)).stdout)
        r = self.crew("clean")
        self.assertIn("aren't in your repo yet", r.stdout)
        self.assertTrue(wt.exists())  # plain `clean` never discards commits
        sh(root, "git", "checkout", "-q", "main")
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("diverged", r.stdout)
        self.assertIn("extra.txt", self.files_on(root, branch))
        self.assertEqual(self.plan_of(root)["git"]["published"], sh(root, "git", "rev-parse", branch).strip())
        self.assertFalse(wt.exists())
        self.assertFalse(Path(f"{wt}.plan").exists())
        self.assertEqual(len(self.workers()), n)  # nothing ran again

    def test_clean_all_says_when_it_discards_commits(self):
        root, branch, wt, r = self.refused_publish()
        r = self.crew("clean", "--all")
        self.assertIn("never reached your repo", r.stdout)
        self.assertFalse(wt.exists())

    def test_deleted_crew_branch_is_put_back(self):
        root = self.repo()
        self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t2": "partial"})})
        branch = self.plan_of(root)["git"]["branch"]
        sh(root, "git", "branch", "-q", "-D", branch)
        r = self.crew("run", "--workdir", str(root), "--retry-failed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(root, "git", "log", "--format=%s", f"main..{branch}").splitlines(),
                         ["crew(t2): second", "crew(t1): first"])  # done means the branch is really there
        self.assertEqual(self.plan_of(root)["git"]["published"], sh(root, "git", "rev-parse", branch).strip())
        sh(root, "git", "branch", "-q", "-D", branch)  # e.g. merged and deleted
        r = self.crew("run", "--workdir", str(root))
        self.assertIn("no longer in your repo", r.stdout)
        self.assertNotIn("its work is on", r.stdout)

    def test_rebuilt_copy_of_an_out_of_tree_plan_is_safe_from_clean(self):
        root = self.repo()
        out = self.tmp / "myplans" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(out), env={"CREW_CRASH_AT": "before-commit"},
                  check=False)
        wt = Path(json.loads(out.read_text())["git"]["worktree"])
        self.crew("clean", "--all")
        self.assertFalse(wt.exists())
        self.assertFalse(Path(f"{wt}.plan").exists())
        run = subprocess.Popen([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run", str(out)],
                               env={**self.env, "FAKE_SLEEP": "3"}, stdout=subprocess.PIPE, text=True)
        for _ in range(100):  # until the copy has been rebuilt and an agent is working in it
            if self.workers() and (wt / ".git").is_dir():
                break
            time.sleep(0.1)
        self.assertEqual(Path(f"{wt}.plan").read_text(), str(out.resolve()))  # written again on the rebuild
        r = self.crew("clean", "--all")
        self.assertIn("its plan is running", r.stdout)
        self.assertTrue(wt.exists())
        run.communicate(timeout=60)
        self.assertEqual(run.returncode, 0)

    def test_plans_with_the_same_file_name_never_share_a_copy(self):
        root = self.repo()
        a, c = self.tmp / "a" / "p.json", self.tmp / "c" / "p.json"
        crash = {"CREW_CRASH_AT": "before-commit"}
        self.crew("go", "x", "--workdir", str(root), "--out", str(a), env=crash, check=False)
        self.crew("clean", "--all")  # a's copy and pointer are gone; only its plan file names the branch
        self.crew("go", "x", "--workdir", str(root), "--out", str(c), env=crash, check=False)
        ga, gc = (json.loads(p.read_text())["git"] for p in (a, c))
        self.assertNotEqual(ga["branch"], gc["branch"])
        self.assertNotEqual(ga["worktree"], gc["worktree"])
        r = self.crew("run", str(a))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((Path(gc["worktree"]) / "t1.txt").exists())  # c's unfinished work is still there
        r = self.crew("run", str(c))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("runs again", r.stdout)
        for g in (ga, gc):
            self.assertEqual(sorted(self.files_on(root, g["branch"])), ["README.md", "t1.txt", "t2.txt"])

    def test_a_copy_that_belongs_to_another_plan_is_refused(self):
        root = self.repo()
        a, b = self.tmp / "a" / "p.json", self.tmp / "b" / "q.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(a), env={"CREW_CRASH_AT": "before-commit"},
                  check=False)
        b.parent.mkdir()
        shutil.copy(a, b)  # a second plan file that names the same branch and copy
        r = self.crew("run", str(b), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("belongs to another plan", r.stdout)
        self.assertEqual(len(self.workers()), 1)  # no agent ran for the intruder
        r = self.crew("run", str(a))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_non_fast_forward_refusal_gives_the_reason_then_reports_divergence(self):
        root = self.repo()
        self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "after-commit"}, check=False)
        branch = self.plan_of(root)["git"]["branch"]
        tree = sh(root, "git", "rev-parse", "main^{tree}").strip()
        mine = sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", tree, "-p", "main",
                  "-m", "mine").strip()
        sh(root, "git", "update-ref", f"refs/heads/{branch}", mine)  # a commit crew's copy doesn't have
        r = self.crew("run", "--workdir", str(root))
        self.assertIn("couldn't update", r.stdout)
        self.assertIn("non-fast-forward", r.stdout)
        self.assertTrue(Path(self.plan_of(root)["git"]["worktree"]).exists())
        self.assertEqual(sh(root, "git", "rev-parse", branch).strip(), mine)  # your branch was not overwritten
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("have diverged", r.stdout)
        # the way out crew names: merge crew's commits into your branch, then run again
        wt = self.plan_of(root)["git"]["worktree"]
        sh(root, "git", "checkout", "-q", branch)
        sh(root, "git", "fetch", "-q", wt, branch)
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "-m", "both", "FETCH_HEAD")
        sh(root, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertFalse(Path(wt).exists())
        self.assertEqual(sorted(self.files_on(root, branch)), ["README.md", "t1.txt", "t2.txt"])

    def test_long_git_commands_are_recorded_and_reaped(self):
        root = self.repo()
        pids = self.tmp / "run.pids"
        got = {}

        def tracked():
            crew._TL.pidfile = pids
            got["r"] = crew.git(root, "-c", "alias.slow=!sleep 60", "slow", track=True, timeout=120)

        old = crew.CREW_HOME
        crew.CREW_HOME = self.home
        try:
            t = threading.Thread(target=tracked)
            t.start()
            for _ in range(100):
                if pids.exists() and pids.read_text().strip():
                    break
                time.sleep(0.1)
            self.assertTrue(pids.read_text().strip())  # a crew killed now would leave a record of this git
            crew.reap_orphans(pids)  # what the next run does first
            t.join(30)
            self.assertFalse(t.is_alive())
            self.assertNotEqual(got["r"].returncode, 0)
        finally:
            crew.CREW_HOME = old

    def test_home_in_a_temp_directory_is_refused_without_the_opt_in(self):
        root = self.repo()
        self.assertTrue(crew.in_temp_dir(Path(tempfile.gettempdir()) / "x" / "y"))
        self.assertFalse(crew.in_temp_dir(Path("/usr/lib")))
        for cmd in ("do", "go"):
            r = self.crew(cmd, "x", "--workdir", str(root), env={"CREW_ALLOW_TEMP_HOME": ""}, check=False)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("inside a temp directory", r.stdout)
            self.assertIn("CREW_ALLOW_TEMP_HOME", r.stdout)
        self.assertEqual(self.calls(), [])  # refused before the planner or any agent was called
        self.crew("plan", "x", "--workdir", str(root), env={"CREW_ALLOW_TEMP_HOME": ""})
        r = self.crew("run", "--workdir", str(root), env={"CREW_ALLOW_TEMP_HOME": ""}, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.workers(), [])
        self.assertEqual(sh(root, "git", "branch", "--list").split(), ["*", "main"])
        self.crew("queue", "add", "g", "--workdir", str(root))
        n = len(self.calls())
        r = self.crew("queue", "run", env={"CREW_ALLOW_TEMP_HOME": ""}, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.calls()), n)  # not even a planner call
        self.assertIn(self.crew("usage", env={"CREW_ALLOW_TEMP_HOME": ""}).returncode, (0, 1))  # reading is fine

    def test_profile_without_a_name_is_a_clear_error(self):
        root = self.repo()
        self.crew("init", "--workdir", str(root))
        key, _ = crew.project_identity(root)
        path = self.home / "projects" / f"{key}.json"
        prof = json.loads(path.read_text())
        del prof["project"]["name"]
        path.write_text(json.dumps(prof))
        r = self.crew("projects")
        self.assertIn("project.name must be", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        for args in (("queue", "add", "g", "--workdir", str(root)), ("queue", "add", "g", "--all")):
            r = self.crew(*args, check=False)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("project.name must be", r.stdout)
        self.assertIn("queue is empty", self.crew("queue", "list").stdout)  # --all didn't queue the current folder
        self.crew("init", "--workdir", str(root), "--force")
        self.assertNotIn("project.name must be", self.crew("projects").stdout)


class TestThreeThree(CrewTest):
    """The 3.2 review's confirmed findings (crew 3.3). Each test fails on 3.2."""

    def plan_of(self, root):
        return self.latest_plan(root)

    def workers(self):
        return [c for c in self.calls() if "argv" in c and not c.get("planner")]

    def planners(self):
        return [c for c in self.calls() if c.get("planner")]

    def files_on(self, root, branch):
        return sh(root, "git", "ls-tree", "-r", "--name-only", branch).split()

    def key_of(self, root):
        key, _ = crew.project_identity(root)
        return key

    def plans_dir_of(self, key):
        old = crew.CREW_HOME
        crew.CREW_HOME = self.home
        try:
            return crew.plans_dir(key)
        finally:
            crew.CREW_HOME = old

    # ---------------------------------------------------------------- publish-and-branch

    def submodule_repo(self):
        """A superproject `super` with a submodule `lib` recorded at s1; libsrc also holds s2."""
        libsrc = self.tmp / "libsrc"
        libsrc.mkdir()
        sh(libsrc, "git", "init", "-q", "-b", "main")
        (libsrc / "f.txt").write_text("one\n")
        sh(libsrc, "git", "add", "-A")
        sh(libsrc, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s1")
        s1 = sh(libsrc, "git", "rev-parse", "HEAD").strip()
        (libsrc / "f.txt").write_text("two\n")
        sh(libsrc, "git", "add", "-A")
        sh(libsrc, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s2")
        s2 = sh(libsrc, "git", "rev-parse", "HEAD").strip()
        sh(libsrc, "git", "checkout", "-q", s1)  # so `submodule add` records s1
        super = self.tmp / "super"
        super.mkdir()
        sh(super, "git", "init", "-q", "-b", "main")
        (super / "README.md").write_text("hi\n")
        sh(super, "git", "add", "-A")
        sh(super, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        sh(super, "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(libsrc), "lib")
        sh(super, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "add lib")
        return super, s1, s2

    def test_your_submodule_bump_on_the_crew_branch_is_not_reverted(self):
        super, s1, s2 = self.submodule_repo()
        fileenv = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
                   "GIT_CONFIG_VALUE_0": "always"}
        # t1 is committed and published; t2 is still pending and the copy is kept.
        self._publish_then_pause_after_t1(super, extra_env=fileenv)
        branch = self.plan_of(super)["git"]["branch"]
        self.assertEqual(self.plan_of(super)["tasks"][0]["status"], "done")
        # You review the crew branch and bump the submodule to s2 on it.
        sh(super, "git", "checkout", "-q", branch)
        sh(super, "git", "-C", "lib", "checkout", "-q", s2)
        sh(super, "git", "add", "lib")
        sh(super, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "bump lib to s2")
        user_commit = sh(super, "git", "rev-parse", "HEAD").strip()
        sh(super, "git", "checkout", "-q", "main")
        lib_s2 = sh(super, "git", "ls-tree", user_commit, "lib").split()[2]
        r = self.crew("run", "--workdir", str(super), env=fileenv)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        branch_tip = sh(super, "git", "rev-parse", branch).strip()
        # crew's new commit sits on top of your bump and changes only its own file, not the submodule.
        self.assertEqual(sh(super, "git", "rev-parse", f"{branch}~1").strip(), user_commit)
        changed = sh(super, "git", "diff", "--name-only", f"{user_commit}..{branch}").split()
        self.assertEqual(changed, ["t2.txt"])  # 3.2 also reverts: ["lib", "t2.txt"]
        self.assertEqual(sh(super, "git", "ls-tree", branch_tip, "lib").split()[2], lib_s2)  # still s2

    def test_git_env_vars_never_touch_the_users_repo(self):
        root = self.repo()
        head_before = sh(root, "git", "rev-parse", "HEAD").strip()
        (root / "staged.txt").write_text("s\n")
        sh(root, "git", "add", "staged.txt")  # a staged change the user has not committed
        (root / "edit.txt").write_text("e\n")
        env = {"GIT_DIR": str(root / ".git"), "GIT_INDEX_FILE": str(root / ".git" / "index")}
        r = self.crew("go", "x", "--workdir", str(root), env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(root, "git", "symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.assertEqual(sh(root, "git", "rev-parse", "HEAD").strip(), head_before)
        self.assertEqual(sh(root, "git", "status", "--porcelain"),
                         "A  staged.txt\n?? edit.txt\n")  # index and worktree exactly as left
        branch = self.plan_of(root)["git"]["branch"]
        self.assertEqual(sorted(self.files_on(root, branch)), ["README.md", "t1.txt", "t2.txt"])

    def _publish_then_pause_after_t1(self, root, extra_env=None):
        """Run until t1 is committed and published, then stop with t2 still pending and the copy kept."""
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "go", "x", "--workdir", str(root)],
            env={**self.env, "FAKE_SLEEP": "6", **(extra_env or {})}, stdout=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                plan = self._latest_or_none(root)
                g = (plan or {}).get("git") or {}
                t1 = next((t for t in (plan or {}).get("tasks", []) if t["id"] == "t1"), {})
                if t1.get("status") == "done" and g.get("published") and g["published"] == g.get("tip"):
                    break
                time.sleep(0.1)
            else:
                run.kill()
                self.fail("t1 never published")
            run.terminate()
            run.communicate(timeout=30)
        finally:
            if run.poll() is None:
                run.kill()
        return self.plan_of(root)

    def _latest_or_none(self, root):
        key = self.key_of(root)
        files = sorted(self.plans_dir_of(key).glob("*.json"))
        return json.loads(files[-1].read_text()) if files else None

    def test_interrupted_commit_then_your_commit_still_lands_the_task(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        plan = self.plan_of(root)
        branch, wt = plan["git"]["branch"], Path(plan["git"]["worktree"])
        self.assertTrue(wt.exists())
        self.assertEqual(plan["tasks"][0]["status"], "done")
        self.assertNotEqual(plan["tasks"][1]["status"], "done")  # t2 not committed yet
        # t2 reaches the commit step (committing marker set at t1's sha) and crew is killed there.
        r = self.crew("run", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False)
        self.assertEqual([t.get("committing") and t["id"] for t in self.plan_of(root)["tasks"]][1], "t2")
        # You commit on the crew branch while t2's finished work is uncommitted in the copy.
        sh(root, "git", "checkout", "-q", branch)
        (root / "note.txt").write_text("yours\n")
        sh(root, "git", "add", "note.txt")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mine")
        user_commit = sh(root, "git", "rev-parse", branch).strip()
        sh(root, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # not "the agent made its own commits"
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertEqual([t["status"] for t in self.plan_of(root)["tasks"]], ["done", "done"])
        self.assertIn("note.txt", self.files_on(root, branch))  # your commit kept
        self.assertIn("t2.txt", self.files_on(root, branch))  # t2 committed on top
        self.assertIn(user_commit, sh(root, "git", "rev-list", branch).split())

    def test_kill_between_adopt_merge_and_recording_resumes(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        branch = self.plan_of(root)["git"]["branch"]
        sh(root, "git", "checkout", "-q", branch)
        (root / "note.txt").write_text("yours\n")
        sh(root, "git", "add", "note.txt")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mine")
        sh(root, "git", "checkout", "-q", "main")
        # crew fetches your commit and fast-forwards its copy, then is killed before it records the move.
        r = self.crew("run", "--workdir", str(root), env={"CREW_CRASH_AT": "adopt-merged"}, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.plan_of(root)["git"].get("adopting"),
                         sh(root, "git", "rev-parse", branch).strip())  # journaled
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertEqual([t["status"] for t in self.plan_of(root)["tasks"]], ["done", "done"])
        self.assertIn("note.txt", self.files_on(root, branch))

    def test_a_file_the_rebuild_and_your_commit_both_add_is_not_a_dead_end(self):
        root = self.repo()
        self.set_profile(root, setup=[{"run": "echo resolved > deps.lock"}])
        self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t2": "partial"})})
        plan = self.plan_of(root)
        branch, wt = plan["git"]["branch"], Path(plan["git"]["worktree"])
        self.assertFalse(wt.exists())  # copy removed; deps.lock went with it
        # You commit deps.lock (the same path setup regenerates) on the crew branch.
        sh(root, "git", "checkout", "-q", branch)
        (root / "deps.lock").write_text("yours\n")
        sh(root, "git", "add", "deps.lock")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "lock")
        sh(root, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(root), "--retry-failed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.2 dies: "would be overwritten by checkout"
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertIn("deps.lock", self.files_on(root, branch))
        self.assertIn("t2.txt", self.files_on(root, branch))

    def test_delivery_hints_name_the_plan_file(self):
        root = self.repo()
        self.crew("go", "x", "--workdir", str(root), env={"FAKE_STATUS": json.dumps({"t2": "partial"})})
        plan_file = sorted(self.plans_dir_of(self.key_of(root)).glob("*.json"))[-1]
        branch = self.plan_of(root)["git"]["branch"]
        sh(root, "git", "checkout", "-q", branch)
        r = self.crew("run", "--workdir", str(root), "--retry-failed",
                      env={"FAKE_EDIT": json.dumps({"extra.txt": "more\n"})})
        self.assertIn(f"crew run {plan_file}", r.stdout)  # the warning names the exact plan
        self.assertIn(f"crew run {plan_file}", self.crew("plans", "--workdir", str(root)).stdout)
        self.assertIn(f"crew run {plan_file}", self.crew("clean").stdout)

    # ---------------------------------------------------------------- ownership-and-processes

    def test_an_interrupted_rebuild_does_not_count_an_uncommitted_task_done(self):
        root = self.repo()
        # t1 finishes but is killed before its commit: its work is uncommitted in the copy.
        self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False)
        self.assertEqual(self.plan_of(root)["tasks"][0]["status"], "done")
        self.crew("clean", "--all")  # the copy, and t1's only work, is gone
        # the rebuild is itself killed, before it finishes; the loss must already be journaled by then
        reset = self.crew("run", "--workdir", str(root), env={"CREW_CRASH_AT": "clone-made"}, check=False)
        self.assertIn("its uncommitted work was lost", reset.stdout)  # recorded before the clone, not after
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        branch = self.plan_of(root)["git"]["branch"]
        self.assertEqual(sorted(self.files_on(root, branch)), ["README.md", "t1.txt", "t2.txt"])  # 3.2 loses t1.txt
        self.assertTrue(all(t["status"] == "done" for t in self.plan_of(root)["tasks"]))
        self.assertTrue(all(t.get("commit") for t in self.plan_of(root)["tasks"]))  # both really committed

    def test_adding_a_remote_mid_run_does_not_start_a_second_run(self):
        root = self.repo()
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "go", "x", "--workdir", str(root)],
            env={**self.env, "FAKE_SLEEP": "6"}, stdout=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                if self.workers():
                    break
                time.sleep(0.1)
            else:
                run.kill()
                self.fail("no worker started")
            sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
            r = self.crew("run", "--workdir", str(root), check=False)  # the migration moves nothing it can't
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("already running", r.stdout)
            run.communicate(timeout=60)
            self.assertEqual(run.returncode, 0)
        finally:
            if run.poll() is None:
                run.kill()
        # exactly one plan was run to completion, on one branch
        branches = [b for b in sh(root, "git", "branch", "--format=%(refname:short)").split() if b.startswith("crew")]
        self.assertEqual(len(branches), 1)

    def test_a_queued_plan_is_resumed_not_replanned_after_a_remote_is_added(self):
        root = self.repo()
        self.crew("queue", "add", "do it", "--workdir", str(root))
        self.crew("queue", "run", env={"CREW_CRASH_AT": "before-commit"}, check=False)  # dies at t1
        self.assertEqual(len(self.planners()), 1)
        sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
        r = self.crew("queue", "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.planners()), 1)  # not planned again
        plans = list(self.plans_dir_of(self.key_of(root)).glob("*.json"))
        self.assertEqual(len(plans), 1)
        branches = [b for b in sh(root, "git", "branch", "--format=%(refname:short)").split() if b.startswith("crew")]
        self.assertEqual(len(branches), 1)
        self.assertEqual(sorted(self.files_on(root, branches[0])), ["README.md", "t1.txt", "t2.txt"])

    def test_clean_keeps_a_running_copy_even_with_a_stray_in_tree_plan(self):
        root = self.repo()
        out = self.tmp / "mine" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(out),
                  env={"CREW_CRASH_AT": "before-commit"}, check=False)  # paused --out plan, copy kept
        wt = Path(json.loads(out.read_text())["git"]["worktree"])
        n0 = len(self.workers())
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run", str(out)],
            env={**self.env, "FAKE_SLEEP": "6"}, stdout=subprocess.PIPE, text=True)
        try:
            for _ in range(300):
                if len(self.workers()) > n0 and (wt / ".git").is_dir():  # the resume is well inside its run
                    break
                time.sleep(0.1)
            else:
                run.kill()
                self.fail("run did not resume")
            # a stray copy of the plan file inside the in-tree plans folder, naming the same copy
            stray = self.plans_dir_of(self.key_of(root)) / "zz-stray.json"
            stray.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(out, stray)
            r = self.crew("clean")
            self.assertIn("its plan is running", r.stdout)
            self.assertTrue(wt.exists())
            run.communicate(timeout=60)
            self.assertEqual(run.returncode, 0)
        finally:
            if run.poll() is None:
                run.kill()

    def test_a_copied_plan_file_is_refused_even_after_clean_all(self):
        root = self.repo()
        a, b = self.tmp / "a" / "p.json", self.tmp / "b" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(a),
                  env={"CREW_CRASH_AT": "before-commit"}, check=False)
        b.parent.mkdir()
        shutil.copy(a, b)  # a hand-made copy of the owner's plan file
        self.crew("clean", "--all")  # removes the copy and the .plan pointer
        self.assertFalse(Path(json.loads(a.read_text())["git"]["worktree"] + ".plan").exists())
        r = self.crew("run", str(b), check=False)  # still refused, by git.owner carried in the file
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("belongs to another plan", r.stdout)
        r = self.crew("run", str(a))  # the real owner still finishes
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_second_stop_signal_does_not_abort_the_kill(self):
        root = self.repo()
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "go", "x", "--workdir", str(root)],
            env={**self.env, "FAKE_SLEEP": "30", "FAKE_IGNORE_TERM": "1"}, stdout=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                if self.workers():
                    break
                time.sleep(0.1)
            else:
                run.kill()
                self.fail("no worker started")
            worker_pid = self.workers()[-1]["pid"]
            t0 = time.time()
            run.send_signal(__import__("signal").SIGTERM)
            time.sleep(0.5)
            run.send_signal(__import__("signal").SIGTERM)  # closing a terminal sends two; must not cut the kill short
            run.communicate(timeout=40)
            elapsed = time.time() - t0
        finally:
            if run.poll() is None:
                run.kill()
        self.assertGreater(elapsed, 5)  # crew waited out the kill window instead of exiting at once
        with self.assertRaises(ProcessLookupError):
            os.kill(worker_pid, 0)  # the agent was killed, not left running

    def test_a_corrupt_pointer_stops_neither_run_nor_clean(self):
        root = self.repo()
        out = self.tmp / "mine" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(out),
                  env={"CREW_CRASH_AT": "before-commit"}, check=False)  # paused --out plan with a live copy
        wt = json.loads(out.read_text())["git"]["worktree"]
        other = self.repo("other")  # a second project with a finished clone for clean to reach
        self.crew("go", "y", "--workdir", str(other))
        Path(f"{wt}.plan").write_bytes(b"\xff\xfe\x00garbage\x80")  # not valid UTF-8
        r = self.crew("clean", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("codec can't decode", r.stdout)
        self.assertIn("no plan claims it", r.stdout)  # the corrupt pointer is treated as naming nothing
        self.assertTrue(Path(wt).exists())  # the running-paused plan's copy is kept
        r = self.crew("run", str(out))  # re-records the pointer (over the garbage) and finishes
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        branch = json.loads(out.read_text())["git"]["branch"]
        self.assertEqual(sorted(self.files_on(root, branch)), ["README.md", "t1.txt", "t2.txt"])

    # ---------------------------------------------------------------- malformed input

    def test_deeply_nested_json_is_a_clean_error_not_a_traceback(self):
        root = self.repo()
        deep = "[" * 20000 + "]" * 20000
        # a plan file
        bad_plan = self.tmp / "bad.json"
        bad_plan.write_text(deep)
        r = self.crew("run", str(bad_plan), check=False)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("nested", r.stdout.lower() + r.stderr.lower())
        # the global config
        cfg_bad = self.tmp / "cfgbad.json"
        cfg_bad.write_text("{" + '"x":' * 20000 + "1" + "}" * 20000)
        r = subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(cfg_bad), "doctor"],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotEqual(r.returncode, 0)
        # a deeply nested state file does not break a read-only command
        (self.home).mkdir(parents=True, exist_ok=True)
        (self.home / "state.json").write_text(deep)
        r = self.crew("usage", check=False)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(r.returncode, (0, 1))


class TestContainment(CrewTest):
    """What keeps a worker contained can't be dropped by accident or swapped out by an agent (crew 3.4)."""

    def workers(self):
        return [c for c in self.calls() if "argv" in c and not c.get("planner")]

    def test_extra_worker_flags_keep_the_sandbox(self):
        root = self.repo()
        self.write_cfg({"providers": {"claude": {"args": ["--verbose"]}, "codex": {"args": ["--color", "never"]}}})
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        claude = [c["argv"] for c in self.workers() if c["p"] == "claude"]
        codex = [c["argv"] for c in self.workers() if c["p"] == "codex"]
        self.assertTrue(claude and codex)
        for a in claude:
            self.assertIn("--verbose", a)
            self.assertEqual(a[a.index("--permission-mode") + 1], "acceptEdits")
            sandbox = json.loads(a[a.index("--settings") + 1])["sandbox"]
            self.assertIs(sandbox["enabled"], True)
            self.assertIs(sandbox["allowUnsandboxedCommands"], False)
            deny = sandbox["filesystem"]["denyWrite"]
            self.assertIn(str(root), deny)  # your checkout
            self.assertTrue(any(d.endswith("/.git") for d in deny))  # the copy's git directory
        for a in codex:
            self.assertIn("--color", a)
            self.assertEqual(a[a.index("--sandbox") + 1], "workspace-write")

    def test_a_sandbox_you_turn_off_yourself_gets_no_shell(self):
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False},
                                      "claude": {"args": ["--settings", '{"sandbox": {"enabled": false}}']}}})
        r = self.crew("do", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for c in self.workers():
            settings = json.loads(c["argv"][c["argv"].index("--settings") + 1])
            self.assertIs(settings["sandbox"]["enabled"], False)  # your explicit choice is kept
            self.assertNotIn("--allowedTools", c["argv"])  # but then no unprompted shell

    def test_a_submodule_git_file_replaced_by_an_agent_stops_crew(self):
        super_, s1, s2 = TestThreeThree.submodule_repo(self)
        fileenv = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
                   "GIT_CONFIG_VALUE_0": "always"}
        r = self.crew("go", "x", "--workdir", str(super_), env={**fileenv, "FAKE_SUBGIT": "t1"}, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("submodule `lib`", r.stdout)
        self.assertIn("stopped without running git there", r.stdout)
        plan = self.latest_plan(super_)
        self.assertFalse(plan["tasks"][0].get("commit"))  # nothing was committed after the swap
        self.assertEqual(sh(super_, "git", "branch", "--list", "crew*").strip(), "")  # nothing was delivered
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(super_), env=fileenv, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)  # still refused on the next run
        self.assertIn("submodule `lib`", r.stdout)
        self.assertEqual(len(self.workers()), n)  # and before any agent starts


class TestSayWhatIsIgnored(CrewTest):
    """Settings that change nothing, and plans crew can't find, are said out loud (crew 3.5)."""

    def test_unknown_config_keys_are_reported(self):
        root = self.repo()
        self.crew("init", "--workdir", str(root))
        key, _ = crew.project_identity(root)
        path = self.home / "projects" / f"{key}.json"
        prof = json.loads(path.read_text())
        prof["overrides"] = {"checks": {"test": "false"}, "split": {"mode": "fixed"}}
        path.write_text(json.dumps(prof))
        r = self.crew("config", "--workdir", str(root))
        self.assertIn("no setting named 'checks'", r.stdout)
        self.assertIn("belong under `project`", r.stdout)
        self.assertNotIn("'split'", r.stdout)  # a real key is not reported
        self.write_cfg({"splt": {"mode": "fixed"}, "_comment": "mine"})
        r = self.crew("usage")
        self.assertIn("no setting named 'splt'", r.stdout)
        self.assertNotIn("_comment", r.stdout)

    def test_plans_lists_a_plan_kept_elsewhere_while_it_has_a_copy(self):
        root = self.repo()
        out = self.tmp / "mine" / "p.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(out), env={"CREW_CRASH_AT": "before-commit"},
                  check=False)  # paused, its copy kept
        for args in (("plans", "--workdir", str(root)), ("plans", "--all")):
            r = self.crew(*args)
            self.assertIn(str(out.resolve()), r.stdout, args)
            self.assertIn("kept outside crew's plans folder", r.stdout)
        other = self.repo("other")
        self.assertNotIn(str(out.resolve()), self.crew("plans", "--workdir", str(other)).stdout)
        self.crew("run", str(out))  # finishes; the copy and its pointer go
        self.assertNotIn(str(out.resolve()), self.crew("plans", "--all").stdout)


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

    def test_install_gives_each_tool_it_finds_the_crew_skill(self):
        home = self.tmp / "userhome"
        for d in (".claude", ".gemini"):  # Claude Code and Antigravity are in use here; Codex is not
            (home / d).mkdir(parents=True)
        mine = home / ".claude" / "skills" / "crew" / "SKILL.md"
        env = dict(self.env, HOME=str(home), PATH="/usr/bin:/bin")
        r = subprocess.run([sys.executable, str(HERE / "crew.py"), "install"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for where in (".claude/skills", ".gemini/config/skills"):
            text = (home / where / "crew" / "SKILL.md").read_text()
            self.assertTrue(text.startswith("---\nname: crew\ndescription: "))
            self.assertIn("crew go '<goal>' --workdir <repo>", text)
        self.assertFalse((home / ".agents").exists())  # nothing written for a tool that isn't there
        self.assertIn("Codex not found", r.stdout)
        mine.write_text("my own skill\n")  # a skill of that name crew didn't write is never replaced
        r = subprocess.run([sys.executable, str(HERE / "crew.py"), "install"], env=env, capture_output=True, text=True)
        self.assertEqual(mine.read_text(), "my own skill\n")
        self.assertIn("not written by crew", r.stdout)

    def test_the_skill_fits_every_tool_that_reads_it(self):
        head, _, body = crew.SKILL_MD.partition("\n---\n")
        fields = dict(line.split(": ", 1) for line in head.splitlines()[1:])
        self.assertEqual(sorted(fields), ["description", "name"])  # only what all three tools accept
        self.assertEqual(fields["name"], "crew")  # must match the folder name
        self.assertLessEqual(len(fields["description"]), 1024)
        r = self.crew("skill")
        self.assertEqual(r.stdout, crew.SKILL_MD)
        known = self.crew("--help").stdout  # every command the skill names exists
        for cmd in ("do", "go", "plan", "run", "queue", "plans", "usage", "doctor", "clean"):
            self.assertIn(f"crew {cmd}", body)
            self.assertIn(cmd, known)

    def test_a_hang_up_ignored_at_start_stays_ignored(self):
        import signal
        root = self.repo()
        run = subprocess.Popen(  # what `nohup crew ...` does: SIGHUP is already ignored when crew starts
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "do", "x", "--workdir", str(root)],
            env={**self.env, "FAKE_SLEEP": "3"}, stdout=subprocess.PIPE, text=True,
            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
        try:
            for _ in range(200):
                if [c for c in self.calls() if "argv" in c]:
                    break
                time.sleep(0.1)
            run.send_signal(signal.SIGHUP)  # the terminal closes
            out, _ = run.communicate(timeout=60)
        finally:
            if run.poll() is None:
                run.kill()
        self.assertEqual(run.returncode, 0, out)  # crew carried on and finished
        self.assertEqual(self.latest_plan(root)["tasks"][0]["status"], "done")

    def test_crew_refuses_to_start_inside_its_own_task(self):
        root = self.repo()
        out = self.tmp / "nested.txt"
        r = self.crew("do", "x", "--workdir", str(root),
                      env={"FAKE_NESTED": "t1", "FAKE_NESTED_CREW": str(HERE / "crew.py"),
                           "FAKE_NESTED_CFG": str(self.cfg), "FAKE_NESTED_OUT": str(out)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the outer task itself is fine
        code, _, said = out.read_text().partition("\n")
        self.assertEqual(code, "2")
        self.assertIn("can't be started from inside one of its own tasks", said)
        self.assertEqual(len([c for c in self.calls() if "argv" in c]), 1)  # the inner crew called no model
        key, _ = crew.project_identity(root)
        self.assertEqual(len(list((self.home / "plans" / key).glob("*.json"))), 1)  # and wrote no plan


if __name__ == "__main__":
    unittest.main()
