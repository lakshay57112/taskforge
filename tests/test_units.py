import json
import subprocess
from pathlib import Path

import pytest

from taskforge.breaker import added_lines, mutants_for, parse_cheats
from taskforge.codemap import _hunks, jaccard
from taskforge.llm import LLM, CacheMiss, ScriptedBackend, parse_json
from taskforge.localcheck import normalize, verify
from taskforge.repo import apply_edits, extract_code_block, parse_edit_blocks


def test_reward_rules():
    f2p, p2p = ["t::a", "t::b"], ["old::x", "old::y"]
    all_pass = {n: "passed" for n in f2p + p2p}
    assert verify.compute_reward(all_pass, f2p, p2p)["reward"] == 1.0
    half = dict(all_pass, **{"t::b": "failed"})
    assert verify.compute_reward(half, f2p, p2p)["reward"] == 0.5
    broken = dict(all_pass, **{"old::y": "failed"})
    assert verify.compute_reward(broken, f2p, p2p)["reward"] == 0.0
    nop = {"old::x": "passed", "old::y": "passed"}       # feature tests missing entirely
    assert verify.compute_reward(nop, f2p, p2p)["reward"] == 0.0


def test_verify_script_cli(tmp_path):
    (tmp_path / "o.json").write_text(json.dumps({"a": "passed", "b": "passed"}))
    (tmp_path / "f.json").write_text(json.dumps(["a"]))
    (tmp_path / "p.json").write_text(json.dumps(["b"]))
    script = Path(verify.__file__)
    subprocess.run(["python", str(script), "score", "--outcomes", str(tmp_path / "o.json"),
                    "--f2p", str(tmp_path / "f.json"), "--p2p", str(tmp_path / "p.json"),
                    "--out", str(tmp_path / "r.txt")], check=True, capture_output=True)
    assert (tmp_path / "r.txt").read_text().strip() == "1.0"


