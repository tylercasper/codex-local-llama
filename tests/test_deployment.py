from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from codex_local_provider.deployment import (
    load_deployment,
    load_release,
    render_install_assets,
    validate_release_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_render_install_assets(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    runtime_root = home / ".local" / "lib" / "codex-local"
    output_dir = tmp_path / "rendered"

    render_install_assets(
        repository_root=REPOSITORY_ROOT,
        deployment_path=REPOSITORY_ROOT / "config/deployment.toml",
        release_path=REPOSITORY_ROOT / "config/codex-release.json",
        output_dir=output_dir,
        home=home,
        user="alice",
        runtime_root=runtime_root,
        ssh_path=Path("/usr/bin/ssh"),
    )

    with (output_dir / "local.config.toml").open("rb") as handle:
        profile = tomllib.load(handle)
    assert profile["model"] == "qwen3.8-27b"
    assert profile["model_provider"] == "llamacpp"
    assert profile["model_catalog_json"] == str(home / ".codex-local/model-catalog.json")
    assert profile["model_instructions_file"] == str(
        home / ".codex-local/model-instructions.md"
    )
    assert profile["hide_agent_reasoning"] is False
    assert profile["show_raw_agent_reasoning"] is True
    assert profile["approval_policy"] == "on-request"
    assert profile["approvals_reviewer"] == "auto_review"
    assert profile["model_providers"]["llamacpp"]["base_url"] == (
        "http://127.0.0.1:18000/v1"
    )

    with (output_dir / "config.toml").open("rb") as handle:
        base_config = tomllib.load(handle)
    assert base_config["hide_agent_reasoning"] is False
    assert base_config["show_raw_agent_reasoning"] is True
    assert base_config["approval_policy"] == "on-request"
    assert base_config["approvals_reviewer"] == "auto_review"

    prompt = (REPOSITORY_ROOT / "prompts/opencode-default-codex.md").read_text(
        encoding="utf-8"
    )
    assert (output_dir / "model-instructions.md").read_text(encoding="utf-8") == prompt
    catalog = json.loads(
        (output_dir / "model-catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["models"][0]["base_instructions"] == prompt
    assert catalog["models"][0]["apply_patch_tool_type"] == "freeform"
    reviewer = next(
        entry for entry in catalog["models"]
        if entry["slug"] == catalog["models"][0]["auto_review_model_override"]
    )
    assert reviewer["visibility"] == "hide"
    assert reviewer["default_reasoning_level"] == "none"
    assert reviewer["base_instructions"] == prompt
    assert reviewer["context_window"] == catalog["models"][0]["context_window"]

    deployment = json.loads(
        (output_dir / "deployment.json").read_text(encoding="utf-8")
    )
    assert deployment["remote"]["ssh_config"] == str(home / ".ssh/config")
    assert deployment["paths"]["runtime_root"] == str(runtime_root)
    assert deployment["codex"] == {
        "version": "0.160.1",
        "target": "x86_64-unknown-linux-musl",
    }

    provider_unit = (output_dir / "codex-local-provider.service").read_text(
        encoding="utf-8"
    )
    assert "User=alice" in provider_unit
    assert str(runtime_root / "provider/current/.venv/bin/codex-local-provider") in (
        provider_unit
    )
    tunnel_unit = (output_dir / "codex-local-tunnel.service").read_text(
        encoding="utf-8"
    )
    configured = load_deployment(REPOSITORY_ROOT / "config/deployment.toml", home=home)
    assert (
        f'-L "{configured.local_host}:{configured.tunnel_port}:'
        f'{configured.remote_host}:{configured.remote_port}"'
    ) in tunnel_unit
    assert f'-F "{home}/.ssh/config"' in tunnel_unit
    assert "@" not in provider_unit
    assert "@" not in tunnel_unit


def test_load_deployment_rejects_duplicate_local_ports(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment.toml"
    deployment.write_text(
        """
[remote]
ssh_target = "llama-server"
ssh_config = "~/.ssh/config"
host = "127.0.0.1"
port = 8001
[local]
host = "127.0.0.1"
provider_port = 18000
tunnel_port = 18000
[model]
id = "qwen"
display_name = "Qwen"
description = "Test"
context_window = 4096
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must differ"):
        load_deployment(deployment, home=tmp_path)


def test_load_release_rejects_untrusted_url(tmp_path: Path) -> None:
    release_path = tmp_path / "release.json"
    release_path.write_text(
        json.dumps(
            {
                "version": "0.160.1",
                "target": "x86_64-unknown-linux-musl",
                "asset": "codex.tar.gz",
                "url": "https://example.com/codex.tar.gz",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="official OpenAI release source"):
        load_release(release_path)


def test_validate_release_directory(tmp_path: Path) -> None:
    release = load_release(REPOSITORY_ROOT / "config/codex-release.json")
    package = tmp_path / "package"
    (package / "bin").mkdir(parents=True)
    (package / "codex-path").mkdir()
    (package / "codex-resources").mkdir()
    (package / "codex-package.json").write_text(
        json.dumps({"version": release.version, "target": release.target}),
        encoding="utf-8",
    )
    for relative in (
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex-path/rg",
        "codex-resources/bwrap",
    ):
        (package / relative).touch()

    validate_release_directory(package, release)
    (package / "codex-path/rg").unlink()
    with pytest.raises(ValueError, match="codex-path/rg"):
        validate_release_directory(package, release)
