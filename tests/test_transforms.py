from __future__ import annotations

import json

from codex_local_provider.transforms import (
    SSETransformer,
    expose_raw_reasoning_to_codex,
    flatten_web_namespace,
    normalize_instruction_messages,
    normalize_view_image_outputs,
    restore_apply_patch_custom_calls,
    restore_web_namespace_calls,
    translate_apply_patch_request,
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


def test_translates_apply_patch_tool_and_history_for_llama_cpp() -> None:
    payload = {
        "tools": [
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch.",
                "format": {"type": "text"},
            }
        ],
        "input": [
            {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": "call_patch",
                "input": "*** Begin Patch\n*** End Patch\n",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "call_patch",
                "output": "Done!",
            },
        ],
    }

    assert translate_apply_patch_request(payload) == 1
    tool = payload["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "apply_patch"
    assert tool["parameters"]["required"] == ["patch"]
    assert tool["strict"] is False
    call, output = payload["input"]
    assert call["type"] == "function_call"
    assert json.loads(call["arguments"]) == {
        "patch": "*** Begin Patch\n*** End Patch\n"
    }
    assert "input" not in call
    assert output["type"] == "function_call_output"


def test_apply_patch_translation_leaves_other_custom_tools_unchanged() -> None:
    payload = {
        "tools": [{"type": "custom", "name": "other"}],
        "input": [
            {
                "type": "custom_tool_call",
                "name": "other",
                "call_id": "call_other",
                "input": "text",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "call_other",
                "output": "ok",
            },
        ],
    }
    original = json.loads(json.dumps(payload))

    assert translate_apply_patch_request(payload) == 0
    assert payload == original


def test_moves_view_image_output_to_user_message_for_llama_cpp() -> None:
    image = {
        "type": "input_image",
        "image_url": "data:image/png;base64,cGl4ZWxz",
        "detail": "high",
    }
    payload = {
        "input": [
            {
                "type": "function_call",
                "name": "view_image",
                "call_id": "call_image",
                "arguments": json.dumps({"path": "/tmp/screenshot.png"}),
            },
            {
                "type": "function_call_output",
                "call_id": "call_image",
                "output": [image],
            },
        ]
    }

    assert normalize_view_image_outputs(payload) == 1
    call, output, message = payload["input"]
    assert call["name"] == "view_image"
    assert output == {
        "type": "function_call_output",
        "call_id": "call_image",
        "output": "Image loaded successfully.",
    }
    assert message == {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "Image returned by view_image for /tmp/screenshot.png.",
            },
            image,
        ],
    }


def test_view_image_normalization_ignores_other_image_outputs() -> None:
    payload = {
        "input": [
            {
                "type": "function_call",
                "name": "other_tool",
                "call_id": "call_other",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_other",
                "output": [
                    {"type": "input_image", "image_url": "data:image/png;base64,eA=="}
                ],
            },
        ]
    }
    original = json.loads(json.dumps(payload))

    assert normalize_view_image_outputs(payload) == 0
    assert payload == original


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


def test_restores_apply_patch_function_call_as_custom_call() -> None:
    patch = "*** Begin Patch\n*** Add File: ok.txt\n+ok\n*** End Patch\n"
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": "apply_patch",
            "call_id": "call_patch",
            "arguments": json.dumps({"patch": patch}),
        },
    }

    restore_apply_patch_custom_calls(event)

    assert event["item"] == {
        "type": "custom_tool_call",
        "name": "apply_patch",
        "call_id": "call_patch",
        "input": patch,
    }


def test_restores_raw_apply_patch_arguments_without_data_loss() -> None:
    call = {
        "type": "function_call",
        "name": "apply_patch",
        "arguments": "*** Begin Patch\n*** End Patch\n",
    }
    restore_apply_patch_custom_calls(call)
    assert call["type"] == "custom_tool_call"
    assert call["input"] == "*** Begin Patch\n*** End Patch\n"


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


def test_sse_transformer_restores_apply_patch_custom_call() -> None:
    patch = "*** Begin Patch\n*** Add File: ok.txt\n+ok\n*** End Patch\n"
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": "apply_patch",
            "call_id": "call_patch",
            "arguments": json.dumps({"patch": patch}),
        },
    }
    transformer = SSETransformer()

    output = transformer.feed(f"data: {json.dumps(event)}\n\n".encode())

    data = json.loads(output.splitlines()[0].removeprefix(b"data: "))
    assert data["item"]["type"] == "custom_tool_call"
    assert data["item"]["input"] == patch


def test_sse_transformer_preserves_non_json_and_done() -> None:
    transformer = SSETransformer()
    wire = b": keepalive\ndata: [DONE]\n\n"
    assert transformer.feed(wire) + transformer.finish() == wire


def test_exposes_completed_raw_reasoning_without_losing_history_content() -> None:
    response = {
        "type": "response.completed",
        "response": {
            "output": [
                {
                    "id": "rs_1",
                    "type": "reasoning",
                    "summary": [],
                    "content": [
                        {"type": "reasoning_text", "text": "inspect, then patch"}
                    ],
                }
            ]
        },
    }

    expose_raw_reasoning_to_codex(response)

    item = response["response"]["output"][0]
    assert item["content"] == [
        {"type": "reasoning_text", "text": "inspect, then patch"}
    ]
    assert item["summary"] == [
        {"type": "summary_text", "text": "inspect, then patch"}
    ]


def test_sse_transformer_relabels_raw_reasoning_as_visible_reasoning() -> None:
    event = {
        "type": "response.reasoning_text.delta",
        "item_id": "rs_1",
        "content_index": 0,
        "delta": "inspect",
    }
    wire = (
        "event: response.reasoning_text.delta\n"
        f"data: {json.dumps(event)}\n\n"
    ).encode()
    transformer = SSETransformer()

    output = transformer.feed(wire) + transformer.finish()

    lines = output.splitlines()
    assert lines[0] == b"event: response.reasoning_summary_text.delta"
    data = json.loads(lines[1].removeprefix(b"data: "))
    assert data == {
        "type": "response.reasoning_summary_text.delta",
        "item_id": "rs_1",
        "delta": "inspect",
        "summary_index": 0,
        "output_index": 0,
    }


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
