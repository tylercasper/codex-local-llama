import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from codex_local_provider.compatibility import load_manifest
from test_compatibility import make_package
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("native_gui", Path(__file__).resolve().parents[1] / "scripts/install_native_gui.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("config.toml", "model-catalog.json", "model-instructions.md"):
        (assets / name).write_text("rendered " + name)
    (assets / "config.toml").write_text('model = "original"\nmodel_provider = "llamacpp"\n[model_providers.llamacpp]\nbase_url = "http://old/v1"\n')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive = bundle / "chatgpt_amd64.deb"
    archive.write_bytes(b"test fixture")
    manifest = dict(asset=archive.name, package="chatgpt", version="1.0", architecture="amd64", sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    (bundle / "linux.json").write_text(json.dumps(manifest))
    calls = []
    def run(*cmd, **kwargs):
        calls.append(cmd)
        if cmd[0:2] == ("dpkg-deb", "-f"):
            value = {"Package": "chatgpt", "Version": "1.0", "Architecture": "amd64", "Depends": "libgtk-3-0"}[cmd[-1]]
            return subprocess.CompletedProcess(cmd, 0, value, "")
        if cmd[0:2] == ("dpkg-deb", "-x"):
            root = Path(cmd[-1]) / "usr/lib/chatgpt"
            (root / "resources").mkdir(parents=True)
            (root / "ChatGPT").write_text("executable")
            (root / "ChatGPT").chmod(0o755)
            (root / "resources/app.asar").write_text("archive")
            (root / "resources/codex").write_text("backend")
            (root / "resources/codex").chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "", ""))
    args = argparse.Namespace(assets_dir=assets, runtime_root=tmp_path / "runtime", gui_home=tmp_path / "gui-home", home=tmp_path / "home", bundle_dir=bundle, dry_run=False)
    pin = load_manifest(Path(__file__).resolve().parents[1])['codex']
    package = make_package(tmp_path / 'source-package', {'codex': pin})
    (assets / 'codex-local-backend.json').write_text(json.dumps({
        'schema_version': 1, 'backend': 'source', 'package': str(package),
        'source': pin, 'frontend_backend_version': pin['version'],
    }))
    return args, calls


def test_corrupt_bundle_leaves_system_untouched(deployment):
    args, calls = deployment
    (args.bundle_dir / "chatgpt_amd64.deb").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        module.install(args)
    assert not args.runtime_root.exists()
    assert not args.gui_home.exists()
    assert calls == []


def test_dry_run_validates_without_mutations(deployment):
    args, calls = deployment
    args.dry_run = True
    module.install(args)
    assert not args.runtime_root.exists()
    assert not args.gui_home.exists()
    assert all(c[:2] == ("dpkg-deb", "-f") for c in calls)


def test_install_and_rerun_preserve_user_data(deployment):
    args, calls = deployment
    module.install(args)
    config = args.gui_home / "config.toml"
    config.write_text('model = "original"\ncustom_setting = "user setting"\n')
    history = args.gui_home / "state.sqlite"
    history.write_text("history")
    (args.assets_dir / "model-instructions.md").write_text("updated")
    module.install(args)
    assert 'custom_setting = "user setting"' in config.read_text()
    assert history.read_text() == "history"
    assert (args.gui_home / "model-instructions.md").read_text() == "updated"
    assert len([c for c in calls if c[:2] == ("dpkg-deb", "-x")]) == 1
    launcher = (args.home / ".local/bin/codex-local-gui").read_text()
    assert "CODEX_HOME=" in launcher
    assert "CODEX_CLI_PATH=" in launcher
    assert "CODEX_APP_SERVER_FORCE_CLI=1" in launcher
    assert "unset CODEX_APP_SERVER_WS_URL CODEX_APP_SERVER_USE_LOCAL_DAEMON" in launcher
    assert "CODEX_ELECTRON_USER_DATA_PATH=" in launcher
    assert "--no-sandbox" not in launcher
    assert str(args.assets_dir) not in launcher


