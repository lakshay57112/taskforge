"""Fast local checks that mirror Harbor's nop/oracle runs without Docker.

The suite runs in a private virtualenv with the repository's own test runner,
importing the package straight from the worktree. Scoring uses the same
taskforge_verify.py that ships inside each task, so a local pass predicts a
Harbor pass (Harbor then confirms it inside the real container).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .repo import run

TEMPLATES = Path(__file__).parent / "templates"
VERIFY_SCRIPT = TEMPLATES / "taskforge_verify.py"


def _load_verify():
    spec = importlib.util.spec_from_file_location("taskforge_verify", VERIFY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify = _load_verify()


@dataclass
class SuiteResult:
    outcomes: dict[str, str]
    output: str
    returncode: int
    root: str = ""

    def failures_for(self, nodes: list[str]) -> list[str]:
        return [n for n in nodes if self.outcomes.get(n) != "passed"]


_NOISE = [
    (re.compile(r"\bin \d+(\.\d+)?s\b"), "in <time>"),
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "0x<addr>"),
    (re.compile(r"/tmp/pytest-of-[^/\s]+/pytest-\d+"), "<tmp>"),
    (re.compile(r"[A-Za-z]:\\[^\s:]*?\\worktrees\\\w+"), "<repo>"),
]


def normalize(text: str, root: str = "") -> str:
    """Remove machine-specific noise so prompts (and replay keys) are stable across hosts."""
    if root:
        text = text.replace(root, "<repo>").replace(root.replace("\\", "/"), "<repo>")
    text = re.sub(r"\S*/worktrees/\w+", "<repo>", text)
    for pattern, repl in _NOISE:
        text = pattern.sub(repl, text)
    return text


@dataclass
class CheckResult:
    nop: dict
    oracle: dict
    nop_suite: SuiteResult | None = None
    oracle_suite: SuiteResult | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class TestRunner:
    def __init__(self, venv_dir: Path, pytest_version: str, python: str | None = None,
                 source_dir: str = "src", tests_dir: str = "tests"):
        self.venv_dir = Path(venv_dir)
        self.pytest_version = pytest_version
        self.base_python = python or sys.executable
        self.src_root = source_dir.split("/")[0] if source_dir.startswith("src") else "."
        self.tests_dir = tests_dir

    @property
    def python(self) -> str:
        sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        return str(self.venv_dir / sub)

    def ensure(self) -> None:
        if not Path(self.python).exists():
            run([self.base_python, "-m", "venv", str(self.venv_dir)])
        probe = run([self.python, "-c", "import pytest;print(pytest.__version__)"], check=False)
        if probe.returncode != 0 or probe.stdout.strip() != self.pytest_version:
            run([self.python, "-m", "pip", "install", "--quiet", f"pytest=={self.pytest_version}"],
                timeout=900)

    def run_suite(self, worktree: Path, extra_tests: dict[str, str] | None = None,
                  only: list[str] | None = None, timeout: int = 900) -> SuiteResult:
        """Run the test suite in `worktree`, optionally adding test files first.

        `only` limits the run to the given test files (paths relative to the worktree).
        """
        worktree = Path(worktree)
        for rel, content in (extra_tests or {}).items():
            dest = worktree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8", newline="\n")

        out_file = worktree / ".taskforge_outcomes.json"
        if out_file.exists():
            out_file.unlink()
        env = dict(os.environ)
        env["TASKFORGE_OUTCOMES"] = str(out_file)
        plugin_dir = str(TEMPLATES)
        src = str(worktree / self.src_root)
        env["PYTHONPATH"] = os.pathsep.join([src, plugin_dir])
        env.pop("PYTEST_ADDOPTS", None)
        cmd = [self.python, "-m", "pytest", "-p", "taskforge_verify", "-p", "no:cacheprovider",
               "--continue-on-collection-errors", "-q", "--tb=short", "-rN"]
        cmd += only or []
        try:
            proc = subprocess.run(cmd, cwd=worktree, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            output, code = proc.stdout + proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            output, code = f"TIMEOUT after {timeout}s\n{exc.stdout or ''}", -1
        outcomes = {}
        if out_file.exists():
            outcomes = json.loads(out_file.read_text(encoding="utf-8"))
            out_file.unlink()
        return SuiteResult(outcomes=outcomes, output=output, returncode=code, root=str(worktree))


def failure_report(result: SuiteResult, nodes: list[str], limit_chars: int = 6000) -> str:
    """Pull the pytest failure sections for the given nodes out of the run output."""
    failing = result.failures_for(nodes)
    if not failing:
        return ""
    text = normalize(result.output, result.root)
    pieces = []
    for node in failing:
        name = node.split("::")[-1]
        idx = text.find(f"_ {name} _")
        if idx == -1:
            idx = text.find(name)
        state = result.outcomes.get(node, "not run (collection error?)")
        snippet = text[idx : idx + 1500] if idx != -1 else ""
        pieces.append(f"### {node} -> {state}\n{snippet}")
    report = "\n\n".join(pieces)
    if "ERROR collecting" in text or "error during collection" in text.lower():
        i = text.find("ERROR collecting")
        report = f"### Collection errors\n{text[i:i + 2500]}\n\n{report}"
    return report[:limit_chars]


def check_task(runner: TestRunner, base_wt: Path, oracle_wt: Path, test_rel: str, test_code: str,
               p2p: list[str], solution_patch: str, apply_patch) -> CheckResult:
    """Local equivalent of Harbor's nop and oracle passes.

    base_wt is reset to the base commit; oracle_wt gets the solution applied.
    Feature tests that pass on the untouched repo are reported as problems,
    because a fail_to_pass test must fail before the feature exists.
    """
    nop_suite = runner.run_suite(base_wt, {test_rel: test_code})
    f2p = sorted(n for n in nop_suite.outcomes if n.startswith(test_rel + "::"))
    problems: list[str] = []

    ok, err = apply_patch(oracle_wt, solution_patch)
    if not ok:
        return CheckResult(nop={}, oracle={}, problems=[f"solution patch does not apply: {err}"])
    oracle_suite = runner.run_suite(oracle_wt, {test_rel: test_code})
    oracle_f2p = sorted(n for n in oracle_suite.outcomes if n.startswith(test_rel + "::"))
    f2p = sorted(set(f2p) | set(oracle_f2p))

    if not f2p:
        problems.append("no feature tests were collected (syntax or import error in the test file?)")
    passing_at_base = [n for n in f2p if nop_suite.outcomes.get(n) == "passed"]
    if passing_at_base:
        problems.append("feature tests pass before the feature exists: " + ", ".join(passing_at_base))

    nop = verify.compute_reward(nop_suite.outcomes, f2p, p2p)
    oracle = verify.compute_reward(oracle_suite.outcomes, f2p, p2p)
    if nop["p2p_failed"]:
        problems.append("regression tests fail on the untouched repo: "
                        + ", ".join(nop["p2p_failed_examples"]))
    if oracle["reward"] != 1.0:
        if oracle["f2p_failed"]:
            problems.append("feature tests fail with the reference solution: "
                            + ", ".join(oracle["f2p_failed"]))
        if oracle["p2p_failed"]:
            problems.append("reference solution breaks existing tests: "
                            + ", ".join(oracle["p2p_failed_examples"]))
    result = CheckResult(nop=nop, oracle=oracle, nop_suite=nop_suite, oracle_suite=oracle_suite,
                         problems=problems)
    result.nop["f2p_nodes"] = f2p
    return result


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
