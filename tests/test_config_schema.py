import json
import unittest
from pathlib import Path


class ConfigSchemaTests(unittest.TestCase):
    VISIBLE_KEYS = {
        "enable_message_merge",
        "enable_self_reply_mark",
        "enable_content_guard",
        "content_guard_mode",
        "content_guard_block_terms",
    }
    REMOVED_KEYS = {
        "llm_provider_id",
        "enable_llm_style",
        "llm_timeout_seconds",
        "enable_llm_segment",
        "segment_min_chars",
        "enable_de_ai_flavor",
        "enable_image_render",
        "image_min_list_items",
        "image_font_size",
        "image_max_width",
        "multi_message",
        "delay_min",
        "delay_max",
        "gate_seconds",
        "gate_ttl_seconds",
        "wakeup_interval_min",
        "wakeup_interval_max",
        "queue_full_notice",
        "merge_continuation_ttl",
    }

    def _schema(self):
        return json.loads(
            (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )

    def test_schema_uses_astrbot_plugin_config_shape(self):
        schema = self._schema()
        supported_types = {"string", "bool", "int", "float", "list", "object"}

        self.assertNotIn("properties", schema)
        self.assertNotIn("type", schema)
        for key, value in schema.items():
            self.assertIsInstance(value, dict, key)
            self.assertIn(value.get("type"), supported_types, key)
            hidden = key not in self.VISIBLE_KEYS
            self.assertEqual(bool(value.get("invisible", False)), hidden, key)

    def test_removed_keys_are_gone(self):
        schema = self._schema()
        for key in self.REMOVED_KEYS:
            self.assertNotIn(key, schema, key)

    def test_visible_keys_are_exactly_the_common_set(self):
        schema = self._schema()
        visible = {
            key for key, value in schema.items() if not value.get("invisible")
        }
        self.assertEqual(visible, self.VISIBLE_KEYS)

    def test_merge_defaults(self):
        schema = self._schema()
        self.assertEqual(schema["enable_message_merge"]["default"], True)
        self.assertEqual(schema["merge_window_seconds"]["default"], 6.0)
        self.assertEqual(schema["merge_max_messages"]["default"], 5)
        self.assertEqual(schema["merge_max_chars"]["default"], 2000)
        self.assertEqual(schema["merge_ignore_prefixes"]["default"], "/,!")
        self.assertEqual(schema["merge_include_media"]["default"], True)
        self.assertEqual(schema["merge_planning_ttl"]["default"], 60.0)

    def test_self_reply_mark_defaults(self):
        schema = self._schema()
        self.assertEqual(schema["enable_self_reply_mark"]["default"], True)
        self.assertEqual(schema["self_reply_mark_minutes"]["default"], 5.0)
        self.assertEqual(schema["strip_recent_self_meme_context"]["default"], True)
        self.assertEqual(schema["guard_own_media_attribution"]["default"], True)


if __name__ == "__main__":
    unittest.main()
