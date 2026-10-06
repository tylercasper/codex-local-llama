from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

WEB_NAMESPACE = "web"
WEB_TOOL_NAME = "run"
FLAT_WEB_TOOL_NAME = "web_run"

WEB_TOOL_DESCRIPTION = """Search and read the public web through Tavily.

Supported operations:
- search_query: search one or more queries and receive titles, full URLs, and snippets.
- open: extract a page by passing its absolute http(s) URL as ref_id.
- find: find text on a page by passing its absolute http(s) URL and a pattern.

Opaque result references, click, screenshot, and browser sessions are not supported. Open the
full URL returned by search_query instead. Cite those URLs in the final answer.
"""

WEB_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search_query": {
            "type": "array",
            "description": "Web searches to run.",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Search query."},
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional domains to include.",
                    },
                    "recency": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional maximum age in days.",
                    },
                },
                "required": ["q"],
                "additionalProperties": False,
            },
        },
        "open": {
            "type": "array",
            "description": "Pages to extract. ref_id must be an absolute http(s) URL.",
            "items": {
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "lineno": {"type": "integer", "minimum": 1},
                },
                "required": ["ref_id"],
                "additionalProperties": False,
            },
        },
        "find": {
            "type": "array",
            "description": "Find patterns on extracted pages. ref_id must be an absolute URL.",
            "items": {
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "pattern": {"type": "string"},
                },
                "required": ["ref_id", "pattern"],
                "additionalProperties": False,
            },
        },
        "response_length": {
            "type": "string",
            "enum": ["short", "medium", "long"],
        },
    },
    "additionalProperties": False,
}

INSTRUCTION_ROLES = {"system", "developer"}


def normalize_instruction_messages(payload: dict[str, Any]) -> int:
    """Keep Qwen-compatible instructions at the beginning of Responses input.

    Codex can append developer messages when permissions change or a turn is
    interrupted. Qwen's llama.cpp chat template accepts instruction messages
    only at the beginning, so collect them into one leading message while
    preserving their content order and the order of every non-instruction item.
    The payload is mutated in place and the number of collected messages is
    returned for content-free diagnostics.
    """

    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return 0

    instructions: list[dict[str, Any]] = []
    ordinary_items: list[Any] = []
    for item in input_items:
        if _is_instruction_message(item):
            instructions.append(item)
        else:
            ordinary_items.append(item)

    if not instructions:
        return 0
    if len(instructions) == 1 and input_items[0] is instructions[0]:
        return 0

    leading = copy.deepcopy(instructions[0])
    if len(instructions) > 1:
        leading["content"] = _merge_instruction_content(instructions)
    input_items[:] = [leading, *ordinary_items]
    return len(instructions)


def _is_instruction_message(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "message"
        and value.get("role") in INSTRUCTION_ROLES
    )


def _merge_instruction_content(messages: list[dict[str, Any]]) -> list[Any]:
    merged: list[Any] = []
    for index, message in enumerate(messages):
        if index:
            merged.append({"type": "input_text", "text": "\n\n"})
        content = message.get("content")
        if isinstance(content, list):
            merged.extend(copy.deepcopy(content))
        elif isinstance(content, str):
            merged.append({"type": "input_text", "text": content})
    return merged


def flatten_web_namespace(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return a copy with the one Codex web namespace expanded for llama.cpp."""

    transformed = copy.deepcopy(payload)
    replacements = 0
    for tools in _tool_lists(transformed):
        rewritten: list[Any] = []
        for tool in tools:
            replacement = _flatten_web_tool(tool)
            if replacement is None:
                rewritten.append(tool)
            else:
                rewritten.append(replacement)
                replacements += 1
        tools[:] = rewritten
    return transformed, replacements


def _tool_lists(payload: dict[str, Any]) -> Iterator[list[Any]]:
    tools = payload.get("tools")
    if isinstance(tools, list):
        yield tools

    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        additional_tools = item.get("tools")
        if isinstance(additional_tools, list):
            yield additional_tools


def _flatten_web_tool(tool: Any) -> dict[str, Any] | None:
    if not isinstance(tool, dict):
        return None
    if tool.get("type") != "namespace" or tool.get("name") != WEB_NAMESPACE:
        return None

    inner_tools = tool.get("tools")
    if not isinstance(inner_tools, list):
        return None
    if not any(
        isinstance(inner, dict) and inner.get("name") == WEB_TOOL_NAME
        for inner in inner_tools
    ):
        return None

    return {
        "type": "function",
        "name": FLAT_WEB_TOOL_NAME,
        "description": WEB_TOOL_DESCRIPTION,
        "parameters": copy.deepcopy(WEB_TOOL_PARAMETERS),
        "strict": False,
    }


def restore_web_namespace_calls(value: Any) -> Any:
    """Restore the namespace fields Codex needs to dispatch the standalone tool."""

    if isinstance(value, list):
        for item in value:
            restore_web_namespace_calls(item)
        return value
    if not isinstance(value, dict):
        return value

    if value.get("type") == "function_call" and value.get("name") == FLAT_WEB_TOOL_NAME:
        value["name"] = WEB_TOOL_NAME
        value["namespace"] = WEB_NAMESPACE

    for child in value.values():
        restore_web_namespace_calls(child)
    return value


class SSETransformer:
    """Incrementally rewrite JSON data lines while preserving SSE framing."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> bytes:
        self._buffer.extend(chunk)
        output = bytearray()
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._buffer[: newline + 1])
            del self._buffer[: newline + 1]
            output.extend(_transform_sse_line(line))
        return bytes(output)

    def finish(self) -> bytes:
        if not self._buffer:
            return b""
        line = bytes(self._buffer)
        self._buffer.clear()
        return _transform_sse_line(line)


def _transform_sse_line(line: bytes) -> bytes:
    newline = b""
    body = line
    if body.endswith(b"\n"):
        newline = b"\n"
        body = body[:-1]
    carriage_return = b""
    if body.endswith(b"\r"):
        carriage_return = b"\r"
        body = body[:-1]

    if not body.startswith(b"data:"):
        return line

    prefix = b"data:"
    data = body[len(prefix) :].lstrip()
    if not data or data == b"[DONE]":
        return line

    try:
        event = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return line

    restore_web_namespace_calls(event)
    serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    return prefix + b" " + serialized + carriage_return + newline
