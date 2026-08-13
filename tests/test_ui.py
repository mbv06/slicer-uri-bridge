from __future__ import annotations

import unittest
from unittest.mock import patch

from slicer_uri_bridge.ui import BUNDLE_HINT, show_bundle_hint


class BundleHintTests(unittest.TestCase):
    def test_falls_back_to_messagebox_when_custom_dialog_fails(self) -> None:
        with (
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_tk_messagebox", return_value=True) as messagebox,
        ):
            show_bundle_hint()

        messagebox.assert_called_once_with(BUNDLE_HINT, "showinfo")

    def test_skips_messagebox_when_custom_dialog_works(self) -> None:
        with (
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog", return_value=True),
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
        ):
            show_bundle_hint()

        messagebox.assert_not_called()

    def test_skips_windows_when_tkinter_is_missing(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.tkinter", None),
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog") as dialog,
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
        ):
            show_bundle_hint()

        dialog.assert_not_called()
        messagebox.assert_not_called()
