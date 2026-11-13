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
if os.environ.get("FAKE_PROBE_SLEEP") and (sys.argv[1:] in (["--help"], ["exec", "--help"], ["sandbox", "--help"])
                                            or sys.argv[1:3] in (["auth", "status"], ["login", "status"])):
    time.sleep(float(os.environ["FAKE_PROBE_SLEEP"]))  # a CLI whose login check or --help hangs
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
def probe(p, what):
    """A call that is not a task: which credentials did it come with? (Only on request: tests count calls.)"""
    if os.environ.get("FAKE_RECORD_PROBES"):
        record(p=p, probe=what, keys=sorted(k for k in os.environ if k in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
            "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")))
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
        Path(name).parent.mkdir(parents=True, exist_ok=True)
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
    if os.environ.get("FAKE_SUMMARY"):  # JSON, so that it can hold what only a JSON escape can: "\\ud83d"
        return status, json.loads(os.environ["FAKE_SUMMARY"])
    return status, f"{tid} {status}"
'''

FAKE_CLAUDE = FAKE_COMMON + r'''
a = sys.argv[1:]
if os.environ.get("FAKE_PROBE_READS_TERMINAL") and (a == ["--help"] or a[:2] == ["auth", "status"]):
    sys.stdin.read(1)  # the real CLI touches the terminal it is given, even for these two
