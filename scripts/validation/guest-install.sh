#!/usr/bin/env bash
# Run once in a freshly installed Ubuntu guest per mode. Reinstall/reset the
# disposable guest between CLI and GUI runs; this script never wipes a guest.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( ${1:-} != cli && ${1:-} != gui ) ]]; then
    echo 'usage: guest-install.sh cli|gui [optional-tavily-key-file]' >&2
    exit 2
fi
mode=$1
key_file=${2:-}
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || {
    echo 'This acceptance test requires Ubuntu 24.04.' >&2; exit 1;
}
[[ $(uname -r) != *[Mm]icrosoft* ]] || {
    echo 'This acceptance test requires a VM, not the WSL host.' >&2; exit 1;
}
[[ $(systemd-detect-virt) == kvm || $(systemd-detect-virt) == qemu ]] || {
    echo 'This acceptance test requires the dedicated QEMU/KVM guest.' >&2; exit 1;
}
[[ $EUID != 0 && ( -z $key_file || -r $key_file ) ]]
[[ ! -e $HOME/.codex-local && ! -e $HOME/.local/lib/codex-local ]] || {
    echo 'Existing deployment found; reset the guest before the clean-install test.' >&2
    exit 1
}
for unit in codex-local-tunnel.service codex-local-provider.service; do
    [[ $(systemctl show "$unit" --property=LoadState --value) == not-found ]] || {
        echo "Existing service found: $unit; reset the guest." >&2; exit 1;
    }
done
args=()
[[ -z $key_file ]] || args+=(--tavily-key-file "$key_file")
[[ $mode != gui ]] || args+=(--gui)
"$repo_root/scripts/install-local.sh" "${args[@]}"
"$HOME/.local/bin/codex-local-verify"
if [[ $mode == cli ]]; then
    [[ ! -e $HOME/.local/bin/codex-local-gui ]]
else
    [[ -x $HOME/.local/bin/codex-local-gui ]]
fi
printf 'Clean %s installation and provider verification passed on %s (%s).\n' \
    "$mode" "$PRETTY_NAME" "$(uname -r)"
echo 'Interactive GUI, model/tool execution, coexistence, relocation, and reinstall acceptance checks remain separate.'
