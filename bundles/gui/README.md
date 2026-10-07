# Desktop package inputs

The installers use unmodified vendor desktop packages and select the matching
source-built Codex backend through a private launcher. Git contains only the
manifests and installation code.
Supply the archive for the GUI you intend to install in this directory:

| Platform | Archive | Vendor package version |
| --- | --- | --- |
| Native Linux | `chatgpt_amd64.deb` | `26.1002.51308` |
| Windows with WSL | `codex-windows-26.930.7945.0.tar.gz` | `26.930.7945.0` |

`linux.json` and `windows.json` record the required SHA-256 hashes. Installation
fails on missing or mismatched inputs. There is currently no automated downloader
or public artifact mirror for these desktop archives. A Git clone alone is
sufficient for the CLI installer and source tests, but requires the matching
vendor archive for desktop installation.

The Linux input is the original `.deb`. The Windows archive contains the clean
contents of the official package's `app` directory, including `ChatGPT.exe`,
`resources/codex`, and `resources/app.asar` at those relative paths. It excludes
user data, local configuration, and generated Codex Local launchers. The manifest
hash identifies the exact packaged archive; repacking the same directory can
produce a different hash and must be reviewed as an explicit manifest change.

Keep each archive intact, including its bundled backend. The installer does not
replace `resources/codex`: `CODEX_CLI_PATH` selects a shim that validates and runs
the complete source package shared with the CLI. The compatibility manifest
records the actual bundled backend and pins the selected source revision for each
frontend. The Linux stable-channel package currently bundles a prerelease backend;
our explicit pairing keeps the active backend on stable `0.160.1`.

The Linux archive comes from the official stable APT repository, verified against
its signed `InRelease` and package checksums. The Windows archive is created from
the official Microsoft Store MSIX after WinGet verifies its package hash. Neither
archive contains user configuration, credentials, or conversation data.
