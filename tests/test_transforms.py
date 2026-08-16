from __future__ import annotations

import json

from codex_altair_provider.transforms import (
    SSETransformer,
    flatten_web_namespace,
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

