import pytest

from codex_local_provider import keyless_search as backend
from codex_local_provider.search import TavilyUpstreamError


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://169.254.169.254/', 'http://10.2.3.4/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://localhost/', 'http://a.local/', 'file:///etc/passwd', 'http://user:secret@example.com/', 'http://example.com:22/'])
def test_reject_non_public_urls(url):
    with pytest.raises(TavilyUpstreamError):
        backend._validate_url(url)


@pytest.mark.asyncio
async def test_resolver_rejects_private_and_mixed_dns(monkeypatch):
    class Resolver:
        async def resolve(self, *args):
            return [{'host': '93.184.216.34'}, {'host': '127.0.0.1'}]
        async def close(self):
            pass
    monkeypatch.setattr(backend.aiohttp.resolver, 'DefaultResolver', Resolver)
    resolver = backend.PublicResolver()
    with pytest.raises(OSError, match='not public'):
        await resolver.resolve('looks-public.example')


@pytest.mark.asyncio
async def test_search_parsing_domains_dates_and_redirect_links(monkeypatch):
    seen = []
    async def fetch(url):
        seen.append(url)
        return '''<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.ubuntu.com%2Fguide">Ubuntu <b>guide</b></a>
<td class="result-snippet">Useful <b>snippet</b>.</td>
<a class="result-link" href="https://evilubuntu.com/">Exclude</a>
<td class="result-snippet">Wrong domain</td>''', 'text/html'
    client = backend.KeylessSearchClient()
    monkeypatch.setattr(client, '_fetch', fetch)
    result = await client.search({'query': 'Ubuntu', 'include_domains': ['ubuntu.com'], 'start_date': '2026-01-01'})
    assert result['results'] == [{'title': 'Ubuntu guide', 'url': 'https://docs.ubuntu.com/guide', 'content': 'Useful snippet.'}]
    assert 'site%3Aubuntu.com' in seen[0]
    assert 'df=2026-01-01..' in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('html', ['<form action="anomaly.js">challenge</form>', '<html>Unexpected page</html>'])
async def test_challenges_and_changed_markup_are_not_empty_results(monkeypatch, html):
    client = backend.KeylessSearchClient()
    async def fetch(url):
        return html, 'text/html'
    monkeypatch.setattr(client, '_fetch', fetch)
    with pytest.raises(TavilyUpstreamError):
        await client.search({'query': 'test'})


@pytest.mark.asyncio
async def test_html_extraction_discards_scripts_preserves_lines(monkeypatch):
    client = backend.KeylessSearchClient()
    async def fetch(url):
        return '<h1>Title</h1><script>SECRET JAVASCRIPT</script><p>First <b>paragraph</b>.</p><p>Second &amp; last.</p>', 'text/html'
    monkeypatch.setattr(client, '_fetch', fetch)
    result = await client.extract({'urls': ['https://example.com/']})
    assert result['results'][0]['raw_content'] == 'Title\nFirst paragraph.\nSecond & last.'


class Response:
    def __init__(self, status=200, headers=None, body=b'hello', media='text/plain'):
        self.status = status
        self.headers = headers or {}
        self.content_type = media
        self.charset = 'utf-8'
        self.content = self
        self.body = body
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def iter_chunked(self, count):
        yield self.body


def mock_sessions(monkeypatch, responses):
    calls = []
    class Resolver:
        async def close(self):
            pass
    class Session:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['auto_decompress'] is False
            assert 'Authorization' not in kwargs['headers']
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            calls.append(url)
            return responses.pop(0)
    monkeypatch.setattr(backend, 'PublicResolver', Resolver)
    monkeypatch.setattr(backend.aiohttp, 'TCPConnector', lambda **kwargs: None)
    monkeypatch.setattr(backend.aiohttp, 'ClientSession', Session)
    return calls


