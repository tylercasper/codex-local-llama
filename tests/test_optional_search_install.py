import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def verify_environment(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    runtime = tmp_path / 'runtime'
    binary = runtime / 'codex/current/bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\necho codex-test-version\n')
    binary.chmod(0o755)
    (home / 'deployment.json').write_text(json.dumps({
        'local': {'host': '127.0.0.1', 'provider_port': 18000},
        'model': {'id': 'test-model'}, 'codex': {'version': 'test-version'},
        'paths': {'runtime_root': str(runtime)},
    }))
    commands = tmp_path / 'bin'
    commands.mkdir()
    (commands / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (commands / 'curl').write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
if sys.argv[-1].endswith('/healthz'):
    pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text(os.environ['TEST_HEALTH'])
    print(os.environ.get('TEST_STATUS', '200'), end='')
else:
    print(json.dumps({'data': [{'id': 'test-model'}]}))
''')
    for command in commands.iterdir():
        command.chmod(0o755)
    return {**os.environ, 'PATH': f'{commands}:{os.environ["PATH"]}',
            'CODEX_LOCAL_HOME': str(home)}


@pytest.mark.parametrize('upstream,search,expected', [(True, True, 0), (False, True, 1), (True, False, 1)])
def test_verification_requires_upstream_and_search_without_tavily(verify_environment, upstream, search, expected):
    env = {**verify_environment, 'TEST_HEALTH': json.dumps({
        'status': 'ok', 'upstream': upstream, 'search_available': search,
        'tavily_configured': False, 'search_backend': 'keyless',
    })}
    result = subprocess.run(['bash', str(ROOT / 'scripts/verify-local.sh')],
                            env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize('upstream,expected', [(True, 0), (False, 1)])
def test_allow_degraded_still_requires_model_upstream(verify_environment, upstream, expected):
    env = {**verify_environment, 'TEST_STATUS': '503', 'TEST_HEALTH': json.dumps({
        'status': 'degraded', 'upstream': upstream, 'search_available': False,
        'tavily_configured': False,
    })}
    result = subprocess.run(['bash', str(ROOT / 'scripts/verify-local.sh'), '--allow-degraded'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
