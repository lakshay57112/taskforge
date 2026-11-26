"""Stage 3 - builder: requirement document, reference solution, hidden tests, and the
local nop/oracle validation loop that repairs whichever part is wrong."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .localcheck import check_task, failure_report, normalize
from .prompts import doc_title, extract_markdown, parse_requirements, render, source_context, trim
from .propose import Proposal
from .repo import apply_patch, extract_code_block
from .workspace import Workspace


class BuildFailed(RuntimeError):
    pass


@dataclass
class TaskDraft:
    proposal: Proposal
    task_id: str
    title: str = ""
    instruction: str = ""
    requirements: list[dict] = field(default_factory=list)
    solution_patch: str = ""
    test_rel: str = ""
    test_code: str = ""
    f2p: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)
    style_file: str = ""
    metrics: dict = field(default_factory=dict)
    last_check: dict = field(default_factory=dict)


def context_files_for(ws: Workspace, p: Proposal) -> list[str]:
    known = set(ws.repo.list_files(ws.cfg.repo.source_dir))
    files = list(ws.cfg.repo.always_include)
    area = ws.codemap.area(p.area) if ws.codemap else None
    if area:
        files.append(area.file)
    files += [f for f in p.files if f in known]
    return list(dict.fromkeys(f for f in files if f in known))


def _style_example(ws: Workspace, p: Proposal) -> tuple[str, str]:
    area = ws.codemap.area(p.area) if ws.codemap else None
    candidates = (area.tests if area else []) + ["tests/test_options.py", "tests/test_basic.py"]
    for rel in candidates:
        text = ws.repo.read(rel)
        if text:
            return rel, "\n".join(text.splitlines()[:110])
    return "", ""


def build_task(ws: Workspace, p: Proposal, index: int) -> TaskDraft:
    cfg = ws.cfg
    task_id = f"{cfg.repo.slug}-{p.slug}"
    d = TaskDraft(proposal=p, task_id=task_id)
    d.context_files = context_files_for(ws, p)
    d.test_rel = f"{cfg.repo.tests_dir}/test_tf_{p.slug.replace('-', '_')}.py"
    context = source_context(ws.repo.read, d.context_files)
    d.metrics = {"fix_rounds": 0, "solution_attempts": 0, "fixes": []}

    # 1. requirement document
    ws.log(f"  spec: writing instruction.md for '{p.title}'")
    reply = ws.llm.complete(ws.system, render("spec", proposal=p.as_prompt(), context=context), stage="spec")
    d.instruction = extract_markdown(reply)
    d.title = doc_title(d.instruction, p.title)
    d.requirements = parse_requirements(d.instruction)
    if len(d.instruction) < 300 or not d.requirements:
        raise BuildFailed("requirement document is empty or has no requirements")

    # 2. reference solution
    ws.log("  solution: writing the reference implementation")
    d.solution_patch = _write_solution(ws, d, context)

    # 3. hidden tests
    ws.log("  tests: writing fail-to-pass tests")
    d.style_file, style = _style_example(ws, p)
    req_text = "\n".join(f"- {r.get('id')}: {r.get('text')}" for r in d.requirements)
    reply = ws.llm.complete(ws.system, render(
        "tests", instruction=d.instruction, requirements=req_text, style_example=style,
        test_path=d.test_rel, pytest_version=cfg.environment.pytest_version), stage="tests")
    d.test_code = extract_code_block(reply)

    # 4. validate locally and repair
    ensure_valid(ws, d, rounds=cfg.pipeline.max_fix_rounds)
    return d


def _write_solution(ws: Workspace, d: TaskDraft, context: str) -> str:
    prompt = render("solution", instruction=d.instruction, context=context,
                    source_dir=ws.cfg.repo.source_dir)
    problems: list[str] = []
    for attempt in range(3):
        d.metrics["solution_attempts"] += 1
        p = prompt if not problems else (
            prompt + "\n\n## Your previous edit blocks could not be applied\n" + "\n".join(problems)
            + "\nReturn the full corrected set of edit blocks against the ORIGINAL files.")
        reply = ws.llm.complete(ws.system, p, stage="solution", variant=attempt)
        wt = ws.wt("oracle")
        problems = ws.apply_model_edits(wt, reply)
        if not problems:
            patch = ws.source_diff(wt)
            if patch.strip():
                return patch
            problems = ["the edits did not change any file"]
        ws.log(f"    edit blocks rejected (attempt {attempt + 1}): {problems[0][:120]}")
    raise BuildFailed("could not produce an applicable reference solution")


def run_check(ws: Workspace, d: TaskDraft):
    base_wt = ws.wt("base")
    oracle_wt = ws.wt("oracle")
    return check_task(ws.runner, base_wt, oracle_wt, d.test_rel, d.test_code, ws.p2p,
                      d.solution_patch, apply_patch)


def ensure_valid(ws: Workspace, d: TaskDraft, rounds: int) -> None:
    """Run the local nop/oracle check; on failure ask the model to fix solution or tests."""
    for rnd in range(rounds + 1):
        check = run_check(ws, d)
        d.last_check = {"nop": _short(check.nop), "oracle": _short(check.oracle), "problems": check.problems}
        if check.ok:
            d.f2p = check.nop["f2p_nodes"]
            ws.log(f"  local check passed: nop={check.nop['reward']} oracle={check.oracle['reward']} "
                   f"({len(d.f2p)} feature tests, {len(ws.p2p)} regression tests)")
            return
        if rnd == rounds:
            break
        d.metrics["fix_rounds"] += 1
        ws.log(f"  local check failed (round {rnd + 1}): {check.problems[0][:160]}")
        _repair(ws, d, check, rnd)
    raise BuildFailed("local validation still failing after repairs: " + "; ".join(d.last_check["problems"])[:500])


def extract_test_file(reply: str) -> str:
    """The longest ```python block that looks like a pytest file (not an edit block)."""
    blocks = re.findall(r"```python[ \t]*\n(.*?)```", reply, re.S)
    blocks = [b for b in blocks if "def test_" in b and "<<<<<<< SEARCH" not in b]
    return (max(blocks, key=len).rstrip() + "\n") if blocks else ""


def _short(r: dict) -> dict:
    return {k: r[k] for k in ("reward", "f2p_passed", "f2p_total", "p2p_failed") if k in r}


def _repair(ws: Workspace, d: TaskDraft, check, rnd: int) -> None:
    f2p_nodes = sorted({n for s in (check.nop_suite, check.oracle_suite) if s
                        for n in s.outcomes if n.startswith(d.test_rel + "::")})
    report_parts = []
    if check.nop_suite:
        passing = [n for n in f2p_nodes if check.nop_suite.outcomes.get(n) == "passed"]
        if passing:
            report_parts.append("Feature tests that PASS on the untouched repository:\n" + "\n".join(passing))
    if check.oracle_suite:
        rep = failure_report(check.oracle_suite, f2p_nodes + check.oracle.get("p2p_failed_examples", []))
        if rep:
            report_parts.append("Failures with the reference solution applied:\n" + rep)
    if not report_parts and check.oracle_suite:
        report_parts.append(trim(normalize(check.oracle_suite.output, check.oracle_suite.root), 4000))

    sol_wt = ws.solution_tree(d.solution_patch)
    context = source_context(ws.repo.read, d.context_files, root=sol_wt)
    prompt = render("fix", instruction=d.instruction, solution=trim(d.solution_patch, 30000),
                    test_path=d.test_rel, tests=d.test_code, problems="\n".join(f"- {x}" for x in check.problems),
                    report="\n\n".join(report_parts) or "(no output)", context=context)
    reply = ws.llm.complete(ws.system, prompt, stage="fix", variant=rnd)
    diagnosis = (re.search(r"DIAGNOSIS:\s*(.+)", reply) or [None, ""])[1].strip()
    changed = []
    if "<<<<<<< SEARCH" in reply:
        sol_wt = ws.solution_tree(d.solution_patch)
        problems = ws.apply_model_edits(sol_wt, reply)
        if problems:
            ws.log(f"    solution fix not applied: {problems[0][:120]}")
        else:
            d.solution_patch = ws.source_diff(sol_wt)
            changed.append("solution")
    new_tests = extract_test_file(reply)
    if new_tests:
        d.test_code = new_tests
        changed.append("tests")
    d.metrics["fixes"].append({"round": rnd + 1, "diagnosis": diagnosis[:300], "changed": changed})
    ws.event("fix", task=d.task_id, round=rnd + 1, diagnosis=diagnosis, changed=changed)
