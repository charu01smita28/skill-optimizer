"""Port: LLMClient — abstract interface for LLM judge / rewriter calls.

Concrete implementations live in ``skill_optimizer.adapters.<adapter>``.
Detectors and mutations import the Protocol from this module; they never
import the adapter directly. This is the seam unit tests mock.
"""
from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Abstract interface for a single LLM completion call.

    ``complete()`` is synchronous and stateless — caller is responsible for
    any caching, retry, or batching policy.
    """

    def complete(self, system: str, user: str, model: str) -> str:
        """Send a system+user prompt and return the response as a string.

        Raises ``LLMClientError`` on transport failure (timeout, non-zero exit,
        unparseable response). Successful but unhelpful responses (e.g. empty
        string, off-topic) are returned as-is — caller decides how to interpret.
        """
        ...


class LLMClientError(Exception):
    """Raised by an LLMClient adapter when a completion call fails."""
