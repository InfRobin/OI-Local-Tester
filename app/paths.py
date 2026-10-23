"""路径与目录约定。"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "OITester"
APP_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = APP_ROOT / "web"
EXAMPLES_DIR = APP_ROOT / "examples"

_data_dir_cache: Path | None = None


def data_dir() -> Path:
    """用户数据目录（设置、运行产物、浏览器配置）。

    优先级：环境变量 OITESTER_HOME -> %LOCALAPPDATA%\\OITester -> 程序目录/.oit。
    最后一层保证在受限环境或便携使用时依然可以工作。
    """
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache

    override = os.environ.get("OITESTER_HOME")
    if override:
        candidate = Path(override).expanduser()
    else:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / ".local" / "share")
        candidate = Path(base) / APP_DIR_NAME

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        _data_dir_cache = candidate
        return candidate
    except OSError:
        fallback = APP_ROOT / ".oit"
        fallback.mkdir(parents=True, exist_ok=True)
        _data_dir_cache = fallback
        return fallback


def tmp_dir() -> Path:
    """编译器等外部程序使用的临时目录（比系统 TEMP 更可控）。"""
    d = data_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_dir() -> Path:
    d = data_dir() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_root() -> Path:
    d = data_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_dir() -> Path:
    d = data_dir() / "build"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "tester.log"


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False
