from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


PORT_MIN = 1
PORT_MAX = 65_535
USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Deployment:
    ssh_target: str
    ssh_config: Path
    remote_host: str
    remote_port: int
    local_host: str
    provider_port: int
    tunnel_port: int
    model_id: str
    model_display_name: str
    model_description: str
    model_context_window: int


@dataclass(frozen=True, slots=True)
class CodexRelease:
    version: str
    target: str
    asset: str
    url: str
    sha256: str


def load_deployment(path: Path, *, home: Path) -> Deployment:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    remote = _table(payload, "remote")
    local = _table(payload, "local")
    model = _table(payload, "model")
    ssh_config = _expand_home(_string(remote, "ssh_config"), home)
    if not ssh_config.is_absolute():
        raise ValueError("remote.ssh_config must resolve to an absolute path")

    deployment = Deployment(
        ssh_target=_string(remote, "ssh_target"),
        ssh_config=ssh_config,
        remote_host=_string(remote, "host"),
        remote_port=_port(remote, "port"),
        local_host=_string(local, "host"),
        provider_port=_port(local, "provider_port"),
        tunnel_port=_port(local, "tunnel_port"),
        model_id=_string(model, "id"),
        model_display_name=_string(model, "display_name"),
        model_description=_string(model, "description"),
        model_context_window=_positive_integer(model, "context_window"),
    )
    if deployment.provider_port == deployment.tunnel_port:
        raise ValueError("local.provider_port and local.tunnel_port must differ")
    return deployment


def load_release(path: Path) -> CodexRelease:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Codex release manifest must be a JSON object")
    release = CodexRelease(
        version=_json_string(payload, "version"),
        target=_json_string(payload, "target"),
        asset=_json_string(payload, "asset"),
        url=_json_string(payload, "url"),
        sha256=_json_string(payload, "sha256").lower(),
    )
    if release.target != "x86_64-unknown-linux-musl":
        raise ValueError(f"Unsupported Codex target: {release.target}")
    if not SHA256_PATTERN.fullmatch(release.sha256):
        raise ValueError("Codex release sha256 must contain 64 lowercase hex digits")
    official_sources = ("https://releases.openai.com/codex/releases/",
                        "https://github.com/openai/codex/releases/download/")
    if not release.url.startswith(official_sources):
        raise ValueError("Codex release URL must use an official OpenAI release source")
    if not release.url.endswith(f"/{release.asset}"):
        raise ValueError("Codex release URL and asset name do not match")
    return release


