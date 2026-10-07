import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gui_backend import backend_path, load_binding, sqlite_home, validate_install
from merge_gui_config import merge_gui_config
from codex_local_provider.compatibility import load_manifest, package_fingerprints
from test_compatibility import make_package


@pytest.fixture
def installed(tmp_path):
    pin = load_manifest(ROOT)['codex']
    package = make_package(tmp_path / 'release', {'codex': pin})
    current = tmp_path / 'current'
    current.symlink_to(package)
    shim = tmp_path / 'shim'
    shim.mkdir()
    for name in ('codex-local-gui-wsl', 'gui_backend.py'):
        shutil.copy2(ROOT / 'scripts' / name, shim / name)
    binding = shim / 'codex-local-backend.json'
    binding.write_text(json.dumps({'schema_version': 1, 'backend': 'source',
        'package': str(current), 'source': pin, 'frontend_backend_version': pin['version']}))
    return binding, package, current


def test_binding_validates_package_and_rejects_changed_cli(installed, tmp_path):
    binding, package, current = installed
    assert backend_path(validate_install(binding)) == package / 'bin/codex'
    record = json.loads((package / 'codex-local-build.json').read_text())
    record['source']['revision'] = '0' * 40
    other = make_package(tmp_path / 'other', {'codex': record['source']})
    current.unlink()
    current.symlink_to(other)
    with pytest.raises(ValueError, match='source pin'):
        backend_path(load_binding(binding))


def test_preflight_accepts_unbuilt_package_but_real_install_checks_fingerprints(installed):
    binding, package, _ = installed
    (package / 'bin/codex').write_text('damaged')
    validate_install(binding, dry_run=True)
    with pytest.raises(ValueError, match='fingerprints'):
        validate_install(binding)
    binding.unlink()
    with pytest.raises(FileNotFoundError):
        load_binding(binding)


def test_gui_version_mismatch_is_rejected(installed):
    binding, _, _ = installed
    value = json.loads(binding.read_text())
    value['frontend_backend_version'] = 'different'
    binding.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='versions differ'):
        validate_install(binding, dry_run=True)


def test_existing_database_and_desktop_state_survive_refresh_and_launch(installed, tmp_path):
    binding, package, _ = installed
    home = tmp_path / 'home'
    home.mkdir()
    database = tmp_path / 'existing-database'
    database.mkdir()
    history = database / 'state_5.sqlite'
    history.write_bytes(b'existing conversation history')
    state = home / '.codex-global-state.json'
    state.write_text('{"onboarding":"complete","projects":["keep"]}')
    original = 'sqlite_home = ' + json.dumps(str(database)) + '\nmodel = "old"\n'
    rendered = 'sqlite_home = "/different/empty/database"\nmodel = "new"\n'
    (home / 'config.toml').write_text(merge_gui_config(original, rendered))
    assert sqlite_home(home) == database
    binary = package / 'bin/codex'
    binary.write_text('#!/usr/bin/python3\nimport os,json,sys\nprint(json.dumps({"home":os.environ["CODEX_HOME"],"sqlite":os.environ["CODEX_SQLITE_HOME"],"args":sys.argv[1:]}))\n')
    env = dict(os.environ, CODEX_HOME=str(home), CODEX_SQLITE_HOME='/wrong/inherited/location')
    result = subprocess.run([str(binding.parent / 'codex-local-gui-wsl'), '--version'],
                            env=env, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {'home': str(home), 'sqlite': str(database), 'args': ['--version']}
    assert history.read_bytes() == b'existing conversation history'
    assert json.loads(state.read_text())['projects'] == ['keep']


def test_official_fallback_cannot_install_mismatched_gui():
    result = subprocess.run(['bash', str(ROOT / 'scripts/install-local.sh'), '--gui', '--official-release', '--dry-run'], text=True, capture_output=True)
    assert result.returncode != 0
    assert 'matching source backend' in result.stderr


def test_explicit_frontend_pairing_rejects_a_different_archive_or_source(installed):
    binding, _, _ = installed
    value = json.loads(binding.read_text())
    value['schema_version'] = 2
    value.pop('frontend_backend_version')
    manifest = {'version': 'vendor-version', 'sha256': 'a' * 64}
    value['frontends'] = {'linux': {'package': manifest,
        'bundled_backend_version': 'different-vendor-backend',
        'source_revision': value['source']['revision']}}
    binding.write_text(json.dumps(value))
    assert validate_install(binding, True, ('linux', manifest)) == value
    with pytest.raises(ValueError, match='archive differs'):
        validate_install(binding, True, ('linux', {**manifest, 'sha256': 'b' * 64}))
    value['frontends']['linux']['source_revision'] = '0' * 40
    binding.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='source pin'):
        validate_install(binding, True, ('linux', manifest))
