# Disposable Ubuntu acceptance

These are optional development tools, not part of the installed runtime. They
create and operate a dedicated QEMU/KVM guest; they do not restart WSL. Supply
your own staging directory and Ubuntu 24.04.3 Desktop ISO. The preparer verifies
the pinned ISO checksum before generating media.

Host prerequisites include QEMU/KVM, `qemu-img`, `xorriso`, OpenSSL, and OpenSSH.
The chosen staging filesystem needs at least 100 GiB free, and staged logical
file sizes are capped at 64 GiB. Use `--reserve PATH GIB` repeatedly to monitor
additional filesystems, such as a host volume containing a WSL virtual disk.
Pass the same reserve arguments on every invocation.

```bash
python3 scripts/validation/prepare-ubuntu.py --iso /path/to/ubuntu.iso --stage /path/to/stage
python3 scripts/validation/qemu-ubuntu.py start --stage /path/to/stage \
  --iso /path/to/ubuntu.iso --kernel /path/to/stage/vmlinuz \
  --initrd /path/to/stage/initrd --seed /path/to/stage/seed.iso
```

`start` remains attached as a storage monitor and pauses the guest if a reserve
is crossed or the monitor is interrupted. SSH and VNC bind to localhost ports
2222 and 5909. Run only one validation guest using these ports at a time. Use
the same OS user for all VM actions. `baseline` saves a powered-off guest;
`reset` replaces its disk with that saved baseline. These actions operate only
on the explicitly selected staging directory.

Inside the fresh guest, provide the checkout, `uv`, deployment configuration,
SSH access to your model server, and the GUI archive when testing GUI mode.
Run `scripts/validation/guest-install.sh cli` or `gui`, then
`python3 scripts/validation/acceptance.py cli --output-dir /path/to/results`
(use `gui` for desktop acceptance). GUI checks additionally need a logged-in
desktop and `python3-websocket`. Acceptance performs model inference and public
web requests. It expects keyless search, creates test files/projects, and writes
logs/screenshots outside this repository. Review or remove those test projects
in the disposable guest after inspection.

`forwarded-agent.py` can bridge a forwarded SSH-agent socket out of a private
temporary directory for the guest's tunnel service. Its `--source-file` contains
only the socket path; keep the forwarding session alive. Generated SSH keys,
seed media, VM disks, screenshots, and result logs belong in the staging/results
directories, never in Git.