def render_install_assets(
    *,
    repository_root: Path,
    deployment_path: Path,
    release_path: Path,
    output_dir: Path,
    home: Path,
    user: str,
    runtime_root: Path,
    ssh_path: Path,
    codex_home: Path | None = None,
    sqlite_home: Path | None = None,
    source_build: bool = False,
) -> None:
    deployment = load_deployment(deployment_path, home=home)
    release = load_release(release_path)
    if source_build:
        from .compatibility import load_manifest
        compatibility = load_manifest(repository_root)
        pin = compatibility["codex"]
        release = replace(release, version=pin["version"], target=pin["target"])
    if not USER_PATTERN.fullmatch(user):
        raise ValueError(f"Unsupported service user name: {user!r}")
    for path in (repository_root, output_dir, home, runtime_root, ssh_path):
        _reject_newline(path.as_posix(), "path")

    output_dir.mkdir(parents=True, exist_ok=True)
    if source_build:
        (output_dir / "codex-local-backend.json").write_text(json.dumps({
            "schema_version": 2, "backend": "source",
            "package": str(runtime_root / "codex/current"), "source": pin,
            "frontends": {
                platform: {
                    "package": json.loads((repository_root / desktop["manifest"]).read_text()),
                    "bundled_backend_version": desktop["bundled_backend_version"],
                    "source_revision": desktop.get("source_revision", pin["revision"]),
                }
                for platform, desktop in compatibility["desktop"].items()
                if desktop["backend"] == "source"
            },
        }, indent=2) + "\n")
    isolated_home = codex_home if codex_home is not None else home / ".codex-local"
    for path in (isolated_home, sqlite_home):
        if path is not None:
            _reject_newline(path.as_posix(), "state path")
            if not path.is_absolute():
                raise ValueError("Codex home and SQLite home must be absolute paths")
    sqlite_setting = (
        f"sqlite_home = {_toml_string(sqlite_home.as_posix())}\n"
        if sqlite_home is not None else ""
    )
    provider_runtime = runtime_root / "provider" / "current"
    model_catalog_path = isolated_home / "model-catalog.json"
    model_instructions_path = isolated_home / "model-instructions.md"
    provider_base_url = (
        f"http://{deployment.local_host}:{deployment.provider_port}/v1"
    )

    model_instructions = (
        repository_root / "prompts" / "opencode-default-codex.md"
    ).read_text(encoding="utf-8")
    (output_dir / "model-instructions.md").write_text(
        model_instructions, encoding="utf-8"
    )

    profile_template = (
        repository_root / "config" / "codex-local.toml.in"
    ).read_text(encoding="utf-8")
    profile = _replace_tokens(
        profile_template,
        {
            "MODEL_ID": _toml_string(deployment.model_id),
            "MODEL_CATALOG_PATH": _toml_string(model_catalog_path.as_posix()),
            "MODEL_INSTRUCTIONS_PATH": _toml_string(
                model_instructions_path.as_posix()
            ),
            "MODEL_CONTEXT_WINDOW": str(deployment.model_context_window),
            "PROVIDER_BASE_URL": _toml_string(provider_base_url),
        },
    )
    profile = sqlite_setting + profile
    (output_dir / "local.config.toml").write_text(profile, encoding="utf-8")
    (output_dir / "config.toml").write_text(
        sqlite_setting + 'approval_policy = "on-request"\n'
        'sandbox_mode = "workspace-write"\n'
        'approvals_reviewer = "auto_review"\n'
        'hide_agent_reasoning = false\n'
        'show_raw_agent_reasoning = true\n',
        encoding="utf-8",
    )

    catalog = json.loads(
        (repository_root / "config" / "model-catalog.json").read_text(
            encoding="utf-8"
        )
    )
    model = copy.deepcopy(catalog["models"][0])
    model.update(
        {
            "slug": deployment.model_id,
            "display_name": deployment.model_display_name,
            "description": deployment.model_description,
            "context_window": deployment.model_context_window,
            "max_context_window": deployment.model_context_window,
            "auto_compact_token_limit": int(deployment.model_context_window * 0.9),
            "base_instructions": model_instructions,
        }
    )
    models = [model]
    for extra in catalog["models"][1:]:
        extra = copy.deepcopy(extra)
        extra.update(
            context_window=deployment.model_context_window,
            max_context_window=deployment.model_context_window,
            auto_compact_token_limit=int(deployment.model_context_window * 0.9),
            base_instructions=model_instructions,
        )
        models.append(extra)
    (output_dir / "model-catalog.json").write_text(
        json.dumps({"models": models}, indent=2) + "\n", encoding="utf-8"
    )

    provider_template = (
        repository_root / "systemd" / "codex-local-provider.service.in"
    ).read_text(encoding="utf-8")
    provider_service = _replace_tokens(
        provider_template,
        {
            "USER": user,
            "WORKING_DIRECTORY": _systemd_path(provider_runtime),
            "UPSTREAM_ENV": _systemd_string(
                "CODEX_LOCAL_UPSTREAM_URL="
                f"http://{deployment.local_host}:{deployment.tunnel_port}"
            ),
            "PROVIDER_EXEC": _systemd_string(
                (provider_runtime / ".venv" / "bin" / "codex-local-provider").as_posix()
            ),
            "PROVIDER_HOST": _systemd_string(deployment.local_host),
            "PROVIDER_PORT": str(deployment.provider_port),
        },
    )
    (output_dir / "codex-local-provider.service").write_text(
        provider_service, encoding="utf-8"
    )

    tunnel_template = (
        repository_root / "systemd" / "codex-local-tunnel.service.in"
    ).read_text(encoding="utf-8")
    tunnel_service = _replace_tokens(
        tunnel_template,
        {
            "USER": user,
            "HOME_ENV": _systemd_string(f"HOME={home.as_posix()}"),
            "SSH_EXEC": _systemd_string(ssh_path.as_posix()),
            "SSH_CONFIG": _systemd_string(deployment.ssh_config.as_posix()),
            "FORWARD_SPEC": _systemd_string(
                f"{deployment.local_host}:{deployment.tunnel_port}:"
                f"{deployment.remote_host}:{deployment.remote_port}"
            ),
            "SSH_TARGET": _systemd_string(deployment.ssh_target),
        },
    )
    (output_dir / "codex-local-tunnel.service").write_text(
        tunnel_service, encoding="utf-8"
    )

    rendered_deployment = {
        "remote": {
            "ssh_target": deployment.ssh_target,
            "ssh_config": deployment.ssh_config.as_posix(),
            "host": deployment.remote_host,
            "port": deployment.remote_port,
        },
        "local": {
            "host": deployment.local_host,
            "provider_port": deployment.provider_port,
            "tunnel_port": deployment.tunnel_port,
        },
        "model": {
            "id": deployment.model_id,
            "context_window": deployment.model_context_window,
        },
        "codex": {
            "version": release.version,
            "target": release.target,
        },
        "paths": {
            "home": home.as_posix(),
            "isolated_home": isolated_home.as_posix(),
            "runtime_root": runtime_root.as_posix(),
        },
    }
    (output_dir / "deployment.json").write_text(
        json.dumps(rendered_deployment, indent=2) + "\n", encoding="utf-8"
    )


