"""Model client with a content-addressed response cache.

Every request is keyed by (model, system prompt, user prompt, temperature, seed,
json flag, variant). In "live" mode answers are fetched and written to the cache;
in "replay" mode only the cache is read, so a recorded run can be reproduced
exactly without an API key or network access.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


class LLMError(RuntimeError):
    pass


class CacheMiss(LLMError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    by_stage: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "api_calls": self.calls,
            "cache_hits": self.cache_hits,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "api_seconds": round(self.seconds, 1),
            "requests_by_stage": dict(sorted(self.by_stage.items())),
            "calls_by_model": dict(sorted(self.by_model.items())),
        }


class Backend(Protocol):
    model: str

    def generate(
        self, system: str, prompt: str, *, temperature: float, seed: int, json_mode: bool
    ) -> tuple[str, dict]: ...


class GeminiBackend:
    """Google Gemini through the google-genai SDK. Reads GEMINI_API_KEY (or GOOGLE_API_KEY)."""

    def __init__(self, model: str, send_temperature: bool = False):
        self.send_temperature = send_temperature
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise LLMError("google-genai is not installed: pip install google-genai") from exc
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError(
                "Set GEMINI_API_KEY before running in live mode "
                "(or use --replay to run from the recorded cache)."
            )
        self.model = model
        self._types = types
        # a hard per-request timeout, so one stuck call can never stall a whole run
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=600_000))

    def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
        types = self._types
        model = model or self.model
        if model.startswith("gemma"):
            # Gemma models on the Gemini API take no system instruction or JSON mode.
            config = types.GenerateContentConfig(
                temperature=temperature if self.send_temperature else None, seed=seed)
            contents = f"{system}\n\n{prompt}"
        else:
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature if self.send_temperature else None,
                seed=seed,
                response_mime_type="application/json" if json_mode else "text/plain",
            )
            contents = prompt
        resp = self._client.models.generate_content(model=model, contents=contents, config=config)
        text = resp.text or ""
        meta = getattr(resp, "usage_metadata", None)
        usage = {
            "prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
        }
        if not text.strip():
            raise LLMError("Model returned an empty response")
        return text, usage

    def list_models(self) -> list[str]:
        return sorted(m.name for m in self._client.models.list())


PROVIDER_PRESETS = {
    # name: (base_url, environment variable holding the key, default model)
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter/free"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "ollama": ("http://localhost:11434/v1", "", "qwen2.5-coder:14b"),
}


class OpenAICompatBackend:
    """Any provider that speaks the OpenAI chat-completions API (OpenRouter, Groq, Ollama, ...)."""

    def __init__(self, model: str, base_url: str, api_key_env: str = "", send_temperature: bool = False):
        import httpx

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.send_temperature = send_temperature
        key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not key:
            raise LLMError(f"Set {api_key_env} before running with this provider.")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if "openrouter.ai" in self.base_url:
            headers["X-Title"] = "TaskForge"
        self._http = httpx.Client(headers=headers, timeout=900.0)

    def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
        body = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "seed": seed,
        }
        if self.send_temperature:
            body["temperature"] = temperature
        resp = self._http.post(f"{self.base_url}/chat/completions", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code} {resp.text[:400]}")
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"{data['error'].get('code', '')} {data['error'].get('message', '')}")
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if not text.strip():
            raise RuntimeError("503 empty response from provider")
        usage = data.get("usage") or {}
        return text, {"prompt_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens", 0)}

    def list_models(self) -> list[str]:
        resp = self._http.get(f"{self.base_url}/models")
        resp.raise_for_status()
        return sorted(m["id"] for m in resp.json().get("data", []))


class ScriptedBackend:
    """Deterministic backend for tests: a function maps (stage, prompt) to a reply."""

    def __init__(self, responder: Callable[[str, str, str], str], model: str = "scripted"):
        self.model = model
        self._responder = responder
        self.stage = ""

    def generate(self, system, prompt, *, temperature, seed, json_mode, model=None):
        return self._responder(self.stage, system, prompt), {"prompt_tokens": 0, "output_tokens": 0}


class LLM:
    def __init__(
        self,
        backend: Backend | None,
        *,
        model: str,
        cache_dir: Path,
        temperature: float = 0.4,
        seed: int = 1234,
        mode: str = "live",
        log: Callable[[str], None] | None = None,
        fallbacks: list[str] | tuple[str, ...] = (),
        max_wait: float = 1200.0,
    ):
        self.backend = backend
        self.model = model
        self.max_wait = max_wait
        self.disabled: set[str] = set()
        self.fallbacks = [m for m in fallbacks if m and m != model]
        self.cache_dir = Path(cache_dir) / "llm"
        self.temperature = temperature
        self.seed = seed
        self.mode = mode
        self.usage = Usage()
        self._log = log or (lambda _msg: None)
        # Order of requests per stage. Replay falls back to it when a prompt differs
        # only cosmetically from the recorded one (other machine paths, timings).
        self._seq: dict[str, int] = {}
        self.transcript: list[dict] = []
        self._recorded = self._load_transcript()

    def _transcript_path(self) -> Path:
        return self.cache_dir.parent / "transcript.json"

    def _load_transcript(self) -> dict[tuple[str, int], str]:
        path = self._transcript_path()
        if not path.exists():
            return {}
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return {(r["stage"], r["index"]): r["key"] for r in rows}

    def save_transcript(self) -> None:
        if self.mode != "live" or not self.transcript:
            return
        path = self._transcript_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.transcript, indent=0), encoding="utf-8")

    # -- cache -----------------------------------------------------------------
    def _key(self, system: str, prompt: str, temperature: float, seed: int, json_mode: bool, variant: int) -> str:
        blob = json.dumps(
            [self.model, system, prompt, round(temperature, 3), seed, json_mode, variant],
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    # -- public API ------------------------------------------------------------
    def complete(
        self,
        system: str,
        prompt: str,
        *,
        stage: str,
        json_mode: bool = False,
        temperature: float | None = None,
        seed: int | None = None,
        variant: int = 0,
    ) -> str:
        temperature = self.temperature if temperature is None else temperature
        seed = self.seed if seed is None else seed
        key = self._key(system, prompt, temperature, seed, json_mode, variant)
        path = self._cache_path(key)
        self.usage.by_stage[stage] = self.usage.by_stage.get(stage, 0) + 1
        index = self._seq.get(stage, 0)
        self._seq[stage] = index + 1
        self.transcript.append({"stage": stage, "index": index, "key": key})

        if path.exists():
            self.usage.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.mode == "replay":
            recorded = self._recorded.get((stage, index))
            if recorded and self._cache_path(recorded).exists():
                self.usage.cache_hits += 1
                self._log(f"  [{stage}] replayed recorded answer #{index} (prompt differed slightly)")
                return json.loads(self._cache_path(recorded).read_text(encoding="utf-8"))["response"]
            raise CacheMiss(
                f"[{stage}] response not found in cache {self.cache_dir} (replay mode). "
                "Run once in live mode to record it."
            )
        if self.backend is None:
            raise LLMError("No model backend configured")

        if isinstance(self.backend, ScriptedBackend):
            self.backend.stage = stage
        text, usage = self._call_with_retry(system, prompt, temperature, seed, json_mode, stage)
        served = usage.pop("model", self.model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "stage": stage,
                    "model": self.model,
                    "served_by": served,
                    "temperature": temperature,
                    "seed": seed,
                    "variant": variant,
                    "usage": usage,
                    "response": text,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return text

    def complete_json(self, system: str, prompt: str, *, stage: str, **kw) -> dict:
        """Ask for JSON; if the answer does not parse, ask again with the parse error."""
        last_error = ""
        base_variant = kw.pop("variant", 0)
        for attempt in range(3):
            p = prompt if not last_error else (
                f"{prompt}\n\nYour previous answer was not valid JSON ({last_error}). "
                "Reply again with only the JSON object."
            )
            raw = self.complete(system, p, stage=stage, json_mode=True, variant=base_variant + attempt, **kw)
            try:
                return parse_json(raw)
            except ValueError as exc:
                last_error = str(exc)[:200]
                self._log(f"  [{stage}] JSON parse failed (attempt {attempt + 1}): {last_error}")
        raise LLMError(f"[{stage}] model did not return valid JSON after 3 attempts")

    def _call_with_retry(self, system, prompt, temperature, seed, json_mode, stage):
        """Retry transient errors and route around unavailable models.

        - model missing or not allowed for this key (404/403): dropped for the rest of the run
        - overloaded (503) or out of quota (429): try the next model straight away
        - every remaining model busy: wait and go round again, up to max_wait seconds
        """
        errors, waited, pause = 0, 0.0, 20.0
        busy: set[str] = set()
        while True:
            models = [m for m in [self.model] + self.fallbacks if m not in self.disabled]
            if not models:
                raise LLMError(f"[{stage}] none of the configured models can be used with this API key")
            model = next((m for m in models if m not in busy), None)
            if model is None:
                if waited >= self.max_wait:
                    raise LLMError(f"[{stage}] every model stayed busy or out of quota for {waited:.0f}s")
                self._log(f"  [{stage}] all {len(models)} models busy or out of quota; waiting {pause:.0f}s "
                          f"(waited {waited:.0f}s of {self.max_wait:.0f}s)")
                time.sleep(pause)
                waited += pause
                pause = min(pause * 1.5, 120.0)
                busy.clear()
                continue
            start = time.time()
            try:
                text, usage = self.backend.generate(
                    system, prompt, temperature=temperature, seed=seed, json_mode=json_mode, model=model,
                )
                self.usage.calls += 1
                self.usage.seconds += time.time() - start
                self.usage.prompt_tokens += usage.get("prompt_tokens", 0)
                self.usage.output_tokens += usage.get("output_tokens", 0)
                self.usage.by_model[model] = self.usage.by_model.get(model, 0) + 1
                usage["model"] = model
                return text, usage
            except LLMError:
                raise
            except Exception as exc:  # network errors, rate limits, overload, 5xx
                msg = str(exc)
                kind = classify_error(msg)
                if kind == "unusable":
                    self.disabled.add(model)
                    self._log(f"  [{stage}] {model} is not available to this key; dropping it ({msg[:80]})")
                    continue
                if kind in ("busy", "quota"):
                    busy.add(model)
                    label = "overloaded" if kind == "busy" else "out of quota"
                    nxt = next((m for m in models if m not in busy), None)
                    if nxt:
                        self._log(f"  [{stage}] {model} is {label}; trying {nxt}")
                    continue
                errors += 1
                if errors >= 6:
                    raise LLMError(f"[{stage}] model call failed: {msg[:300]}") from exc
                delay = 5.0 * errors
                self._log(f"  [{stage}] {model} failed ({msg[:120]}); retrying in {delay:.0f}s")
                time.sleep(delay)
        raise AssertionError("unreachable")


def classify_error(msg: str) -> str:
    """'busy' (overloaded), 'quota' (rate or quota limit), 'unusable' (model not available to
    this key) or 'other'."""
    if any(k in msg for k in ("404", "NOT_FOUND", "no longer available", "403", "PERMISSION_DENIED",
                              "is not supported", "not enabled for")):
        return "unusable"
    if any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "quota")):
        return "quota"
    if any(k in msg for k in ("503", "UNAVAILABLE", "overloaded", "high demand")):
        return "busy"
    return "other"


def is_overloaded(msg: str) -> bool:
    return classify_error(msg) == "busy"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(raw: str) -> dict:
    text = raw.strip()
    m = _FENCE.search(text)
    if m and not text.startswith("{"):
        text = m.group(1).strip()
    try:
        data = json.loads(text, strict=False)   # tolerate raw newlines inside strings
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        try:
            data = json.loads(text[start : end + 1], strict=False)
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def make_backend(cfg):
    """The model client for cfg.llm.provider: "gemini", a preset name, or "openai" with base_url."""
    provider = cfg.llm.provider
    if provider == "gemini":
        return GeminiBackend(cfg.llm.model, send_temperature=cfg.llm.send_temperature)
    if provider in PROVIDER_PRESETS:
        base_url, key_env, _ = PROVIDER_PRESETS[provider]
        return OpenAICompatBackend(cfg.llm.model, cfg.llm.base_url or base_url,
                                   cfg.llm.api_key_env or key_env, cfg.llm.send_temperature)
    if provider == "openai":
        if not cfg.llm.base_url:
            raise LLMError("provider 'openai' needs llm.base_url")
        return OpenAICompatBackend(cfg.llm.model, cfg.llm.base_url, cfg.llm.api_key_env,
                                   cfg.llm.send_temperature)
    raise LLMError(f"Unknown provider {provider!r} (use gemini, openrouter, groq, ollama or openai)")


def build_llm(cfg, log=None) -> LLM:
    """Create the LLM client described by the [llm] config section."""
    backend = None
    if cfg.llm.mode == "live":
        backend = make_backend(cfg)
    return LLM(
        backend,
        model=cfg.llm.model,
        cache_dir=cfg.path(cfg.llm.cache_dir),
        temperature=cfg.llm.temperature,
        seed=cfg.llm.seed,
        mode=cfg.llm.mode,
        log=log,
        fallbacks=cfg.llm.fallback_models,
        max_wait=cfg.llm.max_wait_sec,
    )
