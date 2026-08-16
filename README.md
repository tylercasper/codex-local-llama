# Codex Altair Provider

This repository supplies the smallest compatibility layer needed to use the pinned Codex CLI
with the Qwen model served by llama.cpp on `llama-server`, including Codex's first-class standalone
`web.run` tool backed by Tavily.

It does **not** implement a generic Responses compatibility gateway. Normal Responses traffic is
forwarded to llama.cpp. The adapter changes only the `web.run` namespace that llama.cpp does not
currently understand, translating it to one ordinary `web_run` function and restoring the
namespace on the resulting call.

## Runtime layout

- `codex-altair` uses `CODEX_HOME=/home/example/.codex-altair` and a private copy of Codex 0.147.0.
- A one-entry model catalog records Qwen's real context and tool metadata without fallback guesses.
- `127.0.0.1:18000` is the adapter exposed to Codex.
- `127.0.0.1:18001` is an SSH tunnel to `llama-server:127.0.0.1:8001`.
- `/v1/responses` streams to llama.cpp.
- `/v1/alpha/search` maps Codex search, URL open, and find commands to Tavily.

The existing `codex` command, `~/.codex` configuration, ChatGPT authentication, and sessions are
not used or modified.

## Development

```bash
uv sync
uv run pytest
```

The tests use mock llama.cpp and Tavily servers and consume no API credits.

## Install

```bash
uv sync --locked
./scripts/install-local.sh
```

The installer creates system services named `codex-altair-tunnel` and
`codex-altair-provider`. It also creates an empty, root-only credential file at
`/etc/codex-altair/tavily.key` if one does not already exist.

Install a Tavily key without writing it to this repository or the Codex environment:

```bash
sudo install -m 0600 /dev/stdin /etc/codex-altair/tavily.key
sudo systemctl restart codex-altair-provider.service
```

Paste only the key on standard input, then send EOF. Check readiness with:

```bash
curl -fsS http://127.0.0.1:18000/healthz
systemctl --no-pager --full status codex-altair-tunnel.service codex-altair-provider.service
```

`/healthz` returns HTTP 503 with `status: degraded` until both llama.cpp and the mandatory Tavily
credential are ready. It does not spend a Tavily credit to check readiness.

Run the isolated agent with:

```bash
codex-altair
```

## Web-search scope

- `search_query` returns titles, full URLs, snippets, and publication dates when available.
- `open` extracts an absolute HTTP(S) URL as Markdown.
- `find` searches extracted content case-insensitively.
- `click`, `screenshot`, opaque result references, and browser sessions are deliberately absent.

Tavily search depth defaults to `basic`. The adapter persists neither content nor credit usage and
does not impose a local monthly cap. Tavily account and billing settings remain the enforcement
boundary; authentication, quota, and rate-limit failures are returned as explicit tool errors.

## Diagnostics

```bash
journalctl -u codex-altair-tunnel.service -u codex-altair-provider.service --since today
CODEX_HOME=/home/example/.codex-altair codex-altair doctor --json
```

Logs contain operation names, statuses, and latency, not queries, page contents, API keys, or a
usage ledger.
