"""Run report: per-task quality metrics, variation (overlap) matrix and model usage."""

from __future__ import annotations

import json
from pathlib import Path

from .codemap import jaccard


def overlap_matrix(tasks: list[dict]) -> dict:
    ids = [t["task_id"] for t in tasks]
    sets = {t["task_id"]: set(t.get("touched_symbols", [])) for t in tasks}
    matrix = {a: {b: round(jaccard(sets[a], sets[b]), 2) for b in ids} for a in ids}
    pairs = [matrix[a][b] for i, a in enumerate(ids) for b in ids[i + 1:]]
    return {"matrix": matrix, "max_pairwise": max(pairs) if pairs else 0.0,
            "mean_pairwise": round(sum(pairs) / len(pairs), 3) if pairs else 0.0}


def build_scorecard(run_info: dict, tasks: list[dict], failures: list[dict]) -> dict:
    return {
        "run": run_info,
        "summary": {
            "tasks_built": len(tasks),
            "candidates_rejected": len(failures),
            "distinct_areas": len({t["area"] for t in tasks}),
            "distinct_axes": len({t["axis"] for t in tasks}),
            "local_validation_passed": sum(1 for t in tasks if t["local"]["oracle"].get("reward") == 1.0
                                           and t["local"]["nop"].get("reward") == 0.0),
            "harbor_validated": sum(1 for t in tasks if (t.get("harbor") or {}).get("passed")),
        },
        "overlap": overlap_matrix(tasks),
        "tasks": tasks,
        "rejected": failures,
    }


def _fmt(v) -> str:
    return "-" if v is None or v == "" else str(v)


def scorecard_markdown(sc: dict) -> str:
    run, s = sc["run"], sc["summary"]
    lines = [
        "# TaskForge run report",
        "",
        f"- Repository: `{run['repo']}` at `{run['base_commit']}` ({run['base_ref']})",
        f"- Model: `{run['model']}` (mode: {run['llm_mode']}) - API calls: {run['usage']['api_calls']}, "
        f"cache hits: {run['usage']['cache_hits']}, tokens in/out: {run['usage']['prompt_tokens']}/{run['usage']['output_tokens']}",
        f"- Duration: {run['seconds']}s",
        f"- Tasks built: **{s['tasks_built']}** - distinct areas: {s['distinct_areas']} - distinct axes: {s['distinct_axes']} "
        f"- rejected candidates: {s['candidates_rejected']}",
        f"- Local nop/oracle passed: {s['local_validation_passed']}/{s['tasks_built']} - "
        f"Harbor validated: {s['harbor_validated']}/{s['tasks_built']}",
        "",
        "## Tasks",
        "",
        "| Task | Area | Axis | Reqs | F2P | Local nop/oracle | Harbor nop/oracle | Cheats caught | Mutation | Blind solve | Difficulty | Fix rounds |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in sc["tasks"]:
        q, h = t.get("quality", {}), t.get("harbor") or {}
        harbor = f"{_fmt(h.get('nop_reward'))} / {_fmt(h.get('oracle_rewards'))}" if h else "not run"
        lines.append(
            f"| `{t['task_id']}` | {t['area']} | {t['axis']} | {t['requirements']} | {t['f2p']} | "
            f"{t['local']['nop'].get('reward')} / {t['local']['oracle'].get('reward')} | {harbor} | "
            f"{_fmt(q.get('cheat_catch_rate'))} | {_fmt(q.get('mutation_score'))} | "
            f"{_fmt(q.get('blind_solve_rate'))} | {_fmt(q.get('difficulty'))} | {t['fix_rounds']} |")
    ov = sc["overlap"]
    ids = list(ov["matrix"])
    lines += ["", "## Variation: overlap of touched code (Jaccard, 0 = disjoint)", "",
              f"Max pairwise overlap: **{ov['max_pairwise']}** - mean: {ov['mean_pairwise']}", ""]
    if ids:
        lines.append("| | " + " | ".join(f"T{i + 1}" for i in range(len(ids))) + " |")
        lines.append("|---" * (len(ids) + 1) + "|")
        for i, a in enumerate(ids):
            lines.append(f"| T{i + 1} `{a}` | " + " | ".join(str(ov["matrix"][a][b]) for b in ids) + " |")
    if sc["rejected"]:
        lines += ["", "## Rejected candidates", ""]
        lines += [f"- `{r['slug']}` ({r.get('stage')}): {r.get('reason', '')[:200]}" for r in sc["rejected"]]
    return "\n".join(lines) + "\n"


def write_scorecard(out_dir: Path, sc: dict) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard.json").write_text(json.dumps(sc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "scorecard.md").write_text(scorecard_markdown(sc), encoding="utf-8")
