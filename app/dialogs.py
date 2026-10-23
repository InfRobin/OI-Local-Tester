"""原生文件选择对话框。

Tk 必须在创建它的线程中使用，这里用一个常驻后台线程串行处理请求，
服务器线程只负责等待结果。
"""

from __future__ import annotations

import queue
import threading
from typing import Dict, List, Optional


class DialogService:
    def __init__(self) -> None:
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="dialog-worker", daemon=True)
            self._thread.start()

    def _loop(self) -> None:  # pragma: no cover - 需要图形界面
        import tkinter as tk
        from tkinter import filedialog

        try:
            root = tk.Tk()
            root.withdraw()
            root.title("OI 对拍器")
            try:
                root.attributes("-topmost", True)
            except tk.TclError:
                pass
        except Exception as exc:  # noqa: BLE001
            self._fail_all(f"无法初始化文件对话框：{exc}")
            return

        while True:
            item = self._queue.get()
            if item is None:
                break
            kind, options, box, done = item
            try:
                root.update()
                if kind == "folder":
                    path = filedialog.askdirectory(
                        parent=root,
                        title=options.get("title", "选择题目文件夹"),
                        mustexist=True,
                        initialdir=options.get("initialdir") or None,
                    )
                    box.append(path or None)
                elif kind == "files":
                    paths = filedialog.askopenfilenames(
                        parent=root,
                        title=options.get("title", "选择文件"),
                        filetypes=options.get("filetypes") or [("所有文件", "*.*")],
                        initialdir=options.get("initialdir") or None,
                    )
                    box.append([str(p) for p in paths])
                else:
                    box.append(None)
            except Exception as exc:  # noqa: BLE001
                box.append(exc)
            finally:
                done.set()

        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _fail_all(self, message: str) -> None:
        pending: List[tuple] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        for item in pending:
            if item is None:
                continue
            _, _, box, done = item
            box.append(RuntimeError(message))
            done.set()

    # ------------------------------------------------------------------ #
    def ask(self, kind: str, **options) -> Optional[object]:
        self._ensure_thread()
        box: List[object] = []
        done = threading.Event()
        self._queue.put((kind, options, box, done))
        done.wait()
        if not box:
            return None
        value = box[0]
        if isinstance(value, Exception):
            raise value
        return value

    def choose_folder(self, title: str = "选择题目文件夹", initialdir: str = "") -> Optional[str]:
        return self.ask("folder", title=title, initialdir=initialdir)  # type: ignore[return-value]

    def choose_files(self, title: str, filetypes=None, initialdir: str = "") -> List[str]:
        result = self.ask("files", title=title, filetypes=filetypes, initialdir=initialdir)
        return list(result or [])  # type: ignore[arg-type]


SOURCE_FILETYPES = [
    ("源程序", "*.cpp *.cc *.cxx *.c *.py"),
    ("C/C++", "*.cpp *.cc *.cxx *.c"),
    ("Python", "*.py"),
    ("所有文件", "*.*"),
]

CASE_FILETYPES = [
    ("测试数据", "*.in *.inp *.input *.ans *.out *.answer *.exp"),
    ("输入文件", "*.in *.inp *.input"),
    ("答案文件", "*.ans *.out *.answer *.exp"),
    ("所有文件", "*.*"),
]

CHECKER_FILETYPES = [
    ("Checker 源程序", "*.cpp *.cc *.cxx *.c *.exe"),
    ("C/C++ 源程序", "*.cpp *.cc *.cxx *.c"),
    ("可执行文件", "*.exe"),
    ("所有文件", "*.*"),
]
