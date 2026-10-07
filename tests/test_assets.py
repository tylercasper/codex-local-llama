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
    assert model["apply_patch_tool_type"] == "freeform"
    assert model["input_modalities"] == ["text", "image"]
    assert "base_instructions" not in model

    config = (REPOSITORY_ROOT / "config/codex-local.toml.in").read_text(
        encoding="utf-8"
    )
    assert "model = @MODEL_ID@" in config
    assert "model_catalog_json = @MODEL_CATALOG_PATH@" in config
    assert "model_instructions_file = @MODEL_INSTRUCTIONS_PATH@" in config
    assert "hide_agent_reasoning = false" in config
    assert "show_raw_agent_reasoning = true" in config
    assert 'approval_policy = "on-request"' in config
    assert 'approvals_reviewer = "auto_review"' in config
    assert 'supports_standalone_web_search = true' in config


def test_prompt_assets_preserve_upstream_and_previous_versions() -> None:
    prompts = REPOSITORY_ROOT / "prompts"
    previous = (prompts / "codex-local-previous.md").read_text(encoding="utf-8")
    upstream = (prompts / "opencode-default-upstream.md").read_text(
        encoding="utf-8"
    )
    active = (prompts / "opencode-default-codex.md").read_text(encoding="utf-8")

    assert previous.startswith("You are Codex, a coding agent running in the Codex CLI.")
    assert upstream.startswith("You are opencode, an interactive CLI tool")
    assert "prefer to use the Task tool" in upstream
    assert "Use `apply_patch` for every file creation" in active
    assert "Never edit files through `exec_command`" in active
    assert "Call `update_plan` immediately as the first assistant output" in active
    assert "Produce no reasoning or commentary before it" in active
    assert "Make inspection the first step when needed" in active
    assert "Tool calls are the work. Reasoning is only for choosing" in active
    assert "the correct reasoning output is empty" in active
    assert "the very next assistant item is `apply_patch`" in active
    assert "Do not insert a reasoning item between a tool result" in active
    assert "at most one sentence and 25 words of reasoning" not in active


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
        assert str(Path.home()) not in path.read_text(encoding="utf-8"), path


def test_pinned_codex_release_has_expected_identity() -> None:
    release = json.loads(
        (REPOSITORY_ROOT / "config/codex-release.json").read_text(encoding="utf-8")
    )
    assert release == {
        "version": "0.160.1",
        "target": "x86_64-unknown-linux-musl",
        "asset": "codex-package-x86_64-unknown-linux-musl.tar.gz",
        "url": (
            "https://github.com/openai/codex/releases/download/rust-v0.160.1/"
            "codex-package-x86_64-unknown-linux-musl.tar.gz"
        ),
        "sha256": (
            "340801565906a7028f6baaa9ab6853addaef221f0016a1417a7c1ffdd96c21f0"
        ),
    }
