"""Health checks for the required AstrBot meme_manager plugin."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MEME_MANAGER_NAMES = {
    "meme_manager",
    "astrbot_plugin_meme_manager",
}


@dataclass(frozen=True)
class MemeManagerHealth:
    status: str
    reason: str
    plugin_name: str | None = None
    data_root: Path | None = None
    category_count: int = 0

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def summary(self) -> str:
        location = str(self.data_root) if self.data_root else "未知"
        plugin = self.plugin_name or "未找到"
        return (
            f"meme_manager: {self.status}\n"
            f"插件: {plugin}\n"
            f"数据目录: {location}\n"
            f"分类数: {self.category_count}\n"
            f"说明: {self.reason}"
        )


def find_loaded_meme_manager(context: Any) -> tuple[bool, str | None]:
    """Use AstrBot's public Context plugin registry to detect a loaded manager."""
    getter = getattr(context, "get_registered_star", None)
    if callable(getter):
        for name in MEME_MANAGER_NAMES:
            try:
                metadata = getter(name)
            except Exception:
                metadata = None
            if _is_loaded_metadata(metadata):
                return True, str(getattr(metadata, "name", name) or name)

    all_getter = getattr(context, "get_all_stars", None)
    if callable(all_getter):
        try:
            stars = all_getter() or []
        except Exception:
            stars = []
        for metadata in stars:
            name = str(getattr(metadata, "name", "") or "").strip()
            if name in MEME_MANAGER_NAMES and _is_loaded_metadata(metadata):
                return True, name
    return False, None


def check_meme_manager_health(context: Any, store: Any) -> MemeManagerHealth:
    loaded, plugin_name = find_loaded_meme_manager(context)
    if not loaded:
        return MemeManagerHealth(
            status="plugin_missing",
            reason="未检测到已加载的 meme_manager 插件，暂停保存。",
            data_root=Path(store.root),
        )

    root = Path(store.root)
    memes_dir = Path(store.memes_dir)
    metadata_path = Path(store.metadata_path)
    if not root.is_dir() or not memes_dir.is_dir():
        return MemeManagerHealth(
            status="data_missing",
            reason="meme_manager 数据目录或 memes 子目录不存在，等待其初始化。",
            plugin_name=plugin_name,
            data_root=root,
        )
    if not os.access(root, os.R_OK | os.W_OK) or not os.access(memes_dir, os.R_OK | os.W_OK):
        return MemeManagerHealth(
            status="not_writable",
            reason="meme_manager 数据目录不可读写，暂停保存。",
            plugin_name=plugin_name,
            data_root=root,
        )

    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return MemeManagerHealth(
                status="metadata_invalid",
                reason="memes_data.json 无法解析，暂停保存以避免破坏分类配置。",
                plugin_name=plugin_name,
                data_root=root,
            )
        if not isinstance(metadata, dict):
            return MemeManagerHealth(
                status="metadata_invalid",
                reason="memes_data.json 不是对象结构，暂停保存。",
                plugin_name=plugin_name,
                data_root=root,
            )
    else:
        metadata = {}

    categories = {
        item.name for item in memes_dir.iterdir() if item.is_dir()
    }
    categories.update(str(key) for key in metadata)
    return MemeManagerHealth(
        status="ready",
        reason="插件已加载，数据目录可读写。",
        plugin_name=plugin_name,
        data_root=root,
        category_count=len(categories),
    )


def _is_loaded_metadata(metadata: Any) -> bool:
    if metadata is None:
        return False
    # Current AstrBot returns metadata for loaded stars. If a version exposes
    # star_cls, a None class means the entry is only a stale registry record.
    return not hasattr(metadata, "star_cls") or getattr(metadata, "star_cls") is not None
