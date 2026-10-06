"""No-key web search and bounded public-page extraction.

DuckDuckGo documents its Lite interface at
https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript.
This is a best-effort HTML interface, so challenges/schema changes are errors,
not empty search results. Page requests have no provider credentials or cookies.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from html.parser import HTMLParser
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from xml.etree import ElementTree

import aiohttp
from aiohttp.abc import AbstractResolver

from .search import TavilyUpstreamError

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 120_000
SEARCH_URL = "https://lite.duckduckgo.com/lite/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.port not in {None, 80, 443}:
            raise ValueError
        if any(c.isspace() for c in url) or '%' in host:
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host.rstrip('.').lower() == 'localhost' or host.lower().endswith(('.localhost', '.local')):
                raise ValueError
        else:
            if not _public_address(host):
                raise ValueError
    except ValueError as exc:
        raise TavilyUpstreamError(400, "Only public HTTP(S) web pages on standard ports can be opened") from exc


class PublicResolver(AbstractResolver):
    """Validate the actual DNS answers the connector uses, avoiding DNS rebinding."""
    def __init__(self) -> None:
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records or any(not _public_address(record['host']) for record in records):
            raise OSError("Web destination is not public")
        return records

    async def close(self) -> None:
        await self._resolver.close()


class SearchHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = set((attr.get('class') or '').split())
        if tag == 'a' and classes & {'result-link', 'result__a'}:
            try:
                href = urljoin(SEARCH_URL, attr.get('href') or '')
                parsed = urlsplit(href)
            except ValueError:
                self.results.append({'title': '', 'url': '', 'content': ''})
                self._capture = None
                return
            if parsed.hostname and parsed.hostname.endswith('duckduckgo.com') and parsed.path == '/l/':
                href = parse_qs(parsed.query).get('uddg', [''])[0]
            self.results.append({'title': '', 'url': href, 'content': ''})
            self._capture, self._tag, self._parts = 'title', tag, []
        elif classes & {'result-snippet', 'result__snippet'} and self.results:
            self._capture, self._tag, self._parts = 'content', tag, []

    def handle_data(self, data):
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if self._capture and tag == self._tag:
            self.results[-1][self._capture] = re.sub(r'\s+', ' ', ''.join(self._parts)).strip()
            self._capture = None


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'svg', 'noscript', 'template'}:
            self.hidden.append(tag)
        elif not self.hidden and tag in {'p', 'div', 'section', 'article', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
        elif tag in {'p', 'div', 'section', 'article', 'li', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.parts).splitlines()]
        return '\n'.join(line for line in lines if line)[:MAX_TEXT]


class KeylessSearchClient:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds
        self._limit = asyncio.Semaphore(4)

    async def _fetch(self, url: str) -> tuple[str, str]:
        async with self._limit:
            try:
                async with asyncio.timeout(self._timeout):
                    resolver = PublicResolver()
                    try:
                        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
                        async with aiohttp.ClientSession(
                            connector=connector, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                            auto_decompress=False, timeout=aiohttp.ClientTimeout(total=self._timeout),
                            headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'},
                        ) as session:
                            for _ in range(6):
                                _validate_url(url)
                                async with session.get(url, allow_redirects=False) as response:
                                    if response.status in {301, 302, 303, 307, 308}:
                                        destination = response.headers.get('Location')
                                        if not destination:
                                            raise TavilyUpstreamError(502, 'Web page returned an invalid redirect')
                                        try:
                                            url = urljoin(url, destination)
                                        except ValueError as exc:
                                            raise TavilyUpstreamError(502, 'Web page returned an invalid redirect') from exc
                                        continue
                                    if response.status != 200:
                                        raise TavilyUpstreamError(502, f'Web request returned HTTP {response.status}')
                                    media = response.content_type
                                    if media not in {'text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown', 'text/xml', 'application/xml', 'application/rss+xml'}:
                                        raise TavilyUpstreamError(502, 'Web page format is not supported by text extraction')
                                    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                                        raise TavilyUpstreamError(502, 'Web page ignored the uncompressed response request')
                                    chunks = bytearray()
                                    async for chunk in response.content.iter_chunked(16384):
                                        chunks.extend(chunk)
                                        if len(chunks) > MAX_BYTES:
                                            raise TavilyUpstreamError(502, 'Web page exceeded the extraction size limit')
                                    try:
                                        text = chunks.decode(response.charset or 'utf-8', errors='replace')
                                    except LookupError:
                                        text = chunks.decode('utf-8', errors='replace')
                                    return text, media
                            raise TavilyUpstreamError(502, 'Web page exceeded the redirect limit')
                    finally:
                        await resolver.close()
            except TimeoutError as exc:
                raise TavilyUpstreamError(504, 'No-key web request timed out') from exc
            except (aiohttp.ClientError, OSError) as exc:
                raise TavilyUpstreamError(502, 'No-key web request could not connect to a public destination') from exc

    async def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get('query', '')).strip()
        if not query:
            raise TavilyUpstreamError(400, 'Search query must not be empty')
        domains = [str(d).lower().strip().rstrip('.') for d in payload.get('include_domains', [])]
        if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', d) for d in domains):
            raise TavilyUpstreamError(400, 'Search domains must be hostnames')
        if domains:
            query += ' (' + ' OR '.join('site:' + d for d in domains) + ')'
        params = {'q': query}
        if payload.get('start_date'):
            try:
                start = date.fromisoformat(payload['start_date'])
            except (ValueError, TypeError) as exc:
                raise TavilyUpstreamError(400, 'Search date must use YYYY-MM-DD') from exc
            # DuckDuckGo Lite accepts an inclusive date range in its df control.
            params['df'] = f'{start.isoformat()}..{datetime.now(UTC).date().isoformat()}'
        notice = ''
        try:
            text, _ = await self._fetch(SEARCH_URL + '?' + urlencode(params))
            if any(marker in text.lower() for marker in ('anomaly.js', 'anomaly-modal', 'unfortunately, bots')):
                raise TavilyUpstreamError(503, 'No-key search is temporarily unavailable (search engine challenge)')
            parser = SearchHTML()
            parser.feed(text)
            if not parser.results and 'no results' not in text.lower():
                raise TavilyUpstreamError(502, 'No-key search returned an unrecognized results page')
            candidates = parser.results
        except TavilyUpstreamError:
            # A distinct engine is preferable to repeatedly retrying a challenge.
            # RSS is an alternate public search interface documented by Bing:
            # https://blogs.bing.com/search/January-2005/RSS-Feeds-for-Search-Results
            rss, _ = await self._fetch('https://www.bing.com/search?' + urlencode({'q': query, 'format': 'rss'}))
            if '<!doctype' in rss.lower() or '<!entity' in rss.lower():
                raise TavilyUpstreamError(502, 'Alternate search returned unsupported XML')
            try:
                root = ElementTree.fromstring(rss)
            except ElementTree.ParseError as exc:
                raise TavilyUpstreamError(502, 'Alternate search returned invalid XML') from exc
            if root.tag != 'rss' or root.find('channel') is None:
                raise TavilyUpstreamError(502, 'Alternate search returned an unrecognized feed')
            candidates = []
            if payload.get('start_date'):
                notice = 'Alternate search does not guarantee the requested publication dates; verify dates.'
            for item in root.findall('./channel/item'):
                description = PageText()
                description.feed(item.findtext('description') or '')
                content = description.text()
                candidates.append({'title': item.findtext('title') or '', 'url': item.findtext('link') or '', 'content': content})
        results = []
        seen = set()
        for result in candidates:
            try:
                _validate_url(result['url'])
            except TavilyUpstreamError:
                continue
            host = (urlsplit(result['url']).hostname or '').lower().rstrip('.')
            if domains and not any(host == domain or host.endswith('.' + domain) for domain in domains):
                continue
            if result['url'] not in seen:
                results.append(result)
                seen.add(result['url'])
        response: dict[str, Any] = {'results': results[:max(1, min(int(payload.get('max_results', 5)), 10))]}
        if notice:
            response['notice'] = notice
        return response

    async def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        urls = payload.get('urls', [])
        if not isinstance(urls, list) or len(urls) > 10:
            raise TavilyUpstreamError(400, 'Extraction accepts at most ten URLs')
        results, failures = [], []
        for url in urls:
            try:
                source, media = await self._fetch(str(url))
                if media in {'text/xml', 'application/xml', 'application/rss+xml'}:
                    raise TavilyUpstreamError(502, 'Web page format is not supported by text extraction')
                if media in {'text/html', 'application/xhtml+xml'}:
                    parser = PageText()
                    parser.feed(source)
                    source = parser.text()
                results.append({'url': url, 'raw_content': source[:MAX_TEXT]})
            except TavilyUpstreamError as exc:
                failures.append({'url': url, 'error': exc.message})
        return {'results': results, 'failed_results': failures}
