"""Command-line entry point: `taskforge <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config


def _cfg(args):
    cfg = load_config(args.config)
    if getattr(args, "replay", False):
        cfg.llm.mode = "replay"
    if getattr(args, "model", None):
        cfg.llm.model = args.model
    return cfg


def cmd_run(args) -> int:
    from .llm import build_llm
    from .pipeline import RunOptions, run_pipeline

    cfg = _cfg(args)
    llm = build_llm(cfg)
    opts = RunOptions(
        num_tasks=args.tasks or cfg.pipeline.num_tasks,
        skip_harbor=args.skip_harbor,
        skip_breaker=args.skip_breaker,
        skip_blind=args.skip_blind,
        skip_mutation=args.skip_mutation,
    )
    sc = run_pipeline(cfg, llm, opts)
    return 0 if sc["summary"]["tasks_built"] else 1


def cmd_validate(args) -> int:
    from .harbor import harbor_available, validate
    from .pipeline import output_dir, rebuild_report

    cfg = _cfg(args)
    ok, why = harbor_available()
    if not ok:
        print(f"Cannot run Harbor: {why}")
        return 2
    dirs = [Path(p) for p in args.tasks] or sorted(p.parent for p in (output_dir(cfg) / "tasks").glob("*/task.toml"))
    all_ok = True
    for task_dir in dirs:
        print(f"validating {task_dir}")
        res = validate(task_dir, output_dir(cfg) / "harbor-jobs", args.oracle_runs or cfg.pipeline.harbor_oracle_runs)
        all_ok &= res["passed"]
        mpath = task_dir / "metrics.json"
        if mpath.exists():
            metrics = json.loads(mpath.read_text(encoding="utf-8"))
            metrics["harbor"] = res
            mpath.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if (output_dir(cfg) / "run.json").exists():
        rebuild_report(cfg)
    return 0 if all_ok else 1


def cmd_check(args) -> int:
    """Re-check task packages locally (no Docker): apply nothing -> 0.0, apply solution -> 1.0."""
    from .llm import LLM
    from .localcheck import verify
    from .pipeline import make_workspace
    from .repo import apply_patch

    cfg = _cfg(args)
    ws = make_workspace(cfg, LLM(None, model=cfg.llm.model, cache_dir=cfg.path(cfg.llm.cache_dir), mode="replay"))
    ws.repo.ensure_clone()
    ws.runner.ensure()
    all_ok = True
    for task_dir in map(Path, args.tasks):
        f2p = json.loads((task_dir / "tests/fail_to_pass.json").read_text(encoding="utf-8"))
        p2p = json.loads((task_dir / "tests/pass_to_pass.json").read_text(encoding="utf-8"))
        test_file = next((task_dir / "tests/feature").glob("*.py"))
        rel = f"{cfg.repo.tests_dir}/{test_file.name}"
        code = test_file.read_text(encoding="utf-8")
        nop = verify.compute_reward(ws.runner.run_suite(ws.wt("base"), {rel: code}).outcomes, f2p, p2p)
        wt = ws.wt("oracle")
        ok, err = apply_patch(wt, (task_dir / "solution/solution.patch").read_text(encoding="utf-8"))
        oracle = verify.compute_reward(ws.runner.run_suite(wt, {rel: code}).outcomes, f2p, p2p) if ok else {"reward": None}
        passed = nop["reward"] == 0.0 and oracle["reward"] == 1.0
        all_ok &= passed
        print(f"{task_dir.name}: nop={nop['reward']} oracle={oracle['reward']} -> {'PASS' if passed else 'FAIL'}")
    return 0 if all_ok else 1


def cmd_codemap(args) -> int:
    from .codemap import build_codemap
    from .repo import Repo

    cfg = _cfg(args)
    repo = Repo(cfg.repo.url, cfg.path(cfg.pipeline.workdir) / "repo", cfg.repo.base_commit,
                cfg.repo.source_dir, cfg.repo.tests_dir)
    repo.ensure_clone()
    print(build_codemap(repo).prompt_summary())
    return 0


def cmd_models(args) -> int:
    from .llm import GeminiBackend

    for name in GeminiBackend("unused").list_models():
        print(name)
    return 0


def cmd_ping(args) -> int:
    """Send a tiny request to each model and report which ones answer right now."""
    import time as _time

    from .llm import GeminiBackend, classify_error

    cfg = _cfg(args)
    models = args.models or [cfg.llm.model] + list(cfg.llm.fallback_models)
    backend = GeminiBackend(cfg.llm.model)
    working = []
    for m in models:
        start = _time.time()
        try:
            text, _ = backend.generate("Reply with the single word OK.", "ping", temperature=1.0,
                                       seed=1, json_mode=False, model=m)
            print(f"  OK         {m:<24} {(_time.time() - start):5.1f}s")
            working.append(m)
        except Exception as exc:  # report every failure kind
            kind = {"busy": "BUSY", "quota": "NO QUOTA", "unusable": "NO ACCESS"}.get(
                classify_error(str(exc)), "ERROR")
            print(f"  {kind:<10} {m:<24} {str(exc)[:80]}")
    if working:
        print(f"\nUse one with: taskforge run --model {working[0]}")
    return 0 if working else 1


def cmd_report(args) -> int:
    from .pipeline import rebuild_report

    sc = rebuild_report(_cfg(args))
    print(json.dumps(sc["summary"], indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taskforge", description="Automated Harbor task factory")
    ap.add_argument("--config", default="taskforge.toml", help="path to taskforge.toml")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="generate and validate tasks")
    r.add_argument("--tasks", type=int, help="number of tasks to build (default from config)")
    r.add_argument("--replay", action="store_true", help="use only recorded model answers (no API key)")
    r.add_argument("--model", help="override the model name")
    r.add_argument("--skip-harbor", action="store_true", help="skip the Docker/Harbor validation")
    r.add_argument("--skip-breaker", action="store_true")
    r.add_argument("--skip-blind", action="store_true")
    r.add_argument("--skip-mutation", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="run Harbor nop/oracle on task packages")
    v.add_argument("tasks", nargs="*", help="task folders (default: all tasks of the configured run)")
    v.add_argument("--oracle-runs", type=int)
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("check", help="local nop/oracle check of task packages (no Docker)")
    c.add_argument("tasks", nargs="+")
    c.set_defaults(func=cmd_check)

    sub.add_parser("codemap", help="print the codebase map").set_defaults(func=cmd_codemap)
    sub.add_parser("models", help="list Gemini models available to your key").set_defaults(func=cmd_models)
    sub.add_parser("report", help="rebuild the scorecard from task folders").set_defaults(func=cmd_report)
    pg = sub.add_parser("ping", help="check which Gemini models are answering right now")
    pg.add_argument("models", nargs="*", help="models to try (default: model + fallback_models)")
    pg.set_defaults(func=cmd_ping)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
