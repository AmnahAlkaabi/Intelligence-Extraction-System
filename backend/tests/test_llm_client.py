"""Tests for llm/client.py -- previously zero coverage. Covers three
fixes verified manually during production testing but never given
permanent regression coverage:

1. complete_json() retries on a JSON-parse failure (not just complete()'s
   own network-level retries) -- a truncated or malformed response is
   often a one-off sampling artifact.
2. A parse failure caused by finish_reason == "length" is NOT random --
   an entity-dense segment can deterministically need more output tokens
   than the budget allows, and a plain retry regenerates the identical
   truncation every time. complete_json escalates max_tokens instead.
3. LLMClient bounds concurrent in-flight requests to each physical
   backend independently of how many callers ask for it at once, so a
   busy pipeline can't flood one on-prem model server with dozens of
   simultaneous requests.
"""
import asyncio

import pytest

from app.llm.client import LLMClient, LLMResponse


def _make_client() -> LLMClient:
    """LLMClient() only builds SDK client objects at construction --
    no network call happens until a request is actually issued, so this
    is safe to instantiate directly against default (unreachable)
    settings in a test."""
    return LLMClient()


@pytest.mark.asyncio
async def test_complete_json_retries_on_parse_failure_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def fake_complete(self, role, system, user, *, json_mode=False, max_tokens=4096, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(text="not valid json", model="fake", prompt_tokens=1,
                                completion_tokens=1, latency_ms=1, finish_reason="stop")
        return LLMResponse(text='{"ok": true}', model="fake", prompt_tokens=1,
                            completion_tokens=1, latency_ms=1, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    client = _make_client()

    data = await client.complete_json("extraction", "sys", "user")

    assert data == {"ok": True}
    assert calls["n"] == 2  # failed once, succeeded on retry


@pytest.mark.asyncio
async def test_complete_json_escalates_max_tokens_on_truncation(monkeypatch):
    """finish_reason == "length" means the response was cut off by the
    token budget -- retrying with the SAME budget would regenerate the
    identical truncation, so complete_json must raise the budget instead
    of blindly repeating the call."""
    seen_max_tokens = []

    async def fake_complete(self, role, system, user, *, json_mode=False, max_tokens=4096, **kwargs):
        seen_max_tokens.append(max_tokens)
        if max_tokens < 8192:
            return LLMResponse(text='{"entities": [{"name": "cut off mid', model="fake",
                                prompt_tokens=1, completion_tokens=max_tokens, latency_ms=1,
                                finish_reason="length")
        return LLMResponse(text='{"entities": []}', model="fake", prompt_tokens=1,
                            completion_tokens=10, latency_ms=1, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    client = _make_client()

    data = await client.complete_json("extraction", "sys", "user")

    assert data == {"entities": []}
    assert seen_max_tokens == [4096, 8192]  # doubled once, then succeeded


@pytest.mark.asyncio
async def test_complete_json_max_tokens_ceiling_holds(monkeypatch):
    """Escalation must stop at the ceiling (8192) -- a segment dense
    enough to still overflow there must keep retrying AT the ceiling,
    never doubling past it."""
    seen_max_tokens = []

    async def fake_complete(self, role, system, user, *, json_mode=False, max_tokens=4096, **kwargs):
        seen_max_tokens.append(max_tokens)
        if len(seen_max_tokens) < 3:
            return LLMResponse(text='{"cut off', model="fake", prompt_tokens=1,
                                completion_tokens=max_tokens, latency_ms=1, finish_reason="length")
        return LLMResponse(text='{"ok": true}', model="fake", prompt_tokens=1,
                            completion_tokens=10, latency_ms=1, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    client = _make_client()

    await client.complete_json("extraction", "sys", "user")

    assert seen_max_tokens == [4096, 8192, 8192]  # never exceeds the ceiling


@pytest.mark.asyncio
async def test_complete_json_gives_up_after_max_retries(monkeypatch):
    async def fake_complete(self, role, system, user, *, json_mode=False, max_tokens=4096, **kwargs):
        return LLMResponse(text="never valid json", model="fake", prompt_tokens=1,
                            completion_tokens=1, latency_ms=1, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    client = _make_client()

    with pytest.raises(Exception):
        await client.complete_json("extraction", "sys", "user")


@pytest.mark.asyncio
async def test_concurrent_requests_never_exceed_the_per_backend_limit(monkeypatch):
    """20 concurrent callers against one backend must never have more
    than MAX_CONCURRENT_LLM_CALLS_PER_BACKEND requests in flight at
    once -- without this, MAX_PARALLEL_FILES files x many segments each
    can flood one on-prem model server with dozens of simultaneous
    requests, which just queues/times out rather than processing any
    faster."""
    current = {"n": 0}
    peak = {"n": 0}
    lock = asyncio.Lock()

    class _FakeCompletions:
        async def create(self, **kwargs):
            async with lock:
                current["n"] += 1
                peak["n"] = max(peak["n"], current["n"])
            await asyncio.sleep(0.05)
            async with lock:
                current["n"] -= 1

            class _Choice:
                class _Msg:
                    content = '{"ok": true}'
                message = _Msg()
                finish_reason = "stop"
            class _Resp:
                choices = [_Choice()]
                usage = None
            return _Resp()

    client = _make_client()
    limit = client._settings.max_concurrent_llm_calls_per_backend
    for backend_client in client._clients.values():
        backend_client.chat.completions = _FakeCompletions()

    await asyncio.gather(*(
        client.complete("extraction", "sys", f"call {i}") for i in range(20)
    ))

    assert peak["n"] <= limit
    assert peak["n"] == limit  # confirms concurrency was actually exercised, not over-throttled
