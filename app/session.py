"""会话状态：文件夹、测试点、编译缓存与评测调度。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import __version__, detect, judge
from .dialogs import CASE_FILETYPES, SOURCE_FILETYPES, DialogService
from .paths import build_dir, data_dir, runs_dir, work_root
from .store import Store


class SessionError(Exception):
    """可以直接展示给用户的错误。"""


class Session:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = threading.RLock()
        self.dialogs = DialogService()

        self.folder: Optional[Path] = None
        self.problem_name: str = ""
        self.cases: List[Dict] = []
        self.results: Dict[str, Dict] = {}
        self.source: Optional[Path] = None
        self.source_candidates: List[Path] = []
        self.exe: Optional[Path] = None

        self.compile_info: Dict = self._empty_compile()
        self._compile_sig: Optional[tuple] = None

        self.checker: Optional[Path] = None
        self.checker_enabled: bool = False
        self.checker_candidates: List[Path] = []
        self.checker_info: Dict = self._empty_compile()
        self._checker_sig: Optional[tuple] = None

        self.judging: Dict = {
            "running": False,
            "total": 0,
            "done": 0,
            "current": None,
            "startedAt": None,
            "finishedAt": None,
            "summary": {},
        }
        self.stop_event = threading.Event()
        self._judge_thread: Optional[threading.Thread] = None
        self._compiler_probe: Optional[Dict] = None
        self._started_at = time.time()

        self._cleanup_workdirs()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _empty_compile() -> Dict:
        return {"status": "none", "output": "", "ms": 0, "at": None, "exe": None, "warnings": 0}

    def _cleanup_workdirs(self) -> None:
        try:
            for entry in work_root().iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass

    def _require_folder(self) -> Path:
        if self.folder is None:
            raise SessionError("还没有打开题目文件夹")
        return self.folder

    @staticmethod
    def _case_dict(case: detect.CaseFile, origin: str = "scan") -> Dict:
        return {
            "id": case.cid,
            "name": case.in_path.name,
            "stem": case.stem,
            "rel": case.rel,
            "in": str(case.in_path),
            "ans": str(case.ans_path) if case.ans_path else None,
            "hasAnswer": bool(case.ans_path and case.ans_path.is_file()),
            "size": case.size,
            "origin": origin,
        }

    def _binary_signature(self) -> tuple:
        settings = self.store.settings
        return (
            str(self.source) if self.source else "",
            self.source.stat().st_mtime if self.source and self.source.exists() else 0,
            str(settings.get("compiler")),
            str(settings.get("flags")),
            int(settings.get("stackMB") or 0),
        )

    def _build_target(self, source: Path) -> Path:
        root = build_dir() / detect.short_id(str(source).lower())
        return root / (source.stem + ".exe")

    # ------------------------------------------------------------------ #
    # 打开 / 关闭
    # ------------------------------------------------------------------ #
    def _halt_judge(self, wait: float = 3.0) -> None:
        """切换题目 / 导入文件前，先让正在进行的评测停下来。"""
        if not self.judging.get("running"):
            return
        self.stop_event.set()
        thread = self._judge_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=wait)

    def open_folder(self, path: str) -> Dict:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise SessionError(f"文件夹不存在：{folder}")

        self._halt_judge()
        scan = detect.scan_folder(folder)
        with self.lock:
            self.folder = folder.resolve()
            self.problem_name = scan.name
            self.cases = [self._case_dict(case) for case in scan.cases]
            self.source_candidates = scan.sources
            self.source = scan.source
            self.exe = scan.exe
            self.results = {}
            self.compile_info = self._empty_compile()
            self._compile_sig = None

            # Special Judge：优先使用上次为该题目保存的配置，其次自动识别
            self.checker_candidates = scan.checkers
            self.checker = None
            self.checker_enabled = False
            self.checker_info = self._empty_compile()
            self._checker_sig = None
            saved = self.store.checker_config(str(self.folder))
            saved_path = Path(saved["path"]) if saved and saved.get("path") else None
            if saved_path is not None and saved_path.is_file():
                self.checker = saved_path
                self.checker_enabled = bool(saved.get("enabled", True))
            elif scan.checkers:
                self.checker = scan.checkers[0]
                self.checker_enabled = True

            self.judging = {
                "running": False, "total": 0, "done": 0, "current": None,
                "startedAt": None, "finishedAt": None, "summary": {},
            }
            self.store.touch_recent(str(self.folder))
        return self.state()

    def close_folder(self) -> Dict:
        with self.lock:
            self.folder = None
            self.problem_name = ""
            self.cases = []
            self.results = {}
            self.source = None
            self.source_candidates = []
            self.exe = None
            self.checker = None
            self.checker_enabled = False
            self.checker_candidates = []
            self.checker_info = self._empty_compile()
            self._checker_sig = None
            self.compile_info = self._empty_compile()
            self._compile_sig = None
        return self.state()

    def rescan(self) -> Dict:
        folder = self._require_folder()
        return self.open_folder(str(folder))

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def import_source(self, path: str) -> Dict:
        candidate = Path(path)
        if not candidate.is_file():
            raise SessionError(f"文件不存在：{candidate}")
        if candidate.suffix.lower() not in detect.SOURCE_EXTS | {".exe"}:
            raise SessionError("请选择 C/C++ 源文件、Python 脚本或可执行文件")
        self._halt_judge()
        with self.lock:
            self.source = candidate
            self.exe = detect.detect_exe(self.folder, candidate) if self.folder else None
            self.compile_info = self._empty_compile()
            self._compile_sig = None
            self.results = {}
        return self.state()

    def import_cases(self, paths: Sequence[str]) -> Dict:
        pairs = detect.pair_import_paths(paths)
        if not pairs:
            raise SessionError("没有识别到测试数据（需要 .in 与同名 .ans/.out）")
        self._halt_judge()
        base = self.folder
        with self.lock:
            existing = {os.path.normcase(case["in"]) for case in self.cases}
            added = 0
            for in_path, ans_path in pairs:
                key = os.path.normcase(str(in_path))
                if key in existing:
                    continue
                existing.add(key)
                case = detect.case_from_paths(in_path, ans_path, base)
                self.cases.append(case)
                added += 1
            self._sort_cases()
        if not added:
            raise SessionError("这些测试数据已经在列表中了")
        return self.state()

    def remove_case(self, case_id: str) -> Dict:
        with self.lock:
            self.cases = [case for case in self.cases if case["id"] != case_id]
            self.results.pop(case_id, None)
        return self.state()

    def _sort_cases(self) -> None:
        self.cases.sort(key=lambda c: (str(Path(c["rel"]).parent).lower(),
                                       detect.natural_key(c["stem"])))

    # ------------------------------------------------------------------ #
    # 编译
    # ------------------------------------------------------------------ #
    def _do_compile(self, force: bool = False) -> Dict:
        settings = self.store.settings
        source = self.source
        if source is None:
            if self.exe and Path(self.exe).is_file():
                return {"ok": True, "argv": [str(self.exe)], "message": ""}
            raise SessionError("还没有选择选手程序")

        if source.suffix.lower() == ".py":
            self.compile_info = {
                "status": "ok", "output": "Python 脚本无需编译。", "ms": 0,
                "at": time.time(), "exe": str(source), "warnings": 0,
            }
            self._compile_sig = self._binary_signature()
            return {"ok": True, "argv": [judge.python_executable(), str(source)], "message": ""}

        target = self._build_target(source)
        signature = self._binary_signature()
        fresh = (target.is_file() and not force and self._compile_sig == signature
                 and target.stat().st_mtime >= source.stat().st_mtime)
        if fresh:
            self.exe = target
            return {"ok": True, "argv": [str(target)], "message": ""}

        result = judge.compile_source(source, target, settings)
        self.compile_info = {
            "status": "ok" if result["status"] == "ok" else "error",
            "output": result["output"],
            "ms": result["ms"],
            "at": time.time(),
            "exe": result.get("exe"),
            "warnings": result.get("warnings", 0),
            "command": result.get("command", ""),
        }
        if result["status"] == "ok" and Path(result["exe"]).is_file():
            self._compile_sig = signature
            self.exe = Path(result["exe"])
            return {"ok": True, "argv": [result["exe"]], "message": ""}

        return {"ok": False, "argv": None, "message": result["output"].strip() or "编译失败"}

    def compile_now(self) -> Dict:
        if self.judging.get("running"):
            raise SessionError("正在评测，请稍后再编译")
        with self.lock:
            try:
                outcome = self._do_compile(force=True)
            except SessionError as exc:
                self.compile_info = {
                    "status": "error", "output": str(exc), "ms": 0,
                    "at": time.time(), "exe": None, "warnings": 0,
                }
        return self.state()

    # ------------------------------------------------------------------ #
    # Special Judge
    # ------------------------------------------------------------------ #
    def _checker_signature(self) -> tuple:
        settings = self.store.settings
        try:
            mtime = self.checker.stat().st_mtime if self.checker and self.checker.exists() else 0
        except OSError:
            mtime = 0
        return (
            str(self.checker) if self.checker else "",
            mtime,
            str(settings.get("compiler")),
            str(settings.get("flags")),
        )

    def _checker_target(self, source: Path) -> Path:
        root = build_dir() / ("checker_" + detect.short_id(str(source).lower()))
        return root / "checker.exe"

    def _do_compile_checker(self, force: bool = False) -> Dict:
        source = self.checker
        if source is None:
            raise SessionError("已启用 Special Judge，但还没有选择 checker 文件")
        if not source.is_file():
            raise SessionError(f"checker 文件不存在：{source}")

        if source.suffix.lower() == ".exe":
            self.checker_info = {
                "status": "ok", "output": "使用已编译好的 checker 可执行文件。", "ms": 0,
                "at": time.time(), "exe": str(source), "warnings": 0,
            }
            self._checker_sig = self._checker_signature()
            return {"ok": True, "argv": [str(source)], "message": ""}

        settings = self.store.settings
        target = self._checker_target(source)
        signature = self._checker_signature()
        fresh = (target.is_file() and not force and self._checker_sig == signature
                 and target.stat().st_mtime >= source.stat().st_mtime)
        if fresh:
            return {"ok": True, "argv": [str(target)], "message": ""}

        extra_flags: List[str] = []
        testlib_dir = judge.find_testlib(str(settings.get("compiler") or "g++"))
        if testlib_dir:
            extra_flags.append("-I" + testlib_dir)

        result = judge.compile_source(source, target, settings, extra_flags=extra_flags)
        output = result["output"]
        if result["status"] != "ok" and "testlib.h" in output and not testlib_dir:
            output += "\n\n[提示] 没有找到 testlib.h，请确认 MinGW 安装包含 testlib，或在编译参数中通过 -I 指定其目录。"
        self.checker_info = {
            "status": "ok" if result["status"] == "ok" else "error",
            "output": output,
            "ms": result["ms"],
            "at": time.time(),
            "exe": result.get("exe"),
            "warnings": result.get("warnings", 0),
            "command": result.get("command", ""),
        }
        if result["status"] == "ok" and Path(result["exe"]).is_file():
            self._checker_sig = signature
            return {"ok": True, "argv": [result["exe"]], "message": ""}
        return {"ok": False, "argv": None, "message": output.strip() or "Checker 编译失败"}

    def compile_checker_now(self) -> Dict:
        if self.judging.get("running"):
            raise SessionError("正在评测，请稍后再编译")
        if self.checker is None:
            raise SessionError("还没有选择 checker 文件")
        with self.lock:
            try:
                self._do_compile_checker(force=True)
            except SessionError as exc:
                self.checker_info = {
                    "status": "error", "output": str(exc), "ms": 0,
                    "at": time.time(), "exe": None, "warnings": 0,
                }
        return self.state()

    def set_checker(self, path: Optional[str] = None, enabled: Optional[bool] = None) -> Dict:
        with self.lock:
            if path is not None:
                candidate = Path(path)
                if not candidate.is_file():
                    raise SessionError(f"文件不存在：{candidate}")
                if candidate.suffix.lower() not in detect.SOURCE_EXTS | {".exe"}:
                    raise SessionError("checker 需要是 C/C++ 源文件或 .exe")
                self.checker = candidate
                self.checker_enabled = True
                self.checker_info = self._empty_compile()
                self._checker_sig = None
            if enabled is not None:
                self.checker_enabled = bool(enabled)
            if self.checker_enabled and self.checker is None:
                raise SessionError("请先选择一个 checker 文件")
            if self.folder is not None:
                self.store.save_checker_config(
                    str(self.folder),
                    str(self.checker) if self.checker else None,
                    self.checker_enabled,
                )
        return self.state()

    def _ensure_checker(self) -> Optional[Dict]:
        if not self.checker_enabled:
            return None
        if self.checker is None:
            raise SessionError("已启用 Special Judge，但还没有选择 checker 文件")
        outcome = self._do_compile_checker(force=False)
        if not outcome.get("ok"):
            raise SessionError(outcome.get("message") or "Checker 编译失败")
        return {
            "exe": outcome["argv"][0],
            "source": str(self.checker),
            "timeMs": judge.CHECKER_TIME_MS,
            "memoryMB": judge.CHECKER_MEMORY_MB,
        }

    def pick_checker(self) -> List[str]:
        from .dialogs import CHECKER_FILETYPES
        return self.dialogs.choose_files(
            "选择 Special Judge checker（testlib）", CHECKER_FILETYPES, self._initial_dir()
        )

    # ------------------------------------------------------------------ #
    # 评测
    # ------------------------------------------------------------------ #
    def _selected_cases(self, ids: Optional[Sequence[str]], all_cases: bool) -> List[Dict]:
        if all_cases or not ids:
            return list(self.cases)
        wanted = set(ids)
        return [case for case in self.cases if case["id"] in wanted]

    def start_judge(self, ids: Optional[Sequence[str]] = None, all_cases: bool = False) -> Dict:
        with self.lock:
            if self.judging.get("running"):
                raise SessionError("已有评测正在进行")
            if not self.cases:
                raise SessionError("当前没有测试用例，请先导入数据")
            selected = self._selected_cases(ids, all_cases)
            if not selected:
                raise SessionError("没有选中任何测试用例")

            settings = self.store.settings
            self.stop_event.clear()
            for case in selected:
                self.results[case["id"]] = {"status": "PENDING", "message": "等待评测"}
            self.judging = {
                "running": True,
                "total": len(selected),
                "done": 0,
                "current": None,
                "startedAt": time.time(),
                "finishedAt": None,
                "summary": {},
                "stopOnFail": bool(settings.get("stopOnFail")),
            }
            self._judge_thread = threading.Thread(
                target=self._judge_worker, args=(selected, settings), name="judge", daemon=True,
            )
            self._judge_thread.start()
        return self.state()

    def stop_judge(self) -> Dict:
        if self.judging.get("running"):
            self.stop_event.set()
        return self.state()

    def _judge_worker(self, selected: List[Dict], settings: Dict) -> None:
        stop_on_fail = bool(settings.get("stopOnFail"))
        artifacts_root = runs_dir()
        try:
            argv: Optional[List[str]]
            compile_error = None
            try:
                auto_compile = bool(settings.get("autoCompile", True))
                reusable = (
                    not auto_compile
                    and self.exe is not None
                    and Path(self.exe).is_file()
                    and self.compile_info.get("status") == "ok"
                )
                if reusable and self.source is not None:
                    argv = ([judge.python_executable(), str(self.source)]
                            if self.source.suffix.lower() == ".py" else [str(self.exe)])
                else:
                    outcome = self._do_compile(force=False)
                    argv = outcome.get("argv")
                    compile_error = None if outcome.get("ok") else outcome.get("message", "编译失败")
            except SessionError as exc:
                argv = None
                compile_error = str(exc)

            if argv is None:
                with self.lock:
                    for case in selected:
                        self.results[case["id"]] = {
                            "status": "CE",
                            "message": compile_error or "编译错误",
                            "finishedAt": time.time(),
                        }
                    self.judging["done"] = len(selected)
                return

            # 启用 Special Judge 时先准备 checker
            checker_conf: Optional[Dict] = None
            checker_error: Optional[str] = None
            if self.checker_enabled:
                try:
                    checker_conf = self._ensure_checker()
                except SessionError as exc:
                    checker_error = str(exc)
            if checker_error:
                with self.lock:
                    for case in selected:
                        self.results[case["id"]] = {
                            "status": "CJF",
                            "message": f"Checker 不可用：{checker_error}",
                            "finishedAt": time.time(),
                        }
                    self.judging["done"] = len(selected)
                return

            for index, case in enumerate(selected):
                if self.stop_event.is_set():
                    with self.lock:
                        for rest in selected[index:]:
                            self.results[rest["id"]] = {"status": "SKIP", "message": "已跳过"}
                    break

                self.judging["current"] = case["id"]
                try:
                    result = judge.judge_case(
                        argv, case, settings,
                        artifact_dir=artifacts_root / case["id"],
                        work_root=work_root(),
                        stop_event=self.stop_event,
                        checker=checker_conf,
                    )
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    result = {
                        "status": "RE",
                        "message": f"评测器内部错误：{exc}",
                        "timeMs": 0, "cpuMs": 0, "memoryKB": 0,
                        "exitCode": 0, "finishedAt": time.time(),
                    }

                with self.lock:
                    self.results[case["id"]] = result
                    self.judging["done"] = index + 1

                status = result.get("status")
                if stop_on_fail and status not in ("AC", "RUN"):
                    with self.lock:
                        for rest in selected[index + 1:]:
                            self.results[rest["id"]] = {"status": "SKIP", "message": "遇错停止"}
                    break
        finally:
            with self.lock:
                self.judging["running"] = False
                self.judging["current"] = None
                self.judging["finishedAt"] = time.time()
                self.judging["summary"] = self._summarize(selected)

    def _summarize(self, selected: List[Dict]) -> Dict:
        counts: Dict[str, int] = {}
        max_time = 0.0
        max_memory = 0
        for case in selected:
            result = self.results.get(case["id"]) or {}
            status = result.get("status", "NONE")
            counts[status] = counts.get(status, 0) + 1
            max_time = max(max_time, float(result.get("timeMs") or 0))
            max_memory = max(max_memory, int(result.get("memoryKB") or 0))
        return {
            "counts": counts,
            "maxTimeMs": round(max_time, 2),
            "maxMemoryKB": max_memory,
        }

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def _compiler_info(self) -> Dict:
        settings = self.store.settings
        compiler = str(settings.get("compiler") or "g++")
        if self._compiler_probe and self._compiler_probe.get("path") == compiler:
            return self._compiler_probe
        probe = judge.probe_compiler(compiler)
        probe["path"] = compiler
        self._compiler_probe = probe
        return probe

    def state(self) -> Dict:
        with self.lock:
            settings = self.store.settings
            cases = []
            for case in self.cases:
                item = dict(case)
                item["hasAnswer"] = bool(case.get("ans") and Path(case["ans"]).is_file())
                item["result"] = self.results.get(case["id"])
                cases.append(item)

            source = None
            if self.source is not None:
                try:
                    stat = self.source.stat()
                    source = {
                        "path": str(self.source),
                        "name": self.source.name,
                        "stem": self.source.stem,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "kind": self.source.suffix.lower().lstrip("."),
                    }
                except OSError:
                    source = {"path": str(self.source), "name": self.source.name, "kind": "unknown"}

            binary = None
            if self.exe is not None and Path(self.exe).is_file():
                try:
                    binary = {"path": str(self.exe), "name": Path(self.exe).name,
                              "mtime": Path(self.exe).stat().st_mtime}
                except OSError:
                    binary = {"path": str(self.exe), "name": Path(self.exe).name}

            folder = None
            if self.folder is not None:
                folder = {"path": str(self.folder), "name": self.folder.name,
                          "exists": self.folder.is_dir()}

            recent = [{"path": path, "exists": Path(path).is_dir()} for path in self.store.recent]

            checker = {
                "enabled": self.checker_enabled,
                "path": str(self.checker) if self.checker else None,
                "name": self.checker.name if self.checker else None,
                "exists": bool(self.checker and self.checker.is_file()),
                "kind": self.checker.suffix.lower().lstrip(".") if self.checker else "",
                "compile": dict(self.checker_info),
                "candidates": [
                    {"path": str(p), "name": p.name} for p in self.checker_candidates[:6]
                ],
                "testlib": judge.find_testlib(str(settings.get("compiler") or "g++")) or "",
                "limits": {
                    "timeMs": judge.CHECKER_TIME_MS,
                    "memoryMB": judge.CHECKER_MEMORY_MB,
                },
            }

            return {
                "app": {
                    "name": "OI 对拍器",
                    "version": __version__,
                    "platform": sys.platform,
                    "uptimeMs": int((time.time() - self._started_at) * 1000),
                    "dataDir": str(data_dir()),
                },
                "settings": settings,
                "folder": folder,
                "problem": {"name": self.problem_name or (self.folder.name if self.folder else "")},
                "source": source,
                "sourceCandidates": [
                    {"path": str(p), "name": p.name} for p in self.source_candidates[:8]
                ],
                "binary": binary,
                "compile": dict(self.compile_info),
                "checker": checker,
                "cases": cases,
                "judging": dict(self.judging),
                "recent": recent,
                "compiler": self._compiler_info() if settings.get("compiler") else {"ok": False},
                "capabilities": {
                    "dialogs": True,
                    "posix": os.name != "nt",
                },
            }

    def case_detail(self, case_id: str, parts: Sequence[str]) -> Dict:
        with self.lock:
            case = next((c for c in self.cases if c["id"] == case_id), None)
            if case is None:
                raise SessionError("测试点不存在")
            result = self.results.get(case_id) or {}
            artifact = Path(result["artifact"]) if result.get("artifact") else None

            def bundle(path: Optional[str], limit: int = 400_000) -> Dict:
                if not path or not Path(path).is_file():
                    return {"text": "", "size": 0, "exists": False, "truncated": False}
                size = judge.file_size(path)
                text = judge.read_text_file(path, limit)
                return {"text": text, "size": size, "exists": True, "truncated": size > limit}

            payload: Dict = {"id": case_id, "case": dict(case), "result": result}
            if "in" in parts:
                payload["in"] = bundle(case.get("in"))
            if "ans" in parts:
                payload["ans"] = bundle(case.get("ans"))
            if "out" in parts:
                payload["out"] = bundle(str(artifact / "stdout.txt")) if artifact else \
                    {"text": "", "size": 0, "exists": False, "truncated": False}
            if "err" in parts:
                payload["err"] = bundle(str(artifact / "stderr.txt")) if artifact else \
                    {"text": "", "size": 0, "exists": False, "truncated": False}
            if "checkerErr" in parts:
                payload["checkerErr"] = bundle(str(artifact / "checker_stderr.txt")) if artifact else \
                    {"text": "", "size": 0, "exists": False, "truncated": False}
            if "checkerOut" in parts:
                payload["checkerOut"] = bundle(str(artifact / "checker_stdout.txt")) if artifact else \
                    {"text": "", "size": 0, "exists": False, "truncated": False}
            return payload

    # ------------------------------------------------------------------ #
    # 设置与系统
    # ------------------------------------------------------------------ #
    def update_settings(self, patch: Dict) -> Dict:
        with self.lock:
            before = self.store.settings
            after = self.store.update_settings(patch)
            if (before.get("compiler") != after.get("compiler")
                    or before.get("flags") != after.get("flags")
                    or before.get("stackMB") != after.get("stackMB")):
                self._compile_sig = None
                self._checker_sig = None
                self._compiler_probe = None
        return self.state()

    def reveal(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            raise SessionError("文件不存在")
        try:
            if target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
        except OSError as exc:
            raise SessionError(f"无法打开资源管理器：{exc}") from exc

    def _initial_dir(self) -> str:
        if self.folder is not None and self.folder.is_dir():
            return str(self.folder)
        for recent in self.store.recent:
            if Path(recent).is_dir():
                return recent
        return ""

    def pick_folder(self) -> Optional[str]:
        return self.dialogs.choose_folder("选择题目文件夹", self._initial_dir())

    def pick_source(self) -> List[str]:
        return self.dialogs.choose_files("选择选手程序", SOURCE_FILETYPES, self._initial_dir())

    def pick_cases(self) -> List[str]:
        return self.dialogs.choose_files(
            "选择测试数据（可多选 .in / .ans / .out）", CASE_FILETYPES, self._initial_dir()
        )

    def shutdown(self) -> None:
        self.stop_event.set()
        self.store.save()
