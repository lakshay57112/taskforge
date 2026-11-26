"""Stage 7 - write a Harbor 0.25 task package.

<task>/
  instruction.md          requirement document (the only file the agent sees)
  task.toml               metadata: repo, base commit, licence, provenance
  environment/Dockerfile  the repository at the recorded commit, deps installed
  solution/solve.sh       applies solution.patch (used by Harbor's oracle agent)
  solution/solution.patch reference implementation
  tests/test.sh           verifier entry point, writes /logs/verifier/reward.txt
  tests/taskforge_verify.py  outcome recorder + reward rule
  tests/fail_to_pass.json / pass_to_pass.json
  tests/feature/<test file>  hidden feature tests
  README.md               human-readable summary (not visible to the agent)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import __version__
from .builder import TaskDraft
from .localcheck import VERIFY_SCRIPT


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return _toml_str(str(v))


def task_toml(cfg, d: TaskDraft, extra_meta: dict) -> str:
    env = cfg.environment
    meta = {
        "repo": cfg.repo.name,
        "repo_url": cfg.repo.url,
        "base_commit": cfg.repo.base_commit,
        "base_ref": cfg.repo.base_ref,
        "license": cfg.repo.license,
        "language": cfg.repo.language,
        "test_runner": f"pytest=={env.pytest_version}",
        "task_type": "feature",
        "title": d.title,
        "area": d.proposal.area,
        "variation_axis": d.proposal.axis,
        "requirements": len(d.requirements),
        "fail_to_pass": len(d.f2p),
        "generator": f"taskforge {__version__}",
        "model": cfg.llm.model,
    }
    meta.update(extra_meta)
    lines = ['schema_version = "1.4"', "", "[metadata]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in meta.items() if v not in (None, "")]
    lines += [
        "",
        "[verifier]",
        f"timeout_sec = {float(env.verifier_timeout_sec)}",
        "",
        "[agent]",
        f"timeout_sec = {float(env.agent_timeout_sec)}",
        "",
        "[environment]",
        f"build_timeout_sec = {float(env.build_timeout_sec)}",
        f"cpus = {env.cpus}",
        f"memory_mb = {env.memory_mb}",
        "",
    ]
    return "\n".join(lines)


def dockerfile(cfg) -> str:
    r, e = cfg.repo, cfg.environment
    return f"""FROM {e.base_image}

