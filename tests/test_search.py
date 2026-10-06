from __future__ import annotations

import pytest

from codex_local_provider.search import SearchProtocolError, execute_search_request


class FakeTavily:
    def __init__(self) -> None:
        self.search_payloads: list[dict] = []
        self.extract_payloads: list[dict] = []

    async def search(self, payload: dict) -> dict:
        self.search_payloads.append(payload)
        return {
            "results": [
                {
                    "title": "Example result",
                    "url": "https://example.com/page",
                    "content": "An example search snippet.",
                    "published_date": "2026-08-15",
                }
            ]
        }

    async def extract(self, payload: dict) -> dict:
        self.extract_payloads.append(payload)
        return {
            "results": [
                {
                    "url": payload["urls"][0],
                    "raw_content": "alpha\nThe target phrase is here.\nomega",
                }
            ]
        }


async def test_search_maps_domains_recency_and_response_length() -> None:
    tavily = FakeTavily()
    result = await execute_search_request(
        {
            "commands": {
                "search_query": [
                    {"q": "current example", "domains": ["example.com"], "recency": 7}
                ],
                "response_length": "medium",
            },
            "max_output_tokens": 2500,
        },
        tavily,
    )

    assert result["encrypted_output"] is None
    assert result["results"] == []
    assert "https://example.com/page" in result["output"]
    payload = tavily.search_payloads[0]
    assert payload["search_depth"] == "basic"
    assert payload["max_results"] == 8
    assert payload["include_domains"] == ["example.com"]
    assert "start_date" in payload


async def test_open_and_find_use_absolute_url_extraction() -> None:
    tavily = FakeTavily()
    result = await execute_search_request(
        {
            "commands": {
                "open": [{"ref_id": "https://example.com/page", "lineno": 2}],
                "find": [
                    {"ref_id": "https://example.com/page", "pattern": "TARGET phrase"}
                ],
            }
        },
        tavily,
    )

    assert "2: The target phrase is here." in result["output"]
    assert "target phrase" in result["output"].lower()
    assert len(tavily.extract_payloads) == 2
    assert all(payload["extract_depth"] == "basic" for payload in tavily.extract_payloads)


@pytest.mark.parametrize("operation", ["open", "find"])
async def test_opaque_references_are_rejected(operation: str) -> None:
    tavily = FakeTavily()
    item = {"ref_id": "turn0search0"}
    if operation == "find":
        item["pattern"] = "text"

    with pytest.raises(SearchProtocolError, match="absolute http"):
        await execute_search_request({"commands": {operation: [item]}}, tavily)


@pytest.mark.parametrize("operation", ["click", "screenshot"])
async def test_stateful_browser_operations_are_rejected(operation: str) -> None:
    tavily = FakeTavily()
    with pytest.raises(SearchProtocolError, match=operation):
        await execute_search_request(
            {"commands": {operation: [{"ref_id": "https://example.com"}]}}, tavily
        )



class StubSearch:
    def __init__(self, *, search=None, extract=None):
        self.search_result = search
        self.extract_result = extract
        self.search_calls = []
        self.extract_calls = []

    async def search(self, payload):
        self.search_calls.append(payload)
        if isinstance(self.search_result, BaseException):
            raise self.search_result
        return self.search_result

    async def extract(self, payload):
        self.extract_calls.append(payload)
        if isinstance(self.extract_result, BaseException):
            raise self.extract_result
        return self.extract_result


async def test_keyless_fallback_when_no_primary():
    from codex_local_provider.search import FallbackSearchClient
    fallback = FakeTavily()
    client = FallbackSearchClient(None, fallback)
    result = await execute_search_request({'commands': {
        'search_query': [{'q': 'example', 'domains': ['example.com'], 'recency': 3}],
        'open': [{'ref_id': 'https://example.com/page'}],
    }}, client)
    assert 'An example search snippet' in result['output']
    assert fallback.search_payloads[0]['include_domains'] == ['example.com']
    assert 'start_date' in fallback.search_payloads[0]
    assert fallback.extract_payloads[0]['urls'] == ['https://example.com/page']


@pytest.mark.parametrize('status', [401, 403, 429, 500, 502, 504])
async def test_primary_http_failure_falls_back_without_exposing_error(status):
    from codex_local_provider.search import FallbackSearchClient, TavilyUpstreamError
    primary = StubSearch(search=TavilyUpstreamError(status, 'secret-key-should-not-leak'))
    fallback = FakeTavily()
    result = await FallbackSearchClient(primary, fallback).search({'query': 'example'})
    assert result['results'][0]['url'] == 'https://example.com/page'
    assert len(primary.search_calls) == len(fallback.search_payloads) == 1
    assert 'secret-key' not in str(result)


@pytest.mark.parametrize('response', [None, [], {}, {'results': None}, {'results': 'oops'},
                                      {'results': [None]}, {'results': [{'url': 5}]},
                                      {'results': [{'url': 'opaque'}]},
                                      {'results': [{'url': 'https://example.com', 'content': {}}]}])
async def test_malformed_primary_search_response_falls_back(response):
    from codex_local_provider.search import FallbackSearchClient
    fallback = FakeTavily()
    result = await FallbackSearchClient(StubSearch(search=response), fallback).search({'query': 'x'})
    assert result['results'][0]['title'] == 'Example result'
    assert len(fallback.search_payloads) == 1


async def test_valid_empty_primary_search_does_not_fall_back():
    from codex_local_provider.search import FallbackSearchClient
    fallback = FakeTavily()
    assert await FallbackSearchClient(StubSearch(search={'results': []}), fallback).search({}) == {'results': []}
    assert not fallback.search_payloads


