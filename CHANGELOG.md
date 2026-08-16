# Changelog

All notable changes to this project are documented here.

## v0.1.6 - 2026-08-16

### Added

- Add support for MakerOnline `acnext://open` links by exchanging their validated payload for a signed model URL through a configured Anycubic download API.

### Security

- Redact the access token embedded in `acnext` links from handler and macOS launcher logs.
- Restrict MakerOnline token exchange requests to locally configured Anycubic API endpoints, then treat their returned signed-download URLs like redirect targets while still requiring HTTPS and public network addresses.

## v0.1.5 - 2026-08-14

### Changed

- Raise the download and model-pack size limit from 200 MiB to 512 MiB to accommodate large model-pack ZIPs. This is the shared DoS bound for HTTPS downloads and extracted STL bytes.

### Added

- Show native dialogs on macOS (`osascript`) and Linux (zenity/kdialog) when tkinter is missing or cannot display windows from the background URI handler. Windows still uses tkinter.

### Fixed

- Show the Printables STL pack hint and error dialogs on macOS when the handler is launched from the AppleScript URI applet.
- Raise model-pack validation failures as `BridgeError` so the user sees the specific reason instead of a generic zip-open error.
- Bring tkinter error and warning dialogs to the front on Windows so they are not hidden behind other windows when the URI handler launches.

## v0.1.4 - 2026-08-13

### Added

- Add support for Printables model-pack ZIP links: only STL files are extracted and opened as separate models using the user's current Bambu Studio presets. Packs with more than 128 STL files, or more than 16 files that share a sanitized name, are rejected.
- Show a hint to keep the STL files as separate objects and press `A` in Bambu Studio to arrange them for the selected printer.
- Add `security.allow_printables_bundle`, enabled by default, to allow disabling model-pack ZIP downloads.
- Add config upgrade on `init-config`: missing options from the bundled template are added to an existing `config.toml` without overwriting current values.
- Use `tomlkit` to edit `config.toml` while preserving comments and layout. The dependency is pinned to `0.15.1` in `pyproject.toml`, with SHA256 hashes in `requirements.lock`.
- Publish GitHub Releases with a versioned sdist, a zip that includes `requirements.lock`, and installers pinned to that tag. The installers unpack the zip and install hashed dependencies before the package.
- Add a `dev` prerelease channel with a stable install URL for test builds, without affecting the latest stable release.


## v0.1.3 - 2026-06-03

### Added

- Add `allow_local_resolved_hosts` as a security setting for cases where you intentionally want to allow downloads from trusted hosts that resolve to local addresses.

## v0.1.2 - 2026-05-20

### Added

- Add optional 3MF post-processing script checks. By default, the bridge notifies the user, writes the script text to the log, and then continues opening the file.
- Add `post_process_action` config support with `warn`, `block`, and `ignore` modes.

### Fixed

- Ignore empty `bambustudioopen` URI launches without showing an error or running the download flow.
- Improve diagnostics for malformed slicer links by logging the original input URI when URL extraction fails.

## v0.1.1 - 2026-04-28

### Added

- Add an automatic Windows installer that sets up the bridge and registers URI handlers.
- Add `python -m slicer_uri_bridge` support as a fallback way to run the CLI when the script entry point is not on `PATH`.

## v0.1.0 - 2026-04-28

### Initial Release

- Add the first Slicer URI Bridge CLI for routing slicer links to Bambu Studio.
- Support Bambu Studio, PrusaSlicer, OrcaSlicer, Cura, and Creality Print style URI links.
- Add safe model download validation before opening files in Bambu Studio.
- Add interactive URI handler registration and status management.
- Add an automatic macOS installer.
