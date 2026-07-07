"""Domain types for Claude Code session traces.

Each parsed record is wrapped in the corresponding SDK type
(`AssistantMessage`, `UserMessage`, with content blocks `TextBlock`,
`ThinkingBlock`, `ToolUseBlock`, `ToolResultBlock`).

For the full per-field accounting against real captured JSONL, see
``scripts/map_trace_fields.py``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

OUTPUT_FILENAME = "output.json"
_INPUT_FILENAME_RE = re.compile(r"sample_inputs/([^\s,)\"'<>]+)")


ContentBlock = Union[TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock]
SessionMessage = Union[AssistantMessage, UserMessage]


@dataclass(frozen=True)
class Trace:
    """One Claude Code session — the unit the optimizer ingests.

    `messages` carries full-fidelity SDK message objects in chronological
    order (user + assistant). 
    """

    session_id: str
    cwd: str
    initial_prompt: str
    version: str
    is_sidechain: bool
    messages: tuple[SessionMessage, ...]

    @property
    def assistant_messages(self) -> tuple[AssistantMessage, ...]:
        return tuple(m for m in self.messages if isinstance(m, AssistantMessage))

    @property
    def user_messages(self) -> tuple[UserMessage, ...]:
        return tuple(m for m in self.messages if isinstance(m, UserMessage))

    @property
    def tool_results_by_use_id(self) -> dict[str, ToolResultBlock]:
        """Map tool_use.id → ToolResultBlock for D003 (tool-retry pairing).

        """
        result: dict[str, ToolResultBlock] = {}
        for um in self.user_messages:
            if isinstance(um.content, list):
                for b in um.content:
                    if isinstance(b, ToolResultBlock):
                        result[b.tool_use_id] = b
        return result

    @property
    def models_used(self) -> tuple[str, ...]:
        seen: list[str] = []
        for m in self.assistant_messages:
            if m.model and m.model not in seen:
                seen.append(m.model)
        return tuple(seen)

    @property
    def total_input_tokens(self) -> int:
        return sum(
            (m.usage or {}).get("input_tokens", 0)
            + (m.usage or {}).get("cache_creation_input_tokens", 0)
            + (m.usage or {}).get("cache_read_input_tokens", 0)
            for m in self.assistant_messages
        )

    @property
    def total_output_tokens(self) -> int:
        return sum((m.usage or {}).get("output_tokens", 0) for m in self.assistant_messages)


def _build_block(raw: dict) -> ContentBlock | None:
    """Convert a raw content-block dict to the matching SDK type.

    Returns None for block types the optimizer doesn't model yet
    (`server_tool_use`, `server_tool_result`).
    """
    btype = raw.get("type")
    if btype == "text":
        return TextBlock(text=str(raw.get("text", "")))
    if btype == "thinking":
        return ThinkingBlock(
            thinking=str(raw.get("thinking", "")),
            signature=str(raw.get("signature", "")),
        )
    if btype == "tool_use":
        return ToolUseBlock(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            input=raw.get("input", {}) or {},
        )
    if btype == "tool_result":
        return ToolResultBlock(
            tool_use_id=str(raw.get("tool_use_id", "")),
            content=raw.get("content"),
            is_error=raw.get("is_error"),
        )
    return None


def _build_assistant(record: dict) -> AssistantMessage:
    msg = record.get("message", {}) or {}
    raw_content = msg.get("content", []) or []
    blocks: list[ContentBlock] = []
    for raw in raw_content:
        if not isinstance(raw, dict):
            continue
        b = _build_block(raw)
        if b is not None:
            blocks.append(b)
    return AssistantMessage(
        content=blocks,
        model=str(msg.get("model", "")),
        usage=msg.get("usage"),
        message_id=msg.get("id"),
        stop_reason=msg.get("stop_reason"),
        session_id=record.get("sessionId"),
        uuid=record.get("uuid"),
    )


def _build_user(record: dict) -> UserMessage:
    msg = record.get("message", {}) or {}
    raw_content = msg.get("content")
    content: str | list[ContentBlock]
    if isinstance(raw_content, str):
        content = raw_content
    else:
        blocks: list[ContentBlock] = []
        for raw in raw_content or []:
            if not isinstance(raw, dict):
                continue
            b = _build_block(raw)
            if b is not None:
                blocks.append(b)
        content = blocks
    return UserMessage(
        content=content,
        uuid=record.get("uuid"),
        tool_use_result=record.get("toolUseResult"),
    )


def _extract_initial_prompt(records: list[dict]) -> str:
    """First user record with parentUuid=None whose message.content is a string."""
    for r in records:
        if r.get("type") != "user":
            continue
        if r.get("parentUuid") is not None:
            continue
        msg = r.get("message", {}) or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
    return ""


def parse_trace(jsonl_text: str) -> Trace:
    """Parse a Claude Code JSONL session log into a Trace.
    """
    records: list[dict] = []
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        raise ValueError("trace contains no valid JSON records")

    session_id = ""
    cwd = ""
    version = ""
    is_sidechain = False
    for r in records:
        if not session_id and r.get("sessionId"):
            session_id = str(r["sessionId"])
        if not cwd and r.get("cwd"):
            cwd = str(r["cwd"])
        if not version and r.get("version"):
            version = str(r["version"])
        if r.get("isSidechain"):
            is_sidechain = True

    messages: list[SessionMessage] = []
    for r in records:
        rtype = r.get("type")
        if rtype == "assistant":
            messages.append(_build_assistant(r))
        elif rtype == "user":
            messages.append(_build_user(r))

    return Trace(
        session_id=session_id,
        cwd=cwd,
        initial_prompt=_extract_initial_prompt(records),
        version=version,
        is_sidechain=is_sidechain,
        messages=tuple(messages),
    )


def parse_trace_file(path: Path) -> Trace:
    return parse_trace(path.read_text())


def extract_output_from_trace(
    trace: Trace, output_filename: str = OUTPUT_FILENAME,
) -> dict | None:
    """Recover the skill's structured output by scanning Write tool calls.

    The skill's prompt instructs ``save the result as {skill_dir}/<output_filename>``,
    so the model emits a Write tool call whose ``file_path`` ends in
    ``output_filename``. The last such call wins (rewrites are valid). Returns
    None when no matching Write happened, the content isn't valid JSON, or
    the parsed JSON isn't a dict (the verifier's primary-field comparison
    expects a top-level object).

    ``output_filename`` defaults to ``output.json`` but can be overridden
    per skill via the ``output_path:`` SKILL.md frontmatter line.
    """
    last_content: str | None = None
    for am in trace.assistant_messages:
        if not isinstance(am.content, list):
            continue
        for block in am.content:
            if not isinstance(block, ToolUseBlock):
                continue
            if block.name != "Write":
                continue
            file_path = str(block.input.get("file_path", ""))
            if not file_path.endswith(output_filename):
                continue
            content = block.input.get("content")
            if isinstance(content, str):
                last_content = content
    if last_content is None:
        return None
    try:
        parsed = json.loads(last_content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_input_filename(trace: Trace, fallback: str) -> str:
    """First ``sample_inputs/<name>`` reference in the initial prompt; else fallback.

    Used when synthesizing run metadata from a JSONL that lacks a manifest entry.
    Matches our standard replay prompt template; bring-your-own traces from
    production runs may not match, in which case we fall back to the trace's
    own identifier so each trace counts as its own input.
    """
    if not trace.initial_prompt:
        return fallback
    match = _INPUT_FILENAME_RE.search(trace.initial_prompt)
    return match.group(1) if match else fallback
