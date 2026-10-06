# Codex Local

Install Codex with the model and connection configured in [`config/deployment.toml`](config/deployment.toml). Requires x86_64 Linux, Python 3.12+, Git, `uv`, systemd, sudo, and working SSH access to the configured server. The checked-in `llama-server` SSH alias is a placeholder; configure it locally or pass `--config /path/to/deployment.toml`. Web search works without a key; optionally supply Tavily with `--tavily-key-file PATH`, with automatic keyless fallback.

Initialize the pinned backend source, then install:

```bash
git submodule update --init --recursive
./scripts/install-local.sh            # CLI only
./scripts/install-local.sh --gui      # Native GUI and CLI
./scripts/install-local.sh --wsl-gui  # Windows GUI with WSL execution, and CLI
```

Launch `codex-local` or the **Codex Local** application shortcut. GUI packages must be supplied locally as described in [`bundles/gui/README.md`](bundles/gui/README.md); their manifests are versioned, but the archives are excluded from Git. Existing official installations are preserved. Run `./scripts/install-local.sh --help` for configuration and verification options.

The CLI is built from the official Codex `0.147.0` revision pinned by
`vendor/codex` and [`config/compatibility.json`](config/compatibility.json).
The default source target is `x86_64-unknown-linux-gnu`; build it on the Linux
system where it will run. This baseline retains each desktop package's bundled
backend. Both recorded GUI packages include backend `0.153.4`; GUI pairing with a
source-built backend remains pending and is explicitly recorded in the manifest.

The Windows launcher starts the bundled backend through WSL normally. The
temporary persistent WebSocket app-server used to recover a broken WSL launch
is not part of this installer.

## Building and selecting the CLI

Install the toolchain recorded in `vendor/codex/codex-rs/rust-toolchain.toml`
(currently Rust 1.95.0). Ubuntu build prerequisites are a C/C++ compiler, CMake,
pkg-config, `libcap-dev`, `libssl-dev`, and `protobuf-compiler`. The upstream
builder downloads checksum-verified V8, ripgrep, and zsh artifacts. Rust crate
dependencies are locked. Building does not modify installed apps or services.

```bash
python3 scripts/build-codex.py --jobs 2 --output /path/to/source-package
./scripts/install-local.sh --codex-source-package /path/to/source-package
```

The installer builds that package automatically when no package argument is
provided. The package includes `codex`, `codex-code-mode-host`, `bwrap`, `rg`, and
the upstream shell resource. `codex-local-build.json` records its source pin,
toolchain, resolved compatibility manifest, and file checksums. Installation
checks those fingerprints and uses a separate versioned runtime directory.
The previous official package is preserved.

The release tag's Cargo lockfile retains placeholder versions for workspace
crates. The builder temporarily normalizes only those local version fields,
builds with `--locked`, and restores the upstream file. External dependency
versions are not updated. Concurrent or unexpected edits cause a failure.

To select the previous official binary distribution explicitly:

```bash
./scripts/install-local.sh --official-release
# Or provide the already-extracted official package:
./scripts/install-local.sh --codex-release-dir /path/to/official-package
```

That fallback retains the original musl archive pin in
[`config/codex-release.json`](config/codex-release.json). The source package is
a native GNU/Linux build of the same revision, not a byte-identical copy of the
official archive. `--dry-run` renders the selected configuration without compiling
or changing services.

## Compatibility record

`config/compatibility.json`, the Git submodule entry, and the GUI archive
manifests together identify the component set. The reference inference record
pins llama.cpp, model/projector checksums, and the chat template without hostnames,
addresses, or credentials. Other inference servers can be configured locally;
the reference describes the configuration used for acceptance.

```bash
PYTHONPATH=src python3 -m codex_local_provider.compatibility check
PYTHONPATH=src python3 -m codex_local_provider.compatibility resolve
```

Installation saves the resolved record as `compatibility.json` in the selected
Codex home. Source packages retain their build record in the runtime directory.
Passing acceptance results belong with the tested Git commit/release; this
manifest does not itself certify that tests passed. The current stage is
`cli-source-baseline`, with desktop backends still supplied by their vendor
packages. Source/backend synchronization is a separate acceptance milestone.

## Shared Windows GUI and WSL CLI home

`--wsl-gui` uses `%USERPROFILE%\.codex-local` for both clients. WSL accesses
the same directory through `/mnt/c/Users/<user>/.codex-local`, following
[OpenAI's shared-home guidance](https://learn.chatgpt.com/docs/windows/windows-app#share-config-auth-and-sessions-with-wsl).
Use `--codex-home /mnt/c/Users/<user>/.codex-local` to select it explicitly.
The installer records that location in the private runtime's `codex-home` file;
the CLI, verification command, and subsequent installs reuse it. An explicit
`CODEX_LOCAL_HOME` overrides the recorded location.

Both WSL backends use the documented `sqlite_home` setting to share
`~/.local/state/codex-local/sqlite`. This keeps the conversation index consistent
while the Windows GUI retains its own desktop database under the Windows home.
Skills, session transcripts, and model assets live in the shared Windows home.
The Linux-native GUI installed by `--gui` keeps its separate home.

Existing separate homes require a migration before switching: close both local
clients, back up their homes, preserve the Windows GUI's settings and desktop
database, and copy session transcripts and personal skills into the shared home
without overwriting conflicts. Keep the original homes until verification is
complete. The runtime can rebuild its history index from session transcripts;
do not merge SQLite files by overwriting one with another. Reopen both clients
and check conversation listing and resumption in both directions. An active
conversation has one writer, so release it in one client before resuming it in
the other.

## Provider and verification

The adapter translates Codex's freeform `apply_patch` protocol and `view_image`
outputs for llama.cpp, and forwards raw reasoning text into Codex's visible
reasoning channel. It cannot display reasoning tokens when the backend does not
generate them. The default effort is `none`; the catalog also exposes `low`,
`medium`, and `xhigh`, with approval reviews kept at `none`. The active custom
prompt also discourages reasoning; its source and license are in [`prompts/`](prompts/).

Search supports `search_query`, `open` with absolute URLs, and `find`. It prefers
Tavily when configured and falls back to DuckDuckGo/Bing and public-page text
extraction. Browser sessions, screenshots, `click`, and opaque result references
are not implemented. Keyless search depends on external HTML/RSS interfaces.

```bash
./scripts/install-local.sh --dry-run
codex-local-verify
uv sync --locked
uv run pytest
```

Tests use local fixtures; the Windows archive integrity check skips when that
optional archive is absent. Set `CODEX_LOCAL_GUI_BUNDLE_DIR` to its directory to
run the integrity check without copying the archive into the checkout. The
verification command checks services, provider health, and model discovery;
it does not run inference. Disposable VM acceptance tools and their prerequisites
are described in [`scripts/validation/README.md`](scripts/validation/README.md).
