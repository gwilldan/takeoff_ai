"""
LLM wrapper with per-call token tracking.

Every LLM invocation goes through chat_with_tracking() so prompt/completion
token counts are recorded and surfaced in the final extraction result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import ollama

from config import LLM_MODEL


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


def _extract_usage(raw: dict, step: str, model: str) -> LLMUsage:
    prompt = int(raw.get("prompt_eval_count") or 0)
    completion = int(raw.get("eval_count") or 0)
    return LLMUsage(
        step=step,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
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
    Call ollama.chat and record token usage for this step.
    """
    model = model or LLM_MODEL

    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if format:
        kwargs["format"] = format

    raw = ollama.chat(**kwargs)
    message = raw["message"]
    usage = _extract_usage(raw, step, model)
    tracker.calls.append(usage)

    print(
        f"[llm:{step}] model={model} "
        f"prompt_tokens={usage.prompt_tokens} "
        f"completion_tokens={usage.completion_tokens} "
        f"total={usage.total_tokens}"
    )

    return LLMResponse(
        content=message.get("content") or "",
        usage=usage,
        message=message,
        raw=raw,
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
