import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'cli_apparmor', Path(__file__).parents[1] / 'scripts/install_cli_apparmor.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_profile_targets_resolved_private_executable(tmp_path):
    executable = tmp_path / 'releases/1/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.touch()
    link = tmp_path / 'current'
    link.symlink_to(executable.parent.parent, target_is_directory=True)
    name, profile = module.profile_for(link / 'bin/codex')
    assert f'"{executable}"' in profile
    assert 'userns,' in profile
    assert '/usr/bin/bwrap' not in profile
    assert name.startswith('codex-local-cli-')
    with pytest.raises(ValueError, match='unsupported'):
        module.profile_for(tmp_path / '*/codex')


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_only_restricted_kernels_install_profile(tmp_path, monkeypatch, enabled):
    restriction = tmp_path / 'restriction'
    restriction.write_text(enabled)
    executable = tmp_path / 'codex'
    executable.touch()
    monkeypatch.setattr(module, 'RESTRICTION', restriction)
    monkeypatch.setattr(module.shutil, 'which', lambda _: '/sbin/apparmor_parser')
    calls = []
    def run(command, **kwargs):
        if command[1] == 'install':
            assert f'"{executable}"' in Path(command[-2]).read_text()
        calls.append(command)
    monkeypatch.setattr(module.subprocess, 'run', run)
    module.install(executable)
    if enabled == '1':
        assert len(calls) == 2
        assert calls[1][:3] == ['sudo', 'apparmor_parser', '-r']
        assert calls[0][-1] == calls[1][-1]
    else:
        assert calls == []
