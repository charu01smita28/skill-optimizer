# Trace Mapping

Reference for the data Claude Code produces during a skill run — the JSONL session trace, mapped onto typed classes from the Claude Agent SDK. Each field is shown alongside the class it populates, or the documented reason it isn't surfaced. Useful for inspecting a session trace, building on top of Claude's session output, or understanding what the optimizer sees.

## What the parser does

`src/skill_optimizer/domain/trace.py` parses a Claude Code session JSONL into a `Trace` aggregate. The parser accepts any path; the `claude` runtime writes session logs to `${CLAUDE_CONFIG_DIR}/projects/<sanitized-cwd>/<uuid>.jsonl`, which the capture pipeline (`scripts/capture_traces.py`) copies out and the verifier (`verifier._locate_replay_trace`) reads.

`Trace` contains:

- Session-level metadata (`session_id`, `cwd`, `version`, `is_sidechain`)
- The first user prompt (`initial_prompt`)
- A chronologically-ordered tuple of messages (`messages`), each wrapped in the corresponding SDK type:
  - `AssistantMessage` from `claude_agent_sdk` — model output with content blocks
  - `UserMessage` from `claude_agent_sdk` — user prompts and tool results

Content blocks inside each message are wrapped in their SDK types: `TextBlock`, `ThinkingBlock`, `ToolUseBlock`, `ToolResultBlock`. No custom dataclasses — first-party SDK shapes throughout.

Convenience accessors on `Trace`:

- `assistant_messages` / `user_messages` — filtered tuples
- `models_used` — distinct models across assistant turns
- `total_input_tokens` / `total_output_tokens` — summed across assistant turns
- `tool_results_by_use_id` — `dict[tool_use.id, ToolResultBlock]` for fast tool-pair lookup

## SDK types in use

`Trace.messages` is a tuple of `AssistantMessage | UserMessage`. Each message's `content` is a list of content blocks. All types come from `claude_agent_sdk`:

```python
@dataclass
class AssistantMessage:
    content: list[ContentBlock]                # see content blocks below
    model: str
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    message_id: str | None = None
    session_id: str | None = None
    uuid: str | None = None
    error: Literal["authentication_failed", "billing_error", "rate_limit",
                   "invalid_request", "server_error", "unknown"] | None = None
    parent_tool_use_id: str | None = None      # not populated by trace.py — see dropped fields

@dataclass
class UserMessage:
    content: str | list[ContentBlock]          # str for initial prompt, list for tool replies
    tool_use_result: dict[str, Any] | None = None
    uuid: str | None = None
    parent_tool_use_id: str | None = None      # not populated by trace.py — see dropped fields

# Content blocks (each message's `content` is a list of these):

@dataclass
class TextBlock:
    text: str

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None

# Also exported but rare in skill traces:
# ServerToolUseBlock, ServerToolResultBlock — for server-side tools like web_search.
```

Source: `claude_agent_sdk` package itself

## Field coverage guarantee

Every field path in real captured JSONL is one of:

- **Captured** — preserved in the SDK type or in `Trace`
- **Intentionally dropped** — with a documented rationale (see table below)
- **Silently dropped** — should be **zero** (audited by `scripts/map_trace_fields.py`)

To regenerate the mapping for any captured trace:

```bash
python scripts/map_trace_fields.py traces/<skill>/<mode>/<file>.jsonl
```

The script will report zero silent drops if the parser is in sync with the JSONL schema. If a future Claude Code release adds a new field, the script will surface it as a silent drop and `trace.py` can be updated.

---

## Captured fields — assistant records

| JSONL path | Goes to |
|---|---|
| `sessionId` | `Trace.session_id` |
| `cwd` | `Trace.cwd` |
| `version` | `Trace.version` |
| `isSidechain` | `Trace.is_sidechain` |
| `uuid` | `AssistantMessage.uuid` |
| `type=assistant` | discriminator → `AssistantMessage` |
| `message.model` | `AssistantMessage.model` |
| `message.usage` (and all sub-fields) | `AssistantMessage.usage` (dict, preserved) |
| `message.stop_reason` | `AssistantMessage.stop_reason` |
| `message.id` | `AssistantMessage.message_id` |
| `message.content[*].type=text` | → `TextBlock` |
| `message.content[*].text` | `TextBlock.text` |
| `message.content[*].type=thinking` | → `ThinkingBlock` |
| `message.content[*].thinking` | `ThinkingBlock.thinking` |
| `message.content[*].signature` | `ThinkingBlock.signature` |
| `message.content[*].type=tool_use` | → `ToolUseBlock` |
| `message.content[*].id` | `ToolUseBlock.id` |
| `message.content[*].name` | `ToolUseBlock.name` |
| `message.content[*].input` (dict) | `ToolUseBlock.input` |

