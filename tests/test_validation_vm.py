import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    'validation_vm', Path(__file__).parents[1] / 'scripts/validation/qemu-ubuntu.py')
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


def test_low_disk_reserve_pauses_guest(monkeypatch):
    commands = []
    monkeypatch.setattr(vm, 'live_status', lambda: {'status': 'running'})
    monkeypatch.setattr(vm, 'storage_state', lambda: {'safe': False})
    monkeypatch.setattr(vm, 'qmp', lambda command: commands.append(command))
    with pytest.raises(RuntimeError, match='Guest paused'):
        vm.monitor()
    assert commands and set(commands) == {'stop'}


def test_monitor_interruption_pauses_guest(monkeypatch):
    commands = []
    monkeypatch.setattr(vm, 'live_status', lambda: {'status': 'running'})
    monkeypatch.setattr(vm, 'storage_state', lambda: {'safe': True})
    monkeypatch.setattr(vm, 'qmp', lambda command: commands.append(command))
    def interrupt(_seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(vm.time, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        vm.monitor()
    assert commands == ['stop']


def test_reserves_and_budget_are_enforced(monkeypatch, tmp_path):
    monkeypatch.setattr(vm, 'ROOT', tmp_path)
    class Usage:
        free = 101 * vm.GIB
    monkeypatch.setattr(vm.shutil, 'disk_usage', lambda _path: Usage())
    assert vm.storage_state()['safe']
    # Sparse file: checks logical usage without allocating a huge test artifact.
    with (tmp_path / 'disk').open('wb') as stream:
        stream.truncate(64 * vm.GIB)
    assert not vm.storage_state()['safe']
    (tmp_path / 'disk').unlink()
    Usage.free = 49 * vm.GIB
    assert not vm.storage_state()['safe']


def test_extra_volume_reserve_is_enforced(monkeypatch, tmp_path):
    monkeypatch.setattr(vm, 'ROOT', tmp_path)
    extra = tmp_path / 'host-volume'
    extra.mkdir()
    monkeypatch.setattr(vm, 'EXTRA_RESERVES', [(extra, 50)])
    class Usage:
        def __init__(self, free):
            self.free = free
    monkeypatch.setattr(vm.shutil, 'disk_usage',
                        lambda path: Usage((49 if path == extra else 200) * vm.GIB))
    assert not vm.storage_state()['safe']


@pytest.mark.skipif(not shutil.which('qemu-img'), reason='qemu-img unavailable')
def test_offline_baseline_reset_preserves_pristine_disk(monkeypatch, tmp_path):
    disk = tmp_path / 'ubuntu.qcow2'
    baseline = tmp_path / 'pristine.qcow2'
    monkeypatch.setattr(vm, 'ROOT', tmp_path)
    monkeypatch.setattr(vm, 'DISK', disk)
    monkeypatch.setattr(vm, 'BASELINE', baseline)
    monkeypatch.setattr(vm, 'live_status', lambda: None)
    class Usage:
        free = 200 * vm.GIB
    monkeypatch.setattr(vm.shutil, 'disk_usage', lambda _path: Usage())
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '64M'], check=True)
    vm.offline_copy('baseline')
    pristine = baseline.read_bytes()
    with pytest.raises(RuntimeError, match='refusing to overwrite'):
        vm.offline_copy('baseline')
    subprocess.run(['qemu-img', 'resize', str(disk), '128M'], check=True)
    vm.offline_copy('reset')
    assert baseline.read_bytes() == pristine
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(disk)]))
    assert info['virtual-size'] == 64 * 1024 ** 2
    monkeypatch.setattr(vm, 'live_status', lambda: {'status': 'running'})
    with pytest.raises(RuntimeError, match='Power off'):
        vm.offline_copy('reset')
