from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

import aiohttp
from aiohttp import web

from .config import Settings
from .keyless_search import KeylessSearchClient
from .search import (
    FallbackSearchClient,
    SearchProtocolError,
    TavilyClient,
    TavilyUpstreamError,
    execute_search_request,
)
from .transforms import (
    SSETransformer,
    expose_raw_reasoning_to_codex,
    flatten_web_namespace,
    normalize_instruction_messages,
    normalize_view_image_outputs,
    restore_apply_patch_custom_calls,
    restore_web_namespace_calls,
    translate_apply_patch_request,
)

LOGGER = logging.getLogger("codex_local_provider")

SESSION_KEY: web.AppKey[aiohttp.ClientSession] = web.AppKey("session")
SETTINGS_KEY: web.AppKey[Settings] = web.AppKey("settings")
TAVILY_KEY: web.AppKey[Any] = web.AppKey("tavily")
KEYLESS_KEY: web.AppKey[Any] = web.AppKey("keyless_search")
SEARCH_KEY: web.AppKey[Any] = web.AppKey("search")

HOP_BY_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def create_app(
    settings: Settings | None = None,
    *,
    tavily_client: Any | None = None,
    keyless_client: Any | None = None,
) -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app[SETTINGS_KEY] = settings or Settings.from_environment()
    app[TAVILY_KEY] = tavily_client
    app[KEYLESS_KEY] = keyless_client
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/v1/models", _proxy_models)
    app.router.add_post("/v1/responses", _proxy_responses)
    app.router.add_post("/v1/alpha/search", _standalone_search)
    return app


