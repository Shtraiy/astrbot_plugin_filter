import json
import unittest
from pathlib import Path


class ConfigSchemaTests(unittest.TestCase):
    def test_schema_uses_astrbot_plugin_config_shape(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )
        supported_types = {"string", "bool", "int", "float", "list", "object"}

        self.assertNotIn("properties", schema)
        self.assertNotIn("type", schema)
        self.assertEqual(schema["wakeup_interval_min"]["default"], 1.0)
        self.assertEqual(schema["wakeup_interval_max"]["default"], 2.0)
        self.assertEqual(schema["enable_message_merge"]["default"], True)
        self.assertEqual(schema["merge_window_seconds"]["default"], 6.0)
        self.assertEqual(schema["merge_max_messages"]["default"], 5)
        self.assertEqual(schema["merge_max_chars"]["default"], 2000)
        self.assertEqual(schema["merge_ignore_prefixes"]["default"], "/,!")
        self.assertEqual(schema["merge_task_cancel"]["default"], False)
        for key, value in schema.items():
            self.assertIsInstance(value, dict, key)
            self.assertIn(value.get("type"), supported_types, key)


if __name__ == "__main__":
    unittest.main()
