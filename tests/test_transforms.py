from __future__ import annotations

import json

from codex_altair_provider.transforms import (
    SSETransformer,
    flatten_web_namespace,
    normalize_instruction_messages,
    restore_web_namespace_calls,
)


def _web_namespace() -> dict:
    return {
        "type": "namespace",
        "name": "web",
        "description": "web tools",
        "tools": [{"type": "function", "name": "run", "parameters": {}}],
    }


def test_flattens_only_web_namespace_in_top_level_tools() -> None:
    original = {
        "tools": [
            _web_namespace(),
            {"type": "function", "name": "exec_command", "parameters": {}},
            {"type": "namespace", "name": "mcp__docs", "tools": []},
        ]
    }

    transformed, replacements = flatten_web_namespace(original)

    assert replacements == 1
    assert transformed["tools"][0]["type"] == "function"
    assert transformed["tools"][0]["name"] == "web_run"
    assert set(transformed["tools"][0]["parameters"]["properties"]) == {
        "search_query",
        "open",
        "find",
        "response_length",
    }
    assert transformed["tools"][1] == original["tools"][1]
    assert transformed["tools"][2] == original["tools"][2]
    assert original["tools"][0]["type"] == "namespace"


def test_flattens_responses_lite_additional_tools() -> None:
    payload = {"input": [{"type": "additional_tools", "tools": [_web_namespace()]}]}

    transformed, replacements = flatten_web_namespace(payload)

    assert replacements == 1
    assert transformed["input"][0]["tools"][0]["name"] == "web_run"


def test_restores_function_call_namespace_recursively() -> None:
    event = {
        "type": "response.output_item.added",
        "item": {
            "type": "function_call",
            "name": "web_run",
            "arguments": "{}",
            "call_id": "call_1",
        },
    }

    restore_web_namespace_calls(event)

    assert event["item"]["name"] == "run"
    assert event["item"]["namespace"] == "web"


def test_sse_transformer_handles_fragmented_lines() -> None:
    event = {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "web_run", "arguments": "{}"},
    }
    wire = f"event: response.output_item.done\ndata: {json.dumps(event)}\n\n".encode()
    transformer = SSETransformer()

    output = b"".join(
        [transformer.feed(wire[:11]), transformer.feed(wire[11:37]), transformer.feed(wire[37:])]
    ) + transformer.finish()

    assert b'"name":"run"' in output
    assert b'"namespace":"web"' in output
    assert output.startswith(b"event: response.output_item.done\n")


def test_sse_transformer_preserves_non_json_and_done() -> None:
    transformer = SSETransformer()
    wire = b": keepalive\ndata: [DONE]\n\n"
    assert transformer.feed(wire) + transformer.finish() == wire


def _message(role: str, text: str) -> dict:
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def test_leading_instruction_message_is_unchanged() -> None:
    payload = {"input": [_message("developer", "rules"), _message("user", "task")]}
    original = json.loads(json.dumps(payload))

    assert normalize_instruction_messages(payload) == 0
    assert payload == original


def test_late_instruction_message_moves_to_front() -> None:
    payload = {
        "input": [
            _message("user", "task"),
            {"type": "function_call", "name": "exec_command", "call_id": "1"},
            _message("developer", "permissions changed"),
        ]
    }

    assert normalize_instruction_messages(payload) == 1
    assert payload["input"][0] == _message("developer", "permissions changed")
    assert [item["type"] for item in payload["input"][1:]] == [
        "message",
        "function_call",
    ]


def test_multiple_instruction_messages_merge_in_order() -> None:
    user = _message("user", "task")
    tool = {"type": "function_call_output", "call_id": "1", "output": "ok"}
    payload = {
        "input": [
            _message("developer", "base rules"),
            user,
            tool,
            _message("developer", "updated permissions"),
            _message("system", "override marker"),
        ]
    }

    assert normalize_instruction_messages(payload) == 3
    leading = payload["input"][0]
    assert leading["role"] == "developer"
    assert [part["text"] for part in leading["content"]] == [
        "base rules",
        "\n\n",
        "updated permissions",
        "\n\n",
        "override marker",
    ]
    assert payload["input"][1:] == [user, tool]


def test_instruction_normalization_does_not_mutate_source_messages() -> None:
    first = _message("developer", "first")
    second = _message("developer", "second")
    original_first = json.loads(json.dumps(first))
    original_second = json.loads(json.dumps(second))
    payload = {"input": [first, _message("user", "task"), second]}

    normalize_instruction_messages(payload)

    assert first == original_first
    assert second == original_second
