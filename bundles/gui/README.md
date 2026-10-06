# Desktop package inputs

The installers use unmodified vendor desktop packages and retain each package's
bundled Codex backend. Git contains only the manifests and installation code.
Supply the archive for the GUI you intend to install in this directory:

| Platform | Archive | Vendor package version |
| --- | --- | --- |
| Native Linux | `chatgpt_amd64.deb` | `26.901.51231` |
| Windows with WSL | `codex-windows-26.901.6511.0.tar.gz` | `26.901.6511.0` |

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

Keep the frontend and bundled backend from the same vendor package. Do not replace
`resources/codex` with the separately pinned CLI binary during baseline setup.
