import unittest

from airtype.config import (
    CONFIG_VERSION,
    DEFAULT_TERMINAL_CLASSES,
    default_config,
    migrate_config,
)
from airtype.registry import DEFAULT_MODEL_KEY, get_model_spec


class ConfigMigrationTests(unittest.TestCase):
    def test_v1_config_migrates(self) -> None:
        spec = get_model_spec(DEFAULT_MODEL_KEY)
        old_model_dir = f"/home/user/models/{spec.dir_name}"
        v1 = {
            "hotkey": "alt",
            "model_dir": old_model_dir,
            "model_download_approved": True,
            "paste_mode": "ctrl_shift_v",
        }
        migrated = migrate_config(v1)

        self.assertEqual(migrated["version"], CONFIG_VERSION)
        self.assertEqual(migrated["start_hotkey"], "super+alt")
        self.assertEqual(migrated["stop_key"], "alt")
        self.assertEqual(migrated["paste_mode"], "ctrl_shift_v")
        self.assertEqual(migrated["model"], DEFAULT_MODEL_KEY)
        self.assertTrue(migrated["model_download_approved"])
        # v1 stored the model folder itself; v2 stores its parent.
        self.assertEqual(migrated["model_dir"], "/home/user/models")

    def test_v1_combo_hotkey_is_preserved(self) -> None:
        migrated = migrate_config({"hotkey": "ctrl+alt"})
        self.assertEqual(migrated["start_hotkey"], "ctrl+alt")
        self.assertEqual(migrated["stop_key"], "alt")

    def test_overlay_enabled_defaults_on_and_survives_migration(self) -> None:
        self.assertTrue(migrate_config({})["overlay_enabled"])
        migrated = migrate_config({"version": CONFIG_VERSION, "overlay_enabled": False})
        self.assertFalse(migrated["overlay_enabled"])

    def test_fresh_and_partial_inputs(self) -> None:
        self.assertEqual(migrate_config({}), default_config() | {"version": CONFIG_VERSION})
        self.assertEqual(migrate_config(None), default_config())
        partial = migrate_config({"version": CONFIG_VERSION, "paste_mode": "copy_only"})
        self.assertEqual(partial["paste_mode"], "copy_only")
        self.assertEqual(partial["terminal_classes"], DEFAULT_TERMINAL_CLASSES)

    def test_unknown_model_falls_back_to_default(self) -> None:
        migrated = migrate_config({"version": CONFIG_VERSION, "model": "not-a-model"})
        self.assertEqual(migrated["model"], DEFAULT_MODEL_KEY)

    def test_bad_threshold_values_recover(self) -> None:
        migrated = migrate_config(
            {"version": CONFIG_VERSION, "double_tap_threshold": "fast", "stop_cooldown": -1}
        )
        self.assertEqual(migrated["double_tap_threshold"], 0.3)
        self.assertEqual(migrated["stop_cooldown"], 0.0)


if __name__ == "__main__":
    unittest.main()
