"""Stage 4 - adversarial test hardening.

Breaker: the model writes deliberately flawed implementations that try to pass the
hidden tests. Any cheat that passes every feature test exposes a weak test suite;
the tests are then strengthened and re-validated.

Mutation testing: small mechanical faults are injected into the reference
solution; a good test suite fails on (kills) most of them.
"""

from __future__ import annotations

import py_compile
import random
import re
from dataclasses import dataclass

from .builder import TaskDraft, ensure_valid, extract_test_file
from .prompts import render, source_context
from .workspace import Workspace


@dataclass
class Cheat:
    n: int
    strategy: str
    violates: str
    body: str
    status: str = "pending"   # invalid | caught | survived | accepted
    passed: int = 0
    total: int = 0


def parse_cheats(reply: str) -> list[Cheat]:
    parts = re.split(r"^#{2,4}\s*CHEAT\s*(\d+)\s*$", reply, flags=re.M)
    cheats = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        strategy = (re.search(r"STRATEGY:\s*(.+)", body) or [None, ""])[1].strip()
        violates = (re.search(r"VIOLATES:\s*(.+)", body) or [None, ""])[1].strip()
        cheats.append(Cheat(n=int(parts[i]), strategy=strategy, violates=violates, body=body))
    return cheats


def _feature_run(ws: Workspace, wt, d: TaskDraft):
    res = ws.runner.run_suite(wt, {d.test_rel: d.test_code}, only=[d.test_rel], timeout=300)
    passed = sum(1 for n in d.f2p if res.outcomes.get(n) == "passed")
    return passed, len(d.f2p)


def _evaluate_cheats(ws: Workspace, d: TaskDraft, cheats: list[Cheat]) -> None:
    for c in cheats:
        if c.status == "invalid":
            continue
        wt = ws.wt("scratch")
        problems = ws.apply_model_edits(wt, c.body)
        if problems:
            c.status = "invalid"
            continue
        c.passed, c.total = _feature_run(ws, wt, d)
        c.status = "survived" if c.total and c.passed == c.total else "caught"


def run_breaker(ws: Workspace, d: TaskDraft) -> dict:
    cfg = ws.cfg
    context = source_context(ws.repo.read, d.context_files)
    reply = ws.llm.complete(ws.system, render(
        "breaker", count=str(cfg.pipeline.breaker_cheats), instruction=d.instruction,
        tests=d.test_code, context=context, source_dir=cfg.repo.source_dir),
        stage="breaker", temperature=0.9)
    cheats = parse_cheats(reply)[: cfg.pipeline.breaker_cheats]
    _evaluate_cheats(ws, d, cheats)
    initial = [c.status for c in cheats]
    ws.log(f"  breaker: {sum(s == 'caught' for s in initial)} caught, "
           f"{sum(s == 'survived' for s in initial)} survived, {sum(s == 'invalid' for s in initial)} invalid")

    rounds = 0
    while any(c.status == "survived" for c in cheats) and rounds < cfg.pipeline.harden_rounds:
        rounds += 1
        survivors = [c for c in cheats if c.status == "survived"]
        desc = "\n\n".join(f"### Cheat {c.n}\nStrategy: {c.strategy}\nViolates: {c.violates}\n{c.body.strip()}"
                           for c in survivors)
        reply = ws.llm.complete(ws.system, render(
            "harden", instruction=d.instruction, test_path=d.test_rel, tests=d.test_code, cheats=desc),
            stage="harden", variant=rounds)
        new_tests = extract_test_file(reply)
        if not new_tests:
            break
        previous = (d.test_code, list(d.f2p))
        d.test_code = new_tests
        try:
            ensure_valid(ws, d, rounds=2)
        except Exception as exc:  # hardened tests broke validity: keep the old ones
            ws.log(f"  harden round {rounds}: new tests invalid ({str(exc)[:100]}); reverted")
            d.test_code, d.f2p = previous
            ensure_valid(ws, d, rounds=0)
            break
        for c in cheats:
            if c.status in ("caught", "survived"):
                c.status = "pending"
        _evaluate_cheats(ws, d, cheats)
        ws.log(f"  harden round {rounds}: {sum(c.status == 'survived' for c in cheats)} cheats still pass")

    # A survivor the hardener explicitly judged correct is reported separately.
    valid = [c for c in cheats if c.status != "invalid"]
    caught = sum(c.status == "caught" for c in valid)
    return {
        "cheats_valid": len(valid),
        "cheats_caught": caught,
        "cheat_catch_rate": f"{caught}/{len(valid)}" if valid else "n/a",
        "initial_status": initial,
        "harden_rounds": rounds,
        "cheats": [{"n": c.n, "strategy": c.strategy, "violates": c.violates, "status": c.status,
                    "feature_tests_passed": f"{c.passed}/{c.total}"} for c in cheats],
    }


