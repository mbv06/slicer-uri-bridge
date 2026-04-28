# Slicer URI Bridge

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows | macOS | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

Slicer URI Bridge helps open 3D model links from websites in Bambu Studio, including sites that do not provide a native Bambu Studio button or where that integration is not available.

https://github.com/user-attachments/assets/c64cba28-f985-4d97-a1d6-0b107dd55ef1

It registers URI handlers for other slicers (PrusaSlicer, OrcaSlicer, Cura, and Creality Print) and routes those links through a small Python bridge that downloads the model safely and opens it in Bambu Studio.

## Installation

### Windows (automatic)

Open PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass -c "iwr -useb https://raw.githubusercontent.com/mbv06/slicer-uri-bridge/main/install-windows.ps1 | iex"
```

The installer creates a private virtual environment in `%LOCALAPPDATA%\slicer-uri-bridge`, installs or upgrades the package there, adds the Scripts directory to the user `PATH`, initializes config if needed, and registers URI handlers.

After installation, open a new terminal window if the command is not found, then test the registered handler by opening a known Benchy model URI:

```powershell
slicer-uri-bridge test
```

### macOS (automatic)

Run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/mbv06/slicer-uri-bridge/main/install-macos.sh | bash && export PATH="$HOME/.local/bin:$PATH"
```

The installer creates a private virtual environment in `~/.local/share/slicer-uri-bridge`, installs or upgrades the package there, creates `~/.local/bin/slicer-uri-bridge`, initializes config if needed, and registers URI handlers.

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
python -m pip install https://github.com/mbv06/slicer-uri-bridge/archive/refs/heads/main.zip
```

Installation only installs the CLI and Python package. It does not register URI handlers automatically.

### First Run

Run the CLI without arguments for interactive setup:

```bash
slicer-uri-bridge
```

This will initialize the config and open the interactive manager, where you can choose which URI schemes to register or unregister. Use `slicer-uri-bridge -h` to see all available commands.

Automatic mode is conservative:

* `bambustudioopen` is always selected, so Bambu-style links are routed through this bridge (to support not only 3mf models).
* `cura`, `crealityprintlink`, `prusaslicer`, and `orcaslicer` are registered only when the system currently has no effective handler for that scheme.

To manage an existing handler, specify the scheme explicitly or select it in interactive registration:

```bash
slicer-uri-bridge manager
```

This command shows the current status and lets you choose which schemes to manage:

```bash
slicer-uri-bridge status
```

## Uninstall

Unregister all URI handlers managed by this package:

```bash
slicer-uri-bridge unregister --auto
```

You can also delete the config and log files manually. Find their location with:

```bash
slicer-uri-bridge config-path
```

Then remove the package:

```bash
pip uninstall slicer-uri-bridge
```

## How It Works

When a slicer URI link is clicked in a browser, the OS routes it to the registered handler, which launches:

```text
python -m slicer_uri_bridge.handler "<incoming-uri>"
```

The bridge reads the user config, validates the incoming URI, downloads the model to a temporary or configured folder, checks the file type, and opens the result in Bambu Studio.

The config file and log files are stored in:

* Linux/macOS: `~/.config/slicer-uri-bridge/`
* Windows: `%APPDATA%\slicer-uri-bridge\`

If `XDG_CONFIG_HOME` is set on Linux or macOS, it is used instead of `~/.config`. The config includes allowed download hosts, allowed model file extensions, optional download folder, and platform-specific Bambu Studio paths. Print the active path with `slicer-uri-bridge config-path`.

## Security Model

The bridge validates downloads before opening them:

* only HTTPS URLs are allowed unless `allow_plain_http = true`
* URLs with embedded credentials are rejected
* resolved hosts must not point to local/private/reserved addresses
* redirect targets are revalidated
* downloaded files must use an allowed model extension
* empty files and obvious executable formats are refused

By default, downloads are accepted from any host. To restrict downloads to specific hosts, set `allow_any_original_host = false` in the config and use the `allowed_hosts` list (the default config includes CDNs for Printables, Thingiverse, and Creality).

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
cura://open?file=https%3A%2F%2F...
crealityprintlink://open?file=https%3A%2F%2F...
prusaslicer://open?file=https%3A%2F%2F...
orcaslicer://open?file=https%3A%2F%2F...
```