async def _startup(app: web.Application) -> None:
    settings = app[SETTINGS_KEY]
    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=10,
        sock_read=settings.upstream_idle_timeout_seconds,
    )
    app[SESSION_KEY] = aiohttp.ClientSession(timeout=timeout, auto_decompress=True)
    if app[TAVILY_KEY] is None and settings.tavily_api_key:
        app[TAVILY_KEY] = TavilyClient(
            app[SESSION_KEY],
            base_url=settings.tavily_base_url,
            api_key=settings.tavily_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if app[KEYLESS_KEY] is None:
        app[KEYLESS_KEY] = KeylessSearchClient(
            timeout_seconds=settings.request_timeout_seconds,
        )
    app[SEARCH_KEY] = FallbackSearchClient(app[TAVILY_KEY], app[KEYLESS_KEY])


async def _cleanup(app: web.Application) -> None:
    await app[SESSION_KEY].close()


async def _health(request: web.Request) -> web.Response:
    app = request.app
    settings = app[SETTINGS_KEY]
    upstream_ok = False
    upstream_status: int | None = None
    try:
        async with app[SESSION_KEY].get(
            f"{settings.upstream_url}/v1/models",
            timeout=aiohttp.ClientTimeout(total=3),
        ) as response:
            upstream_status = response.status
            upstream_ok = response.status < 500
            await response.read()
    except (aiohttp.ClientError, TimeoutError):
        pass

    tavily_configured = app[TAVILY_KEY] is not None
    search_available = app[SEARCH_KEY] is not None
    ready = upstream_ok and search_available
    payload = {
        "status": "ok" if ready else "degraded",
        "upstream": upstream_ok,
        "upstream_status": upstream_status,
        "tavily_configured": tavily_configured,
        "search_available": search_available,
        "search_backend": "tavily+duckduckgo+bing" if tavily_configured else "duckduckgo+bing",
    }
    return web.json_response(payload, status=200 if ready else 503)


async def _proxy_models(request: web.Request) -> web.StreamResponse:
    return await _proxy_unmodified(request, "/v1/models")


async def _proxy_responses(request: web.Request) -> web.StreamResponse:
    started = time.monotonic()
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error_response(400, "Responses body must be valid JSON")
    if not isinstance(payload, dict):
        return _error_response(400, "Responses body must be a JSON object")

    payload, replacements = flatten_web_namespace(payload)
    apply_patch_replacements = translate_apply_patch_request(payload)
    view_image_replacements = normalize_view_image_outputs(payload)
    normalized_instructions = normalize_instruction_messages(payload)
    settings = request.app[SETTINGS_KEY]
    upstream_url = f"{settings.upstream_url}/v1/responses"
    headers = _forward_headers(request.headers)

    try:
        upstream = await request.app[SESSION_KEY].post(
            upstream_url,
            json=payload,
            headers=headers,
        )
    except (aiohttp.ClientError, TimeoutError) as exc:
        LOGGER.warning("responses upstream failure type=%s", type(exc).__name__)
        return _error_response(502, f"llama.cpp connection failed: {type(exc).__name__}")

    LOGGER.info(
        "responses status=%s web_tools=%s apply_patch_tools=%s "
        "view_image_outputs=%s instruction_messages=%s elapsed_ms=%d",
        upstream.status,
        replacements,
        apply_patch_replacements,
        view_image_replacements,
        normalized_instructions,
        int((time.monotonic() - started) * 1000),
    )
    content_type = upstream.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        return await _stream_sse(request, upstream)
    return await _buffered_upstream_response(upstream)


async def _standalone_search(request: web.Request) -> web.Response:
    started = time.monotonic()
    search = request.app[SEARCH_KEY]
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error_response(400, "Search body must be valid JSON")
    if not isinstance(payload, dict):
        return _error_response(400, "Search body must be a JSON object")

    try:
        result = await execute_search_request(
            payload,
            search,
            search_depth=request.app[SETTINGS_KEY].tavily_search_depth,
        )
    except SearchProtocolError as exc:
        LOGGER.info("search rejected status=%s reason=%s", exc.status, exc.message)
        return _error_response(exc.status, exc.message)
    except TavilyUpstreamError as exc:
        status = exc.status if exc.status in {401, 403, 429, 504} else 502
        LOGGER.warning("search upstream failure status=%s", exc.status)
        return _error_response(status, exc.message)

    commands = payload.get("commands", {})
    operations = ",".join(
        name
        for name in ("search_query", "open", "find")
        if isinstance(commands, dict) and commands.get(name)
    )
    LOGGER.info(
        "search status=200 operations=%s elapsed_ms=%d",
        operations or "none",
        int((time.monotonic() - started) * 1000),
    )
    return web.json_response(result)


async def _proxy_unmodified(request: web.Request, path: str) -> web.StreamResponse:
    settings = request.app[SETTINGS_KEY]
    try:
        async with request.app[SESSION_KEY].request(
            request.method,
            f"{settings.upstream_url}{path}",
            params=request.query,
            headers=_forward_headers(request.headers),
        ) as upstream:
            body = await upstream.read()
            return web.Response(
                body=body,
                status=upstream.status,
                headers=_response_headers(upstream.headers),
            )
    except (aiohttp.ClientError, TimeoutError) as exc:
        return _error_response(502, f"llama.cpp connection failed: {type(exc).__name__}")


async def _stream_sse(
    request: web.Request, upstream: aiohttp.ClientResponse
) -> web.StreamResponse:
    response = web.StreamResponse(
        status=upstream.status,
        headers=_response_headers(upstream.headers),
    )
    await response.prepare(request)
    transformer = SSETransformer()
    try:
        async for chunk in upstream.content.iter_any():
            transformed = transformer.feed(chunk)
            if transformed:
                await response.write(transformed)
        tail = transformer.finish()
        if tail:
            await response.write(tail)
    finally:
        upstream.release()
    await response.write_eof()
    return response


async def _buffered_upstream_response(
    upstream: aiohttp.ClientResponse,
) -> web.Response:
    body = await upstream.read()
    headers = _response_headers(upstream.headers)
    if "application/json" in upstream.headers.get("Content-Type", ""):
        try:
            payload = json.loads(body)
            restore_web_namespace_calls(payload)
            restore_apply_patch_custom_calls(payload)
            expose_raw_reasoning_to_codex(payload)
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    upstream.release()
    return web.Response(body=body, status=upstream.status, headers=headers)


def _forward_headers(headers: aiohttp.typedefs.LooseHeaders) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in HOP_BY_HOP_HEADERS or lower == "authorization":
            continue
        forwarded[name] = value
    return forwarded


def _response_headers(headers: aiohttp.typedefs.LooseHeaders) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
    }


def _error_response(status: int, message: str) -> web.Response:
    return web.json_response(
        {"error": {"type": "codex_local_provider_error", "message": message}},
        status=status,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    web.run_app(create_app(), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
