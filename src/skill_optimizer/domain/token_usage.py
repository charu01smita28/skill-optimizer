"""Token bucket sums + cost math.

Replaces the 4 spike cost constants. The ``Trace``-aware functions sum across
assistant turns at each message's actual ``message.model`` — runtime-override
anomalies (Haiku-declared, Sonnet-actual) are real in captured traces, so
costing happens per-message, not per-trace.
"""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.config.pricing import ModelRates, Pricing
from skill_optimizer.domain.trace import Trace


@dataclass(frozen=True)
class TokenUsage:
    """Bucket totals for one trace (or any subset of messages)."""
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
        )

    @classmethod
    def zero(cls) -> "TokenUsage":
        return cls(0, 0, 0, 0)

    @classmethod
    def from_usage_dict(cls, usage: dict | None) -> "TokenUsage":
        u = usage or {}
        return cls(
            input_tokens=int(u.get("input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        )

    def cost_at(self, rates: ModelRates) -> float:
        per_m = 1_000_000.0
        return (
            self.input_tokens * rates.input_per_mtok
            + self.output_tokens * rates.output_per_mtok
            + self.cache_read_tokens * rates.cache_read_per_mtok
            + self.cache_creation_tokens * rates.cache_creation_per_mtok
        ) / per_m


def trace_cost(trace: Trace, pricing: Pricing) -> float:
    """Sum cost across assistant messages, each at its own ``message.model``.

    Falls back to the trace's first declared model for any assistant message
    missing a model field. Raises if the message's model is not in pricing —
    we'd rather surface unknown-model than silently zero-cost it.
    """
    fallback = trace.models_used[0] if trace.models_used else ""
    total = 0.0
    for msg in trace.assistant_messages:
        model = msg.model or fallback
        if not model:
            continue
        usage = TokenUsage.from_usage_dict(msg.usage)
        total += usage.cost_at(pricing.rates_for(model))
    return total


def trace_usage(trace: Trace) -> TokenUsage:
    """Sum bucket counts across assistant messages."""
    total = TokenUsage.zero()
    for msg in trace.assistant_messages:
        total = total + TokenUsage.from_usage_dict(msg.usage)
    return total


def cost_at_model(trace: Trace, pricing: Pricing, model: str) -> float:
    """Cost the trace as if every assistant turn ran on ``model``.

    Used by D004 to predict cost after a model swap — the same token shape
    repriced under the target model's rates.
    """
    rates = pricing.rates_for(model)
    return trace_usage(trace).cost_at(rates)
