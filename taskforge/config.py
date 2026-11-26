"""Loads taskforge.toml into typed settings objects."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class RepoConfig:
    name: str = "pallets/click"
    url: str = "https://github.com/pallets/click.git"
    base_commit: str = ""
    base_ref: str = ""
    license: str = ""
    language: str = "python"
    source_dir: str = "src"
    tests_dir: str = "tests"
    always_include: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.name.split("/")[-1]

    @property
    def short_commit(self) -> str:
        return self.base_commit[:7]


@dataclass
class EnvironmentConfig:
    base_image: str = "python:3.12-slim"
    pytest_version: str = "9.2.0"
    agent_timeout_sec: int = 1800
    verifier_timeout_sec: int = 900
    build_timeout_sec: int = 900
    cpus: int = 2
    memory_mb: int = 4096


@dataclass
class LLMConfig:
    provider: str = "gemini"
    base_url: str = ""
    api_key_env: str = ""
    model: str = "gemini-3.8-flash"
    fallback_models: list[str] = field(default_factory=list)
    max_wait_sec: float = 1200.0
    temperature: float = 1.0
    send_temperature: bool = False
    seed: int = 1234
    mode: str = "live"
    cache_dir: str = ".taskforge_cache"


@dataclass
class PipelineConfig:
    num_tasks: int = 5
    candidates_per_task: int = 2
    max_fix_rounds: int = 4
    breaker_cheats: int = 3
    harden_rounds: int = 2
    mutants: int = 12
    blind_solvers: int = 2
    harbor_oracle_runs: int = 3
    workdir: str = ".taskforge_work"
    output_dir: str = "output"


@dataclass
class Config:
    repo: RepoConfig = field(default_factory=RepoConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    root: Path = field(default_factory=Path.cwd)

    def path(self, value: str) -> Path:
        """Resolve a config path relative to the folder holding taskforge.toml (~ allowed)."""
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p)

    def validate(self) -> None:
        commit = self.repo.base_commit
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError(
                f"repo.base_commit must be a full 40-character lowercase hash, got {commit!r}"
            )
        if self.llm.mode not in {"live", "replay"}:
            raise ValueError("llm.mode must be 'live' or 'replay'")


def _fill(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown keys for [{cls.__name__}]: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | Path = "taskforge.toml") -> Config:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = Config(
        repo=_fill(RepoConfig, data.get("repo", {})),
        environment=_fill(EnvironmentConfig, data.get("environment", {})),
        llm=_fill(LLMConfig, data.get("llm", {})),
        pipeline=_fill(PipelineConfig, data.get("pipeline", {})),
        root=path.resolve().parent,
    )
    cfg.validate()
    return cfg
