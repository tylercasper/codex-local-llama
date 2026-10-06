from __future__ import annotations

import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from codex_local_provider.app import create_app
from codex_local_provider.config import Settings
from codex_local_provider.search import TavilyUpstreamError


class FakeTavily:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, payload: dict) -> dict:
        self.calls += 1
        return {
            "results": [
                {"title": "Result", "url": "https://example.com", "content": "snippet"}
            ]
        }

    async def extract(self, payload: dict) -> dict:
        self.calls += 1
        return {"results": [{"url": payload["urls"][0], "raw_content": "content"}]}


def _namespace() -> dict:
    return {
        "type": "namespace",
        "name": "web",
        "tools": [{"type": "function", "name": "run", "parameters": {}}],
    }


def _apply_patch_tool() -> dict:
    return {
        "type": "custom",
        "name": "apply_patch",
        "description": "Apply a patch.",
        "format": {"type": "text"},
    }


async def test_responses_proxy_flattens_request_and_restores_streamed_call() -> None:
    captured: list[dict] = []

    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "qwen3.8-27b"}]})

    async def responses(request: web.Request) -> web.StreamResponse:
        captured.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        event = {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "web_run",
                "call_id": "call_1",
                "arguments": "{}",
            },
        }
        wire = f"data: {json.dumps(event)}\n\n".encode()
        await response.write(wire[:17])
        await response.write(wire[17:])
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    upstream_app.router.add_post("/v1/responses", responses)

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=FakeTavily(),
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.post(
                "/v1/responses",
                json={
                    "tools": [_namespace(), _apply_patch_tool()],
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "continue"}],
                        },
                        {
                            "type": "message",
                            "role": "developer",
                            "content": [
                                {"type": "input_text", "text": "permissions changed"}
                            ],
                        },
                    ],
                },
            )
            body = await response.text()

    assert response.status == 200
    assert captured[0]["tools"][0]["name"] == "web_run"
    assert captured[0]["tools"][1]["type"] == "function"
    assert captured[0]["tools"][1]["name"] == "apply_patch"
    assert captured[0]["tools"][1]["parameters"]["required"] == ["patch"]
    assert captured[0]["input"][0]["role"] == "developer"
    assert captured[0]["input"][1]["role"] == "user"
    assert '"name":"run"' in body
    assert '"namespace":"web"' in body


async def test_responses_proxy_restores_streamed_apply_patch_call() -> None:
    captured: list[dict] = []
    patch = "*** Begin Patch\n*** Add File: ok.txt\n+ok\n*** End Patch\n"

    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "qwen3.8-27b"}]})

    async def responses(request: web.Request) -> web.StreamResponse:
        captured.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        event = {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "name": "apply_patch",
                "call_id": "call_patch",
                "arguments": json.dumps({"patch": patch}),
            },
        }
        await response.write(f"data: {json.dumps(event)}\n\n".encode())
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    upstream_app.router.add_post("/v1/responses", responses)

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=FakeTavily(),
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.post(
                "/v1/responses",
                json={"tools": [_apply_patch_tool()], "input": "create a file"},
            )
            body = await response.text()

    assert response.status == 200
    assert captured[0]["tools"][0]["type"] == "function"
    assert '"type":"custom_tool_call"' in body
    assert json.dumps(patch)[1:-1] in body


async def test_responses_proxy_normalizes_view_image_output() -> None:
    captured: list[dict] = []

    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "qwen3.8-27b"}]})

    async def responses(request: web.Request) -> web.Response:
        captured.append(await request.json())
        return web.json_response({"id": "response_1", "output": []})

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    upstream_app.router.add_post("/v1/responses", responses)

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=FakeTavily(),
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.post(
                "/v1/responses",
                json={
                    "input": [
                        {
                            "type": "function_call",
                            "name": "view_image",
                            "call_id": "call_image",
                            "arguments": '{"path":"/tmp/screenshot.png"}',
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_image",
                            "output": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/png;base64,cGl4ZWxz",
                                    "detail": "high",
                                }
                            ],
                        },
                    ]
                },
            )

    assert response.status == 200
    output, message = captured[0]["input"][1:]
    assert output["output"] == "Image loaded successfully."
    assert message["role"] == "user"
    assert message["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,cGl4ZWxz",
        "detail": "high",
    }


