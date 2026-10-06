"""Exercise source-package installation with only system/service boundaries mocked."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from codex_local_provider.compatibility import BUILD_RECORD, load_manifest
from test_compatibility import make_package

ROOT = Path(__file__).resolve().parents[1]


def test_verified_source_package_is_activated_and_recorded(tmp_path):
    repo = tmp_path / 'repo'
    for folder in ('config', 'src', 'scripts', 'prompts', 'systemd', 'bundles'):
        shutil.copytree(ROOT / folder, repo / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.deb', '*.tar.gz'))
    for name in ('pyproject.toml', 'uv.lock', 'README.md'):
        shutil.copyfile(ROOT / name, repo / name)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    # Kernel policy installation is outside this test's scope, as are SSH/systemd/uv.
    (repo / 'scripts/install_cli_apparmor.py').write_text('pass\n')
    package = make_package(tmp_path / 'package', load_manifest(repo))
    home, runtime = tmp_path / 'home', tmp_path / 'runtime'
    home.mkdir()
    commands = tmp_path / 'bin'
    commands.mkdir()
    scripts = {
        'ssh': '#!/bin/sh\nexit 0\n',
        'systemctl': '#!/bin/sh\nexit 0\n',
        'sudo': '#!/bin/sh\nexit 0\n',
        'uv': '''#!/usr/bin/env python3
import os, pathlib
binary = pathlib.Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin/codex-local-provider'
binary.parent.mkdir(parents=True)
binary.write_text('#!/bin/sh\\nexit 0\\n')
binary.chmod(0o755)
''',
        'curl': '''#!/usr/bin/env python3
import json, pathlib, sys
url = sys.argv[-1]
if url.endswith('/healthz'):
    pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text(json.dumps({
        'status': 'ok', 'upstream': True, 'search_available': True}))
    print('200', end='')
elif url.endswith('/v1/models'):
    print(json.dumps({'data': [{'id': 'qwen3.8-27b'}]}))
else:
    raise SystemExit('unexpected download: ' + url)
''',
    }
    for name, script in scripts.items():
        path = commands / name
        path.write_text(script)
        path.chmod(0o755)
    (home / '.ssh').mkdir()
    (home / '.ssh/config').write_text('Host llama-server\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('CODEX_')}
    env.update(HOME=str(home), PATH=f'{commands}:/usr/bin:/bin',
               CODEX_LOCAL_RUNTIME_ROOT=str(runtime))
    result = subprocess.run(['bash', str(repo / 'scripts/install-local.sh'),
                             '--codex-source-package', str(package)],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = (runtime / 'codex/current').resolve()
    assert '-source-' in installed.name
    assert (installed / 'bin/codex').read_bytes() == (package / 'bin/codex').read_bytes()
    assert (installed / BUILD_RECORD).read_bytes() == (package / BUILD_RECORD).read_bytes()
    record = json.loads((home / '.codex-local/compatibility.json').read_text())
    assert record['installed_cli']['origin'] == 'source'
    assert record['installed_cli']['package']['target'] == 'x86_64-unknown-linux-gnu'
    assert record['desktop']['windows_wsl']['backend'] == 'source'
    deployment = json.loads((home / '.codex-local/deployment.json').read_text())
    assert deployment['codex']['target'] == 'x86_64-unknown-linux-gnu'
    # A rerun validates/reuses the identical package instead of downloading a release.
    repeated = subprocess.run(['bash', str(repo / 'scripts/install-local.sh'),
                               '--codex-source-package', str(package)],
                              env=env, text=True, capture_output=True)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert (runtime / 'codex/current').resolve() == installed