def test_activation_failure_restores_managed_assets(deployment, monkeypatch):
    args, _ = deployment
    module.install(args)
    current = args.runtime_root / "gui/current"
    old_target = current.readlink()
    original = (args.gui_home / "model-instructions.md").read_bytes()
    (args.assets_dir / "model-instructions.md").write_text("replacement")
    original_replace = module.os.replace
    failed = False
    def replace(source, target):
        nonlocal failed
        if Path(target) == current and not failed:
            failed = True
            raise OSError("activation failure")
        original_replace(source, target)
    monkeypatch.setattr(module.os, "replace", replace)
    with pytest.raises(OSError, match="activation failure"):
        module.install(args)
    assert current.readlink() == old_target
    assert (args.gui_home / "model-instructions.md").read_bytes() == original


def test_missing_asset_and_unsafe_apparmor_paths(deployment):
    args, calls = deployment
    (args.assets_dir / "config.toml").unlink()
    with pytest.raises(ValueError, match="missing rendered"):
        module.install(args)
    assert not args.runtime_root.exists()
    with pytest.raises(ValueError, match="AppArmor"):
        module.apparmor_profile(Path('/tmp/*/ChatGPT'), "test")


def test_extraction_failure_does_not_activate_partial_release(deployment, monkeypatch):
    args, _ = deployment
    original_run = module.run
    def run(*cmd, **kwargs):
        if cmd[:2] == ("dpkg-deb", "-x"):
            raise subprocess.CalledProcessError(2, cmd)
        return original_run(*cmd, **kwargs)
    monkeypatch.setattr(module, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        module.install(args)
    assert not (args.runtime_root / "gui/current").exists()
    assert not list((args.runtime_root / "gui/releases").iterdir())
    assert not args.gui_home.exists()


def test_missing_dependencies_install_before_extraction(deployment, monkeypatch):
    args, calls = deployment
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "missing"))
    module.install(args)
    satisfy = next(i for i, c in enumerate(calls) if c[:3] == ("sudo", "apt-get", "satisfy"))
    extract = next(i for i, c in enumerate(calls) if c[:2] == ("dpkg-deb", "-x"))
    assert satisfy < extract
    assert calls[satisfy][-1] == "libgtk-3-0"


def test_refresh_connection_preserves_user_preferences(deployment):
    import tomllib
    args, _ = deployment
    module.install(args)
    config = args.gui_home / "config.toml"
    config.write_text(config.read_text() + '\n[projects."/srv/project"]\ntrust_level = "trusted"\n')
    (args.assets_dir / "config.toml").write_text('model = "new-model"\nmodel_provider = "llamacpp"\n[model_providers.llamacpp]\nbase_url = "http://new:8090/v1"\nrequires_openai_auth = false\n')
    module.install(args)
    result = tomllib.loads(config.read_text())
    assert result["model"] == "new-model"
    assert result["model_providers"]["llamacpp"]["base_url"] == "http://new:8090/v1"
    assert result["projects"]["/srv/project"]["trust_level"] == "trusted"


def test_fresh_install_preserves_rendered_defaults(deployment):
    import tomllib
    args, _ = deployment
    config = args.assets_dir / "config.toml"
    config.write_text('sandbox_mode = "workspace-write"\n' + config.read_text() + '\n[features]\nexample = true\n')
    module.install(args)
    result = tomllib.loads((args.gui_home / "config.toml").read_text())
    assert result["sandbox_mode"] == "workspace-write"
    assert result["features"]["example"] is True


def test_merge_missing_trailing_newline_and_quoted_provider():
    import tomllib
    from merge_gui_config import merge_gui_config
    existing = 'unrelated = "keep"\n[model_providers."llamacpp"]\nname = "keep name"'
    rendered = 'model = "new"\n[model_providers.llamacpp]\nbase_url = "http://new/v1"\n'
    merged = tomllib.loads(merge_gui_config(existing, rendered))
    assert merged["unrelated"] == "keep"
    assert merged["model"] == "new"
    assert merged["model_providers"]["llamacpp"] == {"name": "keep name", "base_url": "http://new/v1"}


