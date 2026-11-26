"""Stage 5 - blind solvers and the requirement-gap judge.

Independent model attempts implement the task from instruction.md alone. When a
hidden test fails, a judge decides whether the document failed to state that
behaviour (spec gap) or the attempt was simply wrong. Spec gaps are written back
into instruction.md. The final solve rate is used as the difficulty label.
"""

from __future__ import annotations

from .builder import TaskDraft
from .localcheck import failure_report, verify
from .prompts import extract_markdown, render, source_context, trim
from .workspace import Workspace

BLIND_SYSTEM = (
    "You are an autonomous AI coding agent. You implement software tasks in an existing "
    "repository, carefully following the written requirements."
)


def _attempt(ws: Workspace, d: TaskDraft, i: int, tag: str) -> dict:
    cfg = ws.cfg
    context = source_context(ws.repo.read, d.context_files)
    prompt = render("blind", repo=cfg.repo.name, instruction=d.instruction, context=context,
                    source_dir=cfg.repo.source_dir)
    problems: list[str] = []
    for retry in range(2):
        p = prompt if not problems else prompt + "\n\nYour edit blocks could not be applied:\n" + "\n".join(problems)
        reply = ws.llm.complete(BLIND_SYSTEM, p, stage="blind", temperature=0.7,
                                seed=cfg.llm.seed + i, variant=retry + (100 if tag == "after" else 0))
        wt = ws.wt("scratch")
        problems = ws.apply_model_edits(wt, reply)
        if not problems:
            break
    if problems:
        return {"solver": i, "reward": 0.0, "status": "edits_failed", "failed": d.f2p, "diff": ""}
    diff = ws.source_diff(wt)
    suite = ws.runner.run_suite(wt, {d.test_rel: d.test_code}, timeout=600)
    score = verify.compute_reward(suite.outcomes, d.f2p, ws.p2p)
    return {"solver": i, "reward": score["reward"], "status": "ok", "failed": score["f2p_failed"],
            "p2p_failed": score["p2p_failed"], "diff": diff,
            "report": failure_report(suite, score["f2p_failed"], limit_chars=5000)}


def run_blind(ws: Workspace, d: TaskDraft) -> dict:
    k = ws.cfg.pipeline.blind_solvers
    before = [_attempt(ws, d, i, "before") for i in range(k)]
    ws.log("  blind solvers: rewards " + ", ".join(str(a["reward"]) for a in before))

    gaps: list[dict] = []
    gap_solvers: set[int] = set()
    for a in before:
        if a["status"] != "ok" or not a["failed"] or a.get("p2p_failed"):
            continue
        verdict = ws.llm.complete_json(ws.system, render(
            "judge_gap", instruction=d.instruction, attempt=trim(a["diff"], 20000),
            failures=a["report"] or "\n".join(a["failed"])), stage="judge")
        for f in verdict.get("findings", []):
            if f.get("verdict") == "spec_gap" and f.get("missing_detail"):
                gaps.append(f)
                gap_solvers.add(a["solver"])

    after = []
    if gaps:
        details = "\n".join(f"- {g['missing_detail']} (seen in {g.get('test', '?')})" for g in gaps)
        new_doc = extract_markdown(ws.llm.complete(
            ws.system, render("clarify", instruction=d.instruction, details=details), stage="clarify")).strip()
        if len(new_doc) > len(d.instruction) * 0.6:
            d.instruction = new_doc + "\n"
            ws.log(f"  clarified instruction.md with {len(gaps)} missing detail(s); re-running one solver")
            after = [_attempt(ws, d, k, "after")]

    # Attempts that failed only because the document was unclear say nothing about
    # difficulty, so they are left out; the post-clarification attempt counts instead.
    considered = [a for a in before if a["solver"] not in gap_solvers] + after
    solved = sum(1 for a in considered if a["reward"] == 1.0)
    rate = solved / len(considered) if considered else 0.0
    difficulty = "easy" if rate >= 0.99 else ("medium" if rate > 0 else "hard")
    solved_before = sum(1 for a in before if a["reward"] == 1.0)
    rate_text = f"{solved_before}/{len(before)}"
    if after:
        rate_text += f" (then {sum(1 for a in after if a['reward'] == 1.0)}/{len(after)} after clarification)"
    strip = lambda xs: [{k: v for k, v in a.items() if k not in ("diff", "report")} for a in xs]
    return {
        "solvers": k,
        "rewards_before": [a["reward"] for a in before],
        "rewards_after_clarify": [a["reward"] for a in after],
        "blind_solve_rate": rate_text,
        "spec_gaps_found": len(gaps),
        "spec_gaps": [{"test": g.get("test"), "missing_detail": g.get("missing_detail")} for g in gaps],
        "difficulty": difficulty,
        "attempts": strip(before) + strip(after),
    }
