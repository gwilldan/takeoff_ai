"""
LLM wrapper with per-call token tracking.

Uses the DashScope OpenAI-compatible API (Qwen models).
Every LLM invocation goes through chat_with_tracking() so prompt/completion
token counts are recorded and surfaced in the final extraction result.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

from config import DASHSCOPE_BASE_URL, LLM_MODEL


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY environment variable is not set")
        _client = OpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)
    return _client


@dataclass
class LLMUsage:
    step: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage
    message: dict
    raw: dict


@dataclass
class TokenTracker:
    """Accumulates token usage across all LLM calls in one extraction run."""

    calls: list[LLMUsage] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.calls)

    def to_dict(self) -> dict:
        return {
            "calls": [c.to_dict() for c in self.calls],
            "totals": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
        }


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """
    Convert internal message format to OpenAI chat format.

    Supports legacy Ollama-style messages that attach base64 images via an
    ``images`` key on the message dict.
    """
    normalized: list[dict] = []

    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        images = message.get("images") or []

        if images:
            parts: list[dict] = []
            if content:
                parts.append({"type": "text", "text": content})
            for image_b64 in images:
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}",
                    },
                })
            normalized.append({"role": role, "content": parts})
        else:
            normalized.append({"role": role, "content": content})

    return normalized


def _extract_usage(completion: Any, step: str, model: str) -> LLMUsage:
    usage = getattr(completion, "usage", None)
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or (prompt + completion_tokens))
    return LLMUsage(
        step=step,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion_tokens,
        total_tokens=total,
    )


def chat_with_tracking(
    tracker: TokenTracker,
    *,
    step: str,
    messages: list[dict],
    model: Optional[str] = None,
    tools: Optional[list] = None,
    format: Optional[str] = None,
) -> LLMResponse:
    """
    Call the DashScope-compatible chat completions API and record token usage.
    """
    model = model or LLM_MODEL
    client = get_client()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _normalize_messages(messages),
    }
    if tools:
        kwargs["tools"] = tools
    if format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    completion = client.chat.completions.create(**kwargs)
    choice = completion.choices[0].message
    content = choice.content or ""
    usage = _extract_usage(completion, step, model)
    tracker.calls.append(usage)

    print(
        f"[llm:{step}] model={model} "
        f"prompt_tokens={usage.prompt_tokens} "
        f"completion_tokens={usage.completion_tokens} "
        f"total={usage.total_tokens}"
    )

    message = {
        "role": choice.role,
        "content": content,
    }

    return LLMResponse(
        content=content,
        usage=usage,
        message=message,
        raw=completion.model_dump(),
    )


def parse_json_response(content: str) -> dict:
    """Strip markdown fences and parse JSON from an LLM response."""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    text = text.strip()
    return json.loads(text)
