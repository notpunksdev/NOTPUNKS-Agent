"""Persistent file-based storage for pytonconnect."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pytonconnect.storage import IStorage


class FileStorage(IStorage):
    """File-based storage that persists TON Connect session state to disk.

    Uses a JSON file in the NOTPUNKS home directory so wallet connection
    survives agent restarts.
    """

    def __init__(self, storage_path: str | Path | None = None):
        from hermes_cli.config import get_hermes_home

        if storage_path is None:
            storage_path = get_hermes_home() / "wallet_storage.json"
        self._path = Path(storage_path)
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        else:
            self._cache = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2)

    async def set_item(self, key: str, value: str) -> None:
        self._cache[key] = value
        self._save()

    async def get_item(self, key: str, default_value: str | None = None) -> str | None:
        return self._cache.get(key, default_value)

    async def remove_item(self, key: str) -> None:
        if key in self._cache:
            del self._cache[key]
            self._save()

    def clear(self) -> None:
        """Clear all stored data (used on disconnect)."""
        self._cache.clear()
        if self._path.exists():
            os.remove(self._path)
