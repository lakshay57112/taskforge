"""Orchestrates the full factory run."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .blind import run_blind
from .breaker import run_breaker, run_mutation
from .builder import BuildFailed, build_task, ensure_valid
from .codemap import build_codemap
from .harbor import harbor_available, validate
from .llm import LLMError
from .localcheck import TestRunner
from .package import readme, write_package
from .prompts import system_prompt
from .propose import Proposal, check_novelty, propose, select_diverse
from .repo import GitError, Repo
from .scorecard import build_scorecard, write_scorecard
from .workspace import Workspace


@dataclass
class RunOptions:
    num_tasks: int = 5
    skip_harbor: bool = False
    skip_breaker: bool = False
    skip_blind: bool = False
    skip_mutation: bool = False
    max_propose_rounds: int = 4


def output_dir(cfg) -> Path:
    return cfg.path(cfg.pipeline.output_dir) / f"{cfg.repo.slug}@{cfg.repo.short_commit}"


def make_workspace(cfg, llm, run_dir: Path | None = None, quiet: bool = False) -> Workspace:
    workdir = cfg.path(cfg.pipeline.workdir)
    repo = Repo(cfg.repo.url, workdir / "repo", cfg.repo.base_commit, cfg.repo.source_dir, cfg.repo.tests_dir)
    runner = TestRunner(workdir / "venv", cfg.environment.pytest_version,
                        source_dir=cfg.repo.source_dir, tests_dir=cfg.repo.tests_dir)
    ws = Workspace(cfg=cfg, llm=llm, system=system_prompt(cfg), repo=repo, runner=runner, workdir=workdir,
                   events_path=(run_dir / "logs" / "events.jsonl") if run_dir else None, quiet=quiet)
    return ws


def prepare(ws: Workspace) -> None:
    ws.log(f"repo: {ws.cfg.repo.name} @ {ws.cfg.repo.base_commit} ({ws.cfg.repo.base_ref})")
    ws.repo.ensure_clone()
    ws.runner.ensure()
    ws.codemap = build_codemap(ws.repo)
    ws.log(f"codemap: {len(ws.codemap.areas)} areas, {len(ws.codemap.symbols)} symbols")
    ws.p2p = baseline_p2p(ws)
    ws.log(f"baseline: {len(ws.p2p)} existing tests pass at the base commit")


def baseline_p2p(ws: Workspace) -> list[str]:
    cache = ws.workdir / f"p2p-{ws.cfg.repo.short_commit}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    suite = ws.runner.run_suite(ws.wt("base"))
    passed = sorted(n for n, o in suite.outcomes.items() if o == "passed")
    if not passed:
        raise RuntimeError("the existing test suite produced no passing tests:\n" + suite.output[-3000:])
    cache.write_text(json.dumps(passed, indent=0), encoding="utf-8")
    return passed


def build_one(ws: Workspace, p: Proposal, index: int, opts: RunOptions, tasks_dir: Path) -> tuple[dict, Path]:
    t0 = time.time()
    ws.log(f"[{index}] building '{p.slug}' (area={p.area}, axis={p.axis})")
    d = build_task(ws, p, index)
    quality: dict = {}
    if not opts.skip_breaker:
        quality.update(run_breaker(ws, d))
    if not opts.skip_mutation:
        quality.update(run_mutation(ws, d))
    if not opts.skip_blind:
        quality.update(run_blind(ws, d))
    ensure_valid(ws, d, rounds=1)   # final confirmation with the final tests and document

    touched = sorted(ws.codemap.touched_symbols(d.solution_patch))
    metrics = {
        "task_id": d.task_id,
        "title": d.title,
        "area": p.area,
        "axis": p.axis,
        "requirements": len(d.requirements),
        "f2p": len(d.f2p),
        "p2p_count": len(ws.p2p),
        "local": d.last_check,
        "quality": {
            "cheat_catch_rate": quality.get("cheat_catch_rate"),
            "mutation_score": quality.get("mutation_score"),
            "blind_solve_rate": quality.get("blind_solve_rate"),
            "difficulty": quality.get("difficulty", p.difficulty),
            "spec_gaps_fixed": quality.get("spec_gaps_found", 0),
        },
        "details": {k: v for k, v in quality.items()},
        "fix_rounds": d.metrics["fix_rounds"],
        "fixes": d.metrics["fixes"],
        "touched_symbols": touched,
        "novelty": p.novelty,
        "seconds": round(time.time() - t0, 1),
        "harbor": None,
    }
    task_dir = write_package(ws.cfg, d, ws.p2p, tasks_dir, metrics)
    ws.event("task_built", task=d.task_id, seconds=metrics["seconds"])
    ws.log(f"[{index}] wrote {task_dir}")
    return metrics, task_dir


def run_pipeline(cfg, llm, opts: RunOptions) -> dict:
    start = time.time()
    cfg.llm.model = llm.model
    out = output_dir(cfg)
    tasks_dir = out / "tasks"
    ws = make_workspace(cfg, llm, run_dir=out)
    llm._log = ws.log
    prepare(ws)
    (out).mkdir(parents=True, exist_ok=True)
    (out / "codemap.json").write_text(json.dumps(ws.codemap.to_dict(), indent=1), encoding="utf-8")

    built: list[tuple[dict, Path, Proposal]] = []
    failures: list[dict] = []
    tried: set[str] = set()
    rejected_props: list[Proposal] = []

    for rnd in range(opts.max_propose_rounds):
        need = opts.num_tasks - len(built)
        if need <= 0:
            break
        count = max(need * cfg.pipeline.candidates_per_task, 4)
        ws.log(f"proposing {count} candidates (round {rnd + 1})")
        taken = [b[2] for b in built] + rejected_props
        try:
            candidates = propose(ws.llm, ws.system, cfg, ws.codemap, count, taken, round_no=rnd)
        except LLMError as exc:
            ws.log(f"proposal failed: {exc}")
            continue

        pool = []
        for c in candidates:
            if c.slug in tried:
                continue
            c.novelty = check_novelty(ws.repo, c)
            if c.novelty["exists"]:
                tried.add(c.slug)
                rejected_props.append(c)
                failures.append({"slug": c.slug, "stage": "novelty",
                                 "reason": "feature already exists upstream: " + json.dumps(c.novelty["terms"])[:300]})
                ws.log(f"  rejected '{c.slug}': already exists upstream")
                continue
            pool.append(c)

        last_round = rnd == opts.max_propose_rounds - 1
        while pool and len(built) < opts.num_tasks:
            current = [b[2] for b in built]
            c = select_diverse(pool, current, 1)[0]
            pool.remove(c)
            tried.add(c.slug)
            areas, axes = {p.area for p in current}, {p.axis for p in current}
            if c.area in areas and c.axis in axes and not last_round:
                rejected_props.append(c)
                failures.append({"slug": c.slug, "stage": "diversity",
                                 "reason": f"area {c.area} and axis {c.axis} are both already used"})
                continue
            try:
                metrics, task_dir = build_one(ws, c, len(built) + 1, opts, tasks_dir)
                built.append((metrics, task_dir, c))
            except (BuildFailed, LLMError, GitError, RuntimeError) as exc:
                rejected_props.append(c)
                failures.append({"slug": c.slug, "stage": "build", "reason": str(exc)[:500]})
                ws.event("task_failed", slug=c.slug, reason=str(exc)[:500])
                ws.log(f"  gave up on '{c.slug}': {str(exc)[:200]}")
        llm.save_transcript()

    if not opts.skip_harbor and built:
        ok, why = harbor_available()
        if ok:
            ws.log("harbor: running the nop/oracle validation matrix in Docker")
            for metrics, task_dir, _ in built:
                res = validate(task_dir, out / "harbor-jobs", cfg.pipeline.harbor_oracle_runs, log=ws.log)
                metrics["harbor"] = res
                if res["p2p_calibrated_out"]:
                    metrics["p2p_count"] -= len(res["p2p_calibrated_out"])
                (task_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        else:
            ws.log(f"harbor: skipped ({why}). Run `taskforge validate` later.")

    llm.save_transcript()
    run_info = {
        "generator": f"taskforge {__version__}",
        "repo": cfg.repo.name,
        "base_commit": cfg.repo.base_commit,
        "base_ref": cfg.repo.base_ref,
        "model": cfg.llm.model,
        "llm_mode": cfg.llm.mode,
        "seed": cfg.llm.seed,
        "usage": llm.usage.as_dict(),
        "seconds": round(time.time() - start, 1),
        "requested_tasks": opts.num_tasks,
        "p2p_count": len(ws.p2p),
    }
    (out / "run.json").write_text(json.dumps(run_info, indent=2) + "\n", encoding="utf-8")
    (out / "rejected.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")
    sc = build_scorecard(run_info, [m for m, _, _ in built], failures)
    write_scorecard(out / "reports", sc)
    ws.log(f"done: {len(built)} task(s) in {tasks_dir}; report at {out / 'reports' / 'scorecard.md'}")
    return sc


def rebuild_report(cfg) -> dict:
    out = output_dir(cfg)
    run_info = json.loads((out / "run.json").read_text(encoding="utf-8"))
    failures = json.loads((out / "rejected.json").read_text(encoding="utf-8")) if (out / "rejected.json").exists() else []
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "tasks").glob("*/metrics.json"))]
    sc = build_scorecard(run_info, tasks, failures)
    write_scorecard(out / "reports", sc)
    return sc