@pytest.mark.asyncio
async def test_public_redirect_to_private_is_blocked_before_request(monkeypatch):
    calls = mock_sessions(monkeypatch, [Response(302, {'Location': 'http://127.0.0.1/secrets'})])
    with pytest.raises(TavilyUpstreamError, match='Only public'):
        await backend.KeylessSearchClient()._fetch('https://example.com/')
    assert calls == ['https://example.com/']


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [Response(body=b'x' * (backend.MAX_BYTES + 1)), Response(headers={'Content-Encoding': 'gzip'}), Response(media='application/pdf'), Response(status=202)])
async def test_response_bounds_and_unsupported_types(monkeypatch, response):
    mock_sessions(monkeypatch, [response])
    with pytest.raises(TavilyUpstreamError):
        await backend.KeylessSearchClient()._fetch('https://example.com/')


@pytest.mark.asyncio
async def test_extraction_reports_individual_failures(monkeypatch):
    client = backend.KeylessSearchClient()
    async def fetch(url):
        if url.endswith('/bad'):
            raise TavilyUpstreamError(502, 'Page unavailable')
        return 'Good page', 'text/plain'
    monkeypatch.setattr(client, '_fetch', fetch)
    result = await client.extract({'urls': ['https://example.com/good', 'https://example.com/bad']})
    assert len(result['results']) == 1
    assert result['failed_results'] == [{'url': 'https://example.com/bad', 'error': 'Page unavailable'}]


@pytest.mark.asyncio
async def test_challenge_uses_secondary_feed_with_domains_and_recency_notice(monkeypatch):
    client = backend.KeylessSearchClient()
    calls = []
    async def fetch(url):
        calls.append(url)
        if 'duckduckgo.com' in url:
            raise TavilyUpstreamError(502, 'Web request returned HTTP 202')
        return '<rss><channel><item><title>Ubuntu</title><link>https://ubuntu.com/</link><description>Official &amp; useful</description></item><item><title>Excluded</title><link>https://evilubuntu.com/</link></item></channel></rss>', 'text/xml'
    monkeypatch.setattr(client, '_fetch', fetch)
    result = await client.search({'query': 'Ubuntu', 'include_domains': ['ubuntu.com'], 'start_date': '2026-01-01'})
    assert len(calls) == 2
    assert 'bing.com/search?' in calls[1]
    assert result['results'] == [{'title': 'Ubuntu', 'url': 'https://ubuntu.com/', 'content': 'Official & useful'}]
    assert 'does not guarantee' in result['notice']


@pytest.mark.asyncio
@pytest.mark.parametrize('rss', ['<rss>', '<html>Challenge</html>', '<rss/>', '<!DOCTYPE rss [<!ENTITY bomb "no">]><rss><channel/></rss>'])
async def test_invalid_secondary_feeds_fail_cleanly(monkeypatch, rss):
    client = backend.KeylessSearchClient()
    async def fetch(url):
        if 'duckduckgo.com' in url:
            raise TavilyUpstreamError(503, 'Unavailable')
        return rss, 'text/xml'
    monkeypatch.setattr(client, '_fetch', fetch)
    with pytest.raises(TavilyUpstreamError):
        await client.search({'query': 'test'})


@pytest.mark.asyncio
async def test_malformed_redirect_is_sanitized(monkeypatch):
    mock_sessions(monkeypatch, [Response(302, {'Location': 'http://['})])
    with pytest.raises(TavilyUpstreamError, match='invalid redirect'):
        await backend.KeylessSearchClient()._fetch('https://example.com/')


@pytest.mark.asyncio
async def test_malformed_result_link_is_skipped(monkeypatch):
    client = backend.KeylessSearchClient()
    async def fetch(url):
        return '<a class="result-link" href="http://[">bad</a><a class="result-link" href="https://example.com/">Good</a>', 'text/html'
    monkeypatch.setattr(client, '_fetch', fetch)
    assert (await client.search({'query': 'test'}))['results'] == [{'title': 'Good', 'url': 'https://example.com/', 'content': ''}]
