from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slicer_uri_bridge.ui import _macos_dialog_script_file


@unittest.skipUnless(sys.platform == "darwin", "requires macOS osacompile")
class MacOSDialogCompileTests(unittest.TestCase):
    def test_dialog_script_osacompiles(self) -> None:
        osacompile = shutil.which("osacompile")
        if not osacompile:
            self.skipTest("osacompile was not found")

        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "macos-dialog.scpt"
            completed = subprocess.run(
                [osacompile, "-o", str(output_path), str(_macos_dialog_script_file())],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output_path.is_file())


if __name__ == "__main__":
    unittest.main()
