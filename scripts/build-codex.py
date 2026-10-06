#!/usr/bin/env python3
"""Build the pinned complete Codex package and retain component fingerprints."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_local_provider.compatibility import (
    BUILD_RECORD, git, load_manifest, package_fingerprints, package_name,
    resolve_manifest, sha256, validate_source_package, verify_source,
)


def normalized_release_lock(original: bytes, version: str) -> bytes:
    """Release tags bump Cargo.toml but retain 0.0.0 for workspace lock entries.

    Normalize only local package versions. External dependencies remain byte-for-byte
    pinned, and Cargo must accept the result with --locked.
    """
    blocks = original.decode().split("[[package]]")
    for index in range(1, len(blocks)):
        package = tomllib.loads("[[package]]" + blocks[index])["package"][0]
        if "source" not in package and package["version"] == "0.0.0":
            blocks[index] = re.sub(r'(?m)^version = "0\.0\.0"$',
                                   f'version = "{version}"', blocks[index], count=1)
    return "[[package]]".join(blocks).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    manifest = load_manifest(ROOT)
    output = (args.output or ROOT / ".build/packages" / package_name(manifest)).resolve()
    build_root = ROOT / ".build"
    build_root.mkdir(exist_ok=True)
    with (build_root / "source-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source = verify_source(ROOT, manifest)
        if output.exists():
            validate_source_package(output, manifest)
            print(output)
            return
        pin = manifest["codex"]
        tool = subprocess.check_output(["rustc", "--version"], cwd=source / "codex-rs", text=True)
        if tool.split()[1] != pin["rust_toolchain"]:
            raise ValueError("rustc does not match the pinned toolchain")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".source-package-", dir=output.parent) as temporary:
            staged = Path(temporary) / "package"
            lockfile = source / "codex-rs/Cargo.lock"
            original = lockfile.read_bytes()
            normalized = normalized_release_lock(original, pin["version"])
            # Lockfile restoration is conditional: never overwrite concurrent user edits.
            lockfile.write_bytes(normalized)
            try:
                env = dict(os.environ)
                env.setdefault("CARGO_TARGET_DIR", str(build_root / "target"))
                env["CARGO_BUILD_JOBS"] = str(args.jobs)
                env.setdefault("CARGO_NET_GIT_FETCH_WITH_CLI", "true")
                subprocess.run([
                    sys.executable, str(source / "scripts/build_codex_package.py"),
                    "--target", pin["target"], "--cargo-profile", pin["profile"],
                    "--cargo", str(ROOT / "scripts/cargo-locked"),
                    "--package-dir", str(staged),
                ], env=env, check=True)
                lock_sha = sha256(lockfile)
            finally:
                if lockfile.read_bytes() != normalized:
                    raise RuntimeError("Cargo.lock changed unexpectedly; retained it for inspection")
                lockfile.write_bytes(original)
            verify_source(ROOT, manifest)
            record = {"schema_version": 1, "source": pin,
                      "compatibility": resolve_manifest(ROOT), "rustc": tool.strip(),
                      "normalized_cargo_lock_sha256": lock_sha,
                      "files": package_fingerprints(staged)}
            (staged / BUILD_RECORD).write_text(json.dumps(record, indent=2) + "\n")
            validate_source_package(staged, manifest)
            staged.rename(output)
        print(output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"error: {error}")
