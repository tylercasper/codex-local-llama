#!/usr/bin/env python3
"""Prepare a disposable Ubuntu Desktop autoinstall without copying the full ISO.

Canonical autoinstall reference:
https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html
Only validation media contains the generated VM identity; nothing secret is written
into the repository. Boot the original ISO plus seed.iso using the extracted kernel.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess

GIB = 1024**3
ISO_SHA256 = "faabcf33ae53976d2b8207a001ff32f4e5daae013505ac7188c9ea63988f8328"


def reserve(stage, extra_reserves=()):
    for volume, minimum in ((stage, 100), *extra_reserves):
        existing = volume
        while not existing.exists():
            existing = existing.parent
        free = shutil.disk_usage(existing).free
        if free < minimum * GIB:
            raise RuntimeError(f"Storage reserve crossed on {volume}: {free / GIB:.1f} GiB free")
    used = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) if stage.exists() else 0
    if used + 512 * 1024**2 > 64 * GIB:
        raise RuntimeError("Validation staging would exceed its 64 GiB budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--reserve', nargs=2, action='append', default=[],
                        metavar=('PATH', 'GIB'), help='Additional volume and minimum free GiB')
    args = parser.parse_args()
    stage = args.stage.resolve()
    try:
        reserves = [(Path(path).expanduser().resolve(), float(minimum))
                    for path, minimum in args.reserve]
        if any(not 0 < minimum < float('inf') for _, minimum in reserves):
            raise ValueError
    except ValueError:
        parser.error('--reserve requires a path and a positive finite GiB value')
    reserve(stage, reserves)
    with args.iso.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != ISO_SHA256:
        parser.error('Ubuntu ISO checksum does not match verified 24.04.3 desktop image')
    stage.mkdir(parents=True, exist_ok=True)
    for source, filename in (('/casper/vmlinuz', 'vmlinuz'), ('/casper/initrd', 'initrd'), ('/casper/install-sources.yaml', 'install-sources.yaml')):
        reserve(stage, reserves)
        target = stage / filename
        if not target.exists():
            subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(args.iso), '-extract', source, str(target)], check=True)
    if 'id: ubuntu-desktop\n' not in (stage / 'install-sources.yaml').read_text():
        parser.error('verified desktop source is unavailable')
    key = stage / 'id_ed25519'
    if not key.exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'codex-disposable-vm', '-f', str(key)], check=True)
    public = key.with_suffix('.pub').read_text().strip()
    password_hash = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=secrets.token_urlsafe(32), capture_output=True, text=True, check=True).stdout.strip()
    setup = '''set -eu
printf 'codexvm ALL=(ALL) NOPASSWD:ALL\\n' > /etc/sudoers.d/codex-validation
chmod 0440 /etc/sudoers.d/codex-validation
printf '[daemon]\\nAutomaticLoginEnable=true\\nAutomaticLogin=codexvm\\nWaylandEnable=false\\n' > /etc/gdm3/custom.conf
mkdir -p /etc/dconf/db/local.d /etc/dconf/profile
printf 'user-db:user\\nsystem-db:local\\n' > /etc/dconf/profile/user
printf '[org/gnome/desktop/session]\\nidle-delay=uint32 0\\n[org/gnome/desktop/screensaver]\\nlock-enabled=false\\n' > /etc/dconf/db/local.d/00-validation
dconf update
mkdir -p /home/codexvm/.config
touch /home/codexvm/.config/gnome-initial-setup-done
# useradd skips skeleton files when a late-command created the home first.
cp -an /etc/skel/. /home/codexvm/
# The identity user may be created on first boot; own the home itself too.
chown -R 1000:1000 /home/codexvm
systemctl enable ssh
'''
    data = {'autoinstall': {
        'version': 1,
        'refresh-installer': {'update': False},
        'source': {'id': 'ubuntu-desktop'},
        'locale': 'en_US.UTF-8',
        'keyboard': {'layout': 'us'},
        'timezone': 'Etc/UTC',
        'identity': {'hostname': 'codex-validation', 'username': 'codexvm', 'realname': 'Codex Validation', 'password': password_hash},
        'ssh': {'install-server': True, 'allow-pw': False, 'authorized-keys': [public]},
        'storage': {'layout': {'name': 'direct'}},
        'apt': {'geoip': False},
        'packages': ['openssh-server', 'openssh-client', 'curl', 'git', 'sudo', 'python3-websocket', 'python3-venv', 'xdotool', 'wmctrl', 'x11-utils', 'dbus-x11'],
        'late-commands': [['curtin', 'in-target', '--target=/target', '--', 'bash', '-c', setup]],
        'shutdown': 'poweroff',
    }}
    seed = stage / 'seed'
    seed.mkdir(exist_ok=True)
    (seed / 'user-data').write_text('#cloud-config\n' + json.dumps(data, indent=2) + '\n')
    (seed / 'meta-data').write_text('instance-id: codex-validation-fresh\nlocal-hostname: codex-validation\n')
    reserve(stage, reserves)
    output = stage / 'seed.iso'
    output.unlink(missing_ok=True)
    subprocess.run(['xorriso', '-as', 'mkisofs', '-V', 'CIDATA', '-J', '-r', '-o', str(output), str(seed)], check=True)
    (stage / 'ubuntu-install.json').write_text(json.dumps({'iso': str(args.iso.resolve()), 'sha256': digest, 'kernel': str(stage / 'vmlinuz'), 'initrd': str(stage / 'initrd'), 'seed': str(output), 'ssh_key': str(key), 'username': 'codexvm', 'append': 'boot=casper autoinstall ds=nocloud console=tty0 console=ttyS0,115200n8'}, indent=2) + '\n')
    reserve(stage, reserves)
    print(f'Prepared Ubuntu Desktop validation media: {stage}; SSH identity: {key}; user: codexvm')


if __name__ == '__main__':
    main()
