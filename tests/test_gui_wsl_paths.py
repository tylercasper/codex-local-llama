import runpy
from types import SimpleNamespace
from pathlib import Path


shim = SimpleNamespace(**runpy.run_path(
    str(Path(__file__).parents[1] / 'scripts' / 'codex-local-gui-wsl')))


def test_default_project_rpc_paths():
    for method in ('fs/createDirectory', 'fs/getMetadata'):
        message = {'id': 1, 'method': method, 'params': {
            'path': r'C:\Users\Example User\Documents\ChatGPT\Project', 'recursive': True}}
        result = shim.translate(message)
        assert result['params']['path'] == '/mnt/c/Users/Example User/Documents/ChatGPT/Project'
        assert result['params']['recursive'] is True
        assert result['id'] == 1


def test_only_protocol_path_fields_are_translated():
    message = {'method': 'turn/start', 'params': {'path': r'C:\file', 'input': r'C:\file'}}
    assert shim.translate(message) == message
    message = {'method': 'process/spawn', 'params': {'cwd': r'C:\repo', 'command': [r'C:\file']}}
    result = shim.translate(message)
    assert result['params'] == {'cwd': '/mnt/c/repo', 'command': [r'C:\file']}
    assert shim.linux_path('/srv/repos/project') == '/srv/repos/project'


def test_unc_only_for_current_distribution(monkeypatch):
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    assert shim.linux_path(r'\\wsl.localhost\Ubuntu\srv\repos') == '/srv/repos'
    other = r'\\wsl.localhost\Debian\srv\repos'
    assert shim.linux_path(other) == other


def test_project_registration_and_editing():
    for method in ('project/create', 'project/update'):
        message = {'method': method, 'params': {
            'name': 'Test', 'roots': [{'path': r'C:\Users\Example User\Documents\ChatGPT\Test'}],
            'metadata': {'description': r'C:\leave-this-unchanged'}}}
        result = shim.translate(message)
        assert result['params']['roots'] == [{'path': '/mnt/c/Users/Example User/Documents/ChatGPT/Test'}]
        assert result['params']['metadata'] == {'description': r'C:\leave-this-unchanged'}


def test_wrapper_isolates_sqlite(monkeypatch, tmp_path):
    import os
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'private-gui'))
    monkeypatch.setenv('CODEX_SQLITE_HOME', '/home/shared/.codex')
    monkeypatch.setattr(shim.sys, 'argv', ['codex-local-gui-wsl', '--version'])
    calls = []
    monkeypatch.setattr(os, 'execv', lambda backend, args: calls.append((backend, args)))
    # execv normally never returns. Stop at the attempted handoff.
    class Handoff(Exception):
        pass
    def handoff(backend, args):
        calls.append((backend, args))
        raise Handoff
    monkeypatch.setattr(os, 'execv', handoff)
    import pytest
    with pytest.raises(Handoff):
        shim.main()
    assert os.environ['CODEX_SQLITE_HOME'] == str(tmp_path / 'private-gui' / 'sqlite')
    assert calls[0][1][-1] == '--version'


def test_import_existing_projects_before_creation(monkeypatch):
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    message = {'id': 7, 'method': 'project/import', 'params': {
        'idempotencyKey': 'existing-project-id', 'name': 'Existing project',
        'roots': [
            {'path': r'C:\Users\Example User\Documents\ChatGPT\Existing project'},
            {'path': r'\\wsl$\Ubuntu\srv\repos\example-project'},
        ],
        'metadata': {'description': r'C:\keep-the-description'},
        'threads': ['existing-thread-id'],
    }}
    result = shim.translate(message)
    assert result['params']['roots'] == [
        {'path': '/mnt/c/Users/Example User/Documents/ChatGPT/Existing project'},
        {'path': '/srv/repos/example-project'},
    ]
    assert result['params']['idempotencyKey'] == 'existing-project-id'
    assert result['params']['metadata'] == {'description': r'C:\keep-the-description'}
    assert result['params']['threads'] == ['existing-thread-id']
    assert result['id'] == 7
