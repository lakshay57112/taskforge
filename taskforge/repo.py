"""Git helpers: the pinned base checkout, throwaway worktrees and code edits."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path | None = None, *, check: bool = True, timeout: int = 600,
        env: dict | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stderr.strip()[:2000]}")
    return proc


class Repo:
    """A local clone of the starting repository, pinned to one base commit."""

    def __init__(self, url: str, path: Path, base_commit: str, source_dir: str, tests_dir: str):
        self.url = url
        self.path = Path(path)
        self.base = base_commit
        self.source_dir = source_dir
        self.tests_dir = tests_dir

    # -- setup -----------------------------------------------------------------
    def ensure_clone(self) -> None:
        if not (self.path / ".git").exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # keep LF line endings on every OS so patches apply cleanly inside the Linux container
            run(["git", "clone", "--quiet", "-c", "core.autocrlf=false", self.url, str(self.path)], timeout=1800)
        run(["git", "config", "core.autocrlf", "false"], self.path)
        if run(["git", "cat-file", "-e", f"{self.base}^{{commit}}"], self.path, check=False).returncode:
            run(["git", "fetch", "--quiet", "--tags", "origin"], self.path, timeout=1800)
        run(["git", "cat-file", "-e", f"{self.base}^{{commit}}"], self.path)

    def git(self, *args: str, check: bool = True) -> str:
        return run(["git", *args], self.path, check=check).stdout

    # -- reading the base commit -------------------------------------------------
    def read(self, rel: str) -> str | None:
        proc = run(["git", "show", f"{self.base}:{rel}"], self.path, check=False)
        return proc.stdout if proc.returncode == 0 else None

    def list_files(self, prefix: str = "") -> list[str]:
        out = self.git("ls-tree", "-r", "--name-only", self.base, "--", prefix or ".")
        return [line for line in out.splitlines() if line]

    def grep_fixed(self, term: str, pathspec: str) -> list[str]:
        """Fixed-string search in the base commit."""
        proc = run(["git", "grep", "-n", "-I", "-F", "-e", term, self.base, "--", pathspec],
                   self.path, check=False)
        return [l for l in proc.stdout.splitlines() if l]

    def history_search(self, term: str, pathspec: str, limit: int = 5) -> list[str]:
        """Commits on any ref (including ones newer than the base) that add or remove `term`."""
        proc = run(["git", "log", "--all", "--format=%h %ad %s", "--date=short", f"-S{term}",
                    f"-n{limit}", "--", pathspec], self.path, check=False, timeout=300)
        return [l for l in proc.stdout.splitlines() if l]

    # -- worktrees -------------------------------------------------------------
    def worktree(self, dest: Path) -> Path:
        """A clean checkout of the base commit at `dest` (reused if it already exists)."""
        dest = Path(dest)
        if (dest / ".git").exists():
            run(["git", "reset", "--quiet", "--hard", self.base], dest)
            run(["git", "clean", "-qfdx"], dest)
            return dest
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "worktree", "prune"], self.path, check=False)
        run(["git", "worktree", "add", "--force", "--detach", str(dest), self.base], self.path)
        return dest


# -- diffs and patches ---------------------------------------------------------

def diff_against_base(worktree: Path, base: str, pathspec: str = ".") -> str:
    """Unified diff of every change under `pathspec` in the worktree (new files included)."""
    run(["git", "add", "-A", "--", pathspec], worktree)
    out = run(["git", "diff", "--cached", "--binary", base, "--", pathspec], worktree).stdout
    run(["git", "reset", "--quiet"], worktree)
    return out


def apply_patch(worktree: Path, patch: str) -> tuple[bool, str]:
    if not patch.strip():
        return True, ""
    proc = run(["git", "apply", "--whitespace=nowarn", "-"], worktree, check=False, input_text=patch)
    return proc.returncode == 0, proc.stderr.strip()


def changed_files(patch: str) -> list[str]:
    files = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
    return files


# -- search/replace edit blocks ----------------------------------------------------

_BLOCK = re.compile(
    r"FILE:\s*(?P<path>[^\n]+?)\s*\n"
    r"(?:```[\w-]*[ \t]*\n)?"
    r"<{5,9} SEARCH[ \t]*\n(?P<search>.*?)\n?={5,9}[ \t]*\n(?P<replace>.*?)\n?>{5,9} REPLACE",
    re.S,
)


@dataclass
class Edit:
    path: str
    search: str
    replace: str


def parse_edit_blocks(text: str) -> list[Edit]:
    edits = []
    for m in _BLOCK.finditer(text):
        path = m.group("path").strip().strip("`").strip()
        edits.append(Edit(path=path, search=m.group("search"), replace=m.group("replace")))
    return edits


def apply_edits(worktree: Path, edits: list[Edit], *, allowed_prefixes: tuple[str, ...]) -> list[str]:
    """Apply edits in order. Returns a list of problems (empty when everything applied)."""
    problems: list[str] = []
    root = worktree.resolve()
    for e in edits:
        rel = e.path.replace("\\", "/").lstrip("./")
        if not any(rel.startswith(p) for p in allowed_prefixes):
            problems.append(f"{rel}: edits are only allowed under {', '.join(allowed_prefixes)}")
            continue
        target = (root / rel).resolve()
        if root not in target.parents:
            problems.append(f"{rel}: path escapes the repository")
            continue
        if not e.search.strip():
            if target.exists():
                problems.append(f"{rel}: empty SEARCH means 'create file', but the file already exists")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_ensure_newline(e.replace), encoding="utf-8", newline="\n")
            continue
        if not target.exists():
            problems.append(f"{rel}: file does not exist (use an empty SEARCH section to create it)")
            continue
        content = target.read_text(encoding="utf-8")
        count = content.count(e.search)
        if count == 0:
            stripped = _match_ignoring_trailing_ws(content, e.search)
            if stripped is None:
                problems.append(f"{rel}: SEARCH text not found:\n{_preview(e.search)}")
                continue
            content = content.replace(stripped, e.replace, 1)
        elif count > 1:
            problems.append(f"{rel}: SEARCH text matches {count} places; include more context:\n{_preview(e.search)}")
            continue
        else:
            content = content.replace(e.search, e.replace, 1)
        target.write_text(content, encoding="utf-8", newline="\n")
    return problems


def _match_ignoring_trailing_ws(content: str, search: str) -> str | None:
    """Find `search` when the only difference is trailing whitespace on lines."""
    want = [l.rstrip() for l in search.splitlines()]
    lines = content.splitlines(keepends=True)
    n = len(want)
    hits = []
    for i in range(len(lines) - n + 1):
        if [l.rstrip() for l in lines[i : i + n]] == want:
            hits.append("".join(lines[i : i + n]).rstrip("\n"))
    return hits[0] if len(hits) == 1 else None


def _ensure_newline(s: str) -> str:
    return s if s.endswith("\n") else s + "\n"


def _preview(s: str, n: int = 6) -> str:
    lines = s.splitlines()
    out = "\n".join(lines[:n])
    return out + ("\n..." if len(lines) > n else "")


def extract_code_block(text: str, lang: str = "python") -> str:
    """Return the longest fenced code block (falls back to the whole text)."""
    blocks = re.findall(rf"```(?:{lang})?[ \t]*\n(.*?)```", text, re.S)
    if blocks:
        return max(blocks, key=len).rstrip() + "\n"
    return text.strip() + "\n"


def python_executable() -> str:
    return os.environ.get("TASKFORGE_PYTHON") or shutil.which("python3") or shutil.which("python") or "python"
