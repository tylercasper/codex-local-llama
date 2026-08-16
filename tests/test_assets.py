from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_model_catalog_matches_codex_config() -> None:
    catalog = json.loads(
        (REPOSITORY_ROOT / "config/model-catalog.json").read_text(encoding="utf-8")
    )
    assert len(catalog["models"]) == 1
    model = catalog["models"][0]
    assert model["slug"] == "qwen3.8-27b"
    assert model["context_window"] == 262_144
    assert model["max_context_window"] == 262_144
    assert model["supports_parallel_tool_calls"] is False
    assert model["use_responses_lite"] is False

    config = (REPOSITORY_ROOT / "config/codex-altair.toml").read_text(
        encoding="utf-8"
    )
    assert 'model = "qwen3.8-27b"' in config
    assert 'model_catalog_json = "/home/example/.codex-altair/model-catalog.json"' in config
