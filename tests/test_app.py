from __future__ import annotations

import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from codex_altair_provider.app import create_app
from codex_altair_provider.config import Settings


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
                    "tools": [_namespace()],
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
    assert captured[0]["input"][0]["role"] == "developer"
    assert captured[0]["input"][1]["role"] == "user"
    assert '"name":"run"' in body
    assert '"namespace":"web"' in body


async def test_health_does_not_call_tavily() -> None:
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
            response = await client.get("/healthz")
            payload = await response.json()

    assert response.status == 200
    assert payload["upstream"] is True
    assert payload["tavily_configured"] is True
    assert tavily.calls == 0


async def test_health_is_degraded_without_mandatory_tavily_credential() -> None:
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

    assert response.status == 503
    assert payload == {
        "status": "degraded",
        "upstream": True,
        "upstream_status": 200,
        "tavily_configured": False,
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
