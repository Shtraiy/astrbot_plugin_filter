"""Storage adapter for the meme_manager on-disk data contract."""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Pillow is optional at import time for AstrBot startup.
    Image = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


DEFAULT_CATEGORY_DESCRIPTIONS = {
    "angry": "当对话包含抱怨、批评或激烈反对时使用",
    "happy": "用于成功确认、积极反馈或庆祝场景",
    "sad": "表达伤心、歉意、遗憾或安慰场景",
    "surprised": "响应超出预期的信息或意外转折",
    "confused": "请求澄清、表达理解障碍或感到困惑",
    "color": "社交场景中的暧昧表达",
    "cpu": "技术讨论中表示思维卡顿",
    "fool": "自嘲或缓和气氛的幽默场景",
    "givemoney": "涉及报酬、奖励或付费讨论时使用",
    "like": "表达对事物或观点的喜爱",
    "see": "表示偷瞄或持续关注",
    "shy": "涉及隐私话题或收到赞美时使用",
    "work": "工作流程、任务分配或进度汇报场景",
    "reply": "等待用户反馈或需要确认时使用",
    "meow": "卖萌或萌系互动场景",
    "baka": "轻微责备或友善吐槽",
    "morning": "早安问候场景",
    "sleep": "涉及作息、熬夜、疲劳或休息场景",
    "sigh": "表达无奈、无语或感慨",
}


@dataclass(frozen=True)
class SaveResult:
    status: str
    path: Path
    digest: str


class MemeStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.memes_dir = self.root / "memes"
        self.metadata_path = self.root / "memes_data.json"
        self.temp_dir = self.root / "temp"
        self._perceptual_hash_cache: dict[Path, tuple[int, int, str | None]] = {}

    @classmethod
    def from_astrbot(cls) -> "MemeStore":
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            root = Path(get_astrbot_plugin_data_path()) / "meme_manager"
        except Exception:
            root = Path(__file__).resolve().parent / "data" / "plugin_data" / "meme_manager"
        return cls(root)

    def available_categories(self) -> set[str]:
        categories = set()
        if self.memes_dir.is_dir():
            categories = {
                item.name for item in self.memes_dir.iterdir() if item.is_dir()
            }
        metadata = self._load_metadata()
        categories.update(metadata)
        return categories or set(DEFAULT_CATEGORY_DESCRIPTIONS)

    def category_descriptions(self) -> dict[str, str]:
        """Return descriptions used by meme_manager's category prompt."""
        metadata = self._load_metadata()
        categories = self.available_categories()
        return {
            category: str(metadata.get(category) or DEFAULT_CATEGORY_DESCRIPTIONS.get(category, ""))
            for category in categories
        }

    def save_image(
        self,
        content: bytes,
        category: str,
        extension: str = ".png",
        perceptual_threshold: int | None = 6,
    ) -> SaveResult:
        if not content:
            raise ValueError("cannot save an empty image")
        if not _is_safe_segment(category):
            raise ValueError(f"unsafe category: {category!r}")
        digest = hashlib.sha256(content).hexdigest()
        duplicate = self.find_duplicate(content, perceptual_threshold)
        if duplicate is not None:
            return SaveResult("duplicate", duplicate, digest)

        safe_extension = _safe_extension(extension)
        target_dir = self.memes_dir / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / self._next_filename(category, safe_extension, digest)
        self._atomic_write(target, content)
        self._ensure_category_description(category)
        return SaveResult("saved", target, digest)

    def find_duplicate(
        self,
        content: bytes,
        perceptual_threshold: int | None = 6,
    ) -> Path | None:
        """Return an exact or perceptually equivalent existing image."""
        if not content:
            return None
        duplicate = self._find_digest(hashlib.sha256(content).hexdigest())
        if duplicate is not None or perceptual_threshold is None:
            return duplicate
        content_hash = _perceptual_hash(content)
        if content_hash is None:
            return None
        return self._find_perceptual_hash(content_hash, perceptual_threshold)

    @staticmethod
    def perceptual_hash(content: bytes) -> str | None:
        """Return an average perceptual hash, or None when Pillow cannot decode it."""
        return _perceptual_hash(content)

    def image_perceptual_hash(self, path: Path) -> str | None:
        """Return a cached perceptual hash for a local image."""
        return self._cached_perceptual_hash(path)

    def is_similar(
        self,
        first: bytes,
        second: bytes,
        perceptual_threshold: int | None = 6,
    ) -> bool:
        """Compare two incoming images before either one is saved."""
        if first == second:
            return True
        if perceptual_threshold is None:
            return False
        first_hash = _perceptual_hash(first)
        second_hash = _perceptual_hash(second)
        return bool(
            first_hash
            and second_hash
            and _hamming_distance(first_hash, second_hash) <= perceptual_threshold
        )

    def pick_image(self, category: str) -> Path | None:
        """Pick one image from a safe meme_manager category directory."""
        if not _is_safe_segment(category):
            return None
        category_dir = self.memes_dir / category
        if not category_dir.is_dir():
            return None
        candidates = [
            path
            for path in category_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        return random.choice(candidates) if candidates else None

    def image_paths(self, category: str) -> list[Path]:
        """Return image files in one safe category, excluding catalog documents."""
        if not _is_safe_segment(category):
            return []
        category_dir = self.memes_dir / category
        if not category_dir.is_dir():
            return []
        return sorted(
            path for path in category_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    def directory_categories(self) -> set[str]:
        if not self.memes_dir.is_dir():
            return set()
        return {
            item.name for item in self.memes_dir.iterdir()
            if item.is_dir() and _is_safe_segment(item.name)
        }

    def image_digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def load_catalog(self, category: str) -> dict:
        if not _is_safe_segment(category):
            return {"version": 1, "category": category, "items": []}
        path = self.memes_dir / category / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"version": 1, "category": category, "items": []}
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return {"version": 1, "category": category, "items": []}
        return data

    def write_catalog(
        self,
        category: str,
        entries: list[dict],
        metadata: dict | None = None,
    ) -> None:
        if not _is_safe_segment(category):
            raise ValueError(f"unsafe category: {category!r}")
        category_dir = self.memes_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "category": category,
            "updated_at": int(time.time()),
            "items": entries,
        }
        if isinstance(metadata, dict):
            data.update(metadata)
        self._atomic_write_json(category_dir / "index.json", data)
        self._atomic_write(
            category_dir / "README.md",
            self._catalog_markdown(category, entries).encode("utf-8"),
        )

    def upsert_catalog_entry(
        self,
        category: str,
        entry: dict,
        metadata: dict | None = None,
    ) -> None:
        """Add or replace one indexed image without discarding other entries."""
        filename = str(entry.get("filename", ""))
        if not filename:
            return
        data = self.load_catalog(category)
        items = [
            item for item in data.get("items", [])
            if isinstance(item, dict) and item.get("filename") != filename
        ]
        items.append(entry)
        catalog_metadata = {
            key: value
            for key, value in data.items()
            if key not in {"version", "category", "updated_at", "items"}
        }
        if isinstance(metadata, dict):
            catalog_metadata.update(metadata)
        self.write_catalog(category, items, catalog_metadata)

    def renumber_category(self, category: str) -> dict[Path, Path]:
        """Rename all category images to stable names such as happy_0001.png."""
        images = self.image_paths(category)
        if not _is_safe_segment(category) or not images:
            return {}
        category_dir = self.memes_dir / category
        mapping = {
            path: category_dir / f"{category}_{index:04d}{path.suffix.lower()}"
            for index, path in enumerate(images, start=1)
        }
        pending: list[tuple[Path, Path, Path]] = []
        for index, (source, target) in enumerate(mapping.items()):
            if source == target:
                continue
            temporary = category_dir / f".meme-renaming-{time.time_ns()}-{index}{source.suffix.lower()}"
            source.rename(temporary)
            pending.append((temporary, source, target))
        for temporary, _source, target in pending:
            temporary.rename(target)
        return mapping

    def make_temp_file(self, content: bytes, extension: str = ".png") -> Path:
        path = self.temp_dir / f"incoming_{time.time_ns()}{_safe_extension(extension)}"
        self._atomic_write(path, content)
        return path

    @staticmethod
    def remove_temp_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _find_digest(self, digest: str) -> Path | None:
        if not self.memes_dir.is_dir():
            return None
        for candidate in self.memes_dir.rglob("*"):
            if not candidate.is_file() or candidate.name.startswith(".") or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                if hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                    return candidate
            except OSError:
                continue
        return None

    def _find_perceptual_hash(self, content_hash: str, threshold: int) -> Path | None:
        threshold = max(0, min(64, int(threshold)))
        for candidate in self.memes_dir.rglob("*") if self.memes_dir.is_dir() else []:
            if (
                not candidate.is_file()
                or candidate.name.startswith(".")
                or candidate.suffix.lower() not in IMAGE_EXTENSIONS
            ):
                continue
            candidate_hash = self._cached_perceptual_hash(candidate)
            if candidate_hash and _hamming_distance(content_hash, candidate_hash) <= threshold:
                return candidate
        return None

    def _cached_perceptual_hash(self, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        cache_key = (stat.st_mtime_ns, stat.st_size)
        cached = self._perceptual_hash_cache.get(path)
        if cached and cached[:2] == cache_key:
            return cached[2]
        try:
            value = _perceptual_hash(path.read_bytes())
        except OSError:
            value = None
        self._perceptual_hash_cache[path] = (*cache_key, value)
        return value

    def _load_metadata(self) -> dict[str, str]:
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ensure_category_description(self, category: str) -> None:
        metadata = self._load_metadata()
        if category in metadata:
            return
        metadata[category] = DEFAULT_CATEGORY_DESCRIPTIONS.get(
            category, "自动收集的表情包分类，请补充描述"
        )
        self._atomic_write_json(self.metadata_path, metadata)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _next_filename(self, category: str, extension: str, digest: str) -> str:
        pattern = re.compile(rf"^{re.escape(category)}_(\d+)$", re.IGNORECASE)
        numbers = [
            int(match.group(1))
            for path in self.image_paths(category)
            if (match := pattern.match(path.stem))
        ]
        if numbers or (self.memes_dir / category / "index.json").exists():
            return f"{category}_{max(numbers, default=0) + 1:04d}{extension}"
        return f"stolen_{int(time.time() * 1000)}_{digest[:12]}{extension}"

    @staticmethod
    def _catalog_markdown(category: str, entries: list[dict]) -> str:
        lines = [
            f"# {category} 表情包索引",
            "",
            "此文件由 astrbot_plugin_meme_stealer 自动生成，请勿手动修改 index.json。",
            "",
            "| 编号 | 文件 | 情绪 | 描述 | 标签 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in entries:
            tags = ", ".join(str(item) for item in entry.get("tags", []) if item)
            values = [
                entry.get("id", ""),
                entry.get("filename", ""),
                entry.get("emotion", ""),
                entry.get("description", ""),
                tags,
            ]
            escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
            lines.append("| " + " | ".join(escaped) + " |")
        return "\n".join(lines) + "\n"

    @classmethod
    def _atomic_write_json(cls, path: Path, data: dict) -> None:
        cls._atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def _is_safe_segment(value: str) -> bool:
    return bool(value and value == value.strip() and re.fullmatch(r"[A-Za-z0-9_-]+", value))


def _safe_extension(extension: str) -> str:
    extension = str(extension or ".png").lower()
    if not extension.startswith("."):
        extension = f".{extension}"
    return extension if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} else ".png"


def _perceptual_hash(content: bytes) -> str | None:
    """Build a small average hash that survives common resize/compression changes."""
    if Image is None or not content:
        return None
    try:
        with Image.open(io.BytesIO(content)) as image:
            try:
                image.seek(0)
            except EOFError:
                pass
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            pixels = list(image.convert("L").resize((8, 7), resampling).getdata())
    except Exception:
        return None
    if not pixels:
        return None
    average = sum(pixels) / len(pixels)
    bits = sum((1 << index) for index, pixel in enumerate(pixels) if pixel >= average)
    # Keep the signature at 64 bits while retaining brightness information.
    # A plain average hash would make every solid-color image identical.
    return f"{int(round(average)) & 0xff:02x}{bits:014x}"


def _hamming_distance(first: str, second: str) -> int:
    try:
        xor = int(first, 16) ^ int(second, 16)
    except (TypeError, ValueError):
        return 64
    return bin(xor).count("1")