if a == ["--help"]:
    help_text("--print --output-format --json-schema --setting-sources --permission-mode --tools "
              "--allowedTools --disallowedTools --settings --model"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    sys.exit(0)
if a and a[0] == "--version":
    probe("claude", "version")
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
    help_text("--json --output-schema --output-last-message --sandbox --skip-git-repo-check --cd --model "
              "--ignore-rules --config")
    sys.exit(0)
if a == ["sandbox", "--help"]:
    help_text("--permission-profile --config"); sys.exit(0)
if a[:2] == ["login", "status"]:
    sys.exit(0)
if a[:2] == ["login", "--with-api-key"]:
    record(p="codex", login=a, key_on_stdin=sys.stdin.read())
    probe("codex", "login")
    print("Successfully logged in"); sys.exit(0)
if a and a[0] == "--version":
    probe("codex", "version")
    print("fake-codex"); sys.exit(0)
def untrusted(d):
    """Was this call told, as the real CLI must be, not to trust directory d? (-c projects={"<d>"={...}})"""
    return any(x.startswith("projects={") and json.dumps(d) + '={trust_level="untrusted"}' in x
               for x, flag in zip(a[1:], a) if flag in ("-c", "--config"))
if a and a[0] == "sandbox":
    i = a.index("--")
    d = a[a.index("-C") + 1]
    record(p="codex", sandbox=True, cmd=a[i + 1:], flags=a[1:i], reads_project_config=not untrusted(d))
    sys.exit(subprocess.run(a[i + 1:], cwd=d).returncode)
prompt = sys.stdin.read()
# What the real CLI (0.166) obeys from the directory it works in unless it is told not to: execpolicy rules
# (an `allow` rule runs the command outside the sandbox) and, in a directory it trusts, .codex/config.toml
# (tool servers started unsandboxed, sandbox settings, environment policy). `codex exec` trusts its own.
obeys = [n for n, on in ((".codex/rules/default.rules", "--ignore-rules" not in a),
                         (".codex/config.toml", not untrusted(os.getcwd()))) if on and Path(n).exists()]
record(p="codex", argv=a, planner="read-only" in a, resumed="earlier attempt" in prompt, obeys=obeys,
       reads_owner_rules="--ignore-rules" not in a)
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
        self.claude_home = self.tmp / "claude-home"  # the suite never reads the real ~/.claude/settings.json
        self.env = dict(os.environ, CREW_HOME=str(self.home), FAKE_LOG=str(self.log), CREW_ALLOW_TEMP_HOME="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t", CLAUDE_CONFIG_DIR=str(self.claude_home))
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
        if "this is a defect in crew" in r.stdout + r.stderr:  # main()'s last resort: no test may reach it
            self.fail(f"crew {args} hit an error no check foresaw\n{r.stdout}\n{r.stderr}")
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

    def test_a_second_settings_flag_cannot_drop_the_sandbox(self):
        """Claude reads only the last --settings on its command line. crew 3.4 put its sandbox rules into the
        first one and decided the shell grant by the first one, so a second --settings ran every worker's
        shell with no sandbox."""
        root = self.repo()

        def read_by_claude(argv):  # the settings the CLI uses: the value of the last --settings
            return json.loads(argv[len(argv) - 1 - argv[::-1].index("--settings") + 1])

        # 1. Two --settings, neither says a word about the sandbox: it must not be lost.
        self.write_cfg({"providers": {"codex": {"enabled": False},
                                      "claude": {"args": ["--settings", '{"env": {"A": "1"}}',
                                                          '--settings={"env": {"B": "2"}}']}}})
        r = self.crew("do", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first = self.workers()
        self.assertTrue(first)
        for c in first:
            a = c["argv"]
            settings = read_by_claude(a)
            self.assertEqual(settings["env"], {"B": "2"})  # the one claude would have read anyway
            sandbox = settings.get("sandbox") or {}
            self.assertIs(sandbox.get("enabled"), True, a)
            self.assertIs(sandbox.get("allowUnsandboxedCommands"), False)
            deny = sandbox["filesystem"]["denyWrite"]
            self.assertIn(str(root), deny)  # your checkout
            self.assertTrue(any(d.endswith("/.git") for d in deny))  # the copy's git directory
            self.assertEqual(a[a.index("--allowedTools") + 1], "Bash")
            self.assertEqual(a.count("--settings"), 1)  # one reaches claude: the one crew checked

        # 2. The last one turns the sandbox off: that choice is kept, and then there is no unprompted shell.
        self.write_cfg({"providers": {"codex": {"enabled": False}, "claude": {"args": [
            "--settings", '{"sandbox": {"enabled": true, "allowUnsandboxedCommands": false}}',
            "--settings", '{"sandbox": {"enabled": false, "allowUnsandboxedCommands": true}}']}}})
        r = self.crew("do", "y", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        second = self.workers()[len(first):]
        self.assertTrue(second)
        for c in second:
            self.assertIs(read_by_claude(c["argv"])["sandbox"]["enabled"], False)
            self.assertNotIn("--allowedTools", c["argv"])

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


class TestThreeSeven(CrewTest):
    """Stops that were missed, plans that could not be found, and values no check foresaw (3.4 review)."""

    def workers(self):
        return [c for c in self.calls() if "argv" in c and not c.get("planner")]

    def plan_file(self) -> Path:
        return max((self.home / "plans").glob("*/*.json"), key=lambda f: f.stat().st_mtime)

    def crew_with_no_reader(self, *args, env=None) -> int:
        """crew's exit code when whatever was reading its output has gone away (`crew ... | head`)."""
        r, w = os.pipe()
        os.close(r)
        try:
            return subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), *args], stdout=w,
                                  stderr=subprocess.DEVNULL, env={**self.env, **(env or {})}, timeout=120).returncode
        finally:
            os.close(w)

    def files_on(self, root, branch):
        return sh(root, "git", "ls-tree", "-r", "--name-only", branch).split()

    def agents_alive(self) -> list[int]:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "args="], capture_output=True, text=True).stdout
        return [int(ln.split()[0]) for ln in out.splitlines() if f"{self.bin}/" in ln]

    def paused(self, root, **kw) -> Path:
        self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False, **kw)
        return self.plan_file()

    # ---------------------------------------------------------------- stopping

    def test_a_stop_signal_while_a_queue_worker_starts_an_agent_is_acted_on(self):
        import signal
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--workdir", str(a), "--workdir", str(b))
        # crew.py is unchanged; only starting a process in a worker thread takes longer, as on a loaded machine.
        slow = self.tmp / "slow_start.py"
        slow.write_text(textwrap.dedent("""
            import runpy, subprocess, sys, threading, time
            real = subprocess.Popen
            class SlowStart(real):
                def __init__(self, *a, **kw):
                    super().__init__(*a, **kw)
                    if kw.get("start_new_session") and threading.current_thread() is not threading.main_thread():
                        time.sleep(1.5)
            subprocess.Popen = SlowStart
            sys.argv = sys.argv[1:]
            runpy.run_path(sys.argv[0], run_name="__main__")
        """))
        run = subprocess.Popen(
            [sys.executable, str(slow), str(HERE / "crew.py"), "--config", str(self.cfg), "queue", "run"],
            env={**self.env, "FAKE_SLEEP": "20"}, stdout=subprocess.PIPE, text=True)
        try:
            for line in run.stdout:
                if "[t1]" in line:
                    break
            time.sleep(0.5)  # a worker thread has started its agent and not yet put it on record
            run.send_signal(signal.SIGTERM)
            try:
                run.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                self.fail("crew did not stop: it is waiting for an agent it never killed, and ignores signals now")
            self.assertEqual(run.returncode, 143)
            self.assertEqual(self.agents_alive(), [])  # the agent that was being started was killed too
        finally:
            if run.poll() is None:
                run.kill()
            for pid in self.agents_alive():
                os.kill(pid, signal.SIGKILL)
        items = json.loads((self.home / "queue.json").read_text())["items"]
        self.assertEqual({i["status"] for i in items}, {"paused"})

    def test_a_stop_signal_behind_an_ignored_one_is_not_lost(self):
        code = textwrap.dedent(f"""
            import os, signal, sys, time
            sys.path.insert(0, {str(HERE)!r})
            import crew
            signal.signal(signal.SIGINT, signal.SIG_IGN)               # crew started by `sh -c 'crew ... &'`
            signal.signal(signal.SIGTERM, lambda n, f: sys.exit(143))  # what crew's own handler does
            with crew._signals_held():                                 # e.g. while an agent is being killed
                os.kill(os.getpid(), signal.SIGINT)
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(0.2)
            print("carried on")
        """)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 143, r.stdout + r.stderr)  # the SIGTERM took effect when the block ended
        self.assertNotIn("carried on", r.stdout)

    def test_a_stop_signal_that_a_queue_worker_thread_receives_is_acted_on(self):
        import ctypes
        import platform
        import signal
        tgkill = {"x86_64": 234, "aarch64": 131}.get(platform.machine()) if sys.platform.startswith("linux") else None
        if tgkill is None:
            self.skipTest("needs tgkill(2) to choose the thread a signal goes to")
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--agent", "Sol", "--workdir", str(a))  # goes to codex
        self.crew("queue", "add", "do it", "--direct", "--agent", "Astra", "--workdir", str(b))  # to claude
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "queue", "run"],
            env={**self.env, "FAKE_SLEEP": "20"}, stdout=subprocess.PIPE, text=True)
        try:
            for _ in range(100):
                if len(self.workers()) == 2:  # both at work, so no task ends by itself for 20 s
                    break
                time.sleep(0.1)
            else:
                self.fail("the two agents did not start side by side")
            # The system gives a signal to a worker thread when that thread has just started a child and
            # reaches the signal before the main thread wakes (seen under load). Done on purpose here.
            thread = next(int(t) for t in os.listdir(f"/proc/{run.pid}/task") if int(t) != run.pid)
            self.assertEqual(ctypes.CDLL(None).syscall(tgkill, run.pid, thread, int(signal.SIGTERM)), 0)
            try:
                run.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                self.fail("crew did not stop: its main thread is asleep until a task ends by itself")
            self.assertEqual(run.returncode, 143)
            self.assertEqual(self.agents_alive(), [])
        finally:
            if run.poll() is None:
                run.kill()
            for pid in self.agents_alive():
                os.kill(pid, signal.SIGKILL)

    def test_two_different_stop_signals_at_once_give_no_traceback(self):
        import signal
        root = self.repo()
        run = subprocess.Popen(
            [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "do", "x", "--workdir", str(root)],
            env={**self.env, "FAKE_SLEEP": "30"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                if self.workers():
                    break
                time.sleep(0.1)
            else:
                self.fail("no worker started")
            worker_pid = self.workers()[-1]["pid"]
            os.kill(run.pid, signal.SIGSTOP)  # so that both are waiting when crew next runs, whatever the timing
            time.sleep(0.2)
            os.kill(run.pid, signal.SIGHUP)
            os.kill(run.pid, signal.SIGTERM)
            os.kill(run.pid, signal.SIGCONT)
            out, err = run.communicate(timeout=40)
        finally:
            if run.poll() is None:
                run.kill()
        self.assertNotIn("Traceback", err)
        self.assertNotIn("race condition", err)
        self.assertIn(run.returncode, (129, 143))
        with self.assertRaises(ProcessLookupError):
            os.kill(worker_pid, 0)  # and the stop itself was complete

    def test_crew_in_the_background_of_a_terminal_is_not_stopped_by_its_probes(self):
        try:
            import pty
            master, slave = pty.openpty()
        except (ImportError, OSError) as e:
            self.skipTest(f"no pseudo-terminal here: {e}")
        root = self.repo()
        # What an interactive shell does for `crew ... &`: this process leads the terminal's session and stays
        # in the foreground; crew is a process group of its own (a background job) with the terminal as stdin.
        job = self.tmp / "background_job.py"
        job.write_text(textwrap.dedent("""
            import fcntl, os, signal, subprocess, sys, termios
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            crew = subprocess.Popen(sys.argv[1:], preexec_fn=lambda: os.setpgid(0, 0))
            _, status = os.waitpid(crew.pid, os.WUNTRACED)
            if os.WIFSTOPPED(status):
                print("STOPPED by signal", os.WSTOPSIG(status), flush=True)
                os.killpg(crew.pid, signal.SIGKILL)
                sys.exit(99)
            sys.exit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 98)
        """))
        try:
            r = subprocess.run(
                [sys.executable, str(job), sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg),
                 "do", "x", "--workdir", str(root)],
                stdin=slave, capture_output=True, text=True, start_new_session=True, timeout=120,
                env={**self.env, "FAKE_PROBE_READS_TERMINAL": "1"})
        finally:
            os.close(master)
            os.close(slave)
        self.assertNotIn("STOPPED", r.stdout, r.stdout + r.stderr)  # job control stopped crew at `claude auth status`
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.latest_plan(root)["tasks"][0]["status"], "done")

    def test_no_process_crew_starts_is_given_the_terminal(self):
        import ast
        tree = ast.parse((HERE / "crew.py").read_text())
        starts = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
                  and n.func.attr in ("run", "Popen", "call", "check_call", "check_output")]
        self.assertGreater(len(starts), 4)  # git (twice), ps (twice), taskkill and _exec's Popen; probes use _exec
        bare = [n.lineno for n in starts if not {"stdin", "input"} & {k.arg for k in n.keywords}]
        self.assertEqual(bare, [], "crew.py starts a process at these lines without stdin= or input=")
        self.assertNotIn("os.system(", (HERE / "crew.py").read_text())

    def test_a_run_whose_output_reader_went_away_does_not_exit_0(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        plan = self.plan_file()
        rc = self.crew_with_no_reader("run", str(plan))
        self.assertNotEqual(crew.plan_state(json.loads(plan.read_text())), "done")
        self.assertEqual(rc, 141)  # 128 + SIGPIPE, never 0: exit 0 is for a plan that is done
        self.assertEqual(self.agents_alive(), [])
        r = self.crew("run", str(plan))  # and nothing was left in the way of a resume
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.crew_with_no_reader("plans", "--all"), 0)  # a listing cut short is still exit 0

    # ---------------------------------------------------------------- finding a checkout's plans

    def test_plans_and_profile_are_still_found_after_the_remote_changes(self):
        for n, change in enumerate((("set-url", "origin", "git@github.com:me/renamed.git"),
                                    ("rename", "origin", "upstream"), ("remove", "origin"))):
            with self.subTest(change=change):
                root = self.repo(f"proj{n}")
                sh(root, "git", "remote", "add", "origin", f"git@github.com:me/proj{n}.git")
                self.set_profile(root, notes=["MY EDIT"])
                plan = self.paused(root)
                n0 = len(self.workers())
                sh(root, "git", "remote", *change)
                r = self.crew("plans", "--workdir", str(root))
                self.assertIn(str(plan), r.stdout)  # this checkout's plan, under the name it had
                self.assertIn(f"`crew run {plan}` continues it", self.crew("clean").stdout)
                shown = self.crew("config", "--workdir", str(root)).stdout
                self.assertIn("MY EDIT", shown)  # the profile that was edited for this checkout came along
                r = self.crew("run", "--workdir", str(root))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"resuming {plan}", r.stdout)
                self.assertEqual(len(self.workers()), n0 + 1)  # t2 only: t1 was not repeated
                done = json.loads(plan.read_text())
                self.assertEqual(sorted(self.files_on(root, done["git"]["branch"])), ["README.md", "t1.txt", "t2.txt"])

    def test_run_by_path_follows_a_plan_that_moved_when_a_remote_was_added(self):
        root = self.repo()
        old = self.paused(root)
        sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
        r = self.crew("run", str(old), check=False)  # this very command moves the file it was given
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        new = self.plan_file()
        self.assertNotEqual(new, old)
        self.assertIn(f"is now {new}", r.stdout)
        self.assertTrue(all(t["status"] == "done" for t in json.loads(new.read_text())["tasks"]))
        r = self.crew("run", str(old), check=False)  # and the path printed earlier keeps working afterwards
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to do", r.stdout)

    def test_a_stale_copy_of_a_finished_plan_is_refused(self):
        root = self.repo()
        original = self.paused(root)
        stale = self.tmp / "mine" / "plan-copy.json"
        stale.parent.mkdir()
        shutil.copy(original, stale)  # made while the plan was paused
        r = self.crew("run", str(original))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the plan finishes; its copy and pointer go
        self.crew("clean", "--all")
        n0 = len(self.workers())
        branch = json.loads(original.read_text())["git"]["branch"]
        tip = sh(root, "git", "rev-parse", branch)
        r = self.crew("run", str(stale), check=False)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("belongs to another plan", r.stdout)
        self.assertEqual(len(self.workers()), n0)  # no task was done a second time on the finished branch
        self.assertEqual(sh(root, "git", "rev-parse", branch), tip)
        original.unlink()  # once the original is gone, the file is nobody's copy
        r = self.crew("run", str(stale))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ---------------------------------------------------------------- values no check foresaw

    def test_out_of_range_and_wrong_typed_values_are_errors_not_tracebacks(self):
        root = self.repo()

        def no_traceback(r, what, codes=(0, 1, 2)):
            self.assertNotIn("Traceback", r.stdout + r.stderr, what)
            self.assertIn(r.returncode, codes, f"{what}\n{r.stdout}{r.stderr}")
            return r

        def cfg_raw(extra, literal):  # 1e999 is valid JSON, and json.loads reads it as infinity
            self.write_cfg(extra)
            self.cfg.write_text(self.cfg.read_text().replace('"@"', literal))

        for extra, literal in (({"providers": {"claude": {"max_parallel": "@"}}}, "1e999"),
                               ({"providers": {"codex": {"timeout_sec": "@"}}}, "1e999"),
                               ({"verify": {"timeout_sec": "@"}}, "1e12"),
                               ({"verify": {"fix_rounds": "@"}}, "1e999"),
                               ({"split": {"min_runs": "@"}}, "1e999"),
                               ({"split": {"cooldown_minutes": "@"}}, "NaN"),
                               ({"planner": {"max_tasks": "@"}}, "1" + "0" * 400),
                               ({"git": {"branch_prefix": "@"}}, "true")):
            cfg_raw(extra, literal)
            r = no_traceback(self.crew("do", "x", "--workdir", str(root), check=False), f"{extra} = {literal}", (2,))
            self.assertIn("error: config: ", r.stdout)
        cfg_raw({"verify": {"fixer": "@"}}, "[]")  # not an agent's name: the first agent fixes, as for a wrong name
        no_traceback(self.crew("usage"), "verify.fixer = []", (0,))
        self.write_cfg({})
        self.assertEqual(self.workers(), [])  # none of those ran anything

        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "state.json").write_text('{"cooldown_until": {"claude": 1e300, "codex": 1e999}}')
        (self.home / "usage.jsonl").write_text(
            '{"ts": %f, "provider": "claude", "tokens": 1e999, "seconds": 1e999, "ok": true, "project": "p"}\n'
            % time.time())
        no_traceback(self.crew("usage"), "state and usage log", (0,))
        (self.home / "state.json").unlink()
        (self.home / "queue.json").write_text('{"items": null, "next_id": 1e999}')
        r = no_traceback(self.crew("queue", "list"), "queue items = null", (0,))
        self.assertIn("not a list", r.stdout)
        no_traceback(self.crew("queue", "add", "x", "--workdir", str(root)), "queue next_id = 1e999", (0,))
        (self.home / "queue.json").unlink()

        plan = self.paused(root)  # t1's work is in the copy, its commit not made yet
        good = plan.read_text()
        n0 = len(self.workers())
        edits = {"committing = true": lambda p: p["tasks"][0].update(committing=True),
                 "committing = {}": lambda p: p["tasks"][0].update(committing={"a": 1}),
                 "commit = 5": lambda p: p["tasks"][0].update(commit=5),
                 "untracked_before = true": lambda p: p["tasks"][0].update(untracked_before=True),
                 "untracked_before.untracked = true": lambda p: p["tasks"][0]["untracked_before"].update(untracked=True),
                 "status removed": lambda p: p["tasks"][0].pop("status"),
                 "status = []": lambda p: p["tasks"][0].update(status=[]),
                 "git.untracked_base = true": lambda p: p["git"].update(untracked_base=True),
                 "git.tip = true": lambda p: p["git"].update(tip=True)}
        for what, edit in edits.items():
            p = json.loads(good)
            edit(p)
            plan.write_text(json.dumps(p))
            r = no_traceback(self.crew("run", str(plan), check=False), what, (2,))
            self.assertIn("error: ", r.stdout, what)
        self.assertEqual(len(self.workers()), n0)  # refused before anything ran
        plan.write_text(good)
        r = self.crew("run", str(plan))  # and the plan itself was not harmed by being refused
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_an_error_no_check_foresaw_is_one_line_and_stops_cleanly(self):
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(HERE)!r})
            import crew
            def broken(*a, **kw):
                raise RuntimeError("boom")
            crew.print_split = broken
            sys.exit(crew.main(["--config", {str(self.cfg)!r}, "usage"]))
        """)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("error: RuntimeError: boom", r.stdout)
        self.assertIn("this is a defect in crew", r.stdout)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                           env={**self.env, "CREW_TRACEBACK": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Traceback", r.stderr)  # on request, the whole trace

    def test_a_queue_item_without_an_absolute_workdir_is_not_run(self):
        started_in = self.tmp / "somewhere"
        started_in.mkdir()
        self.home.mkdir(parents=True, exist_ok=True)
        for workdir in ("", "somewhere", "."):
            (self.home / "queue.json").write_text(json.dumps({"next_id": 2, "items": [
                {"id": "q1", "key": "k", "name": "n", "workdir": workdir, "goal": "do it", "status": "queued",
                 "plan": None}]}))
            r = subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "queue", "run"],
                               capture_output=True, text=True, env=self.env, cwd=started_in, timeout=120)
            self.assertIn("malformed", r.stdout, workdir)
            self.assertEqual(self.calls(), [], workdir)  # no planner, no agent
            self.assertEqual(os.listdir(started_in), [], workdir)  # nothing was done where crew happened to start


class TestOnlyTheOwnersOwnSettings(CrewTest):
    """What the 3.7 review found (crew 3.8): a worker obeyed settings an earlier agent left in crew's copy,
    and settings of the owner's own that take a command out of the sandbox."""

    def workers(self, p=None):
        return [c for c in self.calls() if "argv" in c and not c.get("planner") and p in (None, c["p"])]

    def two_step_plan(self):
        """t1 for Astra (Claude), t2 for Sol (Codex)."""
        plan = self.tmp / "fake-plan.json"
        plan.write_text(json.dumps({"summary": "two", "tasks": [
            {"id": "t1", "title": "first", "agent": "Astra", "instructions": "a", "acceptance": "b", "depends_on": []},
            {"id": "t2", "title": "second", "agent": "Sol", "instructions": "a", "acceptance": "b",
             "depends_on": ["t1"]}]}))
        return str(plan)

    def claude_then_codex(self, root, planted=None, cfg=None):
        """Plan with Codex, then t1 on Claude and t2 on Codex in crew's copy; the fake agents leave the
        `planted` files in the copy as they work. Returns (the two Codex calls, the checks' sandbox calls)."""
        self.set_profile(root, checks={"ok": "true"}, setup=[])
        self.write_cfg(crew.deep_merge({"split": {"mode": "fixed"}, "planner": {"provider": "codex"}}, cfg or {}))
        r = self.crew("go", "x", "--workdir", str(root),
                      env={"FAKE_PLAN": self.two_step_plan(), "FAKE_EDIT": json.dumps(planted or {})})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c["p"] for c in self.workers()], ["claude", "codex"])
        codex = [c for c in self.calls() if c["p"] == "codex" and "argv" in c]
        self.assertEqual([c["planner"] for c in codex], [True, False])  # the planner, then the second task
        self.assertNotEqual(Path(codex[1]["cwd"]), root)  # which worked in crew's copy, after Claude
        checks = [c for c in self.calls() if c.get("sandbox") and c["cmd"][-1] == "true"]
        self.assertTrue(checks)
        return codex, checks

    def test_codex_is_started_without_execpolicy_rules(self):
        """An `allow` rule in a Codex rules file runs the matching command outside the sandbox. The real CLI
        obeyed the owner's own rules in every worker and in the read-only planner (the README suggests one
        that allows `crew`), and a rules file an earlier agent left in crew's copy."""
        root = self.repo()
        rules = {".codex/rules/default.rules": 'prefix_rule(pattern = ["sh"], decision = "allow")\n'}
        codex, _ = self.claude_then_codex(root, rules)
        for c in codex:
            self.assertFalse(c["reads_owner_rules"], c["argv"])
            self.assertEqual(c["obeys"], [], c["argv"])  # nor the file Claude's task left in the copy
            self.assertEqual(c["argv"].count("--ignore-rules"), 1)
            self.assertEqual(c["argv"][-1], "-")  # the prompt still comes on stdin, after every flag
        # a flag the owner already passes is not given twice (the real CLI refuses a repeated --ignore-rules)
        self.write_cfg({"providers": {"claude": {"enabled": False}, "codex": {"args": ["--ignore-rules"]}}})
        self.crew("do", "y", "--workdir", str(root))
        a = self.workers("codex")[-1]["argv"]
        self.assertEqual(a.count("--ignore-rules"), 1)
        self.assertEqual(a[a.index("--sandbox") + 1], "workspace-write")

    def test_codex_reads_no_settings_an_earlier_agent_left_in_the_copy(self):
        """A Claude worker can write `.codex/config.toml` in crew's copy; crew never commits or shows it. The
        real Codex CLI trusts the directory it is started in and obeyed that file in the next task: it started
        the tool server the file named, outside any sandbox, and widened its own sandbox from it."""
        root = self.repo()
        planted = {".codex/config.toml": '[mcp_servers.x]\ncommand = "sh"\n[sandbox_workspace_write]\n'
                                         'writable_roots = ["/"]\n'}
        codex, checks = self.claude_then_codex(root, planted)
        self.assertEqual(codex[1]["obeys"], [], codex[1]["argv"])
        for c in checks:  # the sandbox that runs the checks reads that file too, once the copy is trusted
            self.assertFalse(c["reads_project_config"], c["flags"])
        self.assertEqual(sh(root, "git", "status", "--porcelain").strip(), "")  # none of it is in your checkout
        self.assertNotIn(".codex", sh(root, "git", "ls-tree", "-r", "--name-only",
                                      self.latest_plan(root)["git"]["branch"]))  # nor on the branch you review

    def test_the_task_guard_variable_reaches_a_codex_workers_shell(self):
        """crew refuses to start inside its own task by a variable in the worker's environment. Codex hands
        its commands only what the owner's `shell_environment_policy` lets through: with `inherit = "core"`
        the variable never reached a worker's shell, or a check."""
        root = self.repo()
        codex, checks = self.claude_then_codex(root)
        for flags in [c["argv"] for c in codex] + [c["flags"] for c in checks]:
            at = [i for i, x in enumerate(flags) if x == 'shell_environment_policy.set.CREW_TASK="1"']
            self.assertEqual(len(at), 1, flags)
            self.assertEqual(flags[at[0] - 1], "-c")

    def test_codex_sandbox_written_as_one_word_is_kept(self):
        """`-sread-only` and `-s=read-only` are the same flag as `-s read-only`. crew didn't see them, added
        its own --sandbox, and the real CLI refused every Codex task ("cannot be used multiple times")."""
        root = self.repo()
        for own in ("-sread-only", "-s=read-only"):
            self.write_cfg({"providers": {"claude": {"enabled": False}, "codex": {"args": [own]}}})
            self.crew("do", "x", "--workdir", str(root))
            a = self.workers("codex")[-1]["argv"]
            self.assertIn(own, a)
            self.assertNotIn("--sandbox", a)

    def claude_argv(self, root, settings=None, cfg=None, env=None):
        self.write_cfg({"providers": {"codex": {"enabled": False}, **({"claude": cfg} if cfg else {})}})
        if settings is not None:
            self.claude_home.mkdir(exist_ok=True)
            (self.claude_home / "settings.json").write_text(settings)
        n = len(self.workers("claude"))
        r = self.crew("do", "x", "--workdir", str(root), env=env)
        new = self.workers("claude")[n:]
        self.assertEqual(len(new), 1, r.stdout + r.stderr)
        return new[0]["argv"], r.stdout

    def path_without_bubblewrap(self):
        """The same PATH with no bwrap and no socat on it: Claude's sandbox can't start."""
        d = self.tmp / "bin-no-sandbox"
        d.mkdir(exist_ok=True)
        for folder in reversed(os.environ.get("PATH", "").split(os.pathsep)):
            for f in (os.listdir(folder) if os.path.isdir(folder) else []):
                if f not in ("bwrap", "socat"):
                    (d / f).unlink(missing_ok=True)
                    (d / f).symlink_to(os.path.join(folder, f))
        return str(d)

    def assert_no_shell(self, argv):
        self.assertNotIn("--allowedTools", argv)
        self.assertIn("--disallowedTools", argv)  # not offered at all, so nothing of the owner's can allow it
        self.assertEqual(argv[argv.index("--disallowedTools") + 1], "Bash")

    def test_commands_your_claude_settings_take_out_of_the_sandbox_cost_the_shell(self):
        """crew allows Claude every shell command because each one runs in the sandbox. Not the ones in
        `sandbox.excludedCommands`: Claude merges that list from the owner's own settings into crew's, such
        a command asks before it runs unsandboxed, and crew's blanket permission was the answer."""
        root = self.repo()
        a, out = self.claude_argv(root, settings="{}")
        self.assertEqual(a[a.index("--allowedTools") + 1], "Bash")  # the ordinary case keeps its shell
        self.assertNotIn("--disallowedTools", a)
        self.assertNotIn("no shell", out)

        a, out = self.claude_argv(root, settings='{"sandbox": {"excludedCommands": ["docker"]}}')
        self.assert_no_shell(a)
        self.assertIn("Claude workers get no shell", out)
        self.assertIn(str(self.claude_home / "settings.json"), out)  # and says which file to look in
        self.assertIn("Claude workers get no shell", self.crew("doctor", check=False).stdout)

        a, out = self.claude_argv(root, settings="{not json")  # crew can't tell what it holds
        self.assert_no_shell(a)
        self.assertIn("can't read", out)

        a, _ = self.claude_argv(root, settings="{}", cfg={"args": [
            "--settings", '{"sandbox": {"excludedCommands": ["docker"]}}']})
        self.assert_no_shell(a)  # the same key in your own worker flags

    def test_without_a_sandbox_claude_has_no_shell_at_all(self):
        """When Claude's sandbox can't start crew left out its permission for Bash and said shell commands
        were refused. The owner's own allow rules still ran them, with no sandbox: `Bash(crew *)`, which the
        README suggests, let a worker put a goal into the owner's queue."""
        root = self.repo()
        no_sandbox = {"PATH": self.path_without_bubblewrap()}
        a, out = self.claude_argv(root, settings='{"permissions": {"allow": ["Bash(crew *)"]}}', env=no_sandbox)
        self.assert_no_shell(a)
        self.assertIn("Claude workers get no shell", out)
        r = self.crew("doctor", env=no_sandbox, check=False)
        self.assertIn("claude sandbox: NOT available, so Claude workers get no shell", r.stdout)

    def test_sandboxed_bash_false_means_no_shell(self):
        """`providers.claude.sandboxed_bash: false` only left out crew's permission; the settings crew passes
        still allowed every sandboxed command, so the real CLI ran them as before."""
        root = self.repo()
        a, _ = self.claude_argv(root, settings="{}", cfg={"sandboxed_bash": False})
        self.assert_no_shell(a)

    @staticmethod
    def as_claude_reads_it(argv):
        """{flag: value} the way the real CLI pairs them: a flag that takes a value takes the next token,
        whatever it is; --allowedTools and --disallowedTools take every token up to the next flag."""
        takes_one = {"--output-format", "--setting-sources", "--model", "--json-schema", "--permission-mode",
                     "--settings", "--append-system-prompt", "--system-prompt", "--tools"}
        read, i = {}, 0
        while i < len(argv):
            flag = argv[i]
            if flag in takes_one and i + 1 < len(argv):
                read[flag] = argv[i + 1]
                i += 2
            elif flag in ("--allowedTools", "--disallowedTools"):
                j = i + 1
                while j < len(argv) and not argv[j].startswith("-"):
                    j += 1
                read[flag] = argv[i + 1:j]
                i = j
            else:
                read[flag] = True
                i += 1
        return read

    def test_a_flag_without_its_value_cannot_swallow_the_sandbox(self):
        """crew put its --settings and --allowedTools after the configured flags. A flag there that lacked
        its value (`--append-system-prompt` at the end) took crew's `--settings` for it: the worker ran with
        no sandbox settings at all and with Bash allowed, and wrote outside its copy and into .git. A
        `--settings` with no value made the CLI load a file named `--settings` from the working tree."""
        root = self.repo()
        a, _ = self.claude_argv(root, settings="{}", cfg={"args": ["--verbose", "--append-system-prompt"]})
        read = self.as_claude_reads_it(a)
        self.assertIsInstance(read.get("--settings"), str, a)  # not taken as the other flag's value
        sandbox = json.loads(read["--settings"])["sandbox"]  # still the settings the CLI reads
        self.assertIs(sandbox["enabled"], True)
        self.assertIs(sandbox["allowUnsandboxedCommands"], False)
        self.assertTrue(any(d.endswith("/.git") for d in sandbox["filesystem"]["denyWrite"]))
        self.assertEqual(read["--permission-mode"], "acceptEdits")
        self.assertEqual(read["--allowedTools"], ["Bash"])
        self.assertEqual(a[-1], "--append-system-prompt")  # left without a value: the CLI's own error, not crew's flags

        for dangling in (["--settings"], ["--settings", "--verbose"]):
            n = len(self.workers())
            self.write_cfg({"providers": {"codex": {"enabled": False}, "claude": {"args": dangling}}})
            r = self.crew("do", "x", "--workdir", str(root), check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--settings has no value", r.stdout)
            self.assertEqual(len(self.workers()), n, dangling)  # no worker was started with it

    def test_a_switch_in_quotes_is_an_error_not_true(self):
        """`"unsandboxed": "false"` is a string, and a string is true: with Codex's sandbox unusable the checks,
        which run code the agents edited, were run with no sandbox. The other switches read the same way."""
        root = self.repo()
        marker = self.tmp / "check-ran"
        self.set_profile(root, checks={"guard": f"touch {marker}"}, setup=[])
        self.write_cfg({"verify": {"unsandboxed": "false"}})
        r = self.crew("do", "x", "--workdir", str(root), env={"FAKE_HELP_MISSING": "--permission-profile"},
                      check=False)
        self.assertFalse(marker.exists(), r.stdout)  # the check never ran unsandboxed
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("verify.unsandboxed must be true or false", r.stdout)
        self.assertEqual(self.workers(), [])
        for sec, key in (("verify", "enabled"), ("setup", "enabled"), ("git", "enabled"),
                         ("split", "fallback_on_error")):
            self.write_cfg({sec: {key: "false"}})
            r = self.crew("config", "--workdir", str(root), check=False)
            self.assertEqual(r.returncode, 2, (sec, key))
            self.assertIn(f"{sec}.{key} must be true or false", r.stdout)
        for prov, key in (("codex", "enabled"), ("claude", "sandboxed_bash")):
            self.write_cfg({"providers": {prov: {key: "false"}}})
            r = self.crew("config", "--workdir", str(root), check=False)
            self.assertEqual(r.returncode, 2, (prov, key))
            self.assertIn(f"providers.{prov}.{key} must be true or false", r.stdout)
        self.write_cfg({"verify": {"unsandboxed": True}, "providers": {"codex": {"enabled": False}}})
        self.assertEqual(self.crew("config", "--workdir", str(root)).returncode, 0)  # real booleans still load

    def test_a_misspelt_setting_one_level_down_is_reported(self):
        """Every setting but a section's name sits one level down, and crew only looked at the top:
        `"sandbox_bash": false` under providers.claude left Claude its shell without a word."""
        root = self.repo()
        self.write_cfg({"verify": {"unsandboxd": True}, "setup": {"enable": False},
                        "providers": {"claude": {"sandbox_bash": False}, "claud": {"enabled": False},
                                      "codex": {"sandboxed_bash": False}},
                        "agents": {"Sol": {"role": "Builder.", "prefer": "codex"}}, "split": {"_note": "mine"}})
        r = self.crew("usage")
        for name in ("verify.unsandboxd", "setup.enable", "providers.claude.sandbox_bash", "providers.claud",
                     "providers.codex.sandboxed_bash", "agents.Sol.prefer"):
            self.assertIn(f"no setting named {name!r}", r.stdout)
        self.assertNotIn("_note", r.stdout)
        self.cfg.write_text(json.dumps(crew.deep_merge(crew.DEFAULTS, {"providers": {
            "claude": {"cmd": str(self.bin / "claude")}, "codex": {"cmd": str(self.bin / "codex")}}})))
        self.assertNotIn("no setting named", self.crew("usage").stdout)  # every real setting passes
        self.crew("init", "--workdir", str(root))
        key, _ = crew.project_identity(root)
        path = self.home / "projects" / f"{key}.json"
        prof = json.loads(path.read_text())
        prof["override"] = {"verify": {"fix_rounds": 2}}  # singular: not read
        prof["project"]["check"] = {"test": "false"}
        path.write_text(json.dumps(prof))
        r = self.crew("config", "--workdir", str(root))
        self.assertIn("no setting named 'override'", r.stdout)
        self.assertIn("no setting named 'project.check'", r.stdout)
        self.assertNotIn("'project.checks'", r.stdout)

    def test_doctor_and_codex_login_hand_each_cli_only_its_own_keys(self):
        """`crew doctor` asked each CLI for its version, and `crew codex-login` logged Codex in, with the
        other provider's keys in the environment."""
        keys = {"ANTHROPIC_API_KEY": "a", "ANTHROPIC_AUTH_TOKEN": "b", "CLAUDE_CODE_OAUTH_TOKEN": "c",
                "OPENAI_API_KEY": "d", "CODEX_API_KEY": "e", "CODEX_ACCESS_TOKEN": "f", "FAKE_RECORD_PROBES": "1"}
        self.crew("doctor", env=keys, check=False)
        r = self.crew("codex-login", env=keys)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        probes = [c for c in self.calls() if c.get("probe")]
        self.assertEqual(sorted((c["p"], c["probe"]) for c in probes),
                         [("claude", "version"), ("codex", "login"), ("codex", "version")])
        for c in probes:
            other = crew.FOREIGN_KEYS[c["p"]]
            self.assertEqual([k for k in c["keys"] if k in other], [], (c["p"], c["probe"]))
            self.assertTrue([k for k in c["keys"] if k not in other])  # its own are still there

    def test_a_claude_worker_cannot_read_the_codex_login(self):
        """A sandbox limits writes, not reads: a Claude worker's shell could read ~/.codex/auth.json, and
        what a worker reads goes to its own model provider."""
        root = self.repo()
        codex_home = self.tmp / "codex-home"
        a, _ = self.claude_argv(root, settings="{}", env={"CODEX_HOME": str(codex_home)},
                                cfg={"args": ["--settings", '{"sandbox": {"filesystem": {"denyRead": ["/mine"]}}}']})
        fs = json.loads(a[a.index("--settings") + 1])["sandbox"]["filesystem"]
        self.assertEqual(fs.get("denyRead"), ["/mine", str(codex_home)])  # yours kept, Codex's login store added

    def test_the_skill_tells_a_tool_what_crew_really_does(self):
        """The skill said crew never touches the checkout (a folder that is no git repository is edited in
        place), and told the tool to continue a paused plan with a bare `crew run --workdir`, which resumes
        the newest plan: after a later plan had finished, that printed "nothing to do" and exited 0."""
        root = self.repo()
        self.write_cfg({"providers": {"codex": {"enabled": False}}})
        self.crew("do", "first goal", "--workdir", str(root), env={"FAKE_CLAUDE_LIMIT": "1"}, check=False)
        key, _ = crew.project_identity(root)
        paused = sorted((self.home / "plans" / key).glob("*.json"))[0]
        self.crew("usage", "--clear-cooldowns")
        time.sleep(1.1)  # plan files are named to the second
        self.assertEqual(self.crew("do", "second goal", "--workdir", str(root)).returncode, 0)
        r = self.crew("run", "--workdir", str(root))  # what the skill used to say continues a paused plan
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(crew.plan_state(json.loads(paused.read_text())), "paused")  # it did not
        self.assertNotIn("crew run --workdir", crew.SKILL_MD)
        self.assertIn("crew run <plan file>", crew.SKILL_MD)
        listed = self.crew("plans", "--workdir", str(root)).stdout
        self.assertIn(str(paused), listed)  # where the skill sends the tool for that file
        self.assertEqual(self.crew("run", str(paused)).returncode, 0)
        self.assertEqual(crew.plan_state(json.loads(paused.read_text())), "done")
        folder = self.repo("notes", git=False)
        r = self.crew("do", "x", "--workdir", str(folder))
        self.assertIn("working in place", r.stdout)  # no copy, no branch: the folder itself is edited
        self.assertTrue((folder / "t1.txt").exists())
        self.assertIn("edits it in place", crew.SKILL_MD)


class TestLookupOwnershipInputs(CrewTest):
    """The 3.7 review's confirmed findings about whose plans and profile a checkout gets, one run per plan,
    and what crew reads from its own files. Each test fails on 3.7, except the ones named test_guard_*,
    which pass there and hold a part of an earlier fix that no test held."""

    workers = TestThreeSeven.workers
    plan_file = TestThreeSeven.plan_file
    paused = TestThreeSeven.paused
    files_on = TestThreeSeven.files_on
    planners = TestThreeThree.planners

    def profile_file(self, root) -> Path:
        return self.home / "projects" / f"{crew.project_identity(root)[0]}.json"

    def edit_profile(self, checkout, /, **project_fields):
        path = self.profile_file(checkout)
        prof = json.loads(path.read_text())
        for k, v in project_fields.items():
            if v is None:
                prof["project"].pop(k, None)
            else:
                prof["project"][k] = v
        path.write_text(json.dumps(prof))

    def effective(self, root) -> dict:
        out = self.crew("config", "--workdir", str(root)).stdout
        return json.loads(out[out.index("\n{") if not out.startswith("{") else 0:])

    def branches(self, root):
        return sh(root, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    def crew_in(self, cwd, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), *args], cwd=cwd,
                              capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=120)

    # ---------------------------------------------------------------- whose plans, whose profile

    def test_another_repository_at_a_known_path_inherits_neither_plans_nor_profile(self):
        ran = self.tmp / "setup-ran.txt"

        def first_project(name, origin):
            root = self.repo(name, files={"old.txt": "the first project\n"})
            sh(root, "git", "remote", "add", "origin", origin)
            self.crew("init", "--workdir", str(root))
            self.edit_profile(root, setup=[{"run": f"echo ran >> {ran}"}], checks={"first": "test -f old.txt"})
            return root

        def replaced_by_another(name, origin):
            shutil.rmtree(self.tmp / name)  # the folder goes, and an unrelated repository takes its place
            root = self.repo(name, files={"new.txt": "an unrelated project\n"})
            sh(root, "git", "remote", "add", "origin", origin)
            return root

        # A paused plan and a profile written before crew recorded a repository's first commit.
        old = first_project("site", "git@github.com:acme/first.git")
        self.edit_profile(old, first_commit=None)
        plan = self.paused(old)
        self.assertEqual(ran.read_text(), "ran\n")  # the first project's setup, in its own copy
        n0 = len(self.workers())
        new = replaced_by_another("site", "git@github.com:acme/second.git")
        self.assertNotIn(str(plan), self.crew("plans", "--workdir", str(new)).stdout)  # 3.7 lists it as this checkout's
        r = self.crew("run", "--workdir", str(new), check=False)
        # 3.7: exit 0; t2 runs in the first project's copy and that project's history lands on a crew branch here
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no plans for", r.stdout)
        self.assertIn("is not used for it", r.stdout)  # and says why the profile of that path was left alone
        self.assertEqual(len(self.workers()), n0)
        self.assertEqual(self.branches(new), ["main"])
        self.assertEqual(sh(new, "git", "rev-list", "--all", "--count").strip(), "1")
        r = self.crew("do", "second goal", "--workdir", str(new))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(ran.read_text(), "ran\n")  # 3.7 runs the first project's setup command in the new one's copy
        self.assertEqual(self.effective(new)["project"]["checks"], {})
        self.assertIn(str(plan), self.crew("plans", "--all").stdout)  # the old plan is still there, under its own name
        ran.unlink()

        # No plan at all: the first commit recorded in the profile tells the two repositories apart.
        first_project("shop", "git@github.com:acme/shop.git")
        new = replaced_by_another("shop", "git@github.com:acme/other.git")
        r = self.crew("do", "second goal", "--workdir", str(new))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("is not used for it", r.stdout)
        self.assertFalse(ran.exists())
        self.assertEqual(self.effective(new)["project"]["checks"], {})

    def test_a_second_checkout_keeps_the_shared_profile_when_its_origin_changes(self):
        a = self.repo("a")
        sh(a, "git", "remote", "add", "origin", "https://github.com/me/app.git")
        b = self.tmp / "b"
        sh(self.tmp, "git", "clone", "-q", str(a), str(b))
        sh(b, "git", "remote", "set-url", "origin", "https://github.com/me/app.git")
        self.crew("init", "--workdir", str(a))
        self.edit_profile(a, notes=["MY EDIT"], checks={"mine": "true"})
        self.assertEqual(self.effective(b)["project"]["notes"], ["MY EDIT"])  # one profile for both checkouts
        sh(b, "git", "remote", "set-url", "origin", "https://github.com/me/renamed.git")
        r = self.crew("config", "--workdir", str(b))
        self.assertIn("was known to crew as me/app", r.stdout)  # 3.7 says nothing ...
        shown = self.effective(b)["project"]
        self.assertEqual((shown["name"], shown["notes"], shown["checks"]), ("me/renamed", ["MY EDIT"], {"mine": "true"}))
        self.assertEqual(shown["root"], str(b))  # ... and starts b again with no notes and no checks
        still = self.effective(a)["project"]
        self.assertEqual((still["name"], still["notes"], still["root"]), ("me/app", ["MY EDIT"], str(a)))

    def test_origin_removed_and_added_back_keeps_the_profile_edited_in_between(self):
        root = self.repo()
        sh(root, "git", "remote", "add", "origin", "https://github.com/me/proj.git")
        self.crew("init", "--workdir", str(root))
        # A profile from before crew recorded a repository's first commit: the folder's plan vouches for it.
        self.edit_profile(root, notes=["edit 1"], first_commit=None)
        self.crew("plan", "x", "--workdir", str(root))
        sh(root, "git", "remote", "remove", "origin")
        self.assertEqual(self.effective(root)["project"]["notes"], ["edit 1"])  # carried to the path's name
        self.edit_profile(root, notes=["edit 2"])  # the profile in effect now, edited while there was no origin
        sh(root, "git", "remote", "add", "origin", "https://github.com/me/proj.git")
        shown = self.effective(root)["project"]
        self.assertEqual(shown["notes"], ["edit 2"])  # 3.7: ["edit 1"], and "moved crew's profile" printed
        self.assertEqual(shown["name"], "me/proj")
        self.assertEqual(sorted(f.name for f in (self.home / "projects").iterdir()), ["me__proj.json"])

        # When the profile at the new name is another checkout's, both are kept, and crew says which is used.
        other = self.repo("other")
        self.crew("init", "--workdir", str(other))
        self.edit_profile(other, notes=["other's own"])
        sh(other, "git", "remote", "add", "origin", "https://github.com/me/proj.git")
        r = self.crew("config", "--workdir", str(other))
        self.assertIn("was set aside as", r.stdout)
        self.assertNotIn("moved crew's profile", r.stdout)  # 3.7 reports a move that did not happen
        self.assertEqual(self.effective(other)["project"]["notes"], ["edit 2"])
        self.assertEqual(len(list((self.home / "projects").glob("*.json.moved"))), 1)

    # ---------------------------------------------------------------- one run per plan

    def test_a_plan_run_through_a_symbolic_link_is_locked_like_the_file_itself(self):
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        plan = self.plan_file()
        link = self.tmp / "todo.json"
        link.symlink_to(plan)
        first = subprocess.Popen([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), "run", str(link)],
                                 env={**self.env, "FAKE_SLEEP": "4"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
        try:
            for _ in range(200):
                if self.workers():
                    break
                time.sleep(0.1)
            else:
                self.fail("no worker started")
            r = self.crew("run", str(link), check=False)
            # 3.7: exit 0. crew's first save replaced the link with a file, whose path names another lock:
            # this second run does both tasks in the first run's copy and removes it.
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("already running", r.stdout)
            self.assertIn("its plan is running", self.crew("clean", "--all").stdout)
            out, _ = first.communicate(timeout=90)
        finally:
            if first.poll() is None:
                first.kill()
        self.assertEqual(first.returncode, 0, out)
        self.assertTrue(link.is_symlink())
        self.assertEqual(crew.plan_state(json.loads(plan.read_text())), "done")  # recorded in the file itself
        self.assertEqual(len(self.workers()), 2)
        self.assertIn("nothing to do", self.crew("run", "--workdir", str(root)).stdout)  # 3.7 does both tasks again

    def test_a_plan_whose_folder_was_moved_and_linked_back_is_not_a_copy_of_itself(self):
        root = self.repo()
        (self.tmp / "myplans").mkdir()
        plan = self.tmp / "myplans" / "job.json"
        self.crew("go", "x", "--workdir", str(root), "--out", str(plan), env={"CREW_CRASH_AT": "before-commit"},
                  check=False)  # paused: t1's work is in the copy, not yet committed
        hard, stale = self.tmp / "hard.json", self.tmp / "stale.json"
        os.link(plan, hard)
        shutil.copy(plan, stale)
        (self.tmp / "archive").mkdir()
        shutil.move(str(self.tmp / "myplans"), str(self.tmp / "archive" / "myplans"))
        (self.tmp / "myplans").symlink_to(self.tmp / "archive" / "myplans")
        n0 = len(self.workers())
        r = self.crew("run", str(hard), check=False)  # another file is still another plan, a hard link too
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("belongs to another plan", r.stdout)
        r = self.crew("run", str(plan), check=False)
        # 3.7: exit 2 for good, "belongs to another plan, <this file>, which this file is a copy of"
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.workers()), n0 + 1)  # t2 only
        done = json.loads(plan.read_text())
        self.assertEqual([t["status"] for t in done["tasks"]], ["done", "done"])
        self.assertEqual(sorted(self.files_on(root, done["git"]["branch"])), ["README.md", "t1.txt", "t2.txt"])
        self.crew("clean", "--all")
        tip = sh(root, "git", "rev-parse", done["git"]["branch"])
        for copy in (stale, hard):  # guard: what was a copy before the move is one after it, the plan finished or not
            r = self.crew("run", str(copy), check=False)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("which this file is a copy of", r.stdout)
        self.assertEqual(len(self.workers()), n0 + 1)
        self.assertEqual(sh(root, "git", "rev-parse", done["git"]["branch"]), tip)

    def test_a_paused_queue_item_is_not_planned_again_after_origin_changed_twice(self):
        root = self.repo()
        self.crew("queue", "add", "the goal", "--workdir", str(root))
        r = self.crew("queue", "run", env={"CREW_CRASH_AT": "before-commit"}, check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
        self.crew("config", "--workdir", str(root))  # any command: the plan moves to the remote's name
        sh(root, "git", "remote", "set-url", "origin", "git@github.com:org/proj.git")
        r = self.crew("queue", "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.planners()), 1)  # 3.7: 2, and t1 is done a second time
        self.assertEqual(len(self.workers()), 2)
        plans = list((self.home / "plans").glob("*/*.json"))
        self.assertEqual(len(plans), 1, plans)  # 3.7 leaves the real plan paused beside a second one
        item = json.loads((self.home / "queue.json").read_text())["items"][0]
        self.assertEqual((item["status"], item["plan"]), ("done", str(plans[0])))

    def test_queue_items_for_one_checkout_run_in_order_whatever_it_was_called_when_queued(self):
        root = self.repo()
        sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
        self.crew("queue", "add", "first goal", "--direct", "--agent", "Sol", "--workdir", str(root))
        sh(root, "git", "remote", "set-url", "origin", "git@github.com:me/renamed.git")
        self.crew("queue", "add", "second goal", "--direct", "--agent", "Astra", "--workdir", str(root))
        r = self.crew("queue", "run", env={"FAKE_SLEEP": "1.5"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("across 1 project(s)", r.stdout)  # 3.7: 2 projects, one for each name
        starts = sorted(c["t"] for c in self.workers())
        self.assertGreater(starts[1] - starts[0], 1.4)  # 3.7: both at once

    # ---------------------------------------------------------------- what crew reads

    def test_a_switch_must_be_true_or_false(self):
        root = self.repo()
        self.set_profile(root, checks={"always": "true"})
        for extra in ({"verify": {"unsandboxed": "false"}}, {"verify": {"enabled": "off"}},
                      {"setup": {"enabled": "false"}}, {"git": {"enabled": "no"}},
                      {"split": {"fallback_on_error": 0}}, {"providers": {"codex": {"enabled": "false"}}},
                      {"providers": {"claude": {"sandboxed_bash": "false"}}}, {"planner": {"provider": "gemini"}}):
            self.write_cfg(extra)
            r = self.crew("usage", check=False)
            self.assertEqual(r.returncode, 2, f"{extra}\n{r.stdout}{r.stderr}")  # 3.7: taken as true, no word said
            self.assertIn("error: config: ", r.stdout, extra)
        self.write_cfg({"verify": {"unsandboxed": "false"}})  # in quotes: a string, and every string is true
        r = self.crew("do", "x", "--workdir", str(root), env={"FAKE_HELP_MISSING": "--permission-profile"}, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("unsandboxed):", r.stdout)  # 3.7: "checks (baseline, unsandboxed):" with no codex sandbox
        self.assertEqual(self.workers(), [])

    def test_an_empty_branch_prefix_is_refused_before_it_makes_a_plan_that_cannot_be_resumed(self):
        root = self.repo()
        self.write_cfg({"git": {"branch_prefix": ""}})
        r = self.crew("go", "x", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False)
        # 3.7 starts (killed here after t1), on a branch without a prefix; every later `crew run` of that plan
        # is then "invalid git block", also after the config is put right
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("git.branch_prefix must be a non-empty string", r.stdout)
        self.assertEqual(self.calls(), [])
        self.write_cfg({"git": {"branch_prefix": "wip/"}})  # a prefix of your own still works, and resumes
        plan = self.paused(root)
        r = self.crew("run", str(plan))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(plan.read_text())["git"]["branch"].startswith("wip/"))

    def test_a_plan_with_a_relative_workdir_is_not_run_where_crew_was_started(self):
        one, two = self.repo("one"), self.repo("two")
        for r in (one, two):
            sh(r, "git", "remote", "add", "origin", f"git@github.com:me/{r.name}.git")
        (self.tmp / "mine").mkdir()
        plan = self.tmp / "mine" / "job.json"
        for workdir in (".", "one"):
            plan.write_text(json.dumps({"goal": "g", "workdir": workdir, "tasks": [
                {"id": "t1", "title": "first", "agent": "Sol", "instructions": "i", "acceptance": "a"}]}))
            for started_in in (one, two, self.tmp):
                r = self.crew_in(started_in, "run", str(plan))
                # 3.7 runs "." in whichever repository the command is typed in, and a paused plan continued
                # from another one delivers the first repository's history into that one. In a repository
                # without a remote it writes a profile with an empty name instead, which crew then rejects.
                self.assertEqual(r.returncode, 2, f"{workdir!r} in {started_in}\n{r.stdout}{r.stderr}")
                self.assertIn("workdir must be an absolute path", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.branches(one), self.branches(two)), (["main"], ["main"]))
        self.assertFalse((self.home / "projects").exists())  # and no project was made of the folder crew started in

    def test_a_recorded_baseline_of_the_wrong_type_is_an_error_not_a_plan_called_done(self):
        root = self.repo()
        self.set_profile(root, checks={"nobroken": "test ! -f broken"})
        self.write_cfg({"verify": {"fix_rounds": 0}})
        plan = self.paused(root)  # the baseline is recorded and t1's work waits in the copy
        good = plan.read_text()
        self.assertIs(json.loads(good)["checks"]["baseline"]["nobroken"]["ok"], True)
        n0 = len(self.workers())
        edits = {"baseline = true": lambda c: c.update(baseline=True),
                 "baseline = []": lambda c: c.update(baseline=[]),
                 "baseline = null": lambda c: c.update(baseline=None),
                 "baseline.nobroken = 'passed'": lambda c: c["baseline"].update(nobroken="passed"),
                 "baseline.nobroken.ok = 0": lambda c: c["baseline"]["nobroken"].update(ok=0),
                 "final = 5": lambda c: c.update(final=5),
                 "regressions = 'none'": lambda c: c.update(regressions="none")}
        for what, edit in edits.items():
            p = json.loads(good)
            edit(p["checks"])
            plan.write_text(json.dumps(p))
            r = self.crew("run", str(plan), env={"FAKE_BREAK": "t2"}, check=False)  # t2 breaks the check
            # 3.7, baseline = true: the checks after the tasks are skipped, the plan is `done`, exit 0
            self.assertEqual(r.returncode, 2, f"{what}\n{r.stdout}{r.stderr}")
            self.assertIn("is not what crew records there", r.stdout, what)
        self.assertEqual(len(self.workers()), n0)  # refused before anything ran
        plan.write_text(good)
        r = self.crew("run", str(plan), env={"FAKE_BREAK": "t2"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # the plan itself is unharmed, and the check counts
        self.assertEqual(json.loads(plan.read_text())["checks"]["regressions"], ["nobroken"])

    def test_a_profile_with_a_relative_root_is_a_clear_error(self):
        root = self.repo()
        sh(root, "git", "remote", "add", "origin", "git@github.com:me/proj.git")
        self.crew("init", "--workdir", str(root))
        self.edit_profile(root, root=".", setup=[{"run": "echo proj-setup"}])
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        r = self.crew_in(elsewhere, "queue", "add", "goal", "--all", "--direct")
        # 3.7: exit 0, "queued q1:"; the profile (its setup commands too) is copied to a name made from the
        # folder crew was started in, which `crew projects` then reports as broken, and the item is dropped
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("project.root must be an absolute path", r.stdout)
        self.assertEqual(sorted(f.name for f in (self.home / "projects").iterdir()), ["me__proj.json"])
        self.assertFalse((self.home / "queue.json").exists())
        self.assertIn("project.root must be an absolute path", self.crew_in(elsewhere, "projects").stdout)
        self.crew("init", "--workdir", str(root), "--force")  # the way out the message names
        self.assertEqual(self.crew_in(elsewhere, "queue", "add", "goal", "--all", "--direct").returncode, 0)
        self.assertEqual(json.loads((self.home / "queue.json").read_text())["items"][0]["workdir"], str(root))

    def test_unknown_settings_inside_a_section_are_reported_too(self):
        root = self.repo()
        self.write_cfg({"verify": {"fix_round": 3, "unsandbox": True}, "setup": {"enable": False},
                        "split": {"tolerence": 0.5}, "planner": {"maxtasks": 2, "_note": "mine"},
                        "providers": {"claude": {"enable": False, "timeout": 5}, "gemini": {"cmd": "gemini"}},
                        "agents": {"Astra": {"role": "does it all", "prefer": "codex"}}})
        r = self.crew("usage")
        for name in ("verify.fix_round", "verify.unsandbox", "setup.enable", "split.tolerence", "planner.maxtasks",
                     "providers.claude.enable", "providers.claude.timeout", "providers.gemini", "agents.Astra.prefer"):
            self.assertIn(f"no setting named {name!r}; it is ignored", r.stdout)  # 3.7 reports none of these
        self.assertEqual(r.stdout.count("no setting named"), 9, r.stdout)  # and no real setting, nor "_note"
        self.write_cfg({})
        self.crew("init", "--workdir", str(root))
        path = self.home / "projects" / f"{crew.project_identity(root)[0]}.json"
        prof = json.loads(path.read_text())
        prof["overrides"] = {"verify": {"fix_rounds": 2, "fixround": 2}}
        path.write_text(json.dumps(prof))
        r = self.crew("config", "--workdir", str(root))
        self.assertIn("this project's overrides: crew has no setting named 'verify.fixround'", r.stdout)
        self.assertNotIn("'verify.fix_rounds'", r.stdout)

    def test_an_error_after_the_reader_of_the_output_went_away_is_not_a_traceback(self):
        root = self.repo()

        def with_no_reader(*args):
            r, w = os.pipe()
            os.close(r)
            try:
                return subprocess.run([sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), *args],
                                      stdout=w, stderr=subprocess.PIPE, text=True, env=self.env, timeout=120)
            finally:
                os.close(w)

        for args in (("run", str(self.tmp / "no-such-plan.json")), ("run", "--workdir", str(root)),
                     ("plans", "--workdir", str(self.tmp / "nowhere")), ("do", "", "--workdir", str(root))):
            r = with_no_reader(*args)
            self.assertNotIn("Traceback", r.stderr, args)  # 3.7: BrokenPipeError raised inside the error handler
            self.assertEqual(r.returncode, 2, args)  # 3.7: 1, which is what a plan that needs review exits with
        self.assertEqual(with_no_reader("plans", "--all").returncode, 0)  # a listing cut short, as before

    # ---------------------------------------------------------------- parts of earlier fixes no test held

    def test_guard_a_check_or_a_setup_command_cannot_start_crew(self):
        root = self.repo()
        said = {step: self.tmp / f"{step}.txt" for step in ("setup", "check")}
        inner = {step: f"{sys.executable} {HERE / 'crew.py'} --config {self.cfg} do inner --workdir . > {out} 2>&1; "
                       f"echo exit=$? >> {out}" for step, out in said.items()}
        self.set_profile(root, setup=[{"run": inner["setup"]}], checks={"starts-crew": inner["check"]})
        r = self.crew("do", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for step, out in said.items():  # law 11: every check runs with CREW_TASK set, and setup with them
            self.assertIn("can't be started from inside one of its own tasks", out.read_text(), step)
            self.assertIn("exit=2", out.read_text(), step)
        self.assertEqual(len(self.workers()), 1)  # the outer task only
        self.assertEqual(len(list((self.home / "plans").glob("*/*.json"))), 1)

    def test_guard_every_timeout_is_capped_not_only_the_checks(self):
        for extra in ({"setup": {"timeout_sec": 3000000}}, {"providers": {"codex": {"timeout_sec": 3000000}}},
                      {"providers": {"claude": {"timeout_sec": 3000000}}}, {"verify": {"timeout_sec": 3000000}}):
            self.write_cfg(extra)
            r = self.crew("usage", check=False)
            self.assertEqual(r.returncode, 2, f"{extra}\n{r.stdout}{r.stderr}")
            self.assertIn("timeout_sec must be at most", r.stdout, extra)


class TestThreeEight(CrewTest):
    """Stops that were missed or never ended, output that was lost, and state that said more than was true
    (3.7 review, stops and exits)."""

    def workers(self):
        return [c for c in self.calls() if "argv" in c and not c.get("planner")]

    def plan_file(self) -> Path:
        return max((self.home / "plans").glob("*/*.json"), key=lambda f: f.stat().st_mtime)

    def agents_alive(self) -> list[int]:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "args="], capture_output=True, text=True).stdout
        return [int(ln.split()[0]) for ln in out.splitlines() if f"{self.bin}/" in ln]

    def queue_states(self) -> list[str]:
        f = self.home / "queue.json"
        return [i["status"] for i in json.loads(f.read_text())["items"]] if f.exists() else []

    def argv(self, *args, around: str = "") -> list[str]:
        """The command line that runs crew.py. With `around`, crew.py is unchanged and runs after that code
        has changed something in the Python it runs on (as the 3.7 tests slow down Popen)."""
        if not around:
            return [sys.executable, str(HERE / "crew.py"), "--config", str(self.cfg), *args]
        w = self.tmp / f"around{len(list(self.tmp.glob('around*.py')))}.py"
        w.write_text(textwrap.dedent(around) + textwrap.dedent("""
            import runpy, sys
            sys.argv = sys.argv[1:]
            runpy.run_path(sys.argv[0], run_name="__main__")
        """))
        return [sys.executable, str(w), str(HERE / "crew.py"), "--config", str(self.cfg), *args]

    def patched(self, patch: str, *args) -> list[str]:
        """The command line that runs crew's main() after `patch` has replaced one of crew's own functions:
        how a kill at an exact point is made without a crash_point for it."""
        w = self.tmp / f"patched{len(list(self.tmp.glob('patched*.py')))}.py"
        w.write_text(f"import os, signal, sys\nsys.path.insert(0, {str(HERE)!r})\nimport crew\n"
                     + textwrap.dedent(patch) + "\nsys.exit(crew.main())\n")
        return [sys.executable, str(w), "--config", str(self.cfg), *args]

    def start(self, argv, **env) -> subprocess.Popen:
        return subprocess.Popen(argv, env={**self.env, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def stopped(self, run: subprocess.Popen, within: float, why: str) -> tuple[str, str]:
        """Wait for crew to end by itself; fail (and clean up) if it does not."""
        import signal
        try:
            return run.communicate(timeout=within)
        except subprocess.TimeoutExpired:
            self.fail(why)
        finally:
            if run.poll() is None:
                run.kill()
            for pid in self.agents_alive():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def wait_until(self, cond, secs=20, what="what the test waits for did not happen"):
        for _ in range(int(secs * 10)):
            if cond():
                return
            time.sleep(0.1)
        self.fail(what)

    def no_reader(self, *args, env=None, **kw) -> int:
        r, w = os.pipe()
        os.close(r)
        try:
            return subprocess.run(self.argv(*args), stdout=w, stderr=subprocess.DEVNULL, env={**self.env, **(env or {})},
                                  timeout=120, **kw).returncode
        finally:
            os.close(w)

    # ---------------------------------------------------------------- stopping `queue run`

    def test_a_stop_signal_while_queue_run_starts_its_threads_is_acted_on(self):
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--workdir", str(a), "--workdir", str(b))
        # The signal arrives inside the call that starts the first worker thread.
        run = self.start(self.argv("queue", "run", around="""
            import os, signal
            from concurrent.futures import ThreadPoolExecutor
            real, sent = ThreadPoolExecutor.submit, []
            def submit(self, *a, **kw):
                f = real(self, *a, **kw)
                if not sent:
                    sent.append(1)
                    os.kill(os.getpid(), signal.SIGTERM)
                return f
            ThreadPoolExecutor.submit = submit
        """), FAKE_SLEEP="20")
        out, err = self.stopped(run, 12, "crew did not stop: the thread it had started works through its project's "
                                         "queue, and crew ignores every signal now")
        self.assertEqual(run.returncode, 143, out + err)
        self.assertEqual(self.agents_alive(), [])
        self.assertLessEqual(set(self.queue_states()), {"queued", "paused"})  # nothing was run to its end

    def test_a_stop_signal_while_queue_run_looks_at_its_workers_does_not_hang_it(self):
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--workdir", str(a), "--workdir", str(b))
        # The signal arrives at a moment the main thread holds the lock of one of the pool's futures, which
        # it takes each time it looks at them (two seconds in, so the agents are at work).
        run = self.start(self.argv("queue", "run", around="""
            import os, signal, threading, time
            from concurrent.futures import _base
            start, sent = time.time(), []
            class Watched(threading.Condition):
                def __init__(self):
                    super().__init__()
                    take = self.acquire
                    def acquire(*a, **kw):
                        got = take(*a, **kw)
                        if got and not sent and threading.current_thread() is threading.main_thread() \\
                                and time.time() - start > 2:
                            sent.append(1)
                            os.kill(os.getpid(), signal.SIGTERM)
                        return got
                    self.acquire = acquire
                def __enter__(self):
                    return self.acquire()
            make = _base.Future.__init__
            def init(self):
                make(self)
                self._condition = Watched()
            _base.Future.__init__ = init
        """), FAKE_SLEEP="20")
        out, err = self.stopped(run, 14, "crew never ended: the lock it held when the signal came was never given "
                                         "back, a worker waits for it, crew waits for the worker")
        self.assertEqual(run.returncode, 143, out + err)
        self.assertEqual(self.agents_alive(), [])
        self.assertEqual(set(self.queue_states()), {"paused"})

    def test_a_stop_while_a_queue_worker_probes_a_cli_is_acted_on(self):
        import signal
        a = self.repo("alpha")
        self.crew("queue", "add", "do it", "--direct", "--workdir", str(a))
        run = self.start(self.argv("queue", "run"), FAKE_PROBE_SLEEP="30")  # `claude auth status` hangs
        try:
            self.wait_until(self.agents_alive, what="no CLI probe was started")
            time.sleep(0.3)
            run.send_signal(signal.SIGTERM)
        except BaseException:
            run.kill()
            raise
        out, err = self.stopped(run, 10, "crew did not stop: a worker thread waits for a probe nothing can interrupt")
        self.assertEqual(run.returncode, 143, out + err)
        self.assertEqual(self.agents_alive(), [])  # the probe was stopped with crew, like any other child
        self.assertLessEqual(set(self.queue_states()), {"queued", "paused"})
        r = self.crew("queue", "run")  # and nothing about the stop is in the way of the next run
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ---------------------------------------------------------------- output that is lost

    def test_ctrl_c_with_the_output_reader_gone_is_still_a_clean_stop(self):
        import signal
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--agent", "Sol", "--workdir", str(a))
        self.crew("queue", "add", "do it", "--direct", "--agent", "Astra", "--workdir", str(b))
        run = self.start(self.argv("queue", "run"), FAKE_SLEEP="20")
        try:
            self.wait_until(lambda: len(self.workers()) == 2, what="the two agents did not start")
            run.stdout.close()  # Ctrl-C on `crew queue run | tee log` goes to tee as well: nobody reads any more
            run.send_signal(signal.SIGINT)
            err = run.stderr.read()
            run.wait(timeout=30)
        finally:
            if run.poll() is None:
                run.kill()
            run.stderr.close()
        self.assertNotIn("Traceback", err)
        self.assertEqual(run.returncode, 130, err)  # an interrupt, whatever became of the line that says so
        self.assertEqual(self.agents_alive(), [])
        self.assertEqual(self.queue_states(), ["paused", "paused"])  # not left `running` with nothing running

        root = self.repo("gamma")  # the same for one plan, stopped while its agent works
        run = self.start(self.argv("do", "x", "--workdir", str(root)), FAKE_SLEEP="20")
        try:
            self.wait_until(lambda: len(self.workers()) == 3, what="the agent did not start")
            run.stdout.close()
            run.send_signal(signal.SIGINT)
            err = run.stderr.read()
            run.wait(timeout=30)
        finally:
            if run.poll() is None:
                run.kill()
            run.stderr.close()
        self.assertNotIn("Traceback", err)
        self.assertEqual(run.returncode, 130, err)
        self.assertEqual(self.agents_alive(), [])

    def test_queue_run_that_loses_its_reader_stops_its_agents_and_pauses_its_items(self):
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--agent", "Sol", "--workdir", str(a))    # codex: 2 s
        self.crew("queue", "add", "do it", "--direct", "--agent", "Astra", "--workdir", str(b))  # claude: 30 s
        claude = self.bin / "claude"
        usual = claude.read_text()
        claude.write_text(usual.replace('time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))', "time.sleep(30)"))
        run = self.start(self.argv("queue", "run"), FAKE_SLEEP="2")
        try:
            self.wait_until(lambda: len(self.workers()) == 2, what="the two agents did not start")
            run.stdout.close()  # the reader has left; crew finds out at the line it prints when alpha's agent ends
        except BaseException:
            run.kill()
            raise
        try:
            run.wait(timeout=15)
        except subprocess.TimeoutExpired:
            run.kill()
            self.fail("crew went on for beta's agent after its output was lost")
        finally:
            run.stderr.close()
        self.assertEqual(run.returncode, 141)
        self.assertEqual(self.agents_alive(), [])  # beta's agent had 28 s to go: it was stopped
        self.assertEqual(self.queue_states(), ["paused", "paused"])
        claude.write_text(usual)
        r = self.crew("queue", "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.queue_states(), ["done", "done"])

    def test_an_error_no_check_foresaw_in_queue_run_stops_the_agents_at_work(self):
        a, b = self.repo("alpha"), self.repo("beta")
        self.crew("queue", "add", "do it", "--direct", "--agent", "Sol", "--workdir", str(a))
        self.crew("queue", "add", "do it", "--direct", "--agent", "Astra", "--workdir", str(b))
        # crew.py is unchanged. Once both agents are at work, an exception nothing in crew expects is
        # raised in its main thread, wherever that thread is.
        run = self.start(self.patched("""
            import ctypes, threading, time
            def strike():
                while len(crew.ACTIVE) < 2:
                    time.sleep(0.05)
                time.sleep(0.5)
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(threading.main_thread().ident),
                                                           ctypes.py_object(RuntimeError))
            threading.Thread(target=strike, daemon=True).start()
        """, "queue", "run"), FAKE_SLEEP="20")
        out, err = self.stopped(run, 12, "crew went on waiting for its agents after an error of its own")
        self.assertEqual(run.returncode, 2, out + err)
        self.assertIn("error: RuntimeError", out)
        self.assertIn("this is a defect in crew", out)  # the last resort, with work in progress this time
        self.assertNotIn("Traceback", out + err)
        self.assertEqual(self.agents_alive(), [])  # "stops cleanly": neither agent outlived crew
        self.assertEqual(self.queue_states(), ["paused", "paused"])
        r = self.crew("queue", "run")  # and the next run picks both up
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_queue_add_cut_short_by_its_reader_announces_nothing_it_did_not_do(self):
        a, b = self.repo("alpha"), self.repo("beta")
        for root in (a, b):
            self.set_profile(root)  # so that the two "queued" lines are all it prints
        rc = self.no_reader("queue", "add", "do it", "--workdir", str(a), "--workdir", str(b))
        self.assertEqual(self.queue_states(), ["queued", "queued"])  # saved before any of it is announced
        self.assertEqual(rc, 141)  # and a command that changes something never reports a lost reader as success
        self.assertEqual(self.no_reader("queue", "list"), 0)  # a listing still does: its reader chose to leave

    def test_output_that_cannot_be_written_is_no_traceback_and_no_exit_0(self):
        if not os.path.exists("/dev/full"):
            self.skipTest("needs /dev/full")
        root = self.repo()
        self.crew("plan", "x", "--workdir", str(root))
        plan = self.plan_file()

        def to_a_full_disk(*args):
            with open("/dev/full", "w") as full:
                return subprocess.run(self.argv(*args), stdout=full, stderr=subprocess.PIPE, text=True, env=self.env,
                                      timeout=120)
        r = to_a_full_disk("run", str(plan))
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 141, r.stderr)  # stopped where its output was lost, like a closed pipe
        self.assertIn("No space left", r.stderr)  # said where it still can be
        self.assertEqual(self.workers(), [])
        r = to_a_full_disk("plans", "--all")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 2, r.stderr)  # not 0: nobody chose to stop reading this list
        # stderr closed as well as the reader gone (`crew run plan 2>&- | head -1`)
        self.assertEqual(self.no_reader("run", str(plan), preexec_fn=lambda: os.close(2)), 141)
        r = self.crew("run", str(plan))  # none of it is in the way of a resume
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ---------------------------------------------------------------- what the plan file says after a stop

    def test_a_kill_right_after_a_regression_is_recorded_does_not_lose_the_fix_round(self):
        root = self.repo()
        self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
        killed = self.patched("""
            save = crew._write_json
            def write(path, data):
                save(path, data)
                checks = data.get("checks") if isinstance(data, dict) else None
                if isinstance(checks, dict) and checks.get("regressions"):
                    os.kill(os.getpid(), signal.SIGKILL)  # a kill or power cut right after this save
            crew._write_json = write
        """, "go", "x", "--workdir", str(root))
        r = subprocess.run(killed, capture_output=True, text=True, env={**self.env, "FAKE_BREAK": "t2"}, timeout=120)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        plan = self.plan_file()
        self.assertEqual(json.loads(plan.read_text())["checks"]["regressions"], ["guard"])
        r = self.crew("run", str(plan))
        self.assertIn("[fix1]", r.stdout)  # the fix round the regression called for still happens
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        done = json.loads(plan.read_text())
        self.assertEqual(crew.plan_state(done), "done")
        self.assertEqual([t["id"] for t in done["tasks"]], ["t1", "t2", "fix1"])

    def test_a_plan_stopped_during_its_last_checks_is_not_called_done(self):
        for no_git in (False, True):
            with self.subTest(no_git=no_git):
                root = self.repo("inplace" if no_git else "proj")
                self.set_profile(root, checks={"guard": "test ! -f broken"}, setup=[])
                extra = ["--no-git"] if no_git else []
                killed = self.patched("""
                    run_checks = crew.Run._run_checks
                    def stop_at_the_last_checks(self, label):
                        if label == "after":
                            os.kill(os.getpid(), signal.SIGKILL)
                        return run_checks(self, label)
                    crew.Run._run_checks = stop_at_the_last_checks
                """, "go", "x", "--workdir", str(root), *extra)
                r = subprocess.run(killed, capture_output=True, text=True, env={**self.env, "FAKE_BREAK": "t2"},
                                   timeout=120)
                self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
                plan = self.plan_file()
                state = json.loads(plan.read_text())
                self.assertEqual([t["status"] for t in state["tasks"]], ["done", "done"])
                self.assertEqual(crew.plan_state(state), "paused")  # every task done, and nothing has checked it
                listed = self.crew("plans", "--workdir", str(root)).stdout.splitlines()
                self.assertTrue(any(ln.startswith("paused") for ln in listed), listed)
                self.assertFalse(any(ln.startswith("done") for ln in listed), listed)
                if not no_git:
                    self.assertIn("its plan is paused", self.crew("clean").stdout)  # so its copy is kept for the checks
                n0 = len(self.workers())
                r = self.crew("run", str(plan), *extra)
                self.assertNotIn("nothing to do", r.stdout)
                self.assertIn("checks (after", r.stdout)  # the re-run checks what the stopped run did not
                self.assertEqual(len(self.workers()), n0 + 1, r.stdout)  # and the regression it finds is fixed
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(crew.plan_state(json.loads(plan.read_text())), "done")

    # ---------------------------------------------------------------- values no check foresaw

    def test_odd_values_in_a_repository_a_long_plan_and_the_usage_log_are_not_a_defect_in_crew(self):
        member = {"packages/web/package.json": json.dumps({"scripts": {"lint": "eslint ."}}),
                  "packages/list/package.json": "[1]", "packages/broken/package.json": "{nope",
                  "packages/odd/package.json": json.dumps({"scripts": 5})}
        shapes = [{"scripts": ["build"]}, {"scripts": {"test": 5, "build": "vite build"}}, {"scripts": "x"},
                  {"dependencies": ["react"], "devDependencies": 5}, {"workspaces": 5},
                  {"workspaces": {"packages": 5}}, {"workspaces": ["/abs/*", ".", "", "packages/*"]},
                  {"workspaces": "packages/*"}]
        roots = []
        for n, pkg in enumerate(shapes):
            roots.append(self.repo(f"shape{n}", files={"package.json": json.dumps(pkg), **member}))
            r = self.crew("init", "--workdir", str(roots[-1]))  # self.crew fails on exit 2 and on "a defect in crew"
            self.assertEqual(r.returncode, 0, f"{pkg}\n{r.stdout}{r.stderr}")
        found = crew.detect_project(roots[1], "x")["checks"]  # what has the right shape is still used
        self.assertEqual(found.get("build"), "npm run build")
        self.assertNotIn("test", found)
        for n in (6, 7):
            self.assertEqual(crew.detect_project(roots[n], "x")["checks"].get("lint"),
                             "npm run lint --workspaces --if-present", shapes[n])
        for name, files in (("pnpm", {"package.json": "{}", "pnpm-workspace.yaml": "packages:\n  - .\n  - /abs/*\n"}),
                            ("deep", {"package.json": "[" * 100000 + "]" * 100000})):
            r = self.crew("init", "--workdir", str(self.repo(name, files=files)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        n = 1500  # each task depends on the next one in the file: a long chain, not a cycle
        chain = self.tmp / "chain.json"
        chain.write_text(json.dumps({"goal": "g", "workdir": str(roots[0]), "tasks": [
            {"id": f"t{i}", "title": f"step {i}", "agent": "Sol", "instructions": "i", "acceptance": "a",
             "depends_on": [f"t{i + 1}"] if i < n else []} for i in range(1, n + 1)]}))
        r = self.crew("run", str(chain), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout[-500:] + r.stderr)
        self.assertLess(r.stdout.index(f"[t{n}]"), r.stdout.index("[t1]"))  # and in dependency order
        loop = json.loads(chain.read_text())
        loop["tasks"][-1]["depends_on"] = ["t1"]
        chain.write_text(json.dumps(loop))
        r = self.crew("run", str(chain), "--dry-run", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error: dependency cycle: t1 -> t2 -> ", r.stdout)

        # Two calls that each reported a finite, absurd number of tokens: together they are infinity.
        entry = json.dumps({"ts": time.time(), "provider": "claude", "tokens": 1e308, "seconds": 1e308, "ok": True,
                            "project": "p"})
        (self.home / "usage.jsonl").write_text(entry + "\n" + entry + "\n")
        r = self.crew("usage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.write_cfg({"split": {"metric": "tokens"}})
        r = self.crew("do", "x", "--workdir", str(self.repo("after")))  # and a run after them ends as its plan does
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_text_that_cannot_be_encoded_is_refused_by_name_or_shown_escaped(self):
        root = self.repo()
        # "\ud83d" is valid JSON: what a tool writes for a string cut in the middle of an emoji.
        half = self.tmp / "half.json"
        half.write_text('{"summary": "one", "tasks": [{"id": "t1", "title": "Add the rocket \\ud83d to the page", '
                        '"agent": "Sol", "instructions": "i", "acceptance": "a", "depends_on": []}]}')
        r = self.crew("go", "x", "--workdir", str(root), env={"FAKE_PLAN": str(half)}, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("invalid plan: task t1: 'title' contains half of a surrogate pair", r.stdout)
        self.assertEqual(self.workers(), [])
        self.assertEqual(list((self.home / "plans").glob("*/*.json")), [])  # no plan was left that can never commit
        own = self.tmp / "own.json"  # the same in a plan written by hand
        own.write_text(json.dumps({"goal": "g", "workdir": str(root), **json.loads(half.read_text())}))
        r = self.crew("run", str(own), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("half of a surrogate pair", r.stdout)

        # In what an agent or a CLI says, it is shown escaped; the run goes on and ends as its plan does.
        r = self.crew("do", "x", "--workdir", str(root),
                      env={"FAKE_STATUS": '{"t1": "partial"}', "FAKE_SUMMARY": '"left the rocket \\ud83d out"'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # needs-review, as for any partial task
        self.assertIn("left the rocket \\ud83d out", r.stdout)

        # A byte that isn't UTF-8 in a goal typed on the command line is not such a half. Python hands it
        # back to git as the byte it was; that worked before and still does (not text=True: crew prints it).
        r = subprocess.run(self.argv("do", os.fsdecode(b"fix the caf\xe9 page"), "--workdir", str(root)),
                           capture_output=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        odd = os.fsencode(str(self.tmp)) + b"/caf\xe9"  # a folder whose name is Latin-1, not UTF-8
        try:
            os.mkdir(odd)
        except OSError:
            odd = None  # a file system that has no such names
        if odd:
            sh(odd, "git", "init", "-q", "-b", "main")
            r = self.crew("init", "--workdir", os.fsdecode(odd), check=False)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("not valid UTF-8", r.stdout)  # refused by name, with what to do about it
            self.assertIn("caf\\xe9", r.stdout)

        self.crew("plan", "x", "--workdir", str(root))
        plan = max((self.home / "plans").glob("*/*.json"), key=lambda f: f.stat().st_mtime)
        plan.write_bytes(b"\xff" + plan.read_bytes())
        r = self.crew("run", str(plan), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"error: {plan}: not valid", r.stdout)  # the error says which file

    def test_every_process_crew_waits_for_has_a_time_limit(self):
        import ast
        tree = ast.parse((HERE / "crew.py").read_text())
        waits = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
                 and n.func.attr in ("run", "call", "check_call", "check_output")]
        self.assertGreater(len(waits), 3)

        def taskkill(n):  # Windows only, where nothing has been run (see CLAUDE.md)
            first = n.args[0].elts[0] if n.args and isinstance(n.args[0], ast.List) and n.args[0].elts else None
            return isinstance(first, ast.Constant) and first.value == "taskkill"
        untimed = [n.lineno for n in waits if "timeout" not in {k.arg for k in n.keywords} and not taskkill(n)]
        self.assertEqual(untimed, [], "crew.py waits for a process at these lines with no time limit")
        # `crew codex-login` was the one: it still hands the key over on stdin, never on the command line.
        r = self.crew("codex-login", env={"OPENAI_API_KEY": "sk-test-not-a-real-key"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Successfully logged in", r.stdout)
        login = [c for c in self.calls() if c.get("login")]
        self.assertEqual([c["key_on_stdin"] for c in login], ["sk-test-not-a-real-key"])
        self.assertEqual(login[0]["login"], ["login", "--with-api-key"])


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


FAKE_GIT = r'''
import os, signal, subprocess, sys
real, a = os.environ["FAKE_GIT_REAL"], sys.argv[1:]
cut = os.environ.get("FAKE_GIT_CUT", "")
def out(repo, *args):
    return subprocess.run([real, "-C", repo, *args], capture_output=True).stdout
def half_way(repo, target, write=True, die="killed"):
    """What a git that stops while it checks `target` out leaves behind: some of target's files written
    and HEAD where it was. Killed, it also leaves its index lock, and crew is killed at the same moment;
    giving up by itself (a full disk), it exits with an error."""
    names = [n for n in out(repo, "diff", "--name-only", "-z", "HEAD", target).split(b"\0") if n]
    for n in names[:max(1, len(names) // 2)] if write else []:
        path = os.path.join(os.fsencode(repo), n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(out(repo, "show", target + ":" + os.fsdecode(n)))
    if die == "killed":
        if write:
            gitdir = os.fsdecode(out(repo, "rev-parse", "--absolute-git-dir").strip())
            open(os.path.join(gitdir, "index.lock"), "w").close()
        os.kill(os.getppid(), signal.SIGKILL)
    sys.stderr.write("error: unable to write file: No space left on device\n")
    sys.exit(128)
if cut == "commit" and "commit" in a and "--no-verify" in a:
    os.kill(os.getppid(), signal.SIGKILL)  # crew dies between its `git add` and its `git commit`
    sys.exit(1)
if "-C" in a:
    wt = a[a.index("-C") + 1]
    if cut.startswith("merge") and "merge" in a and "--ff-only" in a:
        # "merge": killed half-way; "merge-early": killed before it wrote anything; "merge-gives-up": fails half-way
        half_way(wt, a[-1], write=cut != "merge-early", die="alone" if cut == "merge-gives-up" else "killed")
    if cut == "submodule" and "submodule" in a and "update" in a and "--checkout" in a:
        half_way(os.path.join(wt, "lib"), os.fsdecode(out(wt, "rev-parse", "HEAD:lib").strip()))
os.execv(real, [real] + a)
'''


class TestYourCommitsAndGit(CrewTest):
    """The 3.4 review's confirmed findings about bringing your commits into crew's copy. Each test fails
    on 3.5, except the one that says it guards the fix."""

    plan_of = TestThreeThree.plan_of
    workers = TestThreeThree.workers
    files_on = TestThreeThree.files_on
    key_of = TestThreeThree.key_of
    plans_dir_of = TestThreeThree.plans_dir_of
    submodule_repo = TestThreeThree.submodule_repo
    _publish_then_pause_after_t1 = TestThreeThree._publish_then_pause_after_t1
    _latest_or_none = TestThreeThree._latest_or_none
    FILEENV = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"}

    def git_cut(self, what: str) -> dict:
        """Environment in which `git` is a stand-in that is killed half-way through one command (and takes
        crew with it), and is the real git for everything else."""
        d = self.tmp / "gitbin"
        if not d.exists():
            d.mkdir()
            (d / "git").write_text(f"#!{sys.executable}\n{FAKE_GIT}")
            (d / "git").chmod(0o755)
        return {"PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "FAKE_GIT_REAL": shutil.which("git"),
                "FAKE_GIT_CUT": what}

    def you_commit(self, root, branch, files, msg="mine"):
        sh(root, "git", "checkout", "-q", branch)
        for name, text in files.items():
            (root / name).write_text(text)
        sh(root, "git", "add", "-A")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
        sha = sh(root, "git", "rev-parse", "HEAD").strip()
        sh(root, "git", "checkout", "-q", "main")
        return sha

    def cut_adoption(self, root):
        """t1 is delivered and t2 was interrupted. You commit on the crew branch, and crew is killed while git
        is half-way through bringing that commit into the copy."""
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt, tip = g["branch"], Path(g["worktree"]), g["tip"]
        mine = self.you_commit(root, branch, {"README.md": "yours\n", "a.txt": "yours\n", "b.txt": "yours\n",
                                              "note.txt": "yours\n"})
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)  # killed inside the merge
        self.assertEqual(sh(wt, "git", "rev-parse", "HEAD").strip(), tip)  # which had not moved HEAD yet
        self.assertEqual(sh(wt, "git", "status", "--porcelain"), " M README.md\n?? a.txt\n")  # but had written these
        return branch, wt, tip, mine

    def test_an_adoption_that_was_cut_short_is_taken_back_and_done_again(self):
        root = self.repo()
        branch, wt, tip, mine = self.cut_adoption(root)
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.5: exit 2 on every run, "would be overwritten"
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertEqual([t["status"] for t in self.plan_of(root)["tasks"]], ["done", "done"])
        self.assertEqual(sh(root, "git", "rev-parse", f"{branch}~1").strip(), mine)  # t2 sits on your commit
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    def test_an_adoption_git_gives_up_on_part_way_is_not_a_dead_end(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        branch = self.plan_of(root)["git"]["branch"]
        mine = self.you_commit(root, branch, {"README.md": "yours\n", "a.txt": "yours\n", "note.txt": "yours\n"})
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge-gives-up"), check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)  # git's own failure is reported, crew is not killed
        self.assertIn("No space left on device", r.stdout)
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.5: exit 2 on every later run
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    def test_a_cut_short_adoption_of_a_commit_you_took_back_is_not_committed_as_a_task(self):
        root = self.repo()
        branch, wt, tip, mine = self.cut_adoption(root)
        sh(root, "git", "branch", "-f", branch, tip)  # you take your commit back before running crew again
        r = self.crew("run", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 3.5 commits what the merge had written as t2's work: ["README.md", "t2.txt"], and a.txt as well when
        # t2 had started before it was interrupted
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{tip}..{branch}").split(), ["t2.txt"])
        self.assertEqual(sh(root, "git", "show", f"{branch}:README.md"), "hi\n")

    def test_guard_a_task_file_in_the_way_is_never_taken_back(self):
        """Passes on 3.5 too: it guards the undo above. A task's own uncommitted edit collides with your
        commit, and crew is killed after journaling the adoption but before git wrote anything."""
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt = g["branch"], Path(g["worktree"])
        (wt / "README.md").write_text("hi\nhalf an edit by the interrupted task\n")
        (wt / "a.txt").write_text("the task's own a.txt\n")
        mine = self.you_commit(root, branch, {"README.md": "yours\n", "a.txt": "yours\n"})
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge-early"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        self.assertEqual(self.plan_of(root)["git"].get("adopting"), mine)  # journaled, nothing merged
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)  # a real collision: git's reason and the ways out
        self.assertIn("would be overwritten by merge", r.stdout)
        self.assertNotIn("put back what it had written", r.stdout)
        self.assertEqual((wt / "README.md").read_text(), "hi\nhalf an edit by the interrupted task\n")
        self.assertEqual((wt / "a.txt").read_text(), "the task's own a.txt\n")
        self.assertEqual(len(self.workers()), n)

    def test_a_submodule_move_that_was_cut_short_is_finished_by_the_next_run(self):
        super_, s1, s2 = self.submodule_repo()
        self._publish_then_pause_after_t1(super_, extra_env=self.FILEENV)
        branch = self.plan_of(super_)["git"]["branch"]
        sh(super_, "git", "checkout", "-q", branch)
        sh(super_, "git", "-C", "lib", "checkout", "-q", s2)
        sh(super_, "git", "add", "lib")
        sh(super_, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "bump lib to s2")
        mine = sh(super_, "git", "rev-parse", "HEAD").strip()
        sh(super_, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(super_), env={**self.FILEENV, **self.git_cut("submodule")}, check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)  # killed while git moved lib's checkout to s2
        wt = Path(self.plan_of(super_)["git"]["worktree"])
        self.assertEqual(sh(wt / "lib", "git", "rev-parse", "HEAD").strip(), s1)
        self.assertEqual((wt / "lib" / "f.txt").read_text(), "two\n")  # s2's file, half-way
        r = self.crew("run", "--workdir", str(super_), env=self.FILEENV, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.5: exit 2 on every run, "could not move the submodule"
        self.assertEqual(sh(super_, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])
        self.assertEqual(sh(super_, "git", "ls-tree", branch, "lib").split()[2], s2)

    def test_a_submodule_commit_you_have_not_pushed_is_not_replaced_by_its_remotes(self):
        super_, s1, s2 = self.submodule_repo()
        # You commit inside your lib checkout and record that in the superproject; lib's remote doesn't have it.
        (super_ / "lib" / "f.txt").write_text("mine, not pushed\n")
        sh(super_ / "lib", "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "local work")
        mine = sh(super_ / "lib", "git", "rev-parse", "HEAD").strip()
        sh(super_, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "use my lib commit")
        r = self.crew("go", "x", "--workdir", str(super_), env=self.FILEENV)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.5: t1 ends "partial", exit 1
        self.assertIn("stays empty in crew's copy", r.stdout)
        branch = self.plan_of(super_)["git"]["branch"]
        self.assertEqual(sh(super_, "git", "ls-tree", branch, "lib").split()[2], mine)  # 3.5: the remote's commit
        self.assertEqual(sorted(sh(super_, "git", "diff", "--name-only", f"main..{branch}").split()),
                         ["t1.txt", "t2.txt"])  # 3.5: crew(t1) also changes `lib`

    def test_merge_autostash_does_not_turn_a_collision_into_conflict_markers(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt = g["branch"], Path(g["worktree"])
        (wt / "README.md").write_text("hi\nhalf an edit by the interrupted task\n")
        self.you_commit(root, branch, {"README.md": "yours\n"})
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "merge.autoStash", "GIT_CONFIG_VALUE_0": "true"}
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(root), env=env, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("would be overwritten by merge", r.stdout)  # 3.5: "the agent left an unfinished unmerged files"
        self.assertEqual(len(self.workers()), n)  # 3.5 runs t2 on a file full of conflict markers
        self.assertEqual((wt / "README.md").read_text(), "hi\nhalf an edit by the interrupted task\n")
        self.assertEqual(sh(wt, "git", "stash", "list"), "")
        self.assertNotEqual(self.plan_of(root)["tasks"][1]["status"], "failed")

    def test_a_submodule_your_commit_removes_is_not_held_against_the_waiting_task(self):
        super_, s1, s2 = self.submodule_repo()
        self._publish_then_pause_after_t1(super_, extra_env=self.FILEENV)
        plan = self.plan_of(super_)
        branch, wt = plan["git"]["branch"], Path(plan["git"]["worktree"])
        sh(super_, "git", "checkout", "-q", branch)
        sh(super_, "git", "rm", "-q", "lib")
        sh(super_, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "drop lib")
        mine = sh(super_, "git", "rev-parse", "HEAD").strip()
        sh(super_, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(super_), env=self.FILEENV, check=False)
        # 3.5: t2 is marked partial for "nested git repository lib/" and the copy is kept, exit 1
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([t["status"] for t in self.plan_of(super_)["tasks"]], ["done", "done"])
        self.assertEqual(sh(super_, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])
        self.assertFalse(wt.exists())

    def test_clone_default_remote_name_does_not_stop_crew_reading_your_branch(self):
        root = self.repo()
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "clone.defaultRemoteName", "GIT_CONFIG_VALUE_0": "upstream"}
        self._publish_then_pause_after_t1(root, extra_env=env)
        branch = self.plan_of(root)["git"]["branch"]
        self.you_commit(root, branch, {"note.txt": "yours\n"})
        r = self.crew("run", "--workdir", str(root), env=env, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.5: exit 2, "'origin' does not appear to be a git repository"
        self.assertIn("continuing from your own commits", r.stdout)
        self.assertIn("note.txt", self.files_on(root, branch))
        self.assertIn("t2.txt", self.files_on(root, branch))

    def test_messages_about_an_older_plan_name_its_file(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)  # plan A: paused, its copy kept
        plan_a = sorted(self.plans_dir_of(self.key_of(root)).glob("*.json"))[-1]
        wt = Path(json.loads(plan_a.read_text())["git"]["worktree"])
        self.crew("do", "another goal", "--workdir", str(root))  # a newer plan: bare `crew run` now means this one
        self.assertIn(f"`crew run {plan_a}` continues it", self.crew("clean").stdout)  # 3.5: "`crew run` continues it"
        sh(wt, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "stray")
        r = self.crew("run", str(plan_a), check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(f"`crew run {plan_a}` start over", r.stdout)  # 3.5: "`crew run` start over"


    def test_guard_taking_back_a_cut_short_adoption_never_deletes_through_a_symbolic_link(self):
        """Passes on 3.6 and 3.7: it holds the "never through a symbolic link" half of the undo, which no
        test held. The repository tracks a link to a folder outside it. Your commit turns the link into a
        folder, with a file named like one in the folder the link leads to."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "notes.txt").write_text("in no repository\n")
        root = self.repo()
        os.symlink(outside, root / "d")
        sh(root, "git", "add", "d")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a link")
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt, tip = g["branch"], Path(g["worktree"]), g["tip"]
        sh(root, "git", "checkout", "-q", branch)
        os.unlink(root / "d")
        (root / "d").mkdir()
        for name in ("README.md", "a.txt", "d/notes.txt"):
            (root / name).write_text("yours\n")
        sh(root, "git", "add", "-A")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mine")
        mine = sh(root, "git", "rev-parse", "HEAD").strip()
        sh(root, "git", "checkout", "-q", "main")
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)  # killed inside the merge ...
        self.assertEqual(sh(wt, "git", "rev-parse", "HEAD").strip(), tip)
        self.assertTrue((wt / "d").is_symlink())  # ... before git got to the link
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("put back what it had written", r.stdout)
        # d/notes.txt is a path the merge writes and the old tip lacks. Looked up through the link, which
        # was still in place, it is the file outside: taking it back there would delete that one.
        self.assertTrue((outside / "notes.txt").is_file(), "crew deleted a file outside its copy")
        self.assertEqual((outside / "notes.txt").read_text(), "in no repository\n")
        self.assertEqual(sh(root, "git", "show", f"{branch}:d/notes.txt"), "yours\n")
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])


class TestAdoptionAndSubmodules(CrewTest):
    """The 3.7 review's confirmed findings about bringing your commits into crew's copy and about
    submodules. Each test fails on 3.7, except the two that say they guard a fix."""

    plan_of = TestThreeThree.plan_of
    workers = TestThreeThree.workers
    files_on = TestThreeThree.files_on
    key_of = TestThreeThree.key_of
    plans_dir_of = TestThreeThree.plans_dir_of
    _publish_then_pause_after_t1 = TestThreeThree._publish_then_pause_after_t1
    _latest_or_none = TestThreeThree._latest_or_none
    git_cut = TestYourCommitsAndGit.git_cut
    you_commit = TestYourCommitsAndGit.you_commit
    FILEENV = TestYourCommitsAndGit.FILEENV
    ID = ("-c", "user.name=t", "-c", "user.email=t@t")

    def remote(self, name, *versions):
        """A repository to use as a submodule's remote, with one commit per version of its files.
        Returns (path, sha of each commit); it is left at the first commit, so `submodule add` records that."""
        src = self.tmp / name
        src.mkdir()
        sh(src, "git", "init", "-q", "-b", "main")
        shas = []
        for files in versions:
            for path, text in files.items():
                (src / path).write_text(text)
            sh(src, "git", "add", "-A")
            sh(src, "git", *self.ID, "commit", "-q", "-m", f"s{len(shas) + 1}")
            shas.append(sh(src, "git", "rev-parse", "HEAD").strip())
        sh(src, "git", "checkout", "-q", shas[0])
        return (src, *shas)

    def superproject(self, **submodules):
        sup = self.tmp / "super"
        sup.mkdir()
        sh(sup, "git", "init", "-q", "-b", "main")
        (sup / "README.md").write_text("hi\n")
        sh(sup, "git", "add", "-A")
        sh(sup, "git", *self.ID, "commit", "-q", "-m", "base")
        for path, src in submodules.items():
            sh(sup, "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), path)
        sh(sup, "git", *self.ID, "commit", "-q", "-m", "add submodules")
        return sup

    def commit_unpushed(self, sup, path):
        """You commit inside your checkout of a submodule; its remote doesn't have that commit."""
        (sup / path / "f.txt").write_text("mine, not pushed\n")
        sh(sup / path, "git", *self.ID, "commit", "-q", "-am", "local work")
        return sh(sup / path, "git", "rev-parse", "HEAD").strip()

    def you_commit_on(self, sup, branch, change, msg="mine"):
        """Like you_commit, for a change made with git commands in a repository that has submodules."""
        sh(sup, "git", "checkout", "-q", branch)
        change()
        sh(sup, "git", *self.ID, "commit", "-q", "-m", msg)
        sha = sh(sup, "git", "rev-parse", "HEAD").strip()
        sh(sup, "git", "checkout", "-q", "-f", "main")
        return sha

    # ---------------------------------------------------------------- the adoption merge

    def staged_then_cut_adoption(self, root):
        """t2 has finished and crew is killed between its `git add` and its `git commit`. You commit on the crew
        branch, and the next run is killed while git is half-way through bringing that commit into the copy."""
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt, tip = g["branch"], Path(g["worktree"]), g["tip"]
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("commit"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        self.assertEqual(sh(wt, "git", "status", "--porcelain"), "A  t2.txt\n")  # staged, not committed
        mine = self.you_commit(root, branch, {"README.md": "yours\n", "a.txt": "yours\n", "b.txt": "yours\n",
                                              "note.txt": "yours\n"})
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        self.assertEqual(sh(wt, "git", "rev-parse", "HEAD").strip(), tip)
        self.assertIn(" M README.md\n", sh(wt, "git", "status", "--porcelain"))  # the merge had written this
        return branch, wt, tip, mine

    def test_a_commit_cut_after_staging_does_not_switch_the_undo_off(self):
        root = self.repo()
        branch, wt, tip, mine = self.staged_then_cut_adoption(root)
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 3.7: exit 2 on every run, "would be overwritten by merge"
        self.assertEqual(len(self.workers()), n)  # the finished t2 is committed, not run again
        self.assertEqual(sh(root, "git", "rev-parse", f"{branch}~1").strip(), mine)
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    def test_a_commit_cut_after_staging_then_a_withdrawn_commit_is_not_delivered_as_the_tasks(self):
        root = self.repo()
        branch, wt, tip, mine = self.staged_then_cut_adoption(root)
        sh(root, "git", "branch", "-f", branch, tip)  # you take your commit back
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 3.7: crew(t2) is ["README.md", "a.txt", "t2.txt"], the half of your withdrawn commit git had written
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{tip}..{branch}").split(), ["t2.txt"])
        self.assertEqual(sh(root, "git", "show", f"{branch}:README.md"), "hi\n")

    def deleted_by_a_waiting_task(self):
        """t2 has finished, having deleted docs/old.txt, and waits to be committed."""
        root = self.repo(files={"README.md": "hi\n", "docs/old.txt": "old\n", "docs/older.txt": "older\n"})
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt, tip = g["branch"], Path(g["worktree"]), g["tip"]
        self.crew("run", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit", "FAKE_DELETE": "docs"},
                  check=False)
        self.assertEqual(self.plan_of(root)["tasks"][1].get("committing"), tip)
        self.assertEqual(sh(wt, "git", "status", "--porcelain"), " D docs/old.txt\n D docs/older.txt\n?? t2.txt\n")
        return root, branch, wt, tip

    def test_your_change_to_a_file_a_task_deleted_is_a_collision_not_a_silent_restore(self):
        root, branch, wt, tip = self.deleted_by_a_waiting_task()
        self.you_commit(root, branch, {"docs/old.txt": "yours\n"})
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(root), check=False)
        # 3.7: exit 0, plan done, crew(t2) is ["A t2.txt"] and the file is back on the branch
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("docs/old.txt", r.stdout)
        self.assertIn("deleted there", r.stdout)
        self.assertFalse((wt / "docs" / "old.txt").exists())  # the task's deletion stands
        self.assertEqual(len(self.workers()), n)
        self.assertNotIn("adopting", self.plan_of(root)["git"])
        # The way out the message names: take your version of the file. The rest of the task's work is kept.
        sh(wt, "git", "checkout", "--", "docs/old.txt")
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(root, "git", "show", f"{branch}:docs/old.txt"), "yours\n")
        self.assertEqual(sh(root, "git", "diff", "--name-status", f"{branch}~1..{branch}").split("\n")[:2],
                         ["D\tdocs/older.txt", "A\tt2.txt"])

    def test_a_cut_short_adoption_never_puts_back_a_file_a_task_deleted(self):
        root, branch, wt, tip = self.deleted_by_a_waiting_task()
        self.you_commit(root, branch, {"docs/old.txt": "yours\n"})
        # crew is killed after journaling the adoption, before git wrote anything; then you take the commit back
        self.crew("run", "--workdir", str(root), env=self.git_cut("merge-early"), check=False)
        sh(root, "git", "branch", "-f", branch, tip)
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 3.7: "crew put back what it had written", and crew(t2) is ["A t2.txt"]: the deletions are lost
        self.assertEqual(sh(root, "git", "diff", "--name-status", f"{tip}..{branch}").split("\n")[:3],
                         ["D\tdocs/old.txt", "D\tdocs/older.txt", "A\tt2.txt"])

    def test_a_file_you_and_a_task_both_deleted_stays_deleted_when_the_adoption_is_cut(self):
        root, branch, wt, tip = self.deleted_by_a_waiting_task()

        def change():
            sh(root, "git", "rm", "-q", "docs/old.txt")
            for name in ("a.txt", "b.txt", "note.txt"):
                (root / name).write_text("yours\n")
            sh(root, "git", "add", "-A")
        self.you_commit_on(root, branch, change)
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)  # killed inside the merge: no collision here
        self.assertFalse((wt / "docs" / "old.txt").exists())
        sh(root, "git", "branch", "-f", branch, tip)  # you take the commit back
        r = self.crew("run", "--workdir", str(root), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("crew put back what it had written", r.stdout)
        # 3.7: the undo puts docs/old.txt back too, and crew(t2) is ["D docs/older.txt", "A t2.txt"]
        self.assertEqual(sh(root, "git", "diff", "--name-status", f"{tip}..{branch}").split("\n")[:3],
                         ["D\tdocs/old.txt", "D\tdocs/older.txt", "A\tt2.txt"])

    def test_the_undo_leaves_an_ignored_file_that_was_there_before_the_merge(self):
        root = self.repo(files={"README.md": "hi\n", ".gitignore": "*.log\n"})
        self._publish_then_pause_after_t1(root)
        g = self.plan_of(root)["git"]
        branch, wt, tip = g["branch"], Path(g["worktree"]), g["tip"]
        (wt / "keep.log").write_text("what the interrupted task logged\n")

        def change():
            (root / "keep.log").write_text("yours\n")
            (root / "note.txt").write_text("yours\n")
            sh(root, "git", "add", "-f", "keep.log", "note.txt")
        self.you_commit_on(root, branch, change)
        r = self.crew("run", "--workdir", str(root), env=self.git_cut("merge-early"), check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        self.assertIs(self.plan_of(root)["git"].get("adopting_clear"), True)  # git lets a merge write over it
        sh(root, "git", "branch", "-f", branch, tip)  # you take the commit back; the merge never wrote a file
        r = self.crew("run", "--workdir", str(root), env={"CREW_CRASH_AT": "before-commit"}, check=False)
        self.assertIn("crew put back what it had written", r.stdout)
        self.assertTrue((wt / "keep.log").exists())  # 3.7: deleted by the undo
        self.assertEqual((wt / "keep.log").read_text(), "what the interrupted task logged\n")
        self.assertFalse((wt / "note.txt").exists())

    def test_merge_verify_signatures_does_not_refuse_your_own_unsigned_commit(self):
        root = self.repo()
        self._publish_then_pause_after_t1(root)
        branch = self.plan_of(root)["git"]["branch"]
        mine = self.you_commit(root, branch, {"note.txt": "yours\n"})
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "merge.verifySignatures", "GIT_CONFIG_VALUE_0": "true"}
        r = self.crew("run", "--workdir", str(root), env=env, check=False)
        # 3.7: exit 2 on every run: "does not have a GPG signature ... git stopped part-way; crew put back ..."
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(root, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    # ---------------------------------------------------------------- submodules

    def test_one_submodule_that_cannot_be_fetched_does_not_break_the_others(self):
        libsrc, _ = self.remote("libsrc", {"f.txt": "one\n"})
        othersrc, _ = self.remote("othersrc", {"o.txt": "one\n"})  # recorded at its remote's HEAD
        zzzsrc, _, _ = self.remote("zzzsrc", {"z.txt": "one\n"}, {"z.txt": "two\n"})
        sup = self.superproject(lib=libsrc, other=othersrc, zzz=zzzsrc)
        sh(zzzsrc, "git", "checkout", "-q", "main")  # zzz's remote has moved on from the commit you record
        mine = self.commit_unpushed(sup, "lib")
        sh(sup, "git", *self.ID, "commit", "-q", "-am", "use my lib commit")
        seen = self.tmp / "seen.txt"
        self.set_profile(sup, checks={"siblings": f"cat other/o.txt zzz/z.txt > {seen}"}, setup=[])
        r = self.crew("go", "x", "--workdir", str(sup), env=self.FILEENV)
        # 3.7: git stops at lib and leaves the two after it cloned but empty. t1 ends "partial" for
        # "uncommitted changes to tracked files" (other), t2 is skipped, exit 1; zzz is emptied as "not pushed yet?"
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([t["status"] for t in self.plan_of(sup)["tasks"]], ["done", "done"])
        self.assertIn("the submodule lib stays empty in crew's copy", r.stdout)
        self.assertNotIn("the submodule other stays empty", r.stdout)
        self.assertNotIn("the submodule zzz stays empty", r.stdout)
        self.assertEqual(seen.read_text(), "one\none\n")  # the checks saw both, at the commits you record
        branch = self.plan_of(sup)["git"]["branch"]
        self.assertEqual(sorted(sh(sup, "git", "diff", "--name-only", f"main..{branch}").split()), ["t1.txt", "t2.txt"])
        self.assertEqual(sh(sup, "git", "ls-tree", branch, "lib").split()[2], mine)

    def test_your_commit_that_moves_one_submodule_and_updates_another_is_not_taken_for_tampering(self):
        libsrc, _ = self.remote("libsrc", {"f.txt": "one\n"})
        othersrc, o1, o2 = self.remote("othersrc", {"o.txt": "one\n"}, {"o.txt": "two\n"})
        sup = self.superproject(lib=libsrc, other=othersrc)
        self._publish_then_pause_after_t1(sup, extra_env=self.FILEENV)
        branch = self.plan_of(sup)["git"]["branch"]

        def change():
            sh(sup, "git", "mv", "lib", "vendor-lib")
            sh(sup, "git", "-C", "other", "checkout", "-q", o2)
            sh(sup, "git", "add", "-A")
        mine = self.you_commit_on(sup, branch, change, "move lib, update other")
        r = self.crew("run", "--workdir", str(sup), env=self.FILEENV, check=False)
        # 3.7: exit 2 on every run: "the submodule `vendor-lib` ... has a different `.git` than when crew last
        # looked". crew's own `git submodule update` had just filled that folder in.
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(sup, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])
        self.assertEqual(sh(sup, "git", "ls-tree", branch, "other").split()[2], o2)

    def agent_file_in_the_way_of_a_submodule_update(self, name, agent_text):
        libsrc, s1, s2 = self.remote("libsrc", {"f.txt": "one\n"}, {"f.txt": "two\n", "agent.txt": "the lib's own\n"})
        sup = self.superproject(lib=libsrc)
        self._publish_then_pause_after_t1(sup, extra_env=self.FILEENV)
        g = self.plan_of(sup)["git"]
        branch, wt = g["branch"], Path(g["worktree"])
        (wt / "lib" / name).parent.mkdir(exist_ok=True)
        (wt / "lib" / name).write_text(agent_text)  # what the interrupted task left in the submodule

        def change():
            sh(sup, "git", "-C", "lib", "checkout", "-q", s2)
            sh(sup, "git", "add", "lib")
        mine = self.you_commit_on(sup, branch, change, "update lib")
        return sup, branch, wt, mine, s2

    def test_an_untracked_file_git_would_not_write_over_is_not_forced_over_by_the_next_run(self):
        sup, branch, wt, mine, s2 = self.agent_file_in_the_way_of_a_submodule_update("agent.txt", "the agent's\n")
        for attempt in (1, 2):
            r = self.crew("run", "--workdir", str(sup), env=self.FILEENV, check=False)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)  # 3.7: the second run goes through
            self.assertIn("untracked working tree files would be overwritten", r.stdout)
            self.assertNotIn("did not finish last time", r.stdout)  # git ran to its end and refused
            # 3.7: the second run forces the checkout, and the file reads "the lib's own"
            self.assertEqual((wt / "lib" / "agent.txt").read_text(), "the agent's\n")

    def test_guard_a_cut_short_submodule_move_is_still_forced_past_generated_files(self):
        """Passes on 3.7 too: it guards the fix above. What a check run leaves in a submodule (__pycache__) is
        not an agent's work, and must not turn a move that was cut short back into a dead end."""
        sup, branch, wt, mine, s2 = self.agent_file_in_the_way_of_a_submodule_update("__pycache__/m.cpython-313.pyc", "x")
        r = self.crew("run", "--workdir", str(sup), env={**self.FILEENV, **self.git_cut("submodule")}, check=False)
        self.assertEqual(r.returncode, -9, r.stdout + r.stderr)
        self.assertEqual(self.plan_of(sup)["git"].get("sync_started"), ["lib"])
        r = self.crew("run", "--workdir", str(sup), env=self.FILEENV, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(sup, "git", "ls-tree", branch, "lib").split()[2], s2)
        self.assertEqual(sh(sup, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    def test_an_unpushed_submodule_commit_on_the_crew_branch_stops_with_the_real_reason(self):
        libsrc, _ = self.remote("libsrc", {"f.txt": "one\n"})
        sup = self.superproject(lib=libsrc)
        self._publish_then_pause_after_t1(sup, extra_env=self.FILEENV)
        branch = self.plan_of(sup)["git"]["branch"]

        def change():
            self.commit_unpushed(sup, "lib")
            sh(sup, "git", "add", "lib")
        mine = self.you_commit_on(sup, branch, change, "use my lib commit")
        n = len(self.workers())
        r = self.crew("run", "--workdir", str(sup), env=self.FILEENV, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        # 3.7, first run only: "could not read crew/... from <repo> into crew's copy: ... Errors during submodule
        # fetch: lib", although the branch had been read
        self.assertNotIn("could not read", r.stdout)
        self.assertIn("could not move the submodule checkout(s) lib", r.stdout)
        self.assertEqual(self.plan_of(sup)["git"]["tip"], mine)  # your commit is adopted; only lib is behind
        self.assertEqual(len(self.workers()), n)
        sh(sup / "lib", "git", "-c", "protocol.file.allow=always", "push", "-q", "origin", "HEAD:refs/heads/mine")
        r = self.crew("run", "--workdir", str(sup), env=self.FILEENV, check=False)  # pushed: the plan goes on
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(sup, "git", "diff", "--name-only", f"{mine}..{branch}").split(), ["t2.txt"])

    def test_files_a_task_leaves_inside_a_submodule_are_reported_and_the_copy_is_kept(self):
        libsrc, _ = self.remote("libsrc", {"f.txt": "one\n"})
        othersrc, _ = self.remote("othersrc", {"f.txt": "one\n"})
        sup = self.superproject(lib=libsrc, other=othersrc)
        self.commit_unpushed(sup, "other")  # so `other` is empty in crew's copy, and `lib` is populated
        sh(sup, "git", *self.ID, "commit", "-q", "-am", "use my other commit")
        made = {"lib/agent-made.txt": "an agent's work\n", "other/agent-made.txt": "an agent's work\n"}
        r = self.crew("go", "x", "--workdir", str(sup), env={**self.FILEENV, "FAKE_EDIT": json.dumps(made)})
        plan = self.plan_of(sup)
        wt = Path(plan["git"]["worktree"])
        # 3.7: both tasks "done", exit 0, "removed crew's copy": the two files are gone and nothing said so
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual([t["status"] for t in plan["tasks"]], ["partial", "skipped"])
        self.assertIn("2 new file(s) inside a submodule", r.stdout)
        self.assertIn("lib/agent-made.txt, other/agent-made.txt", r.stdout)
        self.assertIn("keeping crew's copy", r.stdout)
        for f in made:
            self.assertEqual((wt / f).read_text(), "an agent's work\n")
        self.assertEqual(sh(sup, "git", "diff", "--name-only", f"main..{plan['git']['branch']}").split(), ["t1.txt"])

    def test_guard_what_setup_and_checks_leave_inside_a_submodule_is_not_held_against_a_task(self):
        """Passes on 3.7 too: it guards the fix above. Only files that appear during a task count as its own."""
        libsrc, _ = self.remote("libsrc", {"f.txt": "one\n"})
        sup = self.superproject(lib=libsrc)
        self.set_profile(sup, setup=[{"run": "echo built > lib/setup-made.txt"}],
                         checks={"cache": "mkdir -p lib/__pycache__ && touch lib/__pycache__/m.cpython-313.pyc"})
        r = self.crew("go", "x", "--workdir", str(sup), env=self.FILEENV)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = self.plan_of(sup)
        self.assertEqual([t["status"] for t in plan["tasks"]], ["done", "done"])
        self.assertFalse(Path(plan["git"]["worktree"]).exists())  # nothing of a task's was left: the copy is removed

    # ---------------------------------------------------------------- inputs

    def test_a_plan_whose_workdir_is_a_folder_inside_a_repository_is_refused_before_anything_is_made(self):
        root = self.repo(files={"README.md": "hi\n", "packages/app/x.txt": "x\n"})
        plan = self.tmp / "myplan.json"
        text = json.dumps({"goal": "g", "workdir": str(root / "packages" / "app"), "tasks": [
            {"id": "t1", "title": "one", "agent": "Luna", "instructions": "i", "acceptance": "a"}]})
        plan.write_text(text)
        for args in (("--dry-run",), ()):
            r = self.crew("run", str(plan), *args, check=False)
            # 3.7: the dry run exits 0; every run then fails with "could not clone ...: repository ... does not exist"
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(f'set "workdir" to {root}', r.stdout)
            self.assertNotIn("could not clone", r.stdout)
        self.assertEqual(plan.read_text(), text)  # 3.7 leaves a half-made git block in the file
        self.assertFalse((self.home / "worktrees").exists())
        r = self.crew("run", str(plan), "--no-git")  # in place, as the message says
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((root / "packages" / "app" / "t1.txt").exists())

    def test_an_empty_branch_prefix_is_refused(self):
        root = self.repo()
        self.write_cfg({"git": {"branch_prefix": ""}})
        r = self.crew("go", "x", "--workdir", str(root), check=False)
        # 3.7: runs, puts a branch with no prefix in your repo (20261113-...-x), and then refuses its own plan
        # on every resume: "invalid git block (needs a crew branch, base commit and worktree)"
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("git.branch_prefix", r.stdout)
        self.assertEqual(sh(root, "git", "branch", "--format=%(refname:short)").split(), ["main"])
        self.assertEqual(self.workers(), [])
        self.write_cfg({"git": {"branch_prefix": "wip/"}})  # a prefix of your own still works
        r = self.crew("go", "x", "--workdir", str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.plan_of(root)["git"]["branch"].startswith("wip/"))


if __name__ == "__main__":
    unittest.main()
