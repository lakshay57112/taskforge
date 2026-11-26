"""Stage 2 - feature proposals, novelty check and diverse selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .codemap import CodeMap
from .prompts import AXES, axes_text, render
from .repo import Repo

GENERIC_TERMS = {"option", "options", "command", "group", "value", "default", "help", "type",
                 "name", "error", "param", "context", "click", "self", "return", "true", "false"}


@dataclass
class Proposal:
    slug: str
    title: str
    area: str
    axis: str
    summary: str
    public_api: list[str] = field(default_factory=list)
    behaviours: list[str] = field(default_factory=list)
    novelty_terms: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    difficulty: str = "medium"
    novelty: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        slug = re.sub(r"[^a-z0-9-]+", "-", str(d.get("slug", "")).lower()).strip("-")[:48]
        return cls(
            slug=slug or "feature",
            title=str(d.get("title", slug)),
            area=str(d.get("area", "")),
            axis=str(d.get("axis", "")).lower(),
            summary=str(d.get("summary", "")),
            public_api=[str(x) for x in d.get("public_api", [])],
            behaviours=[str(x) for x in d.get("behaviours", [])],
            novelty_terms=[str(x) for x in d.get("novelty_terms", [])],
            files=[str(x) for x in d.get("files", [])],
            difficulty=str(d.get("difficulty", "medium")),
        )

    def brief(self) -> str:
        api = "; ".join(self.public_api)
        return f"{self.title} [area={self.area}, axis={self.axis}] - {self.summary} API: {api}"

    def as_prompt(self) -> str:
        lines = [f"Title: {self.title}", f"Area: {self.area}", f"Axis: {self.axis}",
                 f"Summary: {self.summary}", "Public API:"]
        lines += [f"  - {x}" for x in self.public_api]
        lines.append("Behaviours:")
        lines += [f"  - {x}" for x in self.behaviours]
        return "\n".join(lines)


def propose(llm, system: str, cfg, codemap: CodeMap, count: int, taken: list[Proposal],
            round_no: int = 0) -> list[Proposal]:
    prompt = render(
        "propose",
        count=str(count),
        repo=cfg.repo.name,
        codemap=codemap.prompt_summary(),
        axes=axes_text(),
        taken="\n".join(f"- {p.brief()}" for p in taken) or "(none yet)",
    )
    data = llm.complete_json(system, prompt, stage="propose", temperature=0.8, variant=round_no * 10)
    out = []
    for raw in data.get("candidates", []):
        p = Proposal.from_dict(raw)
        if p.axis not in AXES:
            p.axis = _closest_axis(p.axis)
        if not codemap.area(p.area):
            p.area = _closest_area(p.area, codemap)
        out.append(p)
    return out


def _closest_axis(axis: str) -> str:
    for key in AXES:
        if key in axis or axis in key:
            return key
    return "validation"


def _closest_area(area: str, codemap: CodeMap) -> str:
    tail = area.split(".")[-1].lower()
    for a in codemap.areas:
        if a.id.lower().endswith(tail) or tail in a.id.lower():
            return a.id
    return area


def check_novelty(repo: Repo, p: Proposal) -> dict:
    """Search the base commit and the whole upstream history for the feature's tell-tale strings."""
    report = {"terms": [], "exists": False}
    for term in p.novelty_terms[:4]:
        t = term.strip()
        if len(t) < 4 or t.lower() in GENERIC_TERMS:
            report["terms"].append({"term": t, "skipped": "too generic"})
            continue
        in_base = repo.grep_fixed(t, repo.source_dir)
        in_history = repo.history_search(t, repo.source_dir)
        found = bool(in_base or in_history)
        report["terms"].append({"term": t, "base_hits": in_base[:3], "history_hits": in_history[:3]})
        report["exists"] = report["exists"] or found
    report["checked"] = sum(1 for t in report["terms"] if "skipped" not in t)
    if not report["checked"]:
        report["exists"] = False
        report["note"] = "no specific terms to search for; relying on the model's claim"
    return report


def select_diverse(candidates: list[Proposal], taken: list[Proposal], n: int) -> list[Proposal]:
    """Greedy pick: prefer unused areas and unused axes, keep proposer order otherwise."""
    chosen: list[Proposal] = []
    used_areas = {p.area for p in taken}
    used_axes = {p.axis for p in taken}
    used_slugs = {p.slug for p in taken}
    pool = [c for c in candidates if c.slug not in used_slugs]
    while pool and len(chosen) < n:
        def score(c: Proposal) -> tuple:
            return (c.area not in used_areas, c.axis not in used_axes)
        best = max(pool, key=score)
        pool.remove(best)
        chosen.append(best)
        used_areas.add(best.area)
        used_axes.add(best.axis)
        used_slugs.add(best.slug)
    return chosen
