from __future__ import annotations

import stat
import tomllib
import unittest
import uuid
from collections import UserDict
from pathlib import Path
from unittest.mock import patch

from slicer_uri_bridge.config import (
    _leaf_paths,
    default_config_text,
    init_user_config,
    upgrade_config_text,
    upgrade_user_config,
)

DEFAULT = """# download_folder = "/tmp/models"

[security]
allow_plain_http = false
allow_any_original_host = true
# Allow Printables ZIP packs.
allow_printables_bundle = true
allowed_extensions = [".3mf", ".stl"]
# Values: "ignore", "warn", or "block".
post_process_action = "warn"
allowed_hosts = [
  "files.printables.com",
  "cdn.thingiverse.com",
]

[bambu_studio]
windows = 'C:\\Program Files\\Bambu Studio\\bambu-studio.exe'
macos = "/Applications/BambuStudio.app"
linux = "~/Applications/BambuStudio.AppImage"
"""

TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def write_temp_config(text: str) -> Path:
    TEMP_ROOT.mkdir(exist_ok=True)
    path = TEMP_ROOT / f"config-{uuid.uuid4().hex}.toml"
    path.write_text(text, encoding="utf-8")
    return path


class UpgradeConfigTextTests(unittest.TestCase):
    def test_complete_config_is_unchanged(self) -> None:
        updated, added = upgrade_config_text(DEFAULT, DEFAULT)
        self.assertEqual(added, [])
        self.assertEqual(updated, DEFAULT)

    def test_adds_missing_key_without_changing_existing_values(self) -> None:
        user = """[security]
allow_plain_http = true
allow_any_original_host = false
allowed_extensions = [".3mf"]
post_process_action = "block"
allowed_hosts = ["example.com"]

[bambu_studio]
windows = 'D:\\Bambu\\bambu-studio.exe'
macos = "/custom/BambuStudio.app"
linux = "/custom/BambuStudio.AppImage"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, ["security.allow_printables_bundle"])
        merged = tomllib.loads(updated)
        self.assertTrue(merged["security"]["allow_plain_http"])
        self.assertFalse(merged["security"]["allow_any_original_host"])
        self.assertEqual(merged["security"]["post_process_action"], "block")
        self.assertEqual(merged["security"]["allowed_hosts"], ["example.com"])
        self.assertEqual(merged["bambu_studio"]["windows"], r"D:\Bambu\bambu-studio.exe")
        self.assertTrue(merged["security"]["allow_printables_bundle"])
        self.assertIn("Allow Printables ZIP packs.", updated)
        self.assertIn("allow_printables_bundle = true", updated)
        self.assertIn("allow_plain_http = true", updated)

    def test_adds_missing_table_from_template(self) -> None:
        user = """[security]
allow_plain_http = false
allow_any_original_host = true
allow_printables_bundle = true
allowed_extensions = [".3mf"]
post_process_action = "warn"
allowed_hosts = ["example.com"]
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(
            added,
            ["bambu_studio.windows", "bambu_studio.macos", "bambu_studio.linux"],
        )
        merged = tomllib.loads(updated)
        self.assertEqual(merged["bambu_studio"]["macos"], "/Applications/BambuStudio.app")
        self.assertIn("[bambu_studio]", updated)
        self.assertNotIn("bambu_studio.macos =", updated)

    def test_preserves_unknown_user_keys(self) -> None:
        user = """extra = 1

[security]
allow_plain_http = false
allow_any_original_host = true
allow_printables_bundle = true
allowed_extensions = [".3mf"]
post_process_action = "warn"
allowed_hosts = ["example.com"]
custom_flag = true

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, [])
        merged = tomllib.loads(updated)
        self.assertEqual(merged["extra"], 1)
        self.assertTrue(merged["security"]["custom_flag"])

    def test_empty_config_is_replaced_with_default(self) -> None:
        updated, added = upgrade_config_text(" \n", DEFAULT)
        self.assertEqual(updated, DEFAULT)
        self.assertIn("security.allow_plain_http", added)
        self.assertIn("bambu_studio.linux", added)

    def test_comment_only_config_keeps_comments_and_adds_defaults(self) -> None:
        updated, added = upgrade_config_text("# keep this note\n", DEFAULT)
        self.assertIn("security.allow_plain_http", added)
        self.assertIn("bambu_studio.linux", added)
        self.assertIn("# keep this note", updated)
        self.assertIn("[security]", updated)
        self.assertIn("allow_printables_bundle = true", updated)

    def test_leaf_paths_walks_mapping_that_is_not_a_dict(self) -> None:
        node: UserDict[str, object] = UserDict({"security": UserDict({"allow_plain_http": False})})
        self.assertNotIsInstance(node, dict)
        self.assertEqual(_leaf_paths(node, ()), ["security.allow_plain_http"])

    def test_does_not_merge_list_values(self) -> None:
        user = """[security]