## Captured fields — user records

| JSONL path | Goes to |
|---|---|
| (session metadata, same as above) | (same as above) |
| `type=user` | discriminator → `UserMessage` |
| `message.content` (str or list) | `UserMessage.content` |
| `message.content[*].type=tool_result` | → `ToolResultBlock` |
| `message.content[*].tool_use_id` | `ToolResultBlock.tool_use_id` |
| `message.content[*].content` | `ToolResultBlock.content` |
| `message.content[*].is_error` | `ToolResultBlock.is_error` |
| `toolUseResult` (dict, all sub-fields) | `UserMessage.tool_use_result` |

## Bookkeeping records — dropped at parse time

These record types carry no signal for the optimizer and are dropped before any wrapping:

| Record type | Purpose |
|---|---|
| `queue-operation` | enqueue/dequeue marker for the user prompt |
| `last-prompt` | end-of-session marker referencing leaf record uuid |
| `ai-title` | model-generated session title (cosmetic) |
| `attachment` | sidecar payloads (e.g., `deferred_tools_delta`) |

---

## Intentionally dropped fields

These fields exist on real captured records but are NOT preserved in the parsed `Trace`. Each has a documented reason:

| JSONL path | Why dropped |
|---|---|
| `parentUuid` | Conversational chain pointer not stored on individual messages — chronological order is encoded by tuple position in `Trace.messages`. If a detector needs the parent/child tree (e.g., D008 parallelization), expose a `parent_uuid_by_uuid` lookup at that point. |
| `sourceToolAssistantUUID` | Redundant — tool_use → tool_result pairing is recoverable via `ToolUseBlock.id` ↔ `ToolResultBlock.tool_use_id` (see `Trace.tool_results_by_use_id`). |
| `message.role` | Redundant with the record `type` discriminator. |
| `message.type` | Redundant with the record `type` discriminator. |
| `entrypoint`, `gitBranch`, `userType` | Session-launch metadata — doesn't shape optimizer signal. |
| `requestId`, `promptId` | API / prompt debugging IDs — not optimization signal. |
| `permissionMode` | Agent-permission state — D011 reads this from `SKILL.md` frontmatter directly. |
| `timestamp` | Per-record wall-clock — current detectors use run-level `elapsed_s` from the manifest. |
| `message.diagnostics` | API warnings — interesting for debugging, not waste-pattern signal. |
| `message.stop_details`, `message.stop_sequence` | Beyond `stop_reason` — not used by current detectors. |
| `message.content[*].caller`, `.caller.type` | Block-level agent attribution — only relevant once D008 supports multi-agent. |

---

## Verification scripts

Two scripts that audit and re-verify the mapping. Both should be re-run whenever `trace.py` or the SDK message types change.

| Script | What it does |
|---|---|
| `scripts/verify_current_trace.py` | Parses one corpus file end-to-end and prints session metadata + block-type histogram. Confirms the parser produces SDK-typed objects with full content-block coverage. |
| `scripts/map_trace_fields.py <jsonl>` | Walks every field path in the JSONL and classifies it as captured / intentionally dropped / silently dropped. Should report zero silent drops. |

To capture a fresh single-input trace for verification (writes to `traces/<skill>/_verify/`, doesn't touch the main corpus):

```bash
python scripts/capture_traces.py --skill demo/skills/ticket_router --input ticket_001.txt
```

---

## Using `trace.py`

The parser is single-file and pure-Python — copy `domain/trace.py` and adjust imports. The SDK types section above shows what's returned; the dropped-fields table shows what's missing and how to add it (`domain/trace.py`'s docstring carries one-line how-to-add notes for `parentUuid` and per-block `caller`).
