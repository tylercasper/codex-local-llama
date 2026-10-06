#!/usr/bin/env python3
"""Install the bundled GUI privately; never install the vendor .deb system-wide."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_gui_config import merge_gui_config

RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def validate_bundle(bundle_dir: Path) -> tuple[Path, dict]:
    manifest = json.loads((bundle_dir / "linux.json").read_text())
    if Path(manifest["asset"]).name != manifest["asset"]:
        raise ValueError("bundle asset must be a filename")
    if not re.fullmatch(r"[A-Za-z0-9.+_-]+", manifest["version"]):
        raise ValueError("invalid GUI version")
    archive = bundle_dir / manifest["asset"]
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError("GUI bundle checksum mismatch")
    for field, key in (("Package", "package"), ("Version", "version"), ("Architecture", "architecture")):
        actual = run("dpkg-deb", "-f", str(archive), field, capture_output=True).stdout.strip()
        if actual != manifest[key]:
            raise ValueError(f"GUI bundle {field} mismatch")
    return archive, manifest


def atomic_write(path: Path, contents: bytes, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".codex-local-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def launcher_text(runtime: Path, gui_home: Path) -> str:
    return f'''#!/bin/sh
set -eu
export CODEX_HOME={shlex.quote(str(gui_home))}
export CODEX_SQLITE_HOME={shlex.quote(str(gui_home / 'sqlite'))}
export CODEX_ELECTRON_USER_DATA_PATH={shlex.quote(str(gui_home / 'app-data'))}
unset OPENAI_API_KEY OPENAI_BASE_URL CODEX_OSS_BASE_URL CODEX_OSS_PORT CODEX_CLI_PATH
# Console shells may omit the display environment supplied by WSLg.
# Keep existing desktop/remote-desktop sessions intact.
if [ -z "${{DISPLAY:-}}" ] && [ -z "${{WAYLAND_DISPLAY:-}}" ] && [ -S /mnt/wslg/.X11-unix/X0 ] && [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY=:0
    if [ -S "/run/user/$(id -u)/bus" ]; then
        case "${{DBUS_SESSION_BUS_ADDRESS:-}}" in
            unix:*|tcp:*|nonce-tcp:*) ;;
            *) export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" ;;
        esac
    fi
fi
cd "$CODEX_HOME"
exec {shlex.quote(str(runtime / 'gui/current/usr/lib/chatgpt/ChatGPT'))} --user-data-dir="$CODEX_ELECTRON_USER_DATA_PATH" "$@"
'''


def apparmor_profile(executable: Path, profile_name: str) -> str:
    # Reject AppArmor pattern syntax rather than accidentally granting a wildcard.
    if any(c in str(executable) for c in '\\"\n\r*?[]{}'):
        raise ValueError("GUI runtime path contains unsupported AppArmor characters")
    return f'''abi <abi/4.0>,
include <tunables/global>
profile {profile_name} "{executable}" flags=(unconfined) {{
  userns,
}}
'''


def desktop_text(launcher: Path, icon: Path) -> str:
    # Desktop Exec quoting differs from shell quoting.
    command = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    return f'''[Desktop Entry]
Type=Application
Name=Codex Local
Comment=Codex with the local model provider
Exec="{command}" %U
Icon={icon}
Terminal=false
Categories=Development;
StartupNotify=true
'''


def install(args):
    archive, manifest = validate_bundle(args.bundle_dir)
    for name in ("config.toml", "model-catalog.json", "model-instructions.md"):
        if not (args.assets_dir / name).is_file():
            raise ValueError(f"missing rendered GUI asset: {name}")
    config = args.gui_home / "config.toml"
    rendered = (args.assets_dir / "config.toml").read_text()
    tomllib.loads(rendered)
    merged = merge_gui_config(config.read_text(), rendered) if config.exists() else rendered
    gui_root = args.runtime_root / "gui"
    release = gui_root / "releases" / (manifest["version"] + "-" + manifest["sha256"][:12])
    executable = release / "usr/lib/chatgpt/ChatGPT"
    profile_name = "codex-local-gui-" + hashlib.sha256(str(executable).encode()).hexdigest()[:16]
    profiles = {
        profile_name: apparmor_profile(executable, profile_name),
        profile_name + '-backend': apparmor_profile(release / 'usr/lib/chatgpt/resources/codex', profile_name + '-backend'),
    }
    if args.dry_run:
        print(f"Native GUI bundle verified: {manifest['version']}; runtime: {release}; home: {args.gui_home}")
        return
    dependencies = run("dpkg-deb", "-f", str(archive), "Depends", capture_output=True).stdout.strip()
    # APT handles Ubuntu's t64 Provides aliases and package alternatives without
    # requiring dpkg-dev on a fresh desktop. Simulation needs no elevation.
    result = subprocess.run(["apt-get", "--simulate", "satisfy", "--no-install-recommends", "--no-remove", dependencies], capture_output=True, text=True)
    if result.returncode or any(line.startswith("Inst ") for line in result.stdout.splitlines()):
        run("sudo", "apt-get", "update")
        run("sudo", "apt-get", "satisfy", "--no-install-recommends", "--no-remove", "-y", dependencies)
    gui_root.mkdir(parents=True, exist_ok=True)
    release.parent.mkdir(exist_ok=True)
    if not release.exists():
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=release.parent))
        try:
            run("dpkg-deb", "-x", str(archive), str(stage))
            if not os.access(stage / "usr/lib/chatgpt/ChatGPT", os.X_OK):
                raise ValueError("GUI bundle lacks its executable")
            if not (stage / "usr/lib/chatgpt/resources/app.asar").is_file():
                raise ValueError("GUI bundle lacks its application archive")
            if not os.access(stage / "usr/lib/chatgpt/resources/codex", os.X_OK):
                raise ValueError("GUI bundle lacks its backend executable")
            stage.rename(release)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if (not os.access(executable, os.X_OK)
            or not os.access(release / "usr/lib/chatgpt/resources/codex", os.X_OK)
            or not (release / "usr/lib/chatgpt/resources/app.asar").is_file()):
        raise ValueError("installed GUI release is incomplete; remove it and rerun")
    # Verify dynamically linked dependencies before changing the active application.
    linked = run("ldd", str(executable), capture_output=True)
    if "not found" in linked.stdout:
        raise ValueError("GUI executable has unresolved shared libraries: " + linked.stdout)
    # Only restricted kernels need these user-namespace allowances (as for the CLI).
    if RESTRICTION.exists() and RESTRICTION.read_text().strip() == "1":
        with tempfile.TemporaryDirectory(prefix="codex-local-apparmor-") as directory:
            for name, profile in profiles.items():
                source = Path(directory) / name
                source.write_text(profile)
                destination = "/etc/apparmor.d/" + name
                run("sudo", "install", "-m", "0644", str(source), destination)
                run("sudo", "apparmor_parser", "-r", destination)
    args.gui_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    launcher = args.home / ".local/bin/codex-local-gui"
    desktop = args.home / ".local/share/applications/codex-local.desktop"
    current = gui_root / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("GUI current path is not a managed symlink")
    writes = {
        args.gui_home / "model-catalog.json": ((args.assets_dir / "model-catalog.json").read_bytes(), 0o600),
        args.gui_home / "model-instructions.md": ((args.assets_dir / "model-instructions.md").read_bytes(), 0o600),
        launcher: (launcher_text(args.runtime_root, args.gui_home).encode(), 0o755),
        desktop: (desktop_text(launcher, gui_root / "current/usr/lib/chatgpt/resources/icon-chatgpt.png").encode(), 0o644),
    }
    writes[config] = (merged.encode(), 0o600)
    compatibility = args.assets_dir / "compatibility.json"
    if compatibility.exists():
        writes[args.gui_home / "compatibility.json"] = (compatibility.read_bytes(), 0o600)
    previous = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None for p in writes}
    old_target = os.readlink(current) if current.is_symlink() else None
    next_link = gui_root / (".current-" + str(os.getpid()))
    try:
        for path, (contents, mode) in writes.items():
            atomic_write(path, contents, mode)
        next_link.symlink_to(Path("releases") / release.name)
        os.replace(next_link, current)
    except BaseException:
        for path, value in previous.items():
            if value is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *value)
        # os.replace is the last operation; rollback also covers an interrupted return.
        if old_target is not None:
            next_link.unlink(missing_ok=True)
            next_link.symlink_to(old_target)
            os.replace(next_link, current)
        elif current.is_symlink():
            current.unlink()
        raise
    finally:
        next_link.unlink(missing_ok=True)
    print(f"Installed Codex Local GUI. Launch with: {launcher}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("assets-dir", "runtime-root", "gui-home"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, default=Path(__file__).resolve().parents[1] / "bundles/gui")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.home = Path.home()
    for name in ("assets_dir", "runtime_root", "gui_home", "bundle_dir"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    try:
        install(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
