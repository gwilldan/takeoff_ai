"""
LLM wrapper with per-call token tracking.

Uses the DashScope OpenAI-compatible API (Qwen models). Every call goes through
`chat_with_tracking` so prompt/completion tokens surface in the result.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

from app.config import settings

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY environment variable is not set")
        _client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)
    return _client


@dataclass
class LLMUsage:
    step: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage


@dataclass
class TokenTracker:
    """Accumulates token usage across the LLM calls of one page."""

    calls: list[LLMUsage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "calls": [c.to_dict() for c in self.calls],
            "totals": {
                key: sum(getattr(c, key) for c in self.calls)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
        }


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """Attach base64 PNGs given under an `images` key as OpenAI image parts."""
    normalized = []
    for message in messages:
        images = message.get("images") or []
        content = message.get("content", "")
        if images:
            content = [{"type": "text", "text": content}] if content else []
            content += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}} for b64 in images]
        normalized.append({"role": message["role"], "content": content})
    return normalized


def chat_with_tracking(tracker: TokenTracker, *, step: str, messages: list[dict], model: str | None = None) -> LLMResponse:
    model = model or settings.llm_model
    completion = get_client().chat.completions.create(model=model, messages=_normalize_messages(messages))

    usage = getattr(completion, "usage", None)
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    answer = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or prompt + answer)
    tracked = LLMUsage(step, model, prompt, answer, total)
    tracker.calls.append(tracked)
    log.info("[%s] model=%s prompt_tokens=%d completion_tokens=%d total=%d", step, model, prompt, answer, total)

    return LLMResponse(content=completion.choices[0].message.content or "", usage=tracked)


def parse_json_response(content: str) -> dict:
    """Strip markdown fences and parse JSON from an LLM response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json")
    return json.loads(text.strip())
