"""Select the installed source package without changing the GUI's state location.

Copied beside the GUI shim. Only the Python standard library is needed at launch.
Full package fingerprints are checked by the installer, before activation.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tomllib

BINDING = "codex-local-backend.json"


def load_binding(path):
    binding = json.loads(Path(path).read_text())
    if binding.get("schema_version") not in {1, 2} or binding.get("backend") != "source":
        raise ValueError("GUI requires a source backend binding; rerun the installer")
    if not Path(binding["package"]).is_absolute():
        raise ValueError("GUI source package path must be absolute")
    if binding["schema_version"] == 1:
        if binding["source"]["version"] != binding["frontend_backend_version"]:
            raise ValueError("GUI and source backend versions differ")
    else:
        frontends = binding.get("frontends")
        if not isinstance(frontends, dict) or not frontends:
            raise ValueError("GUI binding lacks explicit frontend pairings")
        for frontend in frontends.values():
            if frontend["source_revision"] != binding["source"]["revision"]:
                raise ValueError("GUI pairing differs from the source pin")
    return binding


def backend_path(binding):
    package = Path(binding["package"]).resolve(strict=True)
    record = json.loads((package / "codex-local-build.json").read_text())
    if record.get("source") != binding["source"]:
        raise ValueError("Installed CLI differs from the GUI source pin; reinstall both together")
    metadata = json.loads((package / "codex-package.json").read_text())
    if (metadata.get("version") != binding["source"]["version"]
            or metadata.get("entrypoint") != "bin/codex"):
        raise ValueError("Invalid GUI source package metadata")
    binary = package / "bin/codex"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("GUI source backend is not executable")
    return binary


def sqlite_home(home):
    """Ignore Electron's inherited default, but honor this profile's configured DB."""
    home = Path(home)
    config = home / "config.toml"
    setting = tomllib.loads(config.read_text(encoding="utf-8-sig")).get("sqlite_home") if config.exists() else None
    if setting is None:
        return home / "sqlite"
    path = Path(setting).expanduser()
    if not path.is_absolute():
        raise ValueError("Configured sqlite_home must be absolute")
    return path


def validate_install(path, dry_run=False, frontend=None):
    binding = load_binding(path)
    if binding["schema_version"] == 2 and frontend is not None:
        platform, manifest = frontend
        pairing = binding["frontends"].get(platform)
        if pairing is None or pairing["package"] != manifest:
            raise ValueError("GUI archive differs from the explicit frontend pairing")
    if not dry_run:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from codex_local_provider.compatibility import validate_source_package
        validate_source_package(Path(binding["package"]), {"codex": binding["source"]})
        backend_path(binding)
    return binding


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--frontend", choices=("linux", "windows_wsl"))
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        if bool(args.frontend) != bool(args.manifest):
            raise ValueError("Provide both --frontend and --manifest")
        frontend = (args.frontend, json.loads(args.manifest.read_text())) if args.frontend else None
        validate_install(args.binding, args.dry_run, frontend)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
