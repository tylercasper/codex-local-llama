from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

WEB_NAMESPACE = "web"
WEB_TOOL_NAME = "run"
FLAT_WEB_TOOL_NAME = "web_run"
APPLY_PATCH_TOOL_NAME = "apply_patch"
APPLY_PATCH_ARGUMENT_NAME = "patch"
VIEW_IMAGE_TOOL_NAME = "view_image"
RAW_REASONING_DELTA_TYPE = "response.reasoning_text.delta"
RAW_REASONING_DONE_TYPE = "response.reasoning_text.done"
VISIBLE_REASONING_DELTA_TYPE = "response.reasoning_summary_text.delta"
VISIBLE_REASONING_DONE_TYPE = "response.reasoning_summary_text.done"

WEB_TOOL_DESCRIPTION = """Search and read the public web.

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

APPLY_PATCH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        APPLY_PATCH_ARGUMENT_NAME: {
            "type": "string",
            "description": "The complete *** Begin Patch ... *** End Patch patch text.",
        }
    },
    "required": [APPLY_PATCH_ARGUMENT_NAME],
    "additionalProperties": False,
}


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


def translate_apply_patch_request(payload: dict[str, Any]) -> int:
    """Translate Codex's custom apply_patch protocol for llama.cpp.

    Codex 0.147 exposes apply_patch as a Responses custom tool. llama.cpp's
    Responses compatibility layer drops non-function tools, so present it to
    the model as a normal function and translate prior call history back to
    the function-call input shapes llama.cpp accepts. The payload is mutated
    in place and the number of translated tool declarations is returned.
    """

    replacements = 0
    for tools in _tool_lists(payload):
        for index, tool in enumerate(tools):
            replacement = _function_apply_patch_tool(tool)
            if replacement is None:
                continue
            tools[index] = replacement
            replacements += 1

    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return replacements

    apply_patch_call_ids = {
        item.get("call_id")
        for item in input_items
        if isinstance(item, dict)
        and item.get("type") == "custom_tool_call"
        and item.get("name") == APPLY_PATCH_TOOL_NAME
        and isinstance(item.get("call_id"), str)
    }
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if (
            item.get("type") == "custom_tool_call"
            and item.get("name") == APPLY_PATCH_TOOL_NAME
        ):
            patch = item.pop("input", "")
            item["type"] = "function_call"
            item["arguments"] = json.dumps(
                {APPLY_PATCH_ARGUMENT_NAME: patch},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        elif (
            item.get("type") == "custom_tool_call_output"
            and item.get("call_id") in apply_patch_call_ids
        ):
            item["type"] = "function_call_output"
    return replacements


def normalize_view_image_outputs(payload: dict[str, Any]) -> int:
    """Move Codex view-image results into Qwen-compatible user messages.

    Codex records ``view_image`` results as image content inside a
    ``function_call_output``. llama.cpp's Responses parser requires function
    outputs to contain input text, even though it accepts the same image
    content in an ordinary user message. Keep the tool result in the history
    as text and insert its image immediately afterward without changing the
    encoded image data or requested detail level.
    """

    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return 0

    view_image_calls: dict[str, str | None] = {}
    for item in input_items:
        if (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and item.get("name") == VIEW_IMAGE_TOOL_NAME
            and isinstance(item.get("call_id"), str)
        ):
            view_image_calls[item["call_id"]] = _view_image_path(item.get("arguments"))

    if not view_image_calls:
        return 0

    rewritten: list[Any] = []
    replacements = 0
    for item in input_items:
        rewritten.append(item)
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        call_id = item.get("call_id")
        if call_id not in view_image_calls:
            continue

        output = item.get("output")
        if not isinstance(output, list):
            continue
        images = [
            copy.deepcopy(part)
            for part in output
            if isinstance(part, dict) and part.get("type") == "input_image"
        ]
        if not images:
            continue

        text_parts = [
            part.get("text")
            for part in output
            if isinstance(part, dict)
            and part.get("type") == "input_text"
            and isinstance(part.get("text"), str)
        ]
        item["output"] = "\n".join(text_parts) or "Image loaded successfully."

        path = view_image_calls[call_id]
        annotation = "Image returned by the view_image tool."
        if path:
            annotation = f"Image returned by view_image for {path}."
        rewritten.append(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": annotation},
                    *images,
                ],
            }
        )
        replacements += 1

    if replacements:
        input_items[:] = rewritten
    return replacements


def _view_image_path(arguments: Any) -> str | None:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict):
        return None
    path = arguments.get("path")
    return path if isinstance(path, str) else None


def _function_apply_patch_tool(tool: Any) -> dict[str, Any] | None:
    if not isinstance(tool, dict):
        return None
    if tool.get("type") != "custom" or tool.get("name") != APPLY_PATCH_TOOL_NAME:
        return None

    return {
        "type": "function",
        "name": APPLY_PATCH_TOOL_NAME,
        "description": tool.get(
            "description",
            "Apply a file patch using the Codex apply_patch format.",
        ),
        "parameters": copy.deepcopy(APPLY_PATCH_PARAMETERS),
        "strict": False,
    }


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


def restore_apply_patch_custom_calls(value: Any) -> Any:
    """Restore function-form apply_patch calls to Codex's custom-tool shape."""

    if isinstance(value, list):
        for item in value:
            restore_apply_patch_custom_calls(item)
        return value
    if not isinstance(value, dict):
        return value

    if value.get("type") == "function_call" and value.get("name") == APPLY_PATCH_TOOL_NAME:
        arguments = value.pop("arguments", "")
        value["type"] = "custom_tool_call"
        value["input"] = _patch_from_function_arguments(arguments)

    for child in value.values():
        restore_apply_patch_custom_calls(child)
    return value


