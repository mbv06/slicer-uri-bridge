# Changelog

All notable changes to this project are documented here.

## v0.1.4 - 2026-08-13

### Added

- Add support for Printables model-pack ZIP links: only STL files are extracted and opened as separate models using the user's current Bambu Studio presets.
- Show a hint to keep the STL files as separate objects and press `A` in Bambu Studio to arrange them for the selected printer.
- Add `security.allow_printables_bundle`, enabled by default, to allow disabling model-pack ZIP downloads.
- Add config upgrade on `init-config`: missing options from the bundled template are added to an existing `config.toml` without overwriting current values.
- Use `tomlkit` to edit `config.toml` while preserving comments and layout. The dependency is pinned to `0.15.1` in `pyproject.toml`, with SHA256 hashes in `requirements.lock`.
- Publish GitHub Releases with a versioned sdist, a zip that includes `requirements.lock`, and installers pinned to that tag. The installers unpack the zip and install hashed dependencies before the package.


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
