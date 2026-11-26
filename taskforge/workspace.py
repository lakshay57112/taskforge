"""Shared state for one pipeline run: config, model client, repository and worktrees."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .codemap import CodeMap
from .localcheck import TestRunner
from .repo import Repo, apply_edits, apply_patch, diff_against_base, parse_edit_blocks


@dataclass
class Workspace:
    cfg: object
    llm: object
    system: str
    repo: Repo
    runner: TestRunner
    workdir: Path
    events_path: Path | None = None
    p2p: list[str] = field(default_factory=list)
    codemap: CodeMap | None = None
    quiet: bool = False

    # -- logging -----------------------------------------------------------------
    def log(self, msg: str) -> None:
        if not self.quiet:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def event(self, kind: str, **data) -> None:
        if self.events_path:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time(), 1), "event": kind, **data},
                                    ensure_ascii=False) + "\n")

    # -- worktrees ---------------------------------------------------------------
    def wt(self, name: str) -> Path:
        """A clean checkout of the base commit (reset every time it is requested)."""
        return self.repo.worktree(self.workdir / "worktrees" / name)

    def solution_tree(self, patch: str, name: str = "oracle") -> Path:
        wt = self.wt(name)
        ok, err = apply_patch(wt, patch)
        if not ok:
            raise RuntimeError(f"solution patch no longer applies: {err}")
        return wt

    def apply_model_edits(self, wt: Path, text: str) -> list[str]:
        edits = parse_edit_blocks(text)
        if not edits:
            return ["no edit blocks found in the answer"]
        return apply_edits(wt, edits, allowed_prefixes=(self.cfg.repo.source_dir + "/",))

    def source_diff(self, wt: Path) -> str:
        return diff_against_base(wt, self.repo.base, self.cfg.repo.source_dir)
