"""Resolve the pinned component set and verify locally built packages."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

BUILD_RECORD = "codex-local-build.json"
REQUIRED_FILES = ("codex-package.json", "bin/codex", "bin/codex-code-mode-host",
                  "codex-resources/bwrap", "codex-path/rg")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def repository_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Manifest paths must remain within the repository")
    return path


def load_manifest(root: Path) -> dict:
    manifest = json.loads((root / "config/compatibility.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported compatibility manifest schema")
    codex = manifest["codex"]
    if not re.fullmatch(r"[0-9a-f]{40}", codex["revision"]):
        raise ValueError("Codex must be pinned to a full commit ID")
    if codex["target"] not in {"x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"}:
        raise ValueError("Unsupported source-build target")
    if codex["profile"] not in {"release", "dev-small"}:
        raise ValueError("Unsupported Cargo profile")
    repository_path(root, codex["path"])
    for desktop in manifest["desktop"].values():
        package = json.loads(repository_path(root, desktop["manifest"]).read_text())
        if not re.fullmatch(r"[0-9a-f]{64}", package["sha256"]):
            raise ValueError("Desktop archive must have a SHA-256 pin")
        if Path(package["asset"]).name != package["asset"]:
            raise ValueError("Desktop archive name must be a filename")
        if desktop["backend"] not in {"bundled", "source"}:
            raise ValueError("Unsupported desktop backend")
        if desktop["backend"] == "source":
            pairing = desktop.get("source_revision")
            if pairing is not None and pairing != codex["revision"]:
                raise ValueError("Desktop source pairing differs from the pinned revision")
            if desktop["bundled_backend_version"] != codex["version"] and pairing is None:
                raise ValueError("Different desktop/backend versions require an explicit source pairing")
    return manifest


def verify_source(root: Path, manifest: dict) -> Path:
    pin = manifest["codex"]
    source = repository_path(root, pin["path"])
    if not (source / ".git").exists():
        raise ValueError("Codex submodule missing; run git submodule update --init --recursive")
    entry = git(root, "ls-files", "--stage", "--", pin["path"]).split()
    if len(entry) < 3 or entry[:3] != ["160000", pin["revision"], "0"]:
        raise ValueError("Codex gitlink differs from the compatibility manifest")
    if git(source, "rev-parse", "HEAD") != pin["revision"]:
        raise ValueError("Codex checkout differs from the pinned revision")
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Codex source has tracked modifications")
    cargo = tomllib.loads((source / "codex-rs/Cargo.toml").read_text())
    toolchain = tomllib.loads((source / "codex-rs/rust-toolchain.toml").read_text())
    if cargo["workspace"]["package"]["version"] != pin["version"]:
        raise ValueError("Codex workspace version differs from the manifest")
    if toolchain["toolchain"]["channel"] != pin["rust_toolchain"]:
        raise ValueError("Codex Rust toolchain differs from the manifest")
    return source


def resolve_manifest(root: Path) -> dict:
    manifest = load_manifest(root)
    resolved = json.loads(json.dumps(manifest))
    for desktop in resolved["desktop"].values():
        desktop["package"] = json.loads(repository_path(root, desktop["manifest"]).read_text())
    resolved["harness"] = {"revision": git(root, "rev-parse", "HEAD"),
                           "tracked_tree_dirty": bool(git(root, "status", "--porcelain", "--untracked-files=no"))}
    resolved["manifest_sha256"] = sha256(root / "config/compatibility.json")
    return resolved


def package_fingerprints(directory: Path) -> dict:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Source package must not contain symlinks")
        if path.is_file() and path.relative_to(directory).as_posix() != BUILD_RECORD:
            result[path.relative_to(directory).as_posix()] = {
                "sha256": sha256(path), "executable": bool(path.stat().st_mode & 0o111)}
    return result


def validate_source_package(directory: Path, manifest: dict) -> dict:
    record = json.loads((directory / BUILD_RECORD).read_text())
    pin = manifest["codex"]
    if record.get("schema_version") != 1 or record.get("source") != pin:
        raise ValueError("Source package does not match the pinned build")
    if record.get("compatibility", {}).get("codex") != pin:
        raise ValueError("Source package compatibility record differs from its source pin")
    metadata = json.loads((directory / "codex-package.json").read_text())
    expected = {"layoutVersion": 1, "version": pin["version"], "target": pin["target"], "variant": "codex",
                "entrypoint": "bin/codex", "resourcesDir": "codex-resources", "pathDir": "codex-path"}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("Source package layout/version/target mismatch")
    for name in REQUIRED_FILES:
        path = directory / name
        if not path.is_file() or (name != "codex-package.json" and not path.stat().st_mode & 0o111):
            raise ValueError(f"Missing or non-executable package component: {name}")
    if record.get("files") != package_fingerprints(directory):
        raise ValueError("Source package contents do not match their build fingerprints")
    return record


def package_name(manifest: dict) -> str:
    pin = manifest["codex"]
    return f'{pin["version"]}-{pin["target"]}-source-{pin["revision"][:12]}-{pin["profile"]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("resolve")
    sub.add_parser("package-name")
    validate = sub.add_parser("validate-package")
    validate.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    try:
        manifest = load_manifest(root)
        if args.command == "check":
            verify_source(root, manifest)
            print("Compatibility pins and Codex source verified")
        elif args.command == "resolve":
            print(json.dumps(resolve_manifest(root), indent=2))
        elif args.command == "package-name":
            print(package_name(manifest))
        else:
            validate_source_package(args.directory.resolve(), manifest)
            print("Source package verified")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
