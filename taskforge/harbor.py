"""Stage 6 - Harbor validation matrix (runs inside Docker).

nop    -> repository untouched, expected reward 0.0
oracle -> reference solution applied, expected reward 1.0 (repeated to catch flaky tests)

Commands are exactly the ones in the hackathon guide:
    harbor run -p <task_dir> -a <agent> -k 1 -o <out_dir> --no-delete -y
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


def harbor_available() -> tuple[bool, str]:
    if not shutil.which("harbor"):
        return False, "harbor CLI not found on PATH (pip install 'harbor==0.25.*')"
    if not shutil.which("docker"):
        return False, "docker not found on PATH"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return False, "Docker daemon is not running"
    return True, ""


def _read_reward(trial_root: Path) -> tuple[float | None, Path | None]:
    rewards = sorted(trial_root.rglob("verifier/reward.txt"), key=lambda p: p.stat().st_mtime)
    if rewards:
        try:
            return float(rewards[-1].read_text().strip().split()[0]), rewards[-1].parent
        except (ValueError, IndexError):
            return None, rewards[-1].parent
    for result in sorted(trial_root.rglob("result.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(result.read_text())
        except ValueError:
            continue
        vr = data.get("verifier_result") or {}
        rew = vr.get("rewards") or {}
        if "reward" in rew:
            return float(rew["reward"]), result.parent / "verifier"
    return None, None


def run_trial(task_dir: Path, agent: str, out_dir: Path, timeout: int = 3600) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["harbor", "run", "-p", str(task_dir), "-a", agent, "-k", "1", "-o", str(out_dir),
           "--no-delete", "-y"]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        log = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        log = f"timeout after {timeout}s"
    (out_dir / f"harbor-{agent}.log").write_text(log, encoding="utf-8")
    reward, verifier_dir = _read_reward(out_dir)
    return {"agent": agent, "reward": reward, "seconds": round(time.time() - start, 1),
            "command": " ".join(cmd), "verifier_dir": str(verifier_dir) if verifier_dir else None}


def calibrate_p2p(task_dir: Path, verifier_dir: str | None) -> list[str]:
    """Drop regression tests that do not pass inside the container on the untouched repo.

    The pass_to_pass list is computed on the host; a handful of tests can depend on
    the platform or Python version. The nop run shows which ones those are.
    """
    if not verifier_dir:
        return []
    outcomes_path = Path(verifier_dir) / "outcomes.json"
    if not outcomes_path.exists():
        return []
    outcomes = json.loads(outcomes_path.read_text(encoding="utf-8"))
    p2p_path = Path(task_dir) / "tests" / "pass_to_pass.json"
    p2p = json.loads(p2p_path.read_text(encoding="utf-8"))
    if not outcomes:
        return []
    dropped = [t for t in p2p if outcomes.get(t) != "passed"]
    if dropped and len(dropped) < 0.05 * len(p2p):
        kept = [t for t in p2p if t not in set(dropped)]
        p2p_path.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return dropped
    return []


def lint(task_dir: Path) -> list[str]:
    """Harbor's static task checks; returns the findings (empty list = clean)."""
    proc = subprocess.run(["harbor", "lint", str(task_dir)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return [l.strip() for l in (proc.stdout + proc.stderr).splitlines() if "[" in l and "]" in l]


def validate(task_dir: Path, out_root: Path, oracle_runs: int = 3, log=print) -> dict:
    task_dir = Path(task_dir)
    out_root = Path(out_root) / task_dir.name
    lint_findings = lint(task_dir)
    nop = run_trial(task_dir, "nop", out_root / "nop")
    dropped = calibrate_p2p(task_dir, nop["verifier_dir"])
    if dropped:
        log(f"    calibrated pass_to_pass: removed {len(dropped)} environment-dependent tests; re-running nop")
        nop = run_trial(task_dir, "nop", out_root / "nop-recheck")
    oracle = [run_trial(task_dir, "oracle", out_root / f"oracle-{i + 1}") for i in range(oracle_runs)]
    result = {
        "nop_reward": nop["reward"],
        "oracle_rewards": [o["reward"] for o in oracle],
        "p2p_calibrated_out": dropped,
        "passed": nop["reward"] == 0.0 and all(o["reward"] == 1.0 for o in oracle),
        "stable": len({o["reward"] for o in oracle}) == 1,
        "lint": lint_findings,
        "trials": [nop] + oracle,
    }
    log(f"    harbor: nop={result['nop_reward']} oracle={result['oracle_rewards']} "
        f"-> {'PASS' if result['passed'] else 'FAIL'}")
    return result