RUN apt-get update \\
 && apt-get install -y --no-install-recommends git ca-certificates \\
 && rm -rf /var/lib/apt/lists/*

# The repository exactly at the recorded base commit (shallow fetch of that one commit).
WORKDIR /app
RUN git init -q . \\
 && git remote add origin {r.url} \\
 && git fetch -q --depth 1 origin {r.base_commit} \\
 && git checkout -q --detach FETCH_HEAD \\
 && test "$(git rev-parse HEAD)" = "{r.base_commit}"

RUN pip install --no-cache-dir -e . "pytest=={e.pytest_version}"
"""


def solve_sh() -> str:
    return """#!/bin/bash
# Reference solution: apply the patch produced by the TaskForge pipeline.
set -euo pipefail
cd /app
git apply --whitespace=nowarn /solution/solution.patch
"""


def test_sh(cfg, d: TaskDraft) -> str:
    tests_dir = cfg.repo.tests_dir
    name = Path(d.test_rel).name
    return f"""#!/bin/bash
# Verifier for {d.task_id}. Writes the reward to /logs/verifier/reward.txt.
set -uo pipefail
cd /app
mkdir -p /logs/verifier

# Restore the original test suite and test configuration, and drop any pytest hook files
# the attempt added, so changes made during the attempt cannot affect grading.
BASE={cfg.repo.base_commit}
git checkout -q "$BASE" -- {tests_dir}
git clean -qfd -- {tests_dir}
for f in pyproject.toml setup.cfg tox.ini pytest.ini; do
  git cat-file -e "$BASE:$f" 2>/dev/null && git checkout -q "$BASE" -- "$f"
done
find . -path ./.git -prune -o \\( -name conftest.py -o -name pytest.ini -o -name tox.ini \\
  -o -name sitecustomize.py -o -name usercustomize.py \\) -print | while read -r f; do
  git cat-file -e "$BASE:${{f#./}}" 2>/dev/null || rm -f "$f"
done
cp /tests/feature/{name} {tests_dir}/{name}

export TASKFORGE_OUTCOMES=/logs/verifier/outcomes.json
PYTHONPATH=/tests python -m pytest -p taskforge_verify -p no:cacheprovider \\
    --continue-on-collection-errors -q --tb=short > /logs/verifier/pytest.log 2>&1

python /tests/taskforge_verify.py score \\
    --outcomes /logs/verifier/outcomes.json \\
    --f2p /tests/fail_to_pass.json \\
    --p2p /tests/pass_to_pass.json \\
    --out /logs/verifier/reward.txt \\
    --details /logs/verifier/score.json
"""


def readme(cfg, d: TaskDraft, metrics: dict) -> str:
    reqs = "\n".join(f"| {r.get('id')} | {r.get('text')} | "
                     f"{sum(1 for n in d.f2p if n.split('::')[-1].lower().startswith('test_' + str(r.get('id')).lower()))} |"
                     for r in d.requirements)
    q = metrics.get("quality", {})
    return f"""# {d.title}

Generated by TaskForge from `{cfg.repo.name}` at `{cfg.repo.base_commit}` ({cfg.repo.base_ref}).

- Area: `{d.proposal.area}` - variation axis: `{d.proposal.axis}`
- Feature tests (fail_to_pass): {len(d.f2p)} - regression tests (pass_to_pass): {metrics.get('p2p_count', '?')}
- Cheat-catch rate: {q.get('cheat_catch_rate', 'n/a')} - mutation score: {q.get('mutation_score', 'n/a')} - blind solve rate: {q.get('blind_solve_rate', 'n/a')} - difficulty: {q.get('difficulty', 'n/a')}

## Requirement coverage

| Requirement | Summary | Tests |
|---|---|---|
{reqs}

## Run it

```bash
harbor run -p <this folder> -a nop -k 1 -o jobs --no-delete -y      # expected reward 0.0
harbor run -p <this folder> -a oracle -k 1 -o jobs --no-delete -y   # expected reward 1.0
```
"""


def write_package(cfg, d: TaskDraft, p2p: list[str], out_root: Path, metrics: dict) -> Path:
    task_dir = Path(out_root) / d.task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "solution").mkdir()
    (task_dir / "tests" / "feature").mkdir(parents=True)

    def w(rel: str, text: str, exe: bool = False) -> None:
        path = task_dir / rel
        path.write_text(text, encoding="utf-8", newline="\n")
        if exe:
            path.chmod(0o755)

    w("instruction.md", d.instruction)
    extra = {
        "difficulty": metrics.get("quality", {}).get("difficulty", d.proposal.difficulty),
        "cheat_catch_rate": metrics.get("quality", {}).get("cheat_catch_rate"),
        "mutation_score": metrics.get("quality", {}).get("mutation_score"),
        "blind_solve_rate": metrics.get("quality", {}).get("blind_solve_rate"),
    }
    w("task.toml", task_toml(cfg, d, extra))
    w("environment/Dockerfile", dockerfile(cfg))
    w("solution/solve.sh", solve_sh(), exe=True)
    w("solution/solution.patch", d.solution_patch)
    w("tests/test.sh", test_sh(cfg, d), exe=True)
    w("tests/taskforge_verify.py", VERIFY_SCRIPT.read_text(encoding="utf-8"))
    w("tests/fail_to_pass.json", json.dumps(d.f2p, indent=1, ensure_ascii=False) + "\n")
    w("tests/pass_to_pass.json", json.dumps(p2p, indent=1, ensure_ascii=False) + "\n")
    w(f"tests/feature/{Path(d.test_rel).name}", d.test_code)
    w("README.md", readme(cfg, d, metrics))
    w("metrics.json", json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    return task_dir
