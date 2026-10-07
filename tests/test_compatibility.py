import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from codex_local_provider.compatibility import (
    BUILD_RECORD, REQUIRED_FILES, load_manifest, package_fingerprints,
    repository_path, resolve_manifest, validate_source_package, verify_source,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_builder', ROOT / 'scripts/build-codex.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def commit(root):
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-qm', 'fixture')


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    for path in ('config/compatibility.json', 'bundles/gui/linux.json', 'bundles/gui/windows.json'):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    source = root / 'vendor/codex'
    (source / 'codex-rs').mkdir(parents=True)
    git(source, 'init', '-q')
    (source / 'codex-rs/Cargo.toml').write_text('[workspace.package]\nversion = ' + json.dumps(load_manifest(root)['codex']['version']) + '\n')
    (source / 'codex-rs/rust-toolchain.toml').write_text('[toolchain]\nchannel = "1.95.0"\n')
    (source / 'codex-rs/Cargo.lock').write_text('version = 4\n[[package]]\nname = "local"\nversion = "0.0.0"\n')
    commit(source)
    manifest = load_manifest(root)
    manifest['codex']['revision'] = git(source, 'rev-parse', 'HEAD')
    for desktop in manifest['desktop'].values():
        if 'source_revision' in desktop:
            desktop['source_revision'] = manifest['codex']['revision']
    (root / 'config/compatibility.json').write_text(json.dumps(manifest))
    git(root, 'update-index', '--add', '--cacheinfo',
        f'160000,{manifest["codex"]["revision"]},vendor/codex')
    commit(root)
    return root, manifest, source


def test_source_identity_rejects_wrong_gitlink_and_tracked_edits(repository):
    root, manifest, source = repository
    assert verify_source(root, manifest) == source
    (source / 'codex-rs/Cargo.toml').write_text('[workspace.package]\nversion = "other"\n')
    with pytest.raises(ValueError, match='tracked modifications'):
        verify_source(root, manifest)
    git(source, 'restore', 'codex-rs/Cargo.toml')
    git(root, 'update-index', '--cacheinfo', '160000,' + '0' * 39 + '1,vendor/codex')
    with pytest.raises(ValueError, match='gitlink'):
        verify_source(root, manifest)


def test_resolved_manifest_captures_frontend_hashes_and_parent_revision(repository):
    root, manifest, _ = repository
    resolved = resolve_manifest(root)
    assert resolved['codex'] == manifest['codex']
    assert resolved['harness'] == {'revision': git(root, 'rev-parse', 'HEAD'), 'tracked_tree_dirty': False}
    assert resolved['desktop']['windows_wsl']['package'] == json.loads(
        (root / 'bundles/gui/windows.json').read_text())


def make_package(directory, manifest):
    for name in REQUIRED_FILES:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\necho codex-cli ' + manifest['codex']['version'] + '\n')
        path.chmod(0o755)
    (directory / 'codex-package.json').write_text(json.dumps({
        'layoutVersion': 1, 'version': manifest['codex']['version'],
        'target': manifest['codex']['target'], 'variant': 'codex',
        'entrypoint': 'bin/codex', 'resourcesDir': 'codex-resources', 'pathDir': 'codex-path',
    }))
    record = {'schema_version': 1, 'source': manifest['codex'],
              'compatibility': manifest, 'files': package_fingerprints(directory)}
    (directory / BUILD_RECORD).write_text(json.dumps(record))
    return directory


@pytest.mark.parametrize('damage', ['binary', 'mode', 'extra-file', 'source', 'target', 'symlink'])
def test_source_package_rejects_tampering(repository, tmp_path, damage):
    _, manifest, _ = repository
    package = make_package(tmp_path / 'package', manifest)
    assert validate_source_package(package, manifest)['source'] == manifest['codex']
    binary = package / 'bin/codex'
    if damage == 'binary':
        binary.write_text('changed')
    elif damage == 'mode':
        binary.chmod(0o644)
    elif damage == 'extra-file':
        (package / 'unexpected').write_text('changed')
    elif damage == 'source':
        record = json.loads((package / BUILD_RECORD).read_text())
        record['source']['revision'] = '0' * 40
        (package / BUILD_RECORD).write_text(json.dumps(record))
    elif damage == 'target':
        metadata = json.loads((package / 'codex-package.json').read_text())
        metadata['target'] = 'wrong-target'
        (package / 'codex-package.json').write_text(json.dumps(metadata))
    else:
        (package / 'unexpected').symlink_to(binary)
    with pytest.raises(ValueError):
        validate_source_package(package, manifest)


def test_release_lock_normalization_never_updates_external_dependencies():
    original = b'''version = 4
[[package]]
name = "local"
version = "0.0.0"
dependencies = ["external"]
[[package]]
name = "external"
version = "0.0.0"
source = "registry+https://example.invalid"
checksum = "unchanged"
'''
    result = builder.normalized_release_lock(original, '0.147.0')
    expected = tomllib.loads(original.decode())
    expected['package'][0]['version'] = '0.147.0'
    assert tomllib.loads(result.decode()) == expected
    assert builder.normalized_release_lock(result, '0.147.0') == result


def test_manifest_paths_cannot_escape_the_checkout(tmp_path):
    with pytest.raises(ValueError, match='within the repository'):
        repository_path(tmp_path, '../outside.json')


def test_installer_default_is_source_and_official_fallback_is_explicit():
    command = ['bash', str(ROOT / 'scripts/install-local.sh'), '--dry-run']
    source = subprocess.run(command, capture_output=True, text=True, check=True)
    assert 'pinned submodule' in source.stdout
    assert 'x86_64-unknown-linux-gnu' in source.stdout
    official = subprocess.run([*command, '--official-release'], capture_output=True, text=True, check=True)
    assert 'pinned official release download' in official.stdout
    assert 'x86_64-unknown-linux-musl' in official.stdout


@pytest.mark.parametrize('fail_build', [False, True])
def test_build_restores_source_lock_and_only_publishes_complete_packages(repository, tmp_path, monkeypatch, fail_build):
    root, manifest, source = repository
    output = tmp_path / 'output'
    lockfile = source / 'codex-rs/Cargo.lock'
    original = lockfile.read_bytes()
    real_output = subprocess.check_output
    real_run = subprocess.run
    builds = []

    def check_output(command, **kwargs):
        if command == ['rustc', '--version']:
            return 'rustc 1.95.0 (test)\n'
        return real_output(command, **kwargs)

    def run(command, **kwargs):
        if len(command) > 1 and str(command[1]).endswith('scripts/build_codex_package.py'):
            builds.append(command)
            assert kwargs['env']['CODEX_REPO_ROOT'] == str(source)
            assert lockfile.read_bytes() != original
            assert str(command[command.index('--cargo') + 1]).endswith('scripts/cargo-locked')
            if fail_build:
                raise subprocess.CalledProcessError(1, command)
            make_package(Path(command[command.index('--package-dir') + 1]), manifest)
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(builder, 'ROOT', root)
    monkeypatch.setattr(builder.sys, 'argv', ['build-codex.py', '--output', str(output)])
    monkeypatch.setattr(builder.subprocess, 'check_output', check_output)
    monkeypatch.setattr(builder.subprocess, 'run', run)
    if fail_build:
        with pytest.raises(subprocess.CalledProcessError):
            builder.main()
        assert not output.exists()
    else:
        builder.main()
        validate_source_package(output, manifest)
        builder.main()
        assert len(builds) == 1
    assert lockfile.read_bytes() == original
    assert git(source, 'status', '--porcelain') == ''
    assert not list(tmp_path.glob('.source-package-*'))


def test_different_bundled_backend_requires_the_exact_explicit_pairing(repository):
    root, manifest, _ = repository
    desktop = manifest['desktop']['linux']
    desktop['bundled_backend_version'] = 'different-vendor-version'
    desktop.pop('source_revision', None)
    path = root / 'config/compatibility.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='explicit source pairing'):
        load_manifest(root)
    desktop['source_revision'] = manifest['codex']['revision']
    path.write_text(json.dumps(manifest))
    assert load_manifest(root) == manifest
    desktop['source_revision'] = '0' * 40
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='pairing differs'):
        load_manifest(root)
