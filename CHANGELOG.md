# Changelog

All notable changes to this project are documented here.

## v0.1.2 - 2026-05-20

### Added

- Add optional 3MF post-processing script checks. By default, the bridge notifies the user, writes the script text to the log, and then continues opening the file.

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
