from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_model_catalog_matches_deployment_defaults() -> None:
    catalog = json.loads(
        (REPOSITORY_ROOT / "config/model-catalog.json").read_text(encoding="utf-8")
    )
    assert len(catalog["models"]) == 2
    model = catalog["models"][0]
    assert model["default_reasoning_level"] == "none"
    assert [entry["effort"] for entry in model["supported_reasoning_levels"]] == [
        "none", "low", "medium", "xhigh"
    ]
    reviewer = catalog["models"][1]
    assert reviewer["slug"] == model["auto_review_model_override"] == "local"
    assert reviewer["visibility"] == "hide"
    assert reviewer["default_reasoning_level"] == "none"
    assert [entry["effort"] for entry in reviewer["supported_reasoning_levels"]] == ["none"]
    assert model["slug"] == "qwen3.8-27b"
    assert model["context_window"] == 262_144
    assert model["max_context_window"] == 262_144
    assert model["supports_parallel_tool_calls"] is False
    assert model["use_responses_lite"] is False

    config = (REPOSITORY_ROOT / "config/codex-local.toml.in").read_text(
        encoding="utf-8"
    )
    assert "model = @MODEL_ID@" in config
    assert "model_catalog_json = @MODEL_CATALOG_PATH@" in config
    assert 'supports_standalone_web_search = true' in config


def test_portable_assets_do_not_embed_install_user() -> None:
    paths = [
        REPOSITORY_ROOT / "config/codex-local.toml.in",
        REPOSITORY_ROOT / "config/deployment.toml",
        REPOSITORY_ROOT / "scripts/codex-local",
        REPOSITORY_ROOT / "scripts/install-local.sh",
        REPOSITORY_ROOT / "scripts/verify-local.sh",
        REPOSITORY_ROOT / "systemd/codex-local-provider.service.in",
        REPOSITORY_ROOT / "systemd/codex-local-tunnel.service.in",
    ]
    for path in paths:
        assert "/home/example" not in path.read_text(encoding="utf-8"), path


def test_pinned_codex_release_has_expected_identity() -> None:
    release = json.loads(
        (REPOSITORY_ROOT / "config/codex-release.json").read_text(encoding="utf-8")
    )
    assert release == {
        "version": "0.147.0",
        "target": "x86_64-unknown-linux-musl",
        "asset": "codex-package-x86_64-unknown-linux-musl.tar.gz",
        "url": (
            "https://releases.openai.com/codex/releases/0.147.0/"
            "codex-package-x86_64-unknown-linux-musl.tar.gz"
        ),
        "sha256": (
            "bd758d53d56e41dc65e045f4589df79a038ed197a011adcb52a258e6ad64cfda"
        ),
    }
