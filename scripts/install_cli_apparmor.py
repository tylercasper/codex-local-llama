#!/usr/bin/env python3
"""Allow the private CLI to create its sandbox on restricted Ubuntu kernels."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile


RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')


def profile_for(executable):
    executable = executable.resolve()
    if any(character in str(executable) for character in '\\"\n\r*?[]{}'):
        raise ValueError('CLI executable path contains unsupported AppArmor characters')
    name = 'codex-local-cli-' + hashlib.sha256(str(executable).encode()).hexdigest()[:16]
    return name, f'''abi <abi/4.0>,
include <tunables/global>
profile {name} "{executable}" flags=(unconfined) {{
  userns,
}}
'''


def install(executable):
    if not RESTRICTION.exists() or RESTRICTION.read_text().strip() != '1':
        return
    if not executable.is_file():
        raise ValueError('Private CLI executable is missing')
    if not shutil.which('apparmor_parser') and not Path('/sbin/apparmor_parser').is_file():
        raise RuntimeError('apparmor_parser is required by Ubuntu user-namespace restrictions')
    name, profile = profile_for(executable)
    with tempfile.TemporaryDirectory(prefix='codex-local-cli-apparmor-') as directory:
        source = Path(directory) / name
        source.write_text(profile)
        destination = '/etc/apparmor.d/' + name
        subprocess.run(['sudo', 'install', '-m', '0644', str(source), destination], check=True)
        subprocess.run(['sudo', 'apparmor_parser', '-r', destination], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    install(parser.parse_args().executable)
