#!/usr/bin/env bash
# Install the bundled Windows GUI; the backend runs in the invoking WSL distro.
set -euo pipefail
assets_dir= runtime_root= gui_home= dry_run=false
while (($#)); do
    case "$1" in
        --assets-dir) assets_dir=$2; shift 2;;
        --runtime-root) runtime_root=$2; shift 2;;
        --gui-home) gui_home=$2; shift 2;;
        --dry-run) dry_run=true; shift;;
        *) echo "Unknown WSL GUI option: $1" >&2; exit 2;;
    esac
done
[[ -n "$assets_dir" && -n "$runtime_root" && -n "$gui_home" ]] || { echo 'Required: --assets-dir PATH --runtime-root PATH --gui-home PATH' >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
manifest="$repo/bundles/gui/windows.json"
python3 - "$manifest" <<'PY'
import hashlib,json,pathlib,sys
m=pathlib.Path(sys.argv[1]); data=json.loads(m.read_text()); archive=m.parent/data['asset']
with archive.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
if digest != data['sha256']: raise SystemExit('Windows GUI bundle checksum mismatch')
PY
for asset in config.toml model-catalog.json model-instructions.md; do
    [[ -f "$assets_dir/$asset" ]] || { echo "Missing GUI asset: $asset" >&2; exit 1; }
done
validation_args=()
if "$dry_run"; then validation_args+=(--dry-run); fi
python3 "$repo/scripts/gui_backend.py" "$assets_dir/codex-local-backend.json" --frontend windows_wsl --manifest "$manifest" "${validation_args[@]}"
if [[ -z "${WSL_DISTRO_NAME:-}" ]]; then
    # Service-launched agents can lose WSL_DISTRO_NAME; wslpath retains the real distro.
    distro_root=$(wslpath -w / 2>/dev/null || true)
    WSL_DISTRO_NAME=$(python3 - "$distro_root" <<'PYDISTRO'
import re, sys
m = re.fullmatch(r'\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)\\?', sys.argv[1], re.I)
print(m[1] if m else '')
PYDISTRO
)
fi
[[ -n "${WSL_DISTRO_NAME:-}" ]] || { echo '--wsl-gui must run inside WSL.' >&2; exit 1; }
powershell=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
[[ -x "$powershell" ]] || { echo 'Windows PowerShell is unavailable.' >&2; exit 1; }
extra_args=()
if "$dry_run"; then extra_args+=(-DryRun); fi
"$powershell" -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$repo/scripts/install-local-gui.ps1")" \
    -LocalConfigDirectory "$(wslpath -w "$assets_dir")" \
    -ConfigHome "$(wslpath -w "$gui_home")" \
    -BundleManifest "$(wslpath -w "$manifest")" -WslDistribution "$WSL_DISTRO_NAME" "${extra_args[@]}"
