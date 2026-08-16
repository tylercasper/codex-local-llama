from __future__ import annotations

import pytest

from codex_altair_provider.search import SearchProtocolError, execute_search_request


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

