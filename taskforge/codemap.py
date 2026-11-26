"""Stage 1 - codebase map.

Splits the source tree into "areas" (a module, or one class of a large module),
records which symbols live where, and maps any patch back onto the symbols it
touches. The map drives feature placement (one area per task) and the overlap
metric reported in the scorecard.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field

from .repo import Repo

LARGE_MODULE_LINES = 900


@dataclass
class Symbol:
    name: str          # e.g. "Option.get_default" or "convert_type"
    kind: str          # "class" | "function" | "method"
    file: str
    start: int
    end: int
    doc: str = ""


@dataclass
class Area:
    id: str            # e.g. "core.Option" or "types"
    file: str
    lines: int
    summary: str
    symbols: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)


@dataclass
class CodeMap:
    areas: list[Area]
    symbols: list[Symbol]

    def area(self, area_id: str) -> Area | None:
        return next((a for a in self.areas if a.id == area_id), None)

    def to_dict(self) -> dict:
        return {"areas": [asdict(a) for a in self.areas],
                "symbols": [asdict(s) for s in self.symbols]}

    def prompt_summary(self, max_symbols: int = 14) -> str:
        lines = []
        for a in self.areas:
            syms = ", ".join(a.symbols[:max_symbols])
            more = f" (+{len(a.symbols) - max_symbols} more)" if len(a.symbols) > max_symbols else ""
            lines.append(f"- {a.id} [{a.file}, {a.lines} lines] {a.summary}\n    symbols: {syms}{more}")
        return "\n".join(lines)

    def symbols_for_lines(self, file: str, line_ranges: list[tuple[int, int]]) -> set[str]:
        hits = {s.name for s in self.symbols
                if s.file == file and any(s.start <= hi and lo <= s.end for lo, hi in line_ranges)}
        # keep the most specific symbol (a method rather than its class)
        return {h for h in hits if not any(o != h and o.startswith(h + ".") for o in hits)}

    def touched_symbols(self, patch: str) -> set[str]:
        """Symbols (or file:<path> for new or module-level edits) changed by a unified diff."""
        touched: set[str] = set()
        for file, ranges, is_new in _hunks(patch):
            if is_new:
                touched.add(f"file:{file}")
                continue
            syms = self.symbols_for_lines(file, ranges)
            touched |= syms or {f"module:{file}"}
        for name in re.findall(r"^\+\s*(?:async\s+)?(?:def|class)\s+(\w+)", patch, re.M):
            touched.add(f"new:{name}")
        return touched


def _first_doc_line(node) -> str:
    doc = ast.get_docstring(node) or ""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def build_codemap(repo: Repo) -> CodeMap:
    files = [f for f in repo.list_files(repo.source_dir) if f.endswith(".py")]
    test_files = [f for f in repo.list_files(repo.tests_dir) if f.endswith(".py")]
    test_text = {t: repo.read(t) or "" for t in test_files}
    areas: list[Area] = []
    symbols: list[Symbol] = []

    for f in sorted(files):
        src = repo.read(f) or ""
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        module = f.rsplit("/", 1)[-1][:-3]
        private = module.startswith("_")
        n_lines = len(src.splitlines())
        mod_syms: list[str] = []
        class_areas: list[Area] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(Symbol(node.name, "class", f, node.lineno, node.end_lineno, _first_doc_line(node)))
                methods = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = f"{node.name}.{sub.name}"
                        symbols.append(Symbol(name, "method", f, sub.lineno, sub.end_lineno))
                        methods.append(sub.name)
                mod_syms.append(node.name)
                size = node.end_lineno - node.lineno + 1
                if n_lines >= LARGE_MODULE_LINES and size >= 120:
                    class_areas.append(Area(
                        id=f"{module}.{node.name}", file=f, lines=size,
                        summary=_first_doc_line(node) or f"class {node.name}",
                        symbols=[m for m in methods if not m.startswith("__") or m == "__init__"],
                    ))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(Symbol(node.name, "function", f, node.lineno, node.end_lineno, _first_doc_line(node)))
                mod_syms.append(node.name)

        if private:
            continue  # symbols still count for overlap, but no feature is placed in private modules
        public = list(dict.fromkeys(s for s in mod_syms if not s.startswith("_")))
        if class_areas:
            areas.extend(class_areas)
            covered = {a.id.split(".", 1)[1] for a in class_areas}
            rest = [s for s in public if s not in covered]
            if rest:
                areas.append(Area(id=module, file=f, lines=n_lines,
                                  summary=f"other parts of {module}: " + (_first_doc_line(tree) or "functions and smaller classes"),
                                  symbols=rest))
        else:
            areas.append(Area(id=module, file=f, lines=n_lines,
                              summary=_first_doc_line(tree) or f"module {module}", symbols=public))
        for a in areas[-(len(class_areas) + 1):]:
            a.symbols = list(dict.fromkeys(a.symbols))

    for a in areas:
        key = a.id.split(".")[-1]
        pattern = re.compile(rf"\b{re.escape(key)}\b")
        ranked = sorted(((len(pattern.findall(t)), name) for name, t in test_text.items()), reverse=True)
        a.tests = [name for count, name in ranked[:3] if count]
    return CodeMap(areas=areas, symbols=symbols)


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def _hunks(patch: str):
    """Yield (file, [(line, line) changed in the base file], is_new_file) per file in a diff.

    Only lines that are actually removed, or the line an addition follows, are
    counted; context lines are ignored so neighbouring symbols are not blamed.
    """
    file, ranges, is_new = None, [], False
    base_line, in_hunk = 0, False
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            if file:
                yield file, ranges, is_new
            file, ranges, is_new, in_hunk = None, [], False, False
        elif line.startswith("new file mode"):
            is_new = True
        elif line.startswith("+++ b/"):
            file = line[6:].strip()
        elif line.startswith("--- "):
            continue
        elif m := _HUNK.match(line):
            base_line, in_hunk = int(m.group(1)), True
        elif in_hunk:
            if line.startswith("-"):
                ranges.append((base_line, base_line))
                base_line += 1
            elif line.startswith("+"):
                anchor = max(base_line - 1, 1)
                ranges.append((anchor, anchor))
            elif line.startswith("\\"):
                continue
            else:
                base_line += 1
    if file:
        yield file, ranges, is_new


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)