def test_edit_blocks_apply_and_reject(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("a = 1\nb = 2\n")
    text = """Here you go.
FILE: src/m.py
```python
<<<<<<< SEARCH
b = 2
=======
b = 3
>>>>>>> REPLACE
```
FILE: src/new.py
<<<<<<< SEARCH
=======
x = 1
>>>>>>> REPLACE
FILE: tests/test_x.py
<<<<<<< SEARCH
=======
bad
>>>>>>> REPLACE
"""
    edits = parse_edit_blocks(text)
    assert [e.path for e in edits] == ["src/m.py", "src/new.py", "tests/test_x.py"]
    problems = apply_edits(tmp_path, edits, allowed_prefixes=("src/",))
    assert (tmp_path / "src" / "m.py").read_text() == "a = 1\nb = 3\n"
    assert (tmp_path / "src" / "new.py").read_text() == "x = 1\n"
    assert len(problems) == 1 and "only allowed under" in problems[0]


def test_edit_block_ambiguous_and_missing(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\nx = 1\n")
    edits = parse_edit_blocks("FILE: src/m.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"
                              "FILE: src/m.py\n<<<<<<< SEARCH\ny = 1\n=======\ny = 2\n>>>>>>> REPLACE\n")
    problems = apply_edits(tmp_path, edits, allowed_prefixes=("src/",))
    assert "matches 2 places" in problems[0] and "not found" in problems[1]


def test_hunks_count_only_changed_lines():
    patch = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -10,7 +10,8 @@ def f():
     ctx1
     ctx2
     ctx3
-    old
+    new
+    new2
     ctx4
     ctx5
     ctx6
"""
    [(file, ranges, is_new)] = list(_hunks(patch))
    assert file == "src/a.py" and not is_new
    assert {r[0] for r in ranges} == {13}


def test_added_lines_and_mutants():
    patch = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,4 @@
 x = 1
+if x == 1 and y:
+    return 5
 z = 2
"""
    lines = added_lines(patch)
    assert lines == [("src/a.py", 2, "if x == 1 and y:"), ("src/a.py", 3, "    return 5")]
    muts = {m[3] for m in mutants_for(patch, 50, 1)}
    assert "if x != 1 and y:" in muts and "if x == 1 or y:" in muts and "    return None" in muts


def test_parse_cheats():
    reply = "### CHEAT 1\nSTRATEGY: hardcode\nVIOLATES: R2\nFILE: x\n\n### CHEAT 2\nSTRATEGY: skip\nVIOLATES: R4\n"
    cheats = parse_cheats(reply)
    assert [(c.n, c.strategy, c.violates) for c in cheats] == [(1, "hardcode", "R2"), (2, "skip", "R4")]


def test_llm_cache_and_replay(tmp_path):
    calls = []
    backend = ScriptedBackend(lambda stage, s, p: (calls.append(stage), '{"ok": 1}')[1])
    llm = LLM(backend, model="m", cache_dir=tmp_path)
    assert llm.complete_json("sys", "prompt", stage="x") == {"ok": 1}
    assert llm.complete_json("sys", "prompt", stage="x") == {"ok": 1}
    assert calls == ["x"]                       # second answer came from the cache
    llm.save_transcript()

    replay = LLM(None, model="m", cache_dir=tmp_path, mode="replay")
    assert replay.complete_json("sys", "prompt", stage="x") == {"ok": 1}
    # a cosmetically different prompt is served by request order
    assert replay.complete_json("sys", "prompt (path /other/machine)", stage="x") == {"ok": 1}
    with pytest.raises(CacheMiss):
        replay.complete_json("sys", "never recorded", stage="y")


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": [1, 2]} done') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_normalize_removes_machine_noise():
    text = "File /home/u/x/.taskforge_work/worktrees/oracle/src/a.py, 3 failed in 1.23s at 0x7f12ab34cd"
    out = normalize(text)
    assert "<repo>/src/a.py" in out and "in <time>" in out and "0x<addr>" in out


def test_extract_code_block_prefers_longest():
    text = "```python\nshort\n```\nand\n```python\nmuch longer block\n```"
    assert extract_code_block(text) == "much longer block\n"


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 0.0


def test_requirements_and_markdown_parsing():
    from taskforge.prompts import doc_title, extract_markdown, parse_requirements
    doc = "Sure!\n```markdown\n# Title here\n\n- R1: a\n```python\nx = 1\n```\n### R2: b\n1. **R3:** c\n```"
    out = extract_markdown(doc)
    assert out.startswith("# Title here") and "x = 1" in out and not out.rstrip().endswith("```")
    assert [r["id"] for r in parse_requirements(out)] == ["R1", "R2", "R3"]
    assert doc_title(out, "d") == "Title here"


def test_fallback_model_on_overload(tmp_path):
    class Flaky:
        model = "main"
        def __init__(self):
            self.seen = []
        def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
            self.seen.append(model)
            if model == "main":
                raise RuntimeError("503 UNAVAILABLE: high demand")
            return "ok", {}
    backend = Flaky()
    llm = LLM(backend, model="main", cache_dir=tmp_path, fallbacks=["backup"])
    import taskforge.llm as mod
    mod.time.sleep = lambda s: None
    assert llm.complete("s", "p", stage="x") == "ok"
    assert backend.seen == ["main", "backup"]
    assert llm.usage.by_model == {"backup": 1}


def test_all_models_overloaded_waits_then_recovers(tmp_path):
    import taskforge.llm as mod
    mod.time.sleep = lambda s: None
    calls = []

    class Busy:
        model = "a"
        def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
            calls.append(model)
            if len(calls) < 5:
                raise RuntimeError("503 UNAVAILABLE")
            return "ok", {}

    llm = LLM(Busy(), model="a", cache_dir=tmp_path, fallbacks=["b"], max_wait=100)
    assert llm.complete("s", "p", stage="x") == "ok"
    assert calls == ["a", "b", "a", "b", "a"]

    calls.clear()
    llm2 = LLM(Busy(), model="a", cache_dir=tmp_path / "2", fallbacks=["b"], max_wait=0)
    with pytest.raises(mod.LLMError):
        llm2.complete("s", "p2", stage="x")


def test_unusable_models_are_dropped_and_quota_rotates(tmp_path):
    import taskforge.llm as mod
    mod.time.sleep = lambda s: None
    seen = []

    class Mixed:
        model = "a"
        def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
            seen.append(model)
            if model == "a":
                raise RuntimeError("404 NOT_FOUND: no longer available to new users")
            if model == "b":
                raise RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota")
            return "ok", {}

    llm = LLM(Mixed(), model="a", cache_dir=tmp_path, fallbacks=["b", "c"])
    assert llm.complete("s", "p", stage="x") == "ok"
    assert seen == ["a", "b", "c"] and llm.disabled == {"a"}
    seen.clear()
    assert llm.complete("s", "p2", stage="x") == "ok"
    assert seen == ["b", "c"]          # the dropped model is not tried again


def test_classify_error():
    from taskforge.llm import classify_error
    assert classify_error("503 UNAVAILABLE high demand") == "busy"
    assert classify_error("429 RESOURCE_EXHAUSTED") == "quota"
    assert classify_error("404 NOT_FOUND") == "unusable"
    assert classify_error("connection reset") == "other"
