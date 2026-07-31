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
        for key, value in schema.items():
            self.assertIsInstance(value, dict, key)
            self.assertIn(value.get("type"), supported_types, key)


if __name__ == "__main__":
    unittest.main()
