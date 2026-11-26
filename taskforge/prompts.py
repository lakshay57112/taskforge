"""Prompt templates (taskforge/prompts/*.md) and source-context helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"

AXES = {
    "parsing": "parsing and converting user input into values (new parameter types, formats)",
    "validation": "rules that accept or reject combinations of inputs, with clear errors",
    "routing": "how commands and subcommands are found, resolved and dispatched",
    "output": "help pages, formatting and other text the program prints",
    "interactive": "prompts, confirmations and other terminal interaction",
    "configuration": "defaults, environment variables, configuration sources and precedence",
    "completion": "shell completion behaviour",
    "lifecycle": "context objects, callbacks, resources and command lifecycle",
}


def render(name: str, **values) -> str:
    template = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    missing = set(re.findall(r"\{\{(\w+)\}\}", template)) - set(values)
    if missing:
        raise KeyError(f"prompt {name!r} is missing values for: {sorted(missing)}")

    def sub(m: re.Match) -> str:
        value = values[m.group(1)]
        return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)

    # single pass, so braces inside inserted source code are never treated as placeholders
    return re.sub(r"\{\{(\w+)\}\}", sub, template)


def system_prompt(cfg) -> str:
    return render("system", repo=cfg.repo.name, commit=cfg.repo.base_commit, ref=cfg.repo.base_ref)


def axes_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in AXES.items())


def source_context(read, files: list[str], max_chars: int = 260_000, root: Path | None = None) -> str:
    """Concatenate files for a prompt. `read(path)` returns text or None.

    When `root` is given, files are read from that working tree (so the model
    sees the current state, e.g. with the reference solution applied).
    """
    parts, used, seen = [], 0, set()
    for rel in files:
        if rel in seen:
            continue
        seen.add(rel)
        text = (root / rel).read_text(encoding="utf-8") if root and (root / rel).exists() else read(rel)
        if text is None:
            continue
        block = f"### {rel}\n```python\n{text.rstrip()}\n```\n"
        if used + len(block) > max_chars:
            parts.append(f"### {rel}\n(omitted: context budget reached)\n")
            continue
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


def extract_markdown(reply: str) -> str:
    """The document itself: unwraps a ```markdown fence (which may contain code blocks of its
    own) and drops any chatter before the title line."""
    text = reply.strip()
    m = re.search(r"```(?:markdown|md)?[ \t]*\n", text)
    if m and (m.start() == 0 or "markdown" in m.group(0) or "md" in m.group(0)) \
            and not text.lstrip().startswith("# "):
        body = text[m.end():]
        close = body.rfind("```")
        text = (body[:close] if close != -1 else body).strip()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            text = "\n".join(lines[i:])
            break
    return text.strip() + "\n"


_REQ = re.compile(r"^\s*(?:#{1,6}|[-*]|\d+\.)?\s*(?:\*\*)?\s*(R\d+)\s*(?:\*\*)?\s*[:.)\u2013\u2014-]\s*(?:\*\*)?\s*(.+)$")


def parse_requirements(doc: str) -> list[dict]:
    reqs, seen = [], set()
    for line in doc.splitlines():
        m = _REQ.match(line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            reqs.append({"id": m.group(1), "text": m.group(2).replace("**", "").strip()[:240]})
    return reqs


def doc_title(doc: str, default: str) -> str:
    for line in doc.splitlines():
        if line.startswith("# "):
            return line[2:].strip().strip("`") or default
    return default