async def test_timeout_and_connection_failures_fall_back():
    import aiohttp
    from codex_local_provider.search import FallbackSearchClient
    for error in (TimeoutError(), aiohttp.ClientConnectionError('unreachable')):
        fallback = FakeTavily()
        client = FallbackSearchClient(StubSearch(search=error, extract=error), fallback)
        assert (await client.search({}))['results']
        assert (await client.extract({'urls': ['https://example.com/page']}))['results']


async def test_partial_extraction_only_retries_failed_urls_and_preserves_order():
    from codex_local_provider.search import FallbackSearchClient
    first, second = 'https://example.com/a', 'https://example.com/b'
    primary = StubSearch(extract={'results': [{'url': second, 'raw_content': 'from primary'}],
                                  'failed_results': [{'url': first, 'error': 'secret-key'}]})
    fallback = StubSearch(extract={'results': [{'url': first, 'raw_content': 'from fallback'}]})
    result = await FallbackSearchClient(primary, fallback).extract({'urls': [first, second], 'format': 'markdown'})
    assert fallback.extract_calls == [{'urls': [first], 'format': 'markdown'}]
    assert [item['raw_content'] for item in result['results']] == ['from fallback', 'from primary']
    assert result['failed_results'] == []


@pytest.mark.parametrize('response', [{}, {'results': []}, {'results': [None]},
                                      {'results': [{'url': 'https://example.com/page', 'raw_content': ''}]},
                                      {'results': [], 'failed_results': [{'error': 'secret-key'}]}])
async def test_failed_or_empty_extraction_uses_fallback(response):
    from codex_local_provider.search import FallbackSearchClient
    fallback = FakeTavily()
    result = await FallbackSearchClient(StubSearch(extract=response), fallback).extract({'urls': ['https://example.com/page']})
    assert result['results'][0]['raw_content'].startswith('alpha')
    assert len(fallback.extract_payloads) == 1


async def test_partial_extraction_retained_if_fallback_also_fails():
    from codex_local_provider.search import FallbackSearchClient, TavilyUpstreamError
    first, second = 'https://example.com/a', 'https://example.com/b'
    primary = StubSearch(extract={'results': [{'url': first, 'raw_content': 'usable'}]})
    fallback = StubSearch(extract=TavilyUpstreamError(502, 'secret-key'))
    result = await FallbackSearchClient(primary, fallback).extract({'urls': [first, second]})
    assert len(result['results']) == 1
    assert result['failed_results'] == [{'url': second, 'error': 'No extractable content'}]


async def test_all_providers_failed_error_is_sanitized():
    from codex_local_provider.search import FallbackSearchClient, TavilyUpstreamError
    backend = StubSearch(search=TavilyUpstreamError(401, 'secret-key'), extract=TavilyUpstreamError(401, 'secret-key'))
    client = FallbackSearchClient(backend, backend)
    for operation, payload in ((client.search, {}), (client.extract, {'urls': ['https://example.com']})):
        with pytest.raises(TavilyUpstreamError) as error:
            await operation(payload)
        assert 'secret-key' not in str(error.value)
        assert error.value.status == 502


async def test_programmer_errors_and_cancellation_are_not_swallowed():
    import asyncio
    from codex_local_provider.search import FallbackSearchClient
    for error in (RuntimeError('bug'), ValueError('bug'), asyncio.CancelledError()):
        fallback = FakeTavily()
        client = FallbackSearchClient(StubSearch(search=error, extract=error), fallback)
        with pytest.raises(type(error)):
            await client.search({})
        with pytest.raises(type(error)):
            await client.extract({'urls': ['https://example.com']})
        assert not fallback.search_payloads and not fallback.extract_payloads


def test_tavily_http_error_does_not_echo_upstream_key():
    from codex_local_provider.search import _sanitized_tavily_error
    for key in ('error', 'detail', 'message'):
        assert _sanitized_tavily_error({key: 'Invalid key secret-key'}, 401) == 'Tavily returned HTTP 401'


async def test_successful_primary_skips_fallback_for_search_and_extract():
    from codex_local_provider.search import FallbackSearchClient
    primary, fallback = FakeTavily(), FakeTavily()
    client = FallbackSearchClient(primary, fallback)
    assert (await client.search({'query': 'example'}))['results']
    assert (await client.extract({'urls': ['https://example.com/page']}))['results']
    assert len(primary.search_payloads) == len(primary.extract_payloads) == 1
    assert not fallback.search_payloads and not fallback.extract_payloads


@pytest.mark.parametrize('key', ['', '   ', 'secret\r\ninvalid', 'secret\x00invalid'])
async def test_invalid_tavily_key_uses_fallback_before_http_request(key):
    from codex_local_provider.search import FallbackSearchClient, TavilyClient
    class NoHttp:
        def post(self, *args, **kwargs):
            raise AssertionError('Invalid credentials must not reach HTTP headers')
    primary = TavilyClient(NoHttp(), base_url='https://api.tavily.com', api_key=key, timeout_seconds=5)
    fallback = FakeTavily()
    client = FallbackSearchClient(primary, fallback)
    assert (await client.search({'query': 'example'}))['results']
    assert (await client.extract({'urls': ['https://example.com/page']}))['results']
    assert len(fallback.search_payloads) == len(fallback.extract_payloads) == 1


async def test_search_reports_fallback_date_limitations():
    class DateLimitedSearch(FakeTavily):
        async def search(self, payload):
            response = await super().search(payload)
            response['notice'] = 'Alternate search does not guarantee the requested publication dates; verify dates.'
            return response
    result = await execute_search_request(
        {'commands': {'search_query': [{'q': 'Ubuntu', 'recency': 1}]}},
        DateLimitedSearch(),
    )
    assert 'does not guarantee the requested publication dates' in result['output']
