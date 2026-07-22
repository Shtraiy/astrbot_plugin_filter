import json
import tempfile
import unittest
from pathlib import Path

from health import check_meme_manager_health, find_loaded_meme_manager


class Metadata:
    def __init__(self, name, star_cls=object):
        self.name = name
        self.star_cls = star_cls


class Context:
    def __init__(self, stars=None):
        self.stars = stars or []

    def get_registered_star(self, name):
        return next((star for star in self.stars if star.name == name), None)

    def get_all_stars(self):
        return self.stars


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.memes_dir = self.root / "memes"
        self.metadata_path = self.root / "memes_data.json"


class HealthTests(unittest.TestCase):
    def test_manager_is_found_in_registered_plugins(self):
        found, name = find_loaded_meme_manager(Context([Metadata("meme_manager")]))
        self.assertTrue(found)
        self.assertEqual(name, "meme_manager")

    def test_missing_manager_is_not_ready_even_if_path_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.memes_dir.mkdir()
            health = check_meme_manager_health(Context(), store)
            self.assertEqual(health.status, "plugin_missing")
            self.assertFalse(health.ready)

    def test_loaded_manager_requires_readable_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            health = check_meme_manager_health(
                Context([Metadata("astrbot_plugin_meme_manager")]), store
            )
            self.assertEqual(health.status, "data_missing")

            store.memes_dir.mkdir(parents=True)
            store.metadata_path.write_text(json.dumps({"happy": "ok"}), encoding="utf-8")
            health = check_meme_manager_health(
                Context([Metadata("astrbot_plugin_meme_manager")]), store
            )
            self.assertEqual(health.status, "ready")
            self.assertTrue(health.ready)

    def test_invalid_metadata_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.memes_dir.mkdir(parents=True)
            store.metadata_path.write_text("not-json", encoding="utf-8")
            health = check_meme_manager_health(
                Context([Metadata("meme_manager")]), store
            )
            self.assertEqual(health.status, "metadata_invalid")


if __name__ == "__main__":
    unittest.main()
