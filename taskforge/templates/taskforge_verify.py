"""Test outcome recorder and reward calculator.

This file is shipped inside every task's tests/ folder and is also used by the
TaskForge pipeline for its local checks, so the pipeline and the Harbor verifier
always score a run in exactly the same way.

As a pytest plugin (``-p taskforge_verify``) it writes {node_id: outcome} to the
file named by the TASKFORGE_OUTCOMES environment variable.

As a script it computes the reward:

    python taskforge_verify.py score --outcomes o.json --f2p f.json --p2p p.json --out reward.txt

Reward rules:
  * any listed regression test (pass_to_pass) not passing -> 0.0
  * otherwise -> share of feature tests (fail_to_pass) that pass (1.0 when all pass)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_outcomes: dict[str, str] = {}


def pytest_runtest_logreport(report):  # pragma: no cover - runs inside pytest
    node = report.nodeid
    if report.when == "setup":
        if report.failed:
            _outcomes[node] = "error"
        elif report.skipped:
            _outcomes[node] = "xfailed" if hasattr(report, "wasxfail") else "skipped"
    elif report.when == "call":
        if hasattr(report, "wasxfail"):
            _outcomes[node] = "xpassed" if report.passed else "xfailed"
        elif report.passed:
            _outcomes[node] = "passed"
        elif report.failed:
            _outcomes[node] = "failed"
        else:
            _outcomes[node] = "skipped"
    elif report.when == "teardown" and report.failed:
        _outcomes[node] = "error"


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - runs inside pytest
    path = os.environ.get("TASKFORGE_OUTCOMES")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_outcomes, fh, indent=0, sort_keys=True)


def compute_reward(outcomes: dict[str, str], f2p: list[str], p2p: list[str]) -> dict:
    p2p_failed = [t for t in p2p if outcomes.get(t) != "passed"]
    f2p_passed = [t for t in f2p if outcomes.get(t) == "passed"]
    if p2p_failed:
        reward = 0.0
    elif not f2p:
        reward = 0.0
    else:
        reward = len(f2p_passed) / len(f2p)
    return {
        "reward": round(reward, 4),
        "f2p_passed": len(f2p_passed),
        "f2p_total": len(f2p),
        "p2p_failed": len(p2p_failed),
        "p2p_total": len(p2p),
        "p2p_failed_examples": p2p_failed[:10],
        "f2p_failed": [t for t in f2p if t not in f2p_passed],
    }


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--outcomes", required=True)
    s.add_argument("--f2p", required=True)
    s.add_argument("--p2p", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--details")
    args = ap.parse_args(argv)

    try:
        with open(args.outcomes, encoding="utf-8") as fh:
            outcomes = json.load(fh)
    except (OSError, ValueError):
        outcomes = {}
    with open(args.f2p, encoding="utf-8") as fh:
        f2p = json.load(fh)
    with open(args.p2p, encoding="utf-8") as fh:
        p2p = json.load(fh)

    result = compute_reward(outcomes, f2p, p2p)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(f"{result['reward']}\n")
    if args.details:
        with open(args.details, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("examples")}))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
