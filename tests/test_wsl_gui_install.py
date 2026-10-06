"""Verify the shipped bundle without installing or launching the Windows GUI."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_windows_bundle_integrity_and_clean_members():
    manifest = json.loads((ROOT / 'bundles/gui/windows.json').read_text())
    bundle_dir = Path(os.environ.get('CODEX_LOCAL_GUI_BUNDLE_DIR', ROOT / 'bundles/gui'))
    archive = bundle_dir / manifest['asset']
    if not archive.exists():
        pytest.skip('Optional vendor archive absent; set CODEX_LOCAL_GUI_BUNDLE_DIR to verify it')
    with archive.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == manifest['sha256']
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
    names = {entry.name.removeprefix('./') for entry in members}
    assert {'ChatGPT.exe', 'resources/codex', 'resources/app.asar'} <= names
    assert not {'Launch-CodexLocal.ps1', 'codex-local-install.json', 'resources/codex-local-gui-wsl'} & names
    assert all(not Path(entry.name).is_absolute() and '..' not in Path(entry.name).parts for entry in members)
    assert archive.stat().st_size < 1024 ** 3


def test_wsl_helper_unknown_option():
    result = subprocess.run([str(ROOT / 'scripts/install-wsl-gui.sh'), '--bad-option'], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Unknown WSL GUI option' in result.stderr


def test_wsl_helper_requires_arguments():
    result = subprocess.run([str(ROOT / 'scripts/install-wsl-gui.sh')], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Required:' in result.stderr