@pytest.mark.parametrize("restriction", [None, "0", "1"])
def test_gui_profiles_only_on_restricted_kernels(deployment, monkeypatch, tmp_path, restriction):
    args, calls = deployment
    setting = tmp_path / "userns-restriction"
    if restriction is not None:
        setting.write_text(restriction)
    monkeypatch.setattr(module, "RESTRICTION", setting)
    module.install(args)
    loads = [c for c in calls if c[:3] == ("sudo", "apparmor_parser", "-r")]
    assert len(loads) == (2 if restriction == "1" else 0)
    assert (args.home / ".local/bin/codex-local-gui").exists()


def test_required_profile_failure_prevents_activation(deployment, monkeypatch, tmp_path):
    args, _ = deployment
    setting = tmp_path / "userns-restriction"
    setting.write_text("1")
    monkeypatch.setattr(module, "RESTRICTION", setting)
    original_run = module.run
    def run(*cmd, **kwargs):
        if cmd[:3] == ("sudo", "apparmor_parser", "-r"):
            raise subprocess.CalledProcessError(1, cmd)
        return original_run(*cmd, **kwargs)
    monkeypatch.setattr(module, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        module.install(args)
    assert not (args.runtime_root / "gui/current").exists()
    assert not (args.home / ".local/bin/codex-local-gui").exists()


@pytest.mark.parametrize('display,wayland,expected', [('', '', ':0'), (':10', '', ':10'), ('', 'wayland-1', '')])
def test_launcher_wslg_fallback_preserves_desktop_session(tmp_path, display, wayland, expected):
    import os
    import socket
    runtime = tmp_path / 'runtime'
    home = tmp_path / 'home'
    home.mkdir()
    executable = runtime / 'gui/current/usr/lib/chatgpt/ChatGPT'
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$DISPLAY" "$WAYLAND_DISPLAY" "$DBUS_SESSION_BUS_ADDRESS"\n')
    executable.chmod(0o755)
    with socket.socket(socket.AF_UNIX) as sock:
        socket_path = tmp_path / 'X0'
        sock.bind(str(socket_path))
        script = module.launcher_text(runtime, home, tmp_path / "desktop-data")
        script = script.replace('/mnt/wslg/.X11-unix/X0', str(socket_path)).replace('/tmp/.X11-unix/X0', str(socket_path))
        script = script.replace('/run/user/$(id -u)/bus', str(socket_path))
        env = dict(os.environ, DISPLAY=display, WAYLAND_DISPLAY=wayland, DBUS_SESSION_BUS_ADDRESS='invalid')
        result = subprocess.run(['sh', '-c', script], env=env, text=True, capture_output=True, check=True)
        lines = result.stdout.splitlines()
        assert lines[:2] == [expected, wayland]
        assert lines[2] == (f'unix:path={socket_path}' if not display and not wayland else 'invalid')


def test_launcher_follows_shared_home_and_keeps_desktop_data_separate(tmp_path):
    runtime = tmp_path / 'runtime'
    binary = runtime / 'gui/current/usr/lib/chatgpt/ChatGPT'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_HOME" "$CODEX_ELECTRON_USER_DATA_PATH" "$@"\n')
    binary.chmod(0o755)
    shared = tmp_path / 'shared home'
    shared.mkdir()
    user_data = tmp_path / 'desktop data'
    (runtime / 'codex-home').write_text(str(shared) + '\n')
    launcher = tmp_path / 'launcher'
    launcher.write_text(module.launcher_text(runtime, tmp_path / 'stale home', user_data))
    env = {k:v for k,v in os.environ.items() if k != 'CODEX_LOCAL_HOME'}
    result = subprocess.run(['sh', str(launcher)], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines()[:2] == [str(shared), str(user_data)]
    override = tmp_path / 'explicit home'
    override.mkdir()
    env['CODEX_LOCAL_HOME'] = str(override)
    result = subprocess.run(['sh', str(launcher)], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines()[:2] == [str(override), str(user_data)]
