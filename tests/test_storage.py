import json
import tempfile
import unittest
from pathlib import Path

from storage import MemeStore


class StorageTests(unittest.TestCase):
    def test_save_creates_category_and_preserves_existing_descriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memes").mkdir()
            (root / "memes_data.json").write_text(
                json.dumps({"happy": "原有描述"}, ensure_ascii=False), encoding="utf-8"
            )
            store = MemeStore(root)

            saved = store.save_image(b"image-bytes", "happy", ".png")

            self.assertEqual(saved.status, "saved")
            self.assertTrue(saved.path.is_file())
            data = json.loads((root / "memes_data.json").read_text(encoding="utf-8"))
            self.assertEqual(data["happy"], "原有描述")

    def test_duplicate_content_is_not_saved_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            first = store.save_image(b"same", "happy", ".jpg")
            second = store.save_image(b"same", "sad", ".jpg")

            self.assertEqual(first.status, "saved")
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(len(list((Path(directory) / "memes").rglob("*.*"))), 1)

    def test_find_duplicate_returns_existing_path_before_save(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            saved = store.save_image(b"same", "happy", ".jpg")

            self.assertEqual(store.find_duplicate(b"same"), saved.path)
            self.assertIsNone(store.find_duplicate(b"different"))

    def test_pick_image_returns_only_images_from_requested_category(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            store.save_image(b"happy-image", "happy", ".png")
            (Path(directory) / "memes" / "happy" / "notes.txt").write_text("ignore")

            picked = store.pick_image("happy")

            self.assertIsNotNone(picked)
            self.assertEqual(picked.suffix, ".png")
            self.assertIsNone(store.pick_image("sad"))

    def test_renumber_category_and_write_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            first = store.save_image(b"first", "happy", ".png")
            second = store.save_image(b"second", "happy", ".jpg")

            mapping = store.renumber_category("happy")
            entries = [
                {"id": "happy_0001", "filename": mapping[first.path].name, "description": "开心"},
                {"id": "happy_0002", "filename": mapping[second.path].name, "description": "庆祝"},
            ]
            store.write_catalog("happy", entries)

            self.assertEqual(
                sorted(path.name for path in (Path(directory) / "memes" / "happy").iterdir() if path.is_file() and path.suffix in {".png", ".jpg"}),
                ["happy_0001.png", "happy_0002.jpg"],
            )
            self.assertEqual(store.load_catalog("happy")["items"], entries)
            self.assertIn("happy_0001.png", (Path(directory) / "memes" / "happy" / "README.md").read_text(encoding="utf-8"))

    def test_catalog_preserves_index_signature_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            metadata = {
                "index_version": 2,
                "index_prompt_version": "library-batch-v2",
                "index_provider_id": "gemini-test",
            }
            store.write_catalog("happy", [{"filename": "happy_0001.png"}], metadata)

            catalog = store.load_catalog("happy")

            self.assertEqual(catalog["index_version"], 2)
            self.assertEqual(catalog["index_prompt_version"], "library-batch-v2")
            self.assertEqual(catalog["index_provider_id"], "gemini-test")

    def test_upsert_catalog_entry_preserves_index_signature_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            metadata = {"index_version": 2, "index_provider_id": "gemini-test"}
            store.write_catalog("happy", [], metadata)
            store.upsert_catalog_entry("happy", {"filename": "happy_0001.png"})

            catalog = store.load_catalog("happy")

            self.assertEqual(catalog["index_version"], 2)
            self.assertEqual(catalog["index_provider_id"], "gemini-test")

    @unittest.skipIf(__import__("storage").Image is None, "Pillow is not installed")
    def test_perceptual_duplicate_detects_resized_image(self):
        from io import BytesIO
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            store = MemeStore(Path(directory))
            original = Image.new("RGB", (64, 64), "white")
            ImageDraw.Draw(original).rectangle((10, 10, 54, 54), fill="black")
            first_buffer = BytesIO()
            original.save(first_buffer, format="PNG")

            resized = original.resize((128, 128))
            second_buffer = BytesIO()
            resized.save(second_buffer, format="JPEG", quality=75)

            saved = store.save_image(first_buffer.getvalue(), "happy", ".png")
            duplicate = store.find_duplicate(second_buffer.getvalue(), 6)

            self.assertEqual(saved.status, "saved")
            self.assertEqual(duplicate, saved.path)

if __name__ == "__main__":
    unittest.main()