# -- mutation testing ------------------------------------------------------------

MUTATIONS = [
    (r" == ", " != "), (r" != ", " == "), (r" < ", " <= "), (r" <= ", " < "),
    (r" > ", " >= "), (r" >= ", " > "), (r" and ", " or "), (r" or ", " and "),
    (r"\bTrue\b", "False"), (r"\bFalse\b", "True"), (r" \+ ", " - "), (r" - ", " + "),
    (r"\bnot ", ""), (r"\bis None\b", "is not None"), (r"\bis not None\b", "is None"),
    (r"\b(\d+)\b", None),  # numeric constant +1
    (r"^(\s*)return\s+(?!None\b).+$", None),  # return value -> None
]


def added_lines(patch: str) -> list[tuple[str, int, str]]:
    """(file, line number in the patched file, text) for every added line."""
    out, file, new_line = [], None, 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            file = line[6:].strip()
        elif m := re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line):
            new_line = int(m.group(1))
        elif file and line.startswith("+") and not line.startswith("+++"):
            out.append((file, new_line, line[1:]))
            new_line += 1
        elif file and line.startswith(" "):
            new_line += 1
    return out


def mutants_for(patch: str, limit: int, seed: int) -> list[tuple[str, int, str, str]]:
    cands = []
    for file, lineno, text in added_lines(patch):
        stripped = text.strip()
        if not file.endswith(".py") or not stripped or stripped.startswith(("#", '"', "'", "@")) \
                or stripped.startswith(("def ", "class ", "import ", "from ")):
            continue
        for pattern, repl in MUTATIONS:
            m = re.search(pattern, text)
            if not m:
                continue
            if pattern.startswith(r"\b(\d+)"):
                mutated = text[:m.start()] + str(int(m.group(1)) + 1) + text[m.end():]
            elif pattern.startswith("^(\\s*)return"):
                mutated = f"{m.group(1)}return None"
            else:
                mutated = text[:m.start()] + repl + text[m.end():]
            if mutated != text:
                cands.append((file, lineno, text, mutated))
    random.Random(seed).shuffle(cands)
    return cands[:limit]


def run_mutation(ws: Workspace, d: TaskDraft) -> dict:
    mutants = mutants_for(d.solution_patch, ws.cfg.pipeline.mutants, ws.cfg.llm.seed)
    results = []
    for file, lineno, original, mutated in mutants:
        wt = ws.solution_tree(d.solution_patch, name="scratch")
        path = wt / file
        lines = path.read_text(encoding="utf-8").split("\n")
        if lineno - 1 >= len(lines) or lines[lineno - 1] != original:
            continue
        lines[lineno - 1] = mutated
        path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError:
            continue
        passed, total = _feature_run(ws, wt, d)
        killed = passed < total
        results.append({"file": file, "line": lineno, "mutant": mutated.strip()[:120], "killed": killed})
    killed = sum(r["killed"] for r in results)
    ws.log(f"  mutation: {killed}/{len(results)} mutants killed")
    return {
        "mutants_run": len(results),
        "mutants_killed": killed,
        "mutation_score": round(killed / len(results), 3) if results else None,
        "survivors": [r for r in results if not r["killed"]],
    }
