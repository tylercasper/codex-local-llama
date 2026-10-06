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

The CLI and both GUI backends are built from official Codex `0.153.4`, pinned by
`vendor/codex` and [`config/compatibility.json`](config/compatibility.json).
The default source target is `x86_64-unknown-linux-gnu`; build it on the Linux
system where it will run. Both recorded desktop packages shipped with backend
`0.153.4`. Their frontend archives remain unchanged; the managed launchers select
the complete source package at the same `codex/current` link used by the CLI.

The Windows launcher starts the source backend through WSL normally. The
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
Previous installed packages are preserved. GUI installation requires a matching
source package and validates its complete fingerprints. At launch, the GUI checks
the installed package's source pin against its own binding and fails explicitly
if they differ. Updating the CLI independently cannot silently switch the GUI to
an incompatible backend. Update both with `--gui` or `--wsl-gui`.

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

That CLI-only fallback retains the original `0.147.0` musl archive pin in
[`config/codex-release.json`](config/codex-release.json). It cannot be combined
with GUI installation. An existing source-bound GUI will refuse that older CLI
until the matching source package is restored. `--dry-run` renders the selected
configuration without compiling or changing services.

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
`synchronized-source`: CLI and desktop select the same official source revision.
The per-platform validation fields distinguish protocol checks from interactive
GUI acceptance; version alignment alone is not a GUI test.

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

Upgrades preserve an existing `sqlite_home` value, session transcripts, desktop
state, and Electron user-data directory. The backend shim reads that configured
database path instead of substituting a new directory. Back up the existing home
and SQLite databases before changing component versions; SQLite's backup API
captures committed WAL contents, unlike a plain copy of a live database file.

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
