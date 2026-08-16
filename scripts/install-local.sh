#!/usr/bin/env bash
set -euo pipefail

repo_root=/home/example/agent-sandbox/codex-altair-provider
source_release=/home/example/.codex/packages/standalone/releases/0.147.0-x86_64-unknown-linux-musl
pinned_release=/home/example/.local/lib/codex-altair/0.147.0
isolated_home=/home/example/.codex-altair

if [[ ! -x "$source_release/bin/codex" ]]; then
    echo "Pinned Codex source binary not found: $source_release/bin/codex" >&2
    exit 1
fi
if [[ ! -x "$repo_root/.venv/bin/codex-altair-provider" ]]; then
    echo "Run 'uv sync --locked' in $repo_root before installing." >&2
    exit 1
fi

install -d -m 0755 /home/example/.local/lib/codex-altair
install -d -m 0755 "$pinned_release"
cp -a "$source_release/." "$pinned_release/"

install -d -m 0700 "$isolated_home"
install -m 0600 "$repo_root/config/codex-altair.toml" "$isolated_home/config.toml"
install -m 0600 "$repo_root/config/model-catalog.json" "$isolated_home/model-catalog.json"
install -m 0755 "$repo_root/scripts/codex-altair" /home/example/.local/bin/codex-altair

sudo install -d -m 0755 /etc/codex-altair
if ! sudo test -e /etc/codex-altair/tavily.key; then
    sudo install -m 0600 /dev/null /etc/codex-altair/tavily.key
fi
sudo install -m 0644 \
    "$repo_root/systemd/codex-altair-tunnel.service" \
    /etc/systemd/system/codex-altair-tunnel.service
sudo install -m 0644 \
    "$repo_root/systemd/codex-altair-provider.service" \
    /etc/systemd/system/codex-altair-provider.service
sudo systemctl daemon-reload
sudo systemctl enable --now codex-altair-tunnel.service codex-altair-provider.service

echo "Installed codex-altair. Tavily search becomes ready after /etc/codex-altair/tavily.key contains a key."
