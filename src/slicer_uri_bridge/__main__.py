"""Allow running the CLI as ``python -m slicer_uri_bridge``."""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
