
# Slicer URI Bridge

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows | macOS | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()
[![Tests](https://img.shields.io/github/actions/workflow/status/mbv06/slicer-uri-bridge/ci.yml?label=tests)](https://github.com/mbv06/slicer-uri-bridge/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/mbv06/slicer-uri-bridge?logo=github)](https://github.com/mbv06/slicer-uri-bridge/releases/latest)

[Installation](#installation) · [Security Model](#security-model) · [Changelog](CHANGELOG.md)

Slicer URI Bridge helps open 3D model links from websites in Bambu Studio, including sites that do not provide a native Bambu Studio button or where that integration is not available.

https://github.com/user-attachments/assets/32b1fd48-4498-42de-81d6-629b452712b9

It registers URI handlers for other slicers (Anycubic Slicer Next, PrusaSlicer, OrcaSlicer, Cura, and Creality Print) and routes those links through a small Python bridge that downloads the model safely and opens it in Bambu Studio.

## Installation

### Windows (automatic)

Open PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass -c "iwr -useb -ErrorAction Stop https://github.com/mbv06/slicer-uri-bridge/releases/latest/download/install-windows.ps1 -OutFile ([IO.Path]::GetTempPath()+'slicer-uri-bridge-install.ps1'); & ([IO.Path]::GetTempPath()+'slicer-uri-bridge-install.ps1')"
```

The installer creates a private virtual environment in `%LOCALAPPDATA%\slicer-uri-bridge`, installs or upgrades the package there, adds the Scripts directory to the user `PATH`, initializes or upgrades config if needed, and registers URI handlers.

To install a specific version, download that release's `install-windows.ps1` or set `SLICER_URI_BRIDGE_VERSION=v0.1.4`.

After installation, open a new terminal window if the command is not found, then test the registered handler by opening a known Benchy model URI:

```powershell
slicer-uri-bridge test
```

### macOS (automatic)

Run the installer:

```bash
curl -fsSL https://github.com/mbv06/slicer-uri-bridge/releases/latest/download/install-macos.sh | bash && export PATH="$HOME/.local/bin:$PATH"
```

The installer creates a private virtual environment in `~/.local/share/slicer-uri-bridge`, installs or upgrades the package there, creates `~/.local/bin/slicer-uri-bridge`, initializes or upgrades config if needed, and registers URI handlers.

To install a specific version, download that release's `install-macos.sh` or set `SLICER_URI_BRIDGE_VERSION=v0.1.4`.

After installation, open a new Terminal window if the command is not found, then test the registered handler by opening a known Benchy model URI:

```bash
slicer-uri-bridge test
```

### Manual

First, install Python 3.11 or newer on the target system:

* Windows: install Python from [python.org](https://www.python.org/downloads/windows/) and enable the `Add python.exe to PATH` option.
* macOS: install Python from [python.org](https://www.python.org/downloads/macos/) and run the bundled `Install Certificates.command`, or install Python with [Homebrew](https://brew.sh/).
* Linux: install Python 3.11+ from your distribution package manager.

Then install the package from GitHub:

```bash
python -m pip install --upgrade https://github.com/mbv06/slicer-uri-bridge/releases/latest/download/slicer-uri-bridge-python.tar.gz
```

Installation only installs the CLI and Python package. It does not register URI handlers automatically.

### First Run

Run the CLI without arguments for interactive setup:

```bash
slicer-uri-bridge
```

This will initialize or upgrade the config and open the interactive manager, where you can choose which URI schemes to register or unregister. Use `slicer-uri-bridge -h` to see all available commands.

Automatic mode is conservative:

* `bambustudioopen` is always selected, so Bambu-style links are routed through this bridge (to support not only 3mf models).
* `acnext`, `cura`, `crealityprintlink`, `prusaslicer`, and `orcaslicer` are registered only when the system currently has no effective handler for that scheme.

To manage an existing handler, specify the scheme explicitly or select it in interactive registration:

```bash
slicer-uri-bridge manager
```

For example, if Anycubic Slicer Next already owns `acnext` and you want MakerOnline buttons routed to Bambu Studio instead:

```bash
slicer-uri-bridge register anycubic
```

This command shows the current status and lets you choose which schemes to manage:

```bash
slicer-uri-bridge status
```

## Uninstall

First, unregister all URI handlers managed by this package:

```bash
slicer-uri-bridge unregister --auto
```

Then remove the installed package or app files for your installation type.

Manual install:

```bash
python -m pip uninstall slicer-uri-bridge
```

Automatic Windows install:

```powershell
Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'slicer-uri-bridge') -Recurse -Force
```

Automatic macOS install:

```bash
rm -rf "$HOME/.local/share/slicer-uri-bridge" "$HOME/Applications/SlicerURIBridge.app"; rm -f "$HOME/.local/bin/slicer-uri-bridge"
```

## How It Works

When a slicer URI link is clicked in a browser, the OS routes it to the registered handler, which launches:

```text
python -m slicer_uri_bridge.handler "<incoming-uri>"
```

The bridge reads the user config, validates the incoming URI, downloads the model to a temporary or configured folder, checks the file type, and opens the result in Bambu Studio.

For an all-model-files ZIP such as the packs served by Printables, the bridge extracts only STL files and opens them in Bambu Studio. A message asks you to choose `No` if Bambu Studio offers to load them as a single multipart object, then press `A` to arrange the separate models for your currently selected printer. Other archive entries are ignored. Set `allow_printables_bundle = false` to disable these ZIP downloads.


The config file and log files are stored in:

* Linux/macOS: `~/.config/slicer-uri-bridge/`
* Windows: `%APPDATA%\slicer-uri-bridge\`

If `XDG_CONFIG_HOME` is set on Linux or macOS, it is used instead of `~/.config`.

## Security Model

The bridge validates downloads before opening them:

* only HTTPS URLs are allowed unless `allow_plain_http = true`
* URLs with embedded credentials are rejected
* resolved hosts must not point to local/private/reserved addresses unless `allow_local_resolved_hosts = true`
* redirect targets are revalidated
* downloaded files must use an allowed model extension
* empty files and obvious executable formats are refused
* MakerOnline `acnext` payloads are size-limited and sent only to a packaged Anycubic API endpoint (or a user `[acnext]` override); endpoints and API-returned signed URLs must use HTTPS even when `allow_plain_http = true`, must resolve to public addresses, and embedded access tokens are redacted from logs
* Printables model-pack ZIP downloads can be disabled with `allow_printables_bundle`; only 1–128 STL entries are extracted, with at most 16 files per sanitized name and a 512 MiB total-size limit
* 3MF files are checked for embedded post-processing scripts ([scripts that can run after slicing](https://manual.slic3r.org/advanced/post-processing))

By default, downloads are accepted from any public host. To restrict initial URLs supplied directly by protocol links, set `allow_any_original_host = false`. The packaged host list (Printables, Thingiverse, Creality, and similar) is always included and updates with the bridge; add more with `extra_allowed_hosts`. This allowlist is not applied to redirect targets or to signed URLs returned by a MakerOnline API; those destinations are instead required to use HTTPS and resolve to public addresses.

Allowed model extensions are also packaged and update with the bridge. Add more with `extra_allowed_extensions`.

MakerOnline API URLs ship in the packaged [`package_config.toml`](src/slicer_uri_bridge/resources/package_config.toml). The link's `regionCn` and `prod` flags only select one of those four values; the link cannot provide an endpoint directly. Put `[acnext]` in your user config only to override a packaged URL. Treat overrides as trust settings because the selected endpoint receives the access token embedded in the link.

User options are described in the bundled [`default_config.toml`](src/slicer_uri_bridge/resources/default_config.toml) template and copied into the generated `config.toml` file. After upgrading the package, run `slicer-uri-bridge init-config` to add any new user options to an existing config; current values and comments are left unchanged. Packaged hosts, extensions, and MakerOnline endpoints are not copied into the user file, so they follow the installed package version. Use `init-config --force` to replace the user file with the bundled template. The automatic installers already run `init-config` during upgrades.

## Troubleshooting

The bridge writes log files next to the config file. To find their location:

```bash
slicer-uri-bridge config-path
```

Log files in that directory record each handler invocation and can help diagnose download failures, URI parsing issues, or slicer launch problems.

If the `slicer-uri-bridge` command is not found after installation, make sure the Python scripts directory is on your `PATH`. On macOS with the automatic installer, open a new Terminal window. On Windows, ensure the `Add python.exe to PATH` option was enabled during Python installation, or use the automatic installer above. As a fallback, you can always run `python -m slicer_uri_bridge` instead of `slicer-uri-bridge`.

If URI links do not open after registration, verify the current handler status:

```bash
slicer-uri-bridge status
```

Then try re-registering with `slicer-uri-bridge register` and select the scheme you want to replace. 

To verify the default `bambustudioopen` handler, run:

```bash
slicer-uri-bridge test
```

## Known Limitations

On Windows, OrcaSlicer re-registers the `orcaslicer://` URI scheme to itself on every launch. Registering this scheme to the bridge only makes sense if you do not use OrcaSlicer.

## Supported URI Formats

Supported URI formats include:

```text
bambustudioopen://https%3A%2F%2F...
acnext://open?jsonvalue=BASE64_ENCODED_JSON&timestamp=...
cura://open?file=https%3A%2F%2F...
crealityprintlink://open?file=https%3A%2F%2F...
prusaslicer://open?file=https%3A%2F%2F...
orcaslicer://open?file=https%3A%2F%2F...
```
