from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

import aiohttp


class SearchProtocolError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class TavilyUpstreamError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class TavilyProtocol(Protocol):
    async def search(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def extract(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class TavilyClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/search", payload)

    async def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/extract", payload)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                parsed = await _read_json(response)
                if response.status >= 400:
                    raise TavilyUpstreamError(
                        response.status, _sanitized_tavily_error(parsed, response.status)
                    )
                if not isinstance(parsed, dict):
                    raise TavilyUpstreamError(502, "Tavily returned a non-object response")
                return parsed
        except TimeoutError as exc:
            raise TavilyUpstreamError(504, "Tavily request timed out") from exc
        except aiohttp.ClientError as exc:
            raise TavilyUpstreamError(502, f"Tavily connection failed: {type(exc).__name__}") from exc


async def execute_search_request(
    request: Mapping[str, Any],
    tavily: TavilyProtocol,
    *,
    search_depth: str = "basic",
) -> dict[str, Any]:
    commands = request.get("commands")
    if not isinstance(commands, Mapping):
        raise SearchProtocolError(400, "Search request must contain a commands object")

    if _nonempty_list(commands.get("click")):
        raise SearchProtocolError(
            400, "click is not supported; call open with an absolute URL instead"
        )
    if _nonempty_list(commands.get("screenshot")):
        raise SearchProtocolError(400, "screenshot is not supported by this text-only MVP")

    response_length = commands.get("response_length", "short")
    max_results = {"short": 5, "medium": 8, "long": 10}.get(response_length, 5)

    tasks: list[asyncio.Task[str]] = []
    for item in _operation_list(commands, "search_query"):
        tasks.append(
            asyncio.create_task(
                _run_search(item, tavily, search_depth=search_depth, max_results=max_results)
            )
        )
    for item in _operation_list(commands, "open"):
        tasks.append(asyncio.create_task(_run_open(item, tavily)))
    for item in _operation_list(commands, "find"):
        tasks.append(asyncio.create_task(_run_find(item, tavily)))

    if not tasks:
        raise SearchProtocolError(400, "No supported search, open, or find command was provided")

    sections = await asyncio.gather(*tasks)
    output = "\n\n".join(section for section in sections if section.strip())
    max_output_tokens = request.get("max_output_tokens", 2500)
    output = _truncate_output(output, max_output_tokens)
    return {"encrypted_output": None, "output": output, "results": []}


async def _run_search(
    item: Mapping[str, Any],
    tavily: TavilyProtocol,
    *,
    search_depth: str,
    max_results: int,
) -> str:
    query = _required_string(item, "q", "search_query")
    payload: dict[str, Any] = {
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }

    domains = item.get("domains", item.get("allowed_domains"))
    if isinstance(domains, list):
        included = [str(domain).strip() for domain in domains if str(domain).strip()]
        if included:
            payload["include_domains"] = included

    recency = item.get("recency")
    if isinstance(recency, int) and recency > 0:
        start = datetime.now(UTC).date() - timedelta(days=recency)
        payload["start_date"] = start.isoformat()

    response = await tavily.search(payload)
    results = response.get("results", [])
    if not isinstance(results, list):
        raise TavilyUpstreamError(502, "Tavily search response omitted its results list")

    lines = [f"## Search: {query}"]
    if not results:
        lines.append("No results.")
        return "\n".join(lines)

    for index, result in enumerate(results, start=1):
        if not isinstance(result, Mapping):
            continue
        title = str(result.get("title") or "Untitled result").strip()
        url = str(result.get("url") or "").strip()
        snippet = _clean_text(str(result.get("content") or ""))
        published = str(result.get("published_date") or "").strip()
        lines.extend([f"{index}. {title}", f"   URL: {url}"])
        if published:
            lines.append(f"   Published: {published}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")
    return "\n".join(lines)


async def _run_open(item: Mapping[str, Any], tavily: TavilyProtocol) -> str:
    url = _required_absolute_url(item, "open")
    content = await _extract_url(url, tavily)
    lineno = item.get("lineno")
    if isinstance(lineno, int) and lineno > 0:
        content = _line_window(content, lineno)
    return f"## Open: {url}\nURL: {url}\n\n{content or 'No extractable content.'}"


async def _run_find(item: Mapping[str, Any], tavily: TavilyProtocol) -> str:
    url = _required_absolute_url(item, "find")
    pattern = _required_string(item, "pattern", "find")
    content = await _extract_url(url, tavily)
    excerpts = _find_excerpts(content, pattern)
    return f"## Find: {pattern}\nURL: {url}\n\n{excerpts}"


async def _extract_url(url: str, tavily: TavilyProtocol) -> str:
    response = await tavily.extract(
        {
            "urls": [url],
            "extract_depth": "basic",
            "format": "markdown",
            "include_images": False,
        }
    )
    results = response.get("results", [])
    if not isinstance(results, list) or not results:
        failed = response.get("failed_results", [])
        detail = ""
        if isinstance(failed, list) and failed:
            detail = f" ({_clean_text(str(failed[0]))})"
        raise TavilyUpstreamError(502, f"Tavily could not extract the URL{detail}")
    first = results[0]
    if not isinstance(first, Mapping):
        raise TavilyUpstreamError(502, "Tavily returned an invalid extraction result")
    return str(first.get("raw_content") or "")


def _operation_list(commands: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    value = commands.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise SearchProtocolError(400, f"commands.{name} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise SearchProtocolError(400, f"commands.{name} entries must be objects")
    return value


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _required_string(item: Mapping[str, Any], key: str, operation: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SearchProtocolError(400, f"{operation}.{key} must be a non-empty string")
    return value.strip()


def _required_absolute_url(item: Mapping[str, Any], operation: str) -> str:
    url = _required_string(item, "ref_id", operation)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SearchProtocolError(
            400,
            f"{operation}.ref_id must be an absolute http(s) URL; opaque references are unsupported",
        )
    return url


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _line_window(content: str, lineno: int) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    start = max(0, lineno - 6)
    end = min(len(lines), lineno + 20)
    return "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))


def _find_excerpts(content: str, pattern: str) -> str:
    if not content:
        return "No extractable content."
    matches = list(re.finditer(re.escape(pattern), content, flags=re.IGNORECASE))
    if not matches:
        return "Pattern not found."
    excerpts: list[str] = []
    for match in matches[:8]:
        start = max(0, match.start() - 240)
        end = min(len(content), match.end() + 240)
        excerpt = _clean_text(content[start:end])
        excerpts.append(f"- …{excerpt}…")
    return "\n".join(excerpts)


def _truncate_output(output: str, max_output_tokens: Any) -> str:
    try:
        tokens = int(max_output_tokens)
    except (TypeError, ValueError):
        tokens = 2500
    char_limit = max(1_000, min(tokens * 4, 40_000))
    if len(output) <= char_limit:
        return output
    return output[: char_limit - 32].rstrip() + "\n\n[output truncated]"


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        text = await response.text()
        return {"error": _clean_text(text)[:500]}


def _sanitized_tavily_error(payload: Any, status: int) -> str:
    if isinstance(payload, Mapping):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return f"Tavily returned HTTP {status}: {_clean_text(value)[:500]}"
    return f"Tavily returned HTTP {status}"

