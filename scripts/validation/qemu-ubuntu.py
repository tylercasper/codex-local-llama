#!/usr/bin/env python3
"""Dedicated Ubuntu validation VM; run start under sudo if /dev/kvm is restricted.

start stays attached as the storage monitor. SSH is localhost:2222 and VNC is
localhost:5909. If storage crosses a reserve, the guest is paused and start exits;
use start again after resolving storage. stop powers off gracefully using ACPI.
Use the same user for every action (sudo -n for all actions when starting as root).
Installation media must already be verified. Seed/kernel/initrd live under ROOT;
the original read-only Ubuntu ISO may be reused from another directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time

ROOT = Path.home() / '.cache/codex-local-validation'
DISK = ROOT / 'ubuntu.qcow2'
BASELINE = ROOT / 'pristine.qcow2'
CONTROL = Path(f'/tmp/codex-local-validation-qemu-{os.getuid()}')
QMP = CONTROL / 'qmp.sock'
GIB = 1024 ** 3
EXTRA_RESERVES = []


def free_bytes(path):
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def storage_state():
    # Logical file sizes conservatively account for sparse qcow2 storage.
    used = sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file()) if ROOT.exists() else 0
    reserves = [(ROOT, 100), *EXTRA_RESERVES]
    free = [(path, free_bytes(path), minimum) for path, minimum in reserves]
    return dict(free_gib={str(path): round(value / GIB, 2) for path, value, _ in free},
                validation_gib=round(used / GIB, 2),
                safe=all(value >= minimum * GIB for _, value, minimum in free) and used < 64 * GIB)


def assert_storage():
    state = storage_state()
    if not state['safe']:
        raise RuntimeError('Storage reserve crossed: ' + json.dumps(state))
    return state


def qmp(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(QMP))
        with sock.makefile('rwb', buffering=0) as stream:
            json.loads(stream.readline())  # Server greeting.
            for request in ('qmp_capabilities', command):
                stream.write(json.dumps({'execute': request}).encode() + b'\n')
                while True:
                    response = json.loads(stream.readline())
                    if 'error' in response:
                        raise RuntimeError(str(response['error']))
                    if 'return' in response:
                        break
            return response['return']


def live_status():
    try:
        return qmp('query-status')
    except (FileNotFoundError, ConnectionRefusedError):
        return None


def create_disk():
    assert_storage()
    ROOT.mkdir(parents=True, exist_ok=True)
    if not DISK.exists():
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(DISK), '40G'], check=True)
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(DISK)]))
    if info['format'] != 'qcow2' or info['virtual-size'] > 40 * GIB or info.get('backing-filename'):
        raise RuntimeError('Expected a standalone qcow2 disk capped at 40GiB.')


def offline_copy(action):
    if live_status():
        raise RuntimeError('Power off the validation guest before baseline/reset.')
    source, target = (DISK, BASELINE) if action == 'baseline' else (BASELINE, DISK)
    if action == 'baseline' and target.exists():
        raise RuntimeError('Pristine baseline already exists; refusing to overwrite it.')
    if not source.is_file() or source.is_symlink() or target.is_symlink():
        raise RuntimeError('Expected ordinary managed validation disk files.')
    for disk in {source, DISK}:
        if disk.exists():
            info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(disk)]))
            if info['format'] != 'qcow2' or info['virtual-size'] > 40 * GIB or info.get('backing-filename'):
                raise RuntimeError('Expected a standalone qcow2 disk capped at 40GiB.')
            # check obtains an exclusive image lock and refuses active writers.
            subprocess.run(['qemu-img', 'check', str(disk)], check=True)
    assert_storage()
    size = source.stat().st_size
    used = sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file())
    if used + size >= 64 * GIB or free_bytes(ROOT) - size < 100 * GIB:
        raise RuntimeError('Insufficient validation budget/reserve for staged disk copy.')
    fd, filename = tempfile.mkstemp(prefix='.disk-copy-', suffix='.qcow2', dir=ROOT)
    temporary = Path(filename)
    try:
        with source.open('rb') as reader, os.fdopen(fd, 'wb') as writer:
            while data := reader.read(8 * 1024 * 1024):
                assert_storage()
                writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        subprocess.run(['qemu-img', 'check', str(temporary)], check=True)
        if action == 'baseline' and target.exists():
            raise RuntimeError('Pristine baseline appeared during copy; refusing overwrite.')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'action': action, 'storage': storage_state()}))


def start(iso, kernel=None, initrd=None, seed=None):
    assert_storage()
    if any((kernel, initrd, seed)) and not all((iso, kernel, initrd, seed)):
        raise RuntimeError('Autoinstall requires --iso, --kernel, --initrd, and --seed together.')
    for asset in (kernel, initrd, seed):
        if asset and (not asset.resolve().is_relative_to(ROOT) or not asset.is_file()):
            raise RuntimeError('Autoinstall assets must be staged within the validation directory.')
    CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CONTROL.is_symlink() or CONTROL.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe QEMU control directory.')
    status = live_status()
    if status:
        if status['status'] == 'paused':
            qmp('cont')
        else:
            raise RuntimeError('Validation guest already running; use monitor.')
    else:
        if not os.access('/dev/kvm', os.R_OK | os.W_OK):
            raise RuntimeError('/dev/kvm is restricted; run this helper with sudo -n.')
        create_disk()
        QMP.unlink(missing_ok=True)
        command = ['qemu-system-x86_64', '-name', 'codex-local-ubuntu2404-validation',
                   '-enable-kvm', '-cpu', 'host', '-smp', '4', '-m', '8192',
                   '-drive', f'file={DISK},format=qcow2,if=virtio',
                   '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22',
                   '-device', 'virtio-net-pci,netdev=net0',
                   '-display', 'none', '-vnc', '127.0.0.1:9', '-vga', 'virtio',
                   '-device', 'qemu-xhci', '-device', 'usb-tablet',
                   '-qmp', f'unix:{QMP},server=on,wait=off',
                   '-pidfile', str(CONTROL / 'qemu.pid'),
                   '-serial', f'file:{ROOT / "serial.log"}', '-daemonize']
        if iso:
            iso = iso.resolve()
            if not iso.is_file():
                raise RuntimeError('ISO must be an existing file.')
            command.extend(['-cdrom', str(iso), '-boot', 'once=d'])
        if kernel:
            command.extend(['-no-reboot', '-kernel', str(kernel), '-initrd', str(initrd),
                            '-append', 'boot=casper autoinstall ds=nocloud console=tty0 console=ttyS0,115200n8',
                            '-drive', f'file={seed},format=raw,media=cdrom,readonly=on'])
        subprocess.run(command, check=True)
    monitor()


def monitor():
    def terminate(_signum, _frame):
        raise SystemExit('Storage monitor terminated; pausing guest.')
    previous_handlers = {number: signal.signal(number, terminate)
                         for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        while live_status():
            state = storage_state()
            if not state['safe']:
                qmp('stop')
                raise RuntimeError('Guest paused at storage reserve: ' + json.dumps(state))
            print(json.dumps(state), flush=True)
            time.sleep(5)
    except BaseException:
        # A lost monitor must never leave an unmonitored guest consuming storage.
        try:
            if live_status():
                qmp('stop')
        finally:
            raise
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)


def main():
    global ROOT, DISK, BASELINE, CONTROL, QMP, EXTRA_RESERVES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'start', 'monitor', 'status', 'stop', 'baseline', 'reset'))
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--reserve', nargs=2, action='append', default=[],
                        metavar=('PATH', 'GIB'), help='Additional volume and minimum free GiB')
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--initrd', type=Path)
    parser.add_argument('--seed', type=Path)
    args = parser.parse_args()
    ROOT = args.stage.expanduser().resolve()
    DISK, BASELINE = ROOT / 'ubuntu.qcow2', ROOT / 'pristine.qcow2'
    identity = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    CONTROL = Path(f'/tmp/codex-local-validation-qemu-{os.getuid()}-{identity}')
    QMP = CONTROL / 'qmp.sock'
    try:
        EXTRA_RESERVES = [(Path(path).expanduser().resolve(), float(minimum))
                          for path, minimum in args.reserve]
        if any(not 0 < minimum < float('inf') for _, minimum in EXTRA_RESERVES):
            raise ValueError
    except ValueError:
        parser.error('--reserve requires a path and a positive finite GiB value')
    if args.action == 'create':
        create_disk()
    elif args.action == 'start':
        start(args.iso, args.kernel, args.initrd, args.seed)
    elif args.action == 'monitor':
        monitor()
    elif args.action == 'stop':
        print(qmp('system_powerdown'))
    elif args.action in ('baseline', 'reset'):
        offline_copy(args.action)
    else:
        print(json.dumps({'storage': storage_state(), 'guest': live_status()}))


if __name__ == '__main__':
    main()
