#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: install-local.sh [options]

Install an isolated Codex harness for a self-hosted llama.cpp server.

Options:
  --gui                     Include the native Codex Local GUI
  --wsl-gui                 Include the Windows GUI with WSL execution
  --config PATH             Deployment TOML (default: config/deployment.toml)
  --codex-home PATH         Shared configuration and session directory
  --codex-release-dir PATH  Use an already-extracted pinned Codex package
  --tavily-key-file PATH    Install a Tavily API key from this file
  --dry-run                 Render and validate without changing the system
  -h, --help                Show this help
EOF
}

fail() {
    echo "error: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

deployment_cli() {
    PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$python_cmd" -m codex_local_provider.deployment "$@"
}

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
deployment_path="$repo_root/config/deployment.toml"
release_path="$repo_root/config/codex-release.json"
codex_release_dir=""
tavily_key_file=""
dry_run=false
gui_mode=""
selected_codex_home="${CODEX_LOCAL_HOME:-}"

while (($#)); do
    case "$1" in
        --gui|--wsl-gui)
            [[ -z "$gui_mode" || "$gui_mode" == "$1" ]] \
                || fail "--gui and --wsl-gui are mutually exclusive"
            gui_mode="$1"
            shift
            ;;
        --config)
            (($# >= 2)) || fail "--config requires a path"
            deployment_path="$2"
            shift 2
            ;;
        --codex-home)
            (($# >= 2)) || fail "--codex-home requires a path"
            selected_codex_home="$2"
            shift 2
            ;;
        --codex-release-dir)
            (($# >= 2)) || fail "--codex-release-dir requires a path"
            codex_release_dir="$2"
            shift 2
            ;;
        --tavily-key-file)
            (($# >= 2)) || fail "--tavily-key-file requires a path"
            tavily_key_file="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

[[ $EUID -ne 0 ]] || fail "run this installer as the target user, not as root"
[[ -f "$deployment_path" ]] || fail "deployment config not found: $deployment_path"
[[ -f "$release_path" ]] || fail "release manifest not found: $release_path"
[[ -z "$tavily_key_file" || -r "$tavily_key_file" ]] \
    || fail "Tavily key file is not readable: $tavily_key_file"
[[ -z "$codex_release_dir" || -d "$codex_release_dir" ]] \
    || fail "Codex release directory does not exist: $codex_release_dir"

require_command python3
python_cmd="$(command -v python3)"
"$python_cmd" -c 'import sys; assert sys.version_info >= (3, 12)' \
    || fail "Python 3.12 or newer is required"

home_dir="${HOME:?HOME must be set}"
service_user="$(id -un)"
runtime_root="${CODEX_LOCAL_RUNTIME_ROOT:-$home_dir/.local/lib/codex-local}"
if [[ -z "$selected_codex_home" && -r "$runtime_root/codex-home" ]]; then
    IFS= read -r selected_codex_home < "$runtime_root/codex-home"
    if [[ "$gui_mode" == --wsl-gui && "$selected_codex_home" == "$home_dir/.codex-local" ]]; then
        selected_codex_home=""
    fi
fi
if [[ "$gui_mode" == --wsl-gui && -z "$selected_codex_home" ]]; then
    powershell=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
    [[ -x "$powershell" ]] || fail "Windows PowerShell is required to locate the shared Windows home"
    windows_profile="$("$powershell" -NoProfile -NonInteractive -Command '[Environment]::GetFolderPath("UserProfile")' | tr -d '\r')"
    [[ -n "$windows_profile" ]] || fail "Could not locate the Windows user profile"
    selected_codex_home="$(wslpath -u "$windows_profile")/.codex-local"
fi
isolated_home="${selected_codex_home:-$home_dir/.codex-local}"
[[ "$isolated_home" == /* ]] || fail "--codex-home must be an absolute path"
if [[ "$gui_mode" == --wsl-gui && ! "$isolated_home" =~ ^/mnt/[a-zA-Z]/ ]]; then
    fail "--wsl-gui requires a Windows-backed --codex-home (for example /mnt/c/Users/you/.codex-local)"
fi
state_args=(--codex-home "$isolated_home")
if [[ "$gui_mode" == --wsl-gui ]]; then
    state_args+=(--sqlite-home "$home_dir/.local/state/codex-local/sqlite")
fi
bin_dir="$home_dir/.local/bin"
ssh_path="$(command -v ssh || true)"
[[ -n "$ssh_path" ]] || fail "required command not found: ssh"

render_dir="$(mktemp -d)"
download_dir=""
provider_backup=""
cleanup() {
    [[ -z "$render_dir" || ! -d "$render_dir" ]] || rm -rf -- "$render_dir"
    [[ -z "$download_dir" || ! -d "$download_dir" ]] || rm -rf -- "$download_dir"
    [[ -z "$provider_backup" || ! -d "$provider_backup" ]] \
        || rm -rf -- "$provider_backup"
}
trap cleanup EXIT

deployment_cli render \
    --repository-root "$repo_root" \
    --deployment "$deployment_path" \
    --release "$release_path" \
    --output-dir "$render_dir" \
    --home "$home_dir" \
    --user "$service_user" \
    --runtime-root "$runtime_root" \
    --ssh-path "$ssh_path" "${state_args[@]}"

if [[ -n "$codex_release_dir" ]]; then
    deployment_cli validate-release \
        --release "$release_path" "$codex_release_dir"
fi

mapfile -t deployment_values < <(
    "$python_cmd" -c '
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
for value in (
    p["remote"]["ssh_target"], p["remote"]["ssh_config"],
    p["local"]["host"], p["local"]["provider_port"],
    p["model"]["id"], p["codex"]["version"], p["codex"]["target"],
): print(value)
' "$render_dir/deployment.json"
)
ssh_target="${deployment_values[0]}"
ssh_config="${deployment_values[1]}"
local_host="${deployment_values[2]}"
provider_port="${deployment_values[3]}"
model_id="${deployment_values[4]}"
codex_version="${deployment_values[5]}"
codex_target="${deployment_values[6]}"

if [[ -n "$gui_mode" ]]; then
    deployment_cli render-gui --assets-dir "$render_dir" --output-dir "$render_dir/gui"
    if [[ "$gui_mode" == --gui ]]; then
        gui_helper="$repo_root/scripts/install-native-gui.sh"
    else
        gui_helper="$repo_root/scripts/install-wsl-gui.sh"
    fi
    gui_args=(--assets-dir "$render_dir/gui" --runtime-root "$runtime_root"
              --gui-home "$home_dir/.codex-local-gui")
    if [[ "$gui_mode" == --wsl-gui ]]; then
        gui_args=(--assets-dir "$render_dir/gui" --runtime-root "$runtime_root"
                  --gui-home "$isolated_home")
    fi
    # Validate bundled assets before changing either deployment.
    bash "$gui_helper" "${gui_args[@]}" --dry-run
fi

if $dry_run; then
    echo "Dry run complete; no files or services were changed."
    echo "  deployment: $deployment_path"
    echo "  isolated home: $isolated_home"
    echo "  runtime root: $runtime_root"
    echo "  SSH target: $ssh_target (config: $ssh_config)"
    echo "  provider: http://$local_host:$provider_port/v1"
    echo "  model: $model_id"
    echo "  Codex: $codex_version ($codex_target)"
    if [[ -n "$codex_release_dir" ]]; then
        echo "  Codex source: $codex_release_dir (validated)"
    else
        echo "  Codex source: pinned official release download"
    fi
    exit 0
fi

[[ "$(uname -s)" == Linux ]] || fail "only Linux is supported"
[[ "$(uname -m)" == x86_64 ]] || fail "this pinned package requires x86_64 Linux"
for command_name in uv curl tar sha256sum systemctl sudo install cp mv ln find grep awk; do
    require_command "$command_name"
done
[[ -r "$ssh_config" ]] || fail "SSH config is not readable: $ssh_config"
ssh -F "$ssh_config" -o BatchMode=yes "$ssh_target" true \
    || fail "non-interactive SSH preflight failed for $ssh_target"

provider_version="$($python_cmd -c '
import sys, tomllib
with open(sys.argv[1], "rb") as handle: print(tomllib.load(handle)["project"]["version"])
' "$repo_root/pyproject.toml")"
provider_releases="$runtime_root/provider/releases"
provider_release="$provider_releases/$provider_version"
install -d -m 0755 "$provider_releases" "$runtime_root/cache/uv"
if [[ -e "$provider_release" ]]; then
    provider_backup="$(mktemp -d "$provider_releases/.previous-$provider_version-XXXXXX")"
    mv -- "$provider_release" "$provider_backup/release"
fi
install -d -m 0755 "$provider_release"
if ! UV_PROJECT_ENVIRONMENT="$provider_release/.venv" \
    UV_CACHE_DIR="$runtime_root/cache/uv" \
    uv sync --locked --no-dev --no-editable --project "$repo_root"; then
    rm -rf -- "$provider_release"
    if [[ -n "$provider_backup" && -d "$provider_backup/release" ]]; then
        mv -- "$provider_backup/release" "$provider_release"
    fi
    fail "provider installation failed"
fi
"$provider_release/.venv/bin/codex-local-provider" --help >/dev/null
ln -sfn "releases/$provider_version" "$runtime_root/provider/current"

codex_release_name="$codex_version-$codex_target"
codex_releases="$runtime_root/codex/releases"
installed_codex_release="$codex_releases/$codex_release_name"
install -d -m 0755 "$codex_releases"
if [[ ! -d "$installed_codex_release" ]]; then
    download_dir="$(mktemp -d)"
    extracted_dir="$download_dir/extracted"
    install -d -m 0755 "$extracted_dir"
    if [[ -n "$codex_release_dir" ]]; then
        cp -a -- "$codex_release_dir/." "$extracted_dir/"
    else
        mapfile -t release_values < <(
            "$python_cmd" -c '
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
print(p["url"]); print(p["sha256"]); print(p["asset"])
' "$release_path"
        )
        release_url="${release_values[0]}"
        release_sha256="${release_values[1]}"
        release_asset="${release_values[2]}"
        archive="$download_dir/$release_asset"
        curl --fail --location --proto '=https' --tlsv1.2 \
            --output "$archive" "$release_url"
        actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
        [[ "$actual_sha256" == "$release_sha256" ]] \
            || fail "Codex archive checksum mismatch"
        tar -xzf "$archive" -C "$extracted_dir"
        if [[ ! -f "$extracted_dir/codex-package.json" ]]; then
            mapfile -t package_manifests < <(
                find "$extracted_dir" -mindepth 2 -maxdepth 3 \
                    -type f -name codex-package.json -print
            )
            ((${#package_manifests[@]} == 1)) \
                || fail "Codex archive has an unexpected directory layout"
            package_root="$(dirname -- "${package_manifests[0]}")"
            normalized_dir="$download_dir/normalized"
            install -d -m 0755 "$normalized_dir"
            cp -a -- "$package_root/." "$normalized_dir/"
            extracted_dir="$normalized_dir"
        fi
    fi
    deployment_cli validate-release \
        --release "$release_path" "$extracted_dir"
    mv -- "$extracted_dir" "$installed_codex_release"
fi
deployment_cli validate-release \
    --release "$release_path" "$installed_codex_release"
"$installed_codex_release/bin/codex" --version | grep -F "$codex_version" >/dev/null \
    || fail "installed Codex binary did not report version $codex_version"
"$python_cmd" "$repo_root/scripts/install_cli_apparmor.py" "$installed_codex_release/bin/codex"
ln -sfn "releases/$codex_release_name" "$runtime_root/codex/current"

install -d -m 0700 "$isolated_home"
if [[ ! -e "$isolated_home/config.toml" ]]; then
    install -m 0600 "$render_dir/config.toml" "$isolated_home/config.toml"
fi
install -m 0600 "$render_dir/local.config.toml" "$isolated_home/local.config.toml"
install -m 0600 "$render_dir/model-catalog.json" "$isolated_home/model-catalog.json"
install -m 0600 "$render_dir/model-instructions.md" "$isolated_home/model-instructions.md"
install -m 0600 "$render_dir/deployment.json" "$isolated_home/deployment.json"
printf '%s\n' "$isolated_home" > "$runtime_root/codex-home"
install -d -m 0755 "$bin_dir"
install -m 0755 "$repo_root/scripts/codex-local" "$bin_dir/codex-local"
install -m 0755 "$repo_root/scripts/verify-local.sh" "$bin_dir/codex-local-verify"

sudo install -d -m 0755 /etc/codex-local
if [[ -n "$tavily_key_file" ]]; then
    sudo install -m 0600 "$tavily_key_file" /etc/codex-local/tavily.key
elif ! sudo test -e /etc/codex-local/tavily.key; then
    sudo install -m 0600 /dev/null /etc/codex-local/tavily.key
fi
sudo install -m 0644 "$render_dir/codex-local-tunnel.service" \
    /etc/systemd/system/codex-local-tunnel.service
sudo install -m 0644 "$render_dir/codex-local-provider.service" \
    /etc/systemd/system/codex-local-provider.service
sudo systemctl daemon-reload
sudo systemctl enable codex-local-tunnel.service codex-local-provider.service
sudo systemctl restart codex-local-tunnel.service
sudo systemctl restart codex-local-provider.service

"$bin_dir/codex-local-verify"
if [[ -n "$gui_mode" ]]; then
    bash "$gui_helper" "${gui_args[@]}"
fi
echo "Installed codex-local. Launch it with: $bin_dir/codex-local"