allow_plain_http = false
allow_any_original_host = true
allow_printables_bundle = true
allowed_extensions = [".3mf"]
post_process_action = "warn"
allowed_hosts = ["example.com"]

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, [])
        self.assertEqual(tomllib.loads(updated)["security"]["allowed_hosts"], ["example.com"])

    def test_preserves_user_comments_on_existing_keys(self) -> None:
        user = """[security]
# keep this
allow_plain_http = true
allow_any_original_host = false
allowed_extensions = [".3mf"]
post_process_action = "block"
allowed_hosts = ["example.com"]

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, ["security.allow_printables_bundle"])
        self.assertIn("# keep this", updated)
        self.assertIn("allow_plain_http = true", updated)

    def test_adds_multiline_array_with_dotted_key(self) -> None:
        user = """[security]
allow_plain_http = false
allow_any_original_host = true
allow_printables_bundle = true
allowed_extensions = [".3mf"]
post_process_action = "warn"

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, ["security.allowed_hosts"])
        merged = tomllib.loads(updated)
        self.assertEqual(merged["security"]["allowed_hosts"][0], "files.printables.com")
        self.assertIn("allowed_hosts = [", updated)

    def test_adds_dotted_key_when_user_has_no_table_header(self) -> None:
        user = """security.allow_plain_http = false
security.allow_any_original_host = true
security.allowed_extensions = [".3mf"]
security.post_process_action = "warn"
security.allowed_hosts = ["example.com"]

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        updated, added = upgrade_config_text(user, DEFAULT)
        self.assertEqual(added, ["security.allow_printables_bundle"])
        merged = tomllib.loads(updated)
        self.assertTrue(merged["security"]["allow_printables_bundle"])
        self.assertFalse(merged["security"]["allow_plain_http"])
        self.assertIn("allow_printables_bundle = true", updated)

    def test_real_default_template_can_upgrade_an_old_config(self) -> None:
        old = """[security]
allow_plain_http = true
allow_any_original_host = false
allowed_extensions = [".3mf"]
allowed_hosts = ["example.com"]

