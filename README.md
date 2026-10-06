# Codex for a self-hosted llama.cpp server

This repository installs an isolated Codex CLI connected to a model served by llama.cpp.
A narrow local adapter forwards Responses traffic and supplies Codex's standalone
`web.run` tool through Tavily.

The `codex-local` command has its own Codex binary, home, model catalog, provider, and
services. The regular `codex` command and its configuration are not part of this setup.
The `-local` suffix exists only to avoid colliding with that regular installation.

## What gets installed

- `~/.local/bin/codex-local` and `codex-local-verify`
- a pinned Codex release under `~/.local/lib/codex-local/codex/`
- a versioned provider environment under `~/.local/lib/codex-local/provider/`
- isolated configuration under `~/.codex-local/`
- `codex-local-tunnel.service` and `codex-local-provider.service`
- a root-readable Tavily credential at `/etc/codex-local/tavily.key`

The network route is configurable. The checked-in deployment uses:

```text
codex-local -> 127.0.0.1:18000 adapter -> 127.0.0.1:18001 SSH tunnel
             -> configured SSH target -> 127.0.0.1:8001 llama.cpp
```

## Configure and install

Prerequisites are x86_64 Linux, Python 3.12+, `uv`, SSH access to the configured llama.cpp
host, systemd, and sudo access for the two system units and Tavily credential.

Review [`config/deployment.toml`](config/deployment.toml), especially the SSH target and
the llama.cpp port. Then validate the rendered deployment without changing anything:

```bash
./scripts/install-local.sh --dry-run
```

Install using the pinned, checksum-verified official Codex package:

```bash
./scripts/install-local.sh
```

An already-extracted package can avoid the download. It is accepted only when its package
metadata matches [`config/codex-release.json`](config/codex-release.json):

```bash
./scripts/install-local.sh --codex-release-dir /path/to/extracted/package
```

The installer preserves an existing `~/.codex-local/config.toml`. It owns and refreshes
the `local.config.toml`, model catalog, deployment manifest, private runtimes, launcher,
and service units.

Install the Tavily key during setup without adding it to this repository:

```bash
./scripts/install-local.sh --tavily-key-file /secure/path/to/tavily.key
```

If the key file already exists at `/etc/codex-local/tavily.key`, rerunning the installer
preserves it unless `--tavily-key-file` is supplied.

## Verify and launch

The verifier checks the pinned private Codex binary, both systemd services, provider
health, Tavily readiness, and the advertised model. It performs no inference and spends no
Tavily credits.

```bash
codex-local-verify
codex-local
```

For an installation that intentionally has no Tavily key yet, use
`codex-local-verify --allow-degraded`.

## Development

```bash
uv sync --locked
uv run pytest
```

The tests use mock llama.cpp and Tavily servers and consume no API credits. The adapter
normalizes late `system` or `developer` messages to the beginning of Qwen requests, which
also covers permission-review turns after a conversation has started.

## Web-search scope

- `search_query` returns titles, full URLs, snippets, and publication dates when available.
- `open` extracts an absolute HTTP(S) URL as Markdown.
- `find` searches extracted content case-insensitively.
- `click`, `screenshot`, opaque result references, and browser sessions are not implemented.

Tavily search depth defaults to `basic`. The adapter keeps no content or usage ledger and
adds no local quota system. Authentication, account limits, and rate-limit failures become
explicit tool errors.

## Operations

Edit `config/deployment.toml` in a checkout and rerun the installer to change endpoints.
Update `config/codex-release.json` only with an official release URL and verified SHA-256,
then rerun the installer to upgrade the private Codex release.

Useful diagnostics:

```bash
codex-local-verify
journalctl -u codex-local-tunnel.service -u codex-local-provider.service --since today
curl -fsS http://127.0.0.1:18000/healthz
```

To disable the harness without removing its files:

```bash
sudo systemctl disable --now codex-local-provider.service codex-local-tunnel.service
```
