"""设置 / 最近打开记录持久化。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

from .paths import data_dir

DEFAULT_SETTINGS: Dict[str, Any] = {
    "compiler": "g++",
    "flags": "-O2 -std=c++17 -static",
    "stackMB": 256,
    "timeMs": 1000,
    "memoryMB": 256,
    "spaceMB": 64,
    "compareMode": "trim",       # strict | trim | tokens
    "stopOnFail": False,
    "autoCompile": True,
    "appWindow": True,
    "theme": "dark",
}

SETTING_KEYS = tuple(DEFAULT_SETTINGS.keys())

_MAX_RECENT = 8


class Store:
    """线程安全的 JSON 配置存储。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "settings.json")
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "settings": dict(DEFAULT_SETTINGS),
            "recent": [],
            "lastFolder": None,
            "checkers": {},
        }
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        settings = raw.get("settings")
        if isinstance(settings, dict):
            for key in SETTING_KEYS:
                if key in settings:
                    self._data["settings"][key] = settings[key]
        recent = raw.get("recent")
        if isinstance(recent, list):
            self._data["recent"] = [str(x) for x in recent if isinstance(x, str)][:_MAX_RECENT]
        last = raw.get("lastFolder")
        if isinstance(last, str):
            self._data["lastFolder"] = last
        checkers = raw.get("checkers")
        if isinstance(checkers, dict):
            cleaned = {}
            for key, value in checkers.items():
                if isinstance(key, str) and isinstance(value, dict):
                    cleaned[key] = {
                        "path": str(value.get("path") or ""),
                        "enabled": bool(value.get("enabled", True)),
                    }
            self._data["checkers"] = cleaned

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                try:
                    self.path.write_text(payload, encoding="utf-8")
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    @property
    def settings(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data["settings"])

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in patch.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                default = DEFAULT_SETTINGS[key]
                try:
                    if isinstance(default, bool):
                        value = bool(value)
                    elif isinstance(default, int):
                        value = int(float(value))
                    elif isinstance(default, str):
                        value = str(value)
                except (TypeError, ValueError):
                    continue
                self._data["settings"][key] = value
            self.save()
            return dict(self._data["settings"])

    @property
    def recent(self) -> List[str]:
        with self._lock:
            return list(self._data["recent"])

    def touch_recent(self, folder: str) -> None:
        with self._lock:
            folder = str(folder)
            items = [x for x in self._data["recent"] if os.path.normcase(x) != os.path.normcase(folder)]
            items.insert(0, folder)
            self._data["recent"] = items[:_MAX_RECENT]
            self._data["lastFolder"] = folder
            self.save()

    @property
    def last_folder(self) -> str | None:
        with self._lock:
            return self._data.get("lastFolder")

    def drop_recent(self, folder: str) -> None:
        with self._lock:
            self._data["recent"] = [
                x for x in self._data["recent"] if os.path.normcase(x) != os.path.normcase(str(folder))
            ]
            self.save()

    # ------------------------------------------------------------------ #
    # 每道题的 Special Judge 配置
    # ------------------------------------------------------------------ #
    def checker_config(self, folder: str) -> Dict[str, Any] | None:
        with self._lock:
            entry = self._data["checkers"].get(os.path.normcase(str(folder)))
            return dict(entry) if isinstance(entry, dict) else None

    def save_checker_config(self, folder: str, path: str | None, enabled: bool) -> None:
        with self._lock:
            key = os.path.normcase(str(folder))
            if not path:
                self._data["checkers"].pop(key, None)
            else:
                self._data["checkers"][key] = {"path": path, "enabled": bool(enabled)}
            self.save()