[bambu_studio]
windows = 'D:\\custom\\bambu-studio.exe'
macos = "/custom/BambuStudio.app"
linux = "/custom/BambuStudio.AppImage"
"""
        updated, added = upgrade_config_text(old)
        merged = tomllib.loads(updated)
        self.assertIn("security.allow_local_resolved_hosts", added)
        self.assertIn("security.allow_printables_bundle", added)
        self.assertIn("security.post_process_action", added)
        self.assertIn("acnext.global_production_endpoint", added)
        self.assertTrue(merged["security"]["allow_plain_http"])
        self.assertFalse(merged["security"]["allow_any_original_host"])
        self.assertEqual(merged["bambu_studio"]["linux"], "/custom/BambuStudio.AppImage")
        self.assertFalse(merged["security"]["allow_local_resolved_hosts"])
        self.assertTrue(merged["security"]["allow_printables_bundle"])
        self.assertEqual(merged["security"]["post_process_action"], "warn")
        self.assertEqual(
            merged["acnext"]["global_production_endpoint"],
            "https://api.makeronline.com/file/fileService/download",
        )

    def test_real_default_template_preserves_custom_acnext_endpoint(self) -> None:
        custom_endpoint = "https://maker-proxy.example/v2/download"
        user = (
            default_config_text()
            .replace(
                'global_production_endpoint = "https://api.makeronline.com/file/fileService/download"',
                f'global_production_endpoint = "{custom_endpoint}"',
            )
            .replace(
                'china_development_endpoint = "https://common-mo-itdev-cn.anycubic.com/file/fileService/download"\n',
                "",
            )
        )

        updated, added = upgrade_config_text(user)
        merged = tomllib.loads(updated)

        self.assertEqual(added, ["acnext.china_development_endpoint"])
        self.assertEqual(merged["acnext"]["global_production_endpoint"], custom_endpoint)


class UpgradeUserConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEMP_ROOT.exists():
            for path in TEMP_ROOT.glob("config-*.toml.bak"):
                path.unlink(missing_ok=True)
            for path in TEMP_ROOT.glob("config-*.toml"):
                path.unlink(missing_ok=True)
            for path in TEMP_ROOT.glob(".config-*.toml.tmp"):
                path.unlink(missing_ok=True)

    def test_writes_only_when_keys_are_missing(self) -> None:
        path = write_temp_config(default_config_text())
        mtime = path.stat().st_mtime_ns
        added = upgrade_user_config(path)
        self.assertEqual(added, [])
        self.assertEqual(path.read_text(encoding="utf-8"), default_config_text())
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertFalse(path.with_name(f"{path.name}.bak").exists())

    def test_empty_file_is_replaced_with_default(self) -> None:
        path = write_temp_config(" \n")
        added = upgrade_user_config(path)
        self.assertIn("security.allow_plain_http", added)
        self.assertEqual(path.read_text(encoding="utf-8"), default_config_text())

    def test_preserves_original_file_mode(self) -> None:
        path = write_temp_config(
            """[security]
allow_plain_http = true
allow_any_original_host = true
allowed_extensions = [".3mf"]
post_process_action = "block"
allowed_hosts = ["example.com"]

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        )
        path.chmod(0o600)
        expected_mode = stat.S_IMODE(path.stat().st_mode)
        original = path.read_text(encoding="utf-8")

        added = upgrade_user_config(path)

        self.assertIn("security.allow_printables_bundle", added)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode)
        backup = path.with_name(f"{path.name}.bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), expected_mode)

    def test_rejects_invalid_toml_without_writing(self) -> None:
        path = write_temp_config("this is not toml = [\n")
        original = path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "invalid config"):
            upgrade_user_config(path)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(path.with_name(f"{path.name}.bak").exists())

    def test_init_user_config_upgrades_existing_file(self) -> None:
        path = write_temp_config(
            """[security]
allow_plain_http = true
allow_any_original_host = true
allowed_extensions = [".3mf"]
post_process_action = "block"
allowed_hosts = ["example.com"]

[bambu_studio]
windows = "a"
macos = "b"
linux = "c"
"""
        )
        with patch("slicer_uri_bridge.config.user_config_path", return_value=path):
            result_path, created, added = init_user_config(force=False)

        self.assertEqual(result_path, path)
        self.assertFalse(created)
        self.assertIn("security.allow_printables_bundle", added)
        merged = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(merged["security"]["allow_plain_http"])
        self.assertEqual(merged["security"]["post_process_action"], "block")
        self.assertTrue(merged["security"]["allow_printables_bundle"])

    def test_init_user_config_force_replaces_file(self) -> None:
        path = write_temp_config("extra = 1\n")
        with patch("slicer_uri_bridge.config.user_config_path", return_value=path):
            result_path, created, added = init_user_config(force=True)

        self.assertEqual(result_path, path)
        self.assertTrue(created)
        self.assertEqual(added, [])
        self.assertEqual(path.read_text(encoding="utf-8"), default_config_text())


if __name__ == "__main__":
    unittest.main()