def validate_release_directory(directory: Path, release: CodexRelease) -> None:
    package_manifest = directory / "codex-package.json"
    if not package_manifest.is_file():
        raise ValueError(f"Missing Codex package manifest: {package_manifest}")
    payload = json.loads(package_manifest.read_text(encoding="utf-8"))
    if payload.get("version") != release.version:
        raise ValueError(
            f"Codex package version is {payload.get('version')!r}, expected {release.version!r}"
        )
    if payload.get("target") != release.target:
        raise ValueError(
            f"Codex package target is {payload.get('target')!r}, expected {release.target!r}"
        )
    required = (
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex-path/rg",
        "codex-resources/bwrap",
    )
    missing = [relative for relative in required if not (directory / relative).is_file()]
    if missing:
        raise ValueError(f"Codex package is incomplete; missing: {', '.join(missing)}")


def _table(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a TOML table")
    return value


def _string(table: dict[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    _reject_newline(value, key)
    return value


def _json_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Codex release {key} must be a non-empty string")
    return value


def _positive_integer(table: dict[str, Any], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _port(table: dict[str, Any], key: str) -> int:
    value = _positive_integer(table, key)
    if not PORT_MIN <= value <= PORT_MAX:
        raise ValueError(f"{key} must be between {PORT_MIN} and {PORT_MAX}")
    return value


def _expand_home(value: str, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    if value.startswith("~"):
        raise ValueError("Only the current user's ~ expansion is supported")
    return Path(value)


def _replace_tokens(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace(f"@{name}@", value)
    unresolved = sorted(set(re.findall(r"@[A-Z0-9_]+@", rendered)))
    if unresolved:
        raise ValueError(f"Unresolved template tokens: {', '.join(unresolved)}")
    return rendered


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _systemd_string(value: str) -> str:
    _reject_newline(value, "systemd value")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_path(value: Path) -> str:
    rendered = value.as_posix()
    _reject_newline(rendered, "systemd path")
    if any(character.isspace() for character in rendered):
        raise ValueError("systemd service paths must not contain whitespace")
    return rendered


def _reject_newline(value: str, label: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} must not contain newlines")


def render_gui_assets(assets_dir: Path, output_dir: Path) -> None:
    """Reuse the deployment's managed model assets with a separate GUI home."""
    profile = (assets_dir / "local.config.toml").read_text(encoding="utf-8")
    for key, filename in (
        ("model_catalog_json", "model-catalog.json"),
        ("model_instructions_file", "model-instructions.md"),
    ):
        profile, count = re.subn(
            rf"(?m)^{key}\s*=.*$", f'{key} = "{filename}"', profile
        )
        if count != 1:
            raise ValueError(f"Expected one {key} in rendered deployment")
    profile = 'sandbox_mode = "workspace-write"\n' + profile
    tomllib.loads(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.toml").write_text(profile, encoding="utf-8")
    for filename in ("model-catalog.json", "model-instructions.md"):
        shutil.copyfile(assets_dir / filename, output_dir / filename)
    binding = assets_dir / "codex-local-backend.json"
    if binding.exists():
        shutil.copyfile(binding, output_dir / binding.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render portable Codex assets for a self-hosted llama.cpp server"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render")
    render.add_argument("--repository-root", type=Path, required=True)
    render.add_argument("--deployment", type=Path, required=True)
    render.add_argument("--release", type=Path, required=True)
    render.add_argument("--output-dir", type=Path, required=True)
    render.add_argument("--home", type=Path, required=True)
    render.add_argument("--user", required=True)
    render.add_argument("--runtime-root", type=Path, required=True)
    render.add_argument("--ssh-path", type=Path, required=True)
    render.add_argument("--codex-home", type=Path)
    render.add_argument("--sqlite-home", type=Path)
    render.add_argument("--source-build", action="store_true")

    validate = subparsers.add_parser("validate-release")
    validate.add_argument("--release", type=Path, required=True)
    validate.add_argument("directory", type=Path)

    gui = subparsers.add_parser("render-gui")
    gui.add_argument("--assets-dir", type=Path, required=True)
    gui.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "render":
        render_install_assets(
            repository_root=args.repository_root.resolve(),
            deployment_path=args.deployment.resolve(),
            release_path=args.release.resolve(),
            output_dir=args.output_dir.resolve(),
            home=args.home.resolve(),
            user=args.user,
            runtime_root=args.runtime_root.resolve(),
            ssh_path=args.ssh_path.resolve(),
            codex_home=args.codex_home.resolve() if args.codex_home else None,
            sqlite_home=args.sqlite_home.resolve() if args.sqlite_home else None,
            source_build=args.source_build,
        )
    elif args.command == "render-gui":
        render_gui_assets(args.assets_dir.resolve(), args.output_dir.resolve())
    else:
        validate_release_directory(args.directory.resolve(), load_release(args.release))


if __name__ == "__main__":
    main()