def expose_raw_reasoning_to_codex(value: Any) -> Any:
    """Carry llama.cpp raw reasoning through Codex 0.147's visible channel.

    llama.cpp emits the Responses API's ``reasoning_text`` events for parsed
    thinking. Codex 0.147 consumes ``reasoning_summary_text`` events instead.
    The adapter relabels the stream without changing its text and mirrors a
    completed reasoning item's raw content into its summary while retaining
    the original content for llama.cpp conversation history.
    """

    if isinstance(value, list):
        for item in value:
            expose_raw_reasoning_to_codex(item)
        return value
    if not isinstance(value, dict):
        return value

    event_type = value.get("type")
    if event_type == RAW_REASONING_DELTA_TYPE:
        value["type"] = VISIBLE_REASONING_DELTA_TYPE
        value["summary_index"] = value.pop("content_index", 0)
        value.setdefault("output_index", 0)
    elif event_type == RAW_REASONING_DONE_TYPE:
        value["type"] = VISIBLE_REASONING_DONE_TYPE
        value["summary_index"] = value.pop("content_index", 0)
        value.setdefault("output_index", 0)
    elif event_type == "reasoning":
        _mirror_reasoning_content_to_summary(value)

    for child in value.values():
        expose_raw_reasoning_to_codex(child)
    return value


def _mirror_reasoning_content_to_summary(item: dict[str, Any]) -> None:
    content = item.get("content")
    if not isinstance(content, list) or not content:
        return
    summary = item.get("summary")
    if isinstance(summary, list) and summary:
        return

    mirrored = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "reasoning_text":
            continue
        text = part.get("text")
        if isinstance(text, str):
            mirrored.append({"type": "summary_text", "text": text})
    if mirrored:
        item["summary"] = mirrored


def _patch_from_function_arguments(arguments: Any) -> str:
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    else:
        return str(arguments)

    if isinstance(parsed, dict):
        for key in (APPLY_PATCH_ARGUMENT_NAME, "input", "command"):
            value = parsed.get(key)
            if isinstance(value, str):
                return value
    return arguments if isinstance(arguments, str) else json.dumps(parsed)


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

    if body.startswith(b"event:"):
        event_name = body.removeprefix(b"event:").strip()
        replacements = {
            RAW_REASONING_DELTA_TYPE.encode(): VISIBLE_REASONING_DELTA_TYPE.encode(),
            RAW_REASONING_DONE_TYPE.encode(): VISIBLE_REASONING_DONE_TYPE.encode(),
        }
        replacement = replacements.get(event_name)
        if replacement is not None:
            return b"event: " + replacement + carriage_return + newline
        return line

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
    restore_apply_patch_custom_calls(event)
    expose_raw_reasoning_to_codex(event)
    serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    return prefix + b" " + serialized + carriage_return + newline
