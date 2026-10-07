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

The CLI and both GUI backends use stable Codex `0.160.1` with a local patch
extending the automatic approval-review deadline from 90 to **180 seconds**.
`vendor/codex` points to the [Codex fork](https://github.com/tylercasper/codex),
branch `local/0.160.1-review-180s`, based on official tag `rust-v0.160.1`.
[`config/compatibility.json`](config/compatibility.json) records both the official
base revision and the exact patched commit. Approval policy is unchanged.

The desktop archives come from the vendor's stable channels: Windows
`26.930.7945.0` and Linux `26.1002.51308`. Windows ships backend `0.160.1`; the
Linux stable-channel app ships `0.162.0-alpha.2`. Both managed launchers select
our stable source package through `codex/current`. Frontend/backend compatibility
is recorded as an explicit archive-and-source pairing, not inferred from release
channel names or version equality. The complete vendor archives remain intact.

The Windows launcher normally starts its backend through WSL. Temporary recovery
transports for a broken host are not part of the portable installer.

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
Previous installed packages are preserved. GUI installation requires the explicitly
paired source package and validates its complete fingerprints and frontend archive.
At launch, the GUI checks
the installed package's source pin against its own binding and fails explicitly
if they differ. Updating the CLI independently cannot silently switch the GUI to
an incompatible backend. Update both with `--gui` or `--wsl-gui`.

The release tag's Cargo lockfile retains placeholder versions for workspace
crates. The builder temporarily normalizes only those local version fields,
builds with `--locked`, and restores the upstream file. External dependency
versions are not updated. Concurrent or unexpected edits cause a failure.

To select the unpatched official binary distribution explicitly:

```bash
./scripts/install-local.sh --official-release
# Or provide the already-extracted official package:
./scripts/install-local.sh --codex-release-dir /path/to/official-package
```

That CLI-only fallback uses the official `0.160.1` musl archive pin in
[`config/codex-release.json`](config/codex-release.json). It cannot be combined
with GUI installation. An existing source-bound GUI will refuse that unpatched CLI
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
`custom-stable-source`: all local clients select the same patched stable backend.
Each desktop entry records the backend shipped in its archive, the selected source
revision, and the scope of validation performed. Version alignment alone is not a
GUI test. Back up the existing profile before installation, then validate startup,
the local model picker, conversation resumption, and tool use. This pairing passed
GUI startup and **None** selection on both platforms, existing-conversation
resumption, and local llama.cpp inference with sandboxed file tools. The Windows
GUI also passed the standard Windows-to-WSL launch path.

For an upgrade, choose an official stable Codex tag, carry the small timeout patch
on the fork, and update the submodule plus manifest to the resulting commit. Obtain
the current stable-channel desktop archives and record their actual bundled
backend versions and checksums. A different bundled backend requires an explicit
`source_revision` pairing and GUI validation; do not select a prerelease backend
merely because it ships inside a stable-channel desktop package.

## Shared conversation home

`--wsl-gui` uses `%USERPROFILE%\.codex-local` for both clients. WSL accesses
the same directory through `/mnt/c/Users/<user>/.codex-local`, following
[OpenAI's shared-home guidance](https://learn.chatgpt.com/docs/windows/windows-app#share-config-auth-and-sessions-with-wsl).
Use `--codex-home /mnt/c/Users/<user>/.codex-local` to select it explicitly.
Close the local GUI applications before an upgrade so their desktop preferences
can be merged safely. The installer records that location in the private runtime's
`codex-home` file;
the CLI, Linux GUI, verification command, and subsequent installs reuse it. An
explicit `CODEX_LOCAL_HOME` overrides the recorded location for CLI and Linux GUI.
Native Linux installs also share one home between their CLI and GUI.

Both WSL backends use the documented `sqlite_home` setting to share
`~/.local/state/codex-local/sqlite`. This keeps the conversation index consistent
while the Windows GUI retains its own desktop database under the Windows home.
Skills, session transcripts, and model assets live in the shared Windows home.
The Linux-native GUI installed by `--gui` uses this same conversation home. Its
Electron settings live separately in `$XDG_DATA_HOME/codex-local/gui-user-data`
(default `~/.local/share/codex-local/gui-user-data`).

Upgrades preserve an existing `sqlite_home` value, session transcripts, desktop
state, and Electron user-data directory. The backend shim reads that configured
database path instead of substituting a new directory. Back up the existing home
and SQLite databases before changing component versions; SQLite's backup API
captures committed WAL contents, unlike a plain copy of a live database file.

Existing separate homes require a migration before switching: close the local
clients, back up their homes, preserve the Windows GUI's settings and desktop
database, and copy session transcripts and personal skills into the shared home
without overwriting conflicts. Keep the original homes until verification is
complete. The runtime can rebuild its history index from session transcripts;
do not merge SQLite files by overwriting one with another. Reopen the CLI and each
installed GUI and check conversation listing and resumption across them. An active
conversation has one writer, so release it in one client before resuming it in
the other.

## Provider and verification

The adapter translates Codex's freeform `apply_patch` protocol and `view_image`
outputs for llama.cpp, and forwards raw reasoning text into Codex's visible
reasoning channel. The GUI picker exposes **None**, **Light**, **Medium**, and
**Extra High** for the reference model. **None** disables thinking; **Light** is
`low` effort and still enables thinking. Installation enables `none` in the
desktop's separate reasoning-visibility preference while preserving its other
settings and existing per-conversation selections.

It cannot display reasoning tokens when the backend does not
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

## Workspace and runtime layout

Keep one authoritative checkout of this repository. `vendor/codex` is its pinned
upstream source; `.build/` contains disposable build outputs. Installations run
from `~/.local/lib/codex-local`, independently of task or staging directories.
The Codex protocol adapter is maintained here in `src/codex_local_provider`;
llama-server build and deployment belong to the separate `llama-deploy` repository.

The `codex-home` pointer in the runtime identifies the shared configuration and
conversation directory. Its configured `sqlite_home` identifies the matching
history databases. Treat these as one logical profile; do not select a fresh
database when reusing transcripts. Retained migration backups belong under
`~/.local/state/codex-local/backups`, outside working checkouts.

An optional private Rust toolchain can live under
`~/.local/share/codex-local/build-tools`. To use it:

```bash
export CARGO_HOME="$HOME/.local/share/codex-local/build-tools/cargo"
export RUSTUP_HOME="$HOME/.local/share/codex-local/build-tools/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
```

Keep machine-specific endpoints and paths in the private deployment configuration.
Temporary verification projects should use temporary directories and be removed
after verification; they are not additional source workspaces or user profiles.