async def test_responses_proxy_exposes_streamed_raw_reasoning() -> None:
    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "qwen3.8-27b"}]})

    async def responses(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        event = {
            "type": "response.reasoning_text.delta",
            "item_id": "rs_1",
            "delta": "use the plan",
        }
        await response.write(
            b"event: response.reasoning_text.delta\n"
            + f"data: {json.dumps(event)}\n\n".encode()
        )
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    upstream_app.router.add_post("/v1/responses", responses)

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=FakeTavily(),
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.post(
                "/v1/responses",
                json={"model": "qwen3.8-27b", "input": "make a plan"},
            )
            body = await response.text()

    assert response.status == 200
    assert "event: response.reasoning_summary_text.delta" in body
    assert '"type":"response.reasoning_summary_text.delta"' in body
    assert '"delta":"use the plan"' in body


async def test_health_does_not_call_tavily() -> None:
    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": []})

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    tavily = FakeTavily()
    fallback = FakeTavily()

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=tavily,
            keyless_client=fallback,
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.get("/healthz")
            payload = await response.json()

    assert response.status == 200
    assert payload["upstream"] is True
    assert payload["tavily_configured"] is True
    assert tavily.calls == 0
    assert fallback.calls == 0
    assert payload["search_available"] is True
    assert payload["search_backend"] == "tavily+duckduckgo+bing"


async def test_health_is_ready_without_tavily_credential() -> None:
    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": []})

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/"))
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.get("/healthz")
            payload = await response.json()

    assert response.status == 200
    assert payload == {
        "status": "ok",
        "upstream": True,
        "upstream_status": 200,
        "tavily_configured": False,
        "search_available": True,
        "search_backend": "duckduckgo+bing",
    }


async def test_native_search_contract() -> None:
    async def models(_: web.Request) -> web.Response:
        return web.json_response({"data": []})

    upstream_app = web.Application()
    upstream_app.router.add_get("/v1/models", models)
    tavily = FakeTavily()

    async with TestServer(upstream_app) as upstream:
        adapter = create_app(
            Settings(upstream_url=str(upstream.make_url("/")).rstrip("/")),
            tavily_client=tavily,
        )
        async with TestClient(TestServer(adapter)) as client:
            response = await client.post(
                "/v1/alpha/search",
                json={
                    "commands": {"search_query": [{"q": "example"}]},
                    "max_output_tokens": 2500,
                },
            )
            payload = await response.json()

    assert response.status == 200
    assert set(payload) == {"encrypted_output", "output", "results"}
    assert "https://example.com" in payload["output"]


async def test_search_works_without_key() -> None:
    fallback = FakeTavily()
    adapter = create_app(Settings(), keyless_client=fallback)
    async with TestClient(TestServer(adapter)) as client:
        response = await client.post('/v1/alpha/search', json={
            'commands': {'search_query': [{'q': 'Ubuntu'}]}
        })
        payload = await response.json()
    assert response.status == 200
    assert 'https://example.com' in payload['output']
    assert fallback.calls == 1


async def test_failed_key_falls_back_at_search_endpoint() -> None:
    class RejectedKey(FakeTavily):
        async def search(self, payload):
            self.calls += 1
            raise TavilyUpstreamError(401, 'Tavily authentication failed')

    primary = RejectedKey()
    fallback = FakeTavily()
    adapter = create_app(Settings(), tavily_client=primary, keyless_client=fallback)
    async with TestClient(TestServer(adapter)) as client:
        response = await client.post('/v1/alpha/search', json={
            'commands': {'search_query': [{'q': 'Ubuntu'}]}
        })
        payload = await response.json()
    assert response.status == 200
    assert primary.calls == fallback.calls == 1
    assert 'https://example.com' in payload['output']


async def test_keyless_failure_is_not_reported_as_empty_success() -> None:
    class Unavailable(FakeTavily):
        async def search(self, payload):
            raise TavilyUpstreamError(502, 'No-key search unavailable')

    adapter = create_app(Settings(), keyless_client=Unavailable())
    async with TestClient(TestServer(adapter)) as client:
        response = await client.post('/v1/alpha/search', json={
            'commands': {'search_query': [{'q': 'Ubuntu'}]}
        })
    assert response.status == 502
