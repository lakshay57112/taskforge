"""Full pipeline run with a scripted model (no API key, no Docker).

Slow (about a minute): it clones click, builds a virtualenv and runs the click test
suite many times. Set TASKFORGE_TEST_REPO to a local clone of pallets/click to avoid
network access.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from scripted_duration import make_responder  # noqa: E402

from taskforge.config import load_config  # noqa: E402
from taskforge.llm import LLM, ScriptedBackend  # noqa: E402
from taskforge.pipeline import RunOptions, run_pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("TASKFORGE_E2E") != "1", reason="set TASKFORGE_E2E=1 to run")
def test_scripted_run(tmp_path):
    cfg = load_config(ROOT / "taskforge.toml")
    # TASKFORGE_E2E_ROOT keeps the generated task (CI runs Harbor on it afterwards)
    root = Path(os.environ.get("TASKFORGE_E2E_ROOT", tmp_path)).resolve()
    cfg.root = root
    if os.environ.get("TASKFORGE_TEST_REPO"):
        cfg.repo.url = os.environ["TASKFORGE_TEST_REPO"]
    cfg.pipeline.workdir = os.environ.get("TASKFORGE_TEST_WORKDIR", str(tmp_path / "work"))
    cfg.pipeline.blind_solvers = 2
    llm = LLM(ScriptedBackend(make_responder()), model="scripted", cache_dir=tmp_path / "cache")

    sc = run_pipeline(cfg, llm, RunOptions(num_tasks=1, skip_harbor=True))

    assert sc["summary"]["tasks_built"] == 1
    assert any(r["stage"] == "novelty" and r["slug"] == "choice-type" for r in sc["rejected"])
    t = sc["tasks"][0]
    assert t["local"]["nop"]["reward"] == 0.0 and t["local"]["oracle"]["reward"] == 1.0
    assert t["fix_rounds"] == 1                                # the buggy first solution was repaired
    assert t["quality"]["cheat_catch_rate"] == "3/3"           # the no-days cheat was caught after hardening
    assert t["details"]["initial_status"].count("survived") == 1
    assert t["quality"]["mutation_score"] is not None
    assert t["quality"]["spec_gaps_fixed"] == 1
    assert t["f2p"] == 14

    task = root / "output" / "click@cf19179" / "tasks" / "click-duration-type"
    for rel in ["instruction.md", "task.toml", "environment/Dockerfile", "solution/solve.sh",
                "solution/solution.patch", "tests/test.sh", "tests/taskforge_verify.py",
                "tests/fail_to_pass.json", "tests/pass_to_pass.json",
                "tests/feature/test_tf_duration_type.py"]:
        assert (task / rel).exists(), rel
    toml = (task / "task.toml").read_text()
    assert 'base_commit = "cf19179e8e2fd54c601378cec9e0d21bb12b6fb1"' in toml
    assert len(json.loads((task / "tests/pass_to_pass.json").read_text())) > 2000
    assert "is not a valid duration" in (task / "instruction.md").read_text()
