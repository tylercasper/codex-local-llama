"""Local model configuration must survive CLI launch."""
import os
import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def write_local_config(home, *, model='local-test', effort=None):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    config = f'model = {json.dumps(model)}\nmodel_provider = "llamacpp"\n'
    if effort is not None:
        config += f'model_reasoning_effort = {json.dumps(effort)}\n'
    config += '[model_providers.llamacpp]\nbase_url = "http://127.0.0.1:18000/v1"\n'
    (home / 'local.config.toml').write_text(config)


def test_cli_uses_installed_home_and_preserves_explicit_override(tmp_path):
    runtime = tmp_path / 'runtime'
    binary = runtime / 'codex/current/bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_HOME" "$@"\n')
    binary.chmod(0o755)
    selected = str(tmp_path / 'Windows User/shared home')
    write_local_config(selected)
    (runtime / 'codex-home').write_text(selected + '\n')
    env = {k:v for k,v in os.environ.items() if k != 'CODEX_LOCAL_HOME'}
    env['CODEX_LOCAL_RUNTIME_ROOT'] = str(runtime)
    command = ['bash', str(ROOT / 'scripts/codex-local'), '--version']
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    args = result.stdout.splitlines()
    assert args[:3] == [selected, '--profile', 'local']
    assert args[-1] == '--version'
    config = tomllib.loads('\n'.join(args[4:-1:2]))
    assert config == {
        'model': 'local-test', 'model_provider': 'llamacpp',
        'model_reasoning_effort': 'none',
        'model_providers': {'llamacpp': {'base_url': 'http://127.0.0.1:18000/v1'}},
    }
    env['CODEX_LOCAL_HOME'] = str(tmp_path / 'override')
    write_local_config(env['CODEX_LOCAL_HOME'], model='different-model', effort='low')
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines()[0] == env['CODEX_LOCAL_HOME']
    assert 'model="different-model"' in result.stdout.splitlines()
    assert 'model_reasoning_effort="low"' in result.stdout.splitlines()


def test_cli_local_defaults_preserve_explicit_arguments_and_toml_escaping(tmp_path):
    runtime = tmp_path / 'runtime'
    binary = runtime / 'codex/current/bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    binary.chmod(0o755)
    home = tmp_path / 'home'
    model = 'local model "quoted"'
    write_local_config(home, model=model)
    (home / 'config.toml').write_text('model = "base-model"\n')
    env = dict(os.environ, CODEX_LOCAL_RUNTIME_ROOT=str(runtime), CODEX_LOCAL_HOME=str(home))
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/codex-local'), '-m', 'explicit-model'],
        env=env, text=True, capture_output=True, check=True,
    )
    args = result.stdout.splitlines()
    assert tomllib.loads(args[3])['model'] == model
    assert args[-2:] == ['-m', 'explicit-model']


def test_cli_missing_local_configuration_fails_before_starting_codex(tmp_path):
    env = dict(os.environ, CODEX_LOCAL_HOME=str(tmp_path / 'missing'))
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/codex-local')],
        env=env, text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert 'cannot resolve local model configuration' in result.stderr
