from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from codex_local_provider.deployment import render_gui_assets, render_install_assets


ROOT = Path(__file__).resolve().parents[1]


def test_gui_assets_reuse_deployment_without_installed_home(tmp_path):
    assets = tmp_path / 'assets'
    home = tmp_path / 'new user'
    # Service runtime paths intentionally cannot contain spaces.
    render_install_assets(
        repository_root=ROOT, deployment_path=ROOT / 'config/deployment.toml',
        release_path=ROOT / 'config/codex-release.json', output_dir=assets,
        home=tmp_path / 'alice', user='alice', runtime_root=tmp_path / 'runtime',
        ssh_path=Path('/usr/bin/ssh'), source_build=True,
    )
    destination = home / 'gui'
    render_gui_assets(assets, destination)
    config = tomllib.loads((destination / 'config.toml').read_text())
    source = tomllib.loads((assets / 'local.config.toml').read_text())
    assert config['model'] == source['model']
    assert config['model_providers'] == source['model_providers']
    assert config['model_catalog_json'] == 'model-catalog.json'
    assert config['model_instructions_file'] == 'model-instructions.md'
    assert config['sandbox_mode'] == 'workspace-write'
    binding = json.loads((destination / 'codex-local-backend.json').read_text())
    assert binding['package'] == str(tmp_path / 'runtime/codex/current')
    assert binding['source']['version'] == binding['frontend_backend_version']
    assert json.loads((destination / 'model-catalog.json').read_text()) == json.loads(
        (assets / 'model-catalog.json').read_text())
    assert (destination / 'model-instructions.md').read_bytes() == (
        assets / 'model-instructions.md').read_bytes()


@pytest.fixture
def installer_repo(tmp_path):
    repo = tmp_path / 'repo'
    for directory in ('config', 'prompts', 'src', 'systemd'):
        shutil.copytree(ROOT / directory, repo / directory, ignore=shutil.ignore_patterns('__pycache__'))
    (repo / 'bundles/gui').mkdir(parents=True)
    for manifest in ('linux.json', 'windows.json'):
        shutil.copyfile(ROOT / 'bundles/gui' / manifest, repo / 'bundles/gui' / manifest)
    (repo / 'scripts').mkdir()
    shutil.copyfile(ROOT / 'scripts/install-local.sh', repo / 'scripts/install-local.sh')
    for name in ('native', 'wsl'):
        helper = repo / 'scripts' / f'install-{name}-gui.sh'
        helper.write_text(f'#!/bin/bash\necho selected-{name}\nprintf "%s\\n" "$@"\n')
    return repo


@pytest.mark.parametrize('flag,expected', [(None, None), ('--gui', 'native'), ('--wsl-gui', 'wsl')])
def test_installer_routes_gui_flags_in_dry_run(installer_repo, tmp_path, flag, expected):
    command = ['bash', str(installer_repo / 'scripts/install-local.sh'), '--dry-run']
    if flag:
        command.append(flag)
    if flag == '--wsl-gui':
        command.extend(['--codex-home', '/mnt/c/Users/test/.codex-local'])
    result = subprocess.run(command, env={**os.environ, 'HOME': str(tmp_path / 'home')},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Dry run complete' in result.stdout
    for mode in ('native', 'wsl'):
        assert (f'selected-{mode}' in result.stdout) == (expected == mode)
    assert not (tmp_path / 'home').exists()


def test_gui_modes_are_mutually_exclusive():
    result = subprocess.run(['bash', str(ROOT / 'scripts/install-local.sh'), '--gui', '--wsl-gui'],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'mutually exclusive' in result.stderr


def test_invalid_gui_bundle_stops_before_deployment(installer_repo):
    (installer_repo / 'scripts/install-native-gui.sh').write_text('exit 17\n')
    result = subprocess.run(['bash', str(installer_repo / 'scripts/install-local.sh'), '--gui'],
                            capture_output=True, text=True)
    assert result.returncode == 17
