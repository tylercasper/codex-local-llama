from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    upstream_url: str = "http://127.0.0.1:18001"
    tavily_base_url: str = "https://api.tavily.com"
    tavily_api_key: str | None = None
    tavily_search_depth: str = "basic"
    request_timeout_seconds: float = 60.0
    upstream_idle_timeout_seconds: float = 910.0

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            upstream_url=os.environ.get(
                "CODEX_LOCAL_UPSTREAM_URL", "http://127.0.0.1:18001"
            ).rstrip("/"),
            tavily_base_url=os.environ.get(
                "TAVILY_BASE_URL", "https://api.tavily.com"
            ).rstrip("/"),
            tavily_api_key=_load_tavily_key(),
            tavily_search_depth=os.environ.get(
                "TAVILY_SEARCH_DEPTH", "basic"
            ).strip(),
            request_timeout_seconds=float(
                os.environ.get("TAVILY_TIMEOUT_SECONDS", "60")
            ),
            upstream_idle_timeout_seconds=float(
                os.environ.get("CODEX_LOCAL_UPSTREAM_IDLE_TIMEOUT_SECONDS", "910")
            ),
        )


def _load_tavily_key() -> str | None:
    direct = os.environ.get("TAVILY_API_KEY", "").strip()
    if direct:
        return direct

    configured_path = os.environ.get("TAVILY_API_KEY_FILE", "").strip()
    if configured_path:
        return _read_nonempty(Path(configured_path))

    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if credentials_directory:
        return _read_nonempty(Path(credentials_directory) / "tavily_key")

    return None


def _read_nonempty(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return value or None
