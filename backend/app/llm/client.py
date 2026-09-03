"""OpenAI-compatible client for on-premise Qwen / Kimi2 deployments.

Both models are expected to be served behind an OpenAI-compatible
/v1/chat/completions endpoint (vLLM, TGI, Ollama's OpenAI shim, LM Studio,
etc.). No calls ever leave the local network — base_url points at the
on-prem host and everything works with the API keys the user already has.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMJSONParseError(Exception):
    """The model's response couldn't be parsed as JSON even after the
    markdown-fence-stripping fallback. Raised rather than silently
    returning {} so this failure mode is distinguishable from the model
    legitimately reporting "nothing found" -- callers already catch and
    log extraction/synthesis failures by step and source file; swallowing
    it here instead would erase that attribution and look identical to a
    real empty result."""


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class LLMClient:
    """Thin wrapper that dispatches to Qwen or Kimi2 by logical role."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._clients: dict[str, AsyncOpenAI] = {
            "qwen": AsyncOpenAI(
                base_url=settings.qwen_base_url,
                api_key=settings.qwen_api_key,
                timeout=settings.llm_request_timeout_s,
            ),
            "kimi": AsyncOpenAI(
                base_url=settings.kimi_base_url,
                api_key=settings.kimi_api_key,
                timeout=settings.llm_request_timeout_s,
            ),
        }
        self._model_names = {
            "qwen": settings.qwen_model_name,
            "kimi": settings.kimi_model_name,
        }
        self._role_backend = {
            "extraction": settings.role_extraction,
            "synthesis": settings.role_synthesis,
            "chat": settings.role_chat,
            "translation": settings.role_translation,
        }
        # One semaphore per physical backend (not per role -- several roles
        # can share one backend, e.g. extraction+synthesis+chat all on Kimi
        # by default, and they all draw from the same on-prem server's
        # actual capacity). Bounds concurrent in-flight requests to that
        # server regardless of how many files/segments the pipeline layer
        # above decides to run in parallel -- see config.py for why this
        # is needed.
        self._semaphores: dict[str, asyncio.Semaphore] = {
            backend: asyncio.Semaphore(settings.max_concurrent_llm_calls_per_backend)
            for backend in self._clients
        }

    def backend_for_role(self, role: str) -> str:
        return self._role_backend.get(role, "qwen")

    def roles_using(self, backend: str) -> list[str]:
        return [role for role, b in self._role_backend.items() if b == backend]

    async def check_reachable(self, backend: str, timeout_s: float = 8.0) -> tuple[bool, str | None]:
        """Fast connectivity probe -- a minimal chat completion (max_tokens=1),
        not GET /v1/models. Some on-prem OpenAI-compatible servers (seen in
        practice with Kimi2) implement /v1/chat/completions but not the
        /v1/models listing endpoint, returning a clean 404 for it -- that
        used to make this check report the backend as unreachable and skip
        synthesis/chat even though completions worked fine. A tiny real
        completion tests the actual capability every such server implements,
        so it can't be fooled by an unrelated endpoint being absent.

        Used as a preflight so a dead endpoint is reported in seconds instead
        of discovered only after several minutes of doomed retries deep
        inside file processing.
        """
        client = self._clients[backend]
        model = self._model_names[backend]
        base_url = self._settings.qwen_base_url if backend == "qwen" else self._settings.kimi_base_url
        try:
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
                timeout=timeout_s,
            )
            return True, None
        except Exception as exc:  # noqa: BLE001 - any failure means "unreachable" for our purposes
            detail = f"Cannot reach {backend} model endpoint at {base_url} ({exc.__class__.__name__}: {exc})"
            logger.warning(detail)
            return False, detail

    async def check_all_backends(self, timeout_s: float = 8.0) -> dict[str, tuple[bool, str | None]]:
        backends = ("qwen", "kimi")
        results = await asyncio.gather(*(self.check_reachable(b, timeout_s) for b in backends))
        return dict(zip(backends, results))

    async def complete(
        self,
        role: str,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        backend_override: str | None = None,
    ) -> LLMResponse:
        """backend_override: bypass the role -> backend mapping and force a
        specific physical backend for this one call -- used by chat's
        automatic failover (see graphrag.py) to answer from Qwen when the
        chat role's configured backend (normally Kimi2) is unreachable,
        without needing a config change + restart to recover from an
        outage."""
        backend = backend_override or self.backend_for_role(role)
        client = self._clients[backend]
        model = self._model_names[backend]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        semaphore = self._semaphores[backend]
        last_err: Exception | None = None
        for attempt in range(1, self._settings.llm_max_retries + 1):
            start = asyncio.get_event_loop().time()
            try:
                # Held only for this one attempt, not the whole retry loop
                # (including its backoff sleep) -- so a call backing off
                # frees its slot for other waiting callers instead of
                # holding it idle.
                async with semaphore:
                    resp = await client.chat.completions.create(**kwargs)
                elapsed_ms = int((asyncio.get_event_loop().time() - start) * 1000)
                choice = resp.choices[0].message.content or ""
                usage = resp.usage
                return LLMResponse(
                    text=choice,
                    model=model,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    latency_ms=elapsed_ms,
                )
            except (APIError, APITimeoutError) as exc:
                last_err = exc
                logger.warning("LLM call failed (attempt %s/%s) on %s: %s",
                                attempt, self._settings.llm_max_retries, backend, exc)
                await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError(f"LLM backend '{backend}' failed after retries: {last_err}")

    async def complete_json(self, role: str, system: str, user: str, **kwargs) -> dict:
        """Convenience wrapper: force JSON mode and parse the result robustly.

        Retries on a parse failure, not just complete()'s own network-level
        retries -- a truncated or markdown-wrapped response is frequently a
        one-off sampling artifact (e.g. the model ran out of max_tokens on
        a long entity list this time), so asking again often succeeds where
        re-parsing the same bad text never would.
        """
        last_err: LLMJSONParseError | None = None
        for attempt in range(1, self._settings.llm_max_retries + 1):
            resp = await self.complete(role, system, user, json_mode=True, **kwargs)
            try:
                return _safe_json_parse(resp.text)
            except LLMJSONParseError as exc:
                last_err = exc
                logger.warning("LLM JSON parse failed (attempt %s/%s) on role %s: %s",
                                attempt, self._settings.llm_max_retries, role, exc)
        raise last_err


def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some local models wrap JSON in markdown fences despite json_mode.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMJSONParseError(text[:500])


_client_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = LLMClient()
    return _client_singleton
