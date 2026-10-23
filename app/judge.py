"""评测内核：编译、受限运行、答案比对。

只依赖标准库。在 Windows 上使用 Job Object 限制进程内存，并读取
PeakJobMemoryUsed / 总 CPU 时间，做到与 OI 评测机一致的计时与内存统计。
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .paths import tmp_dir

IS_WIN = os.name == "nt"

STATUS_LABEL = {
    "AC": "通过",
    "WA": "答案错误",
    "TLE": "时间超限",
    "MLE": "空间超限",
    "RE": "运行错误",
    "OLE": "输出超限",
    "CE": "编译错误",
    "PE": "格式错误",
    "PC": "部分分",
    "CJF": "Checker 异常",
    "RUN": "运行完成",
    "PENDING": "评测中",
    "SKIP": "已跳过",
    "STOPPED": "已中止",
    "NONE": "未评测",
}

STATUS_COLOR = {
    "AC": "ok", "WA": "bad", "TLE": "warn", "MLE": "purple", "RE": "bad",
    "OLE": "warn", "CE": "bad", "PE": "warn", "PC": "info", "CJF": "purple",
    "RUN": "info", "PENDING": "info",
    "SKIP": "muted", "STOPPED": "muted", "NONE": "muted",
}

MAX_TEXT_BYTES = 2 * 1024 * 1024
CHECKER_TIME_MS = 10000
CHECKER_MEMORY_MB = 1024


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_text_file(path: str | os.PathLike, limit: int = MAX_TEXT_BYTES) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[:limit]
    return decode_bytes(data)


def file_size(path: str | os.PathLike) -> int:
    try:
        return max(0, Path(path).stat().st_size)
    except OSError:
        return 0


@dataclass
class Limits:
    time_ms: int = 1000
    memory_mb: int = 256
    space_mb: int = 64

    @property
    def memory_bytes(self) -> int:
        return max(1, int(self.memory_mb)) * 1024 * 1024

    @property
    def enforce_bytes(self) -> int:
        # 允许 2% 的余量，超出用户限制即判 MLE，同时保护机器
        return max(int(self.memory_bytes * 1.02), 24 * 1024 * 1024)

    @property
    def space_bytes(self) -> int:
        return int(self.space_mb) * 1024 * 1024 if int(self.space_mb or 0) > 0 else 0


def limits_from_settings(settings: Dict) -> Limits:
    def _int(key: str, default: int) -> int:
        try:
            return max(0, int(float(settings.get(key, default))))
        except (TypeError, ValueError):
            return default

    return Limits(
        time_ms=_int("timeMs", 1000) or 1000,
        memory_mb=_int("memoryMB", 256) or 256,
        space_mb=_int("spaceMB", 64),
    )


def python_executable() -> str:
    exe = sys.executable
    if IS_WIN:
        candidate = Path(exe).with_name("python.exe")
        if candidate.is_file():
            return str(candidate)
    return exe


# --------------------------------------------------------------------------- #
# 编译
# --------------------------------------------------------------------------- #

def _run_capture(args: Sequence[str], cwd: Optional[Path], timeout: float,
                 env: Optional[Dict[str, str]] = None) -> tuple:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )
        out = decode_bytes(proc.stdout or b"")
        return proc.returncode, out, int((time.perf_counter() - started) * 1000)
    except subprocess.TimeoutExpired as exc:
        out = decode_bytes(exc.output or b"")
        return -1, out + "\n[编译超时]", int((time.perf_counter() - started) * 1000)
    except FileNotFoundError:
        return -2, "找不到编译器，请在“设置”中填写 g++ 的完整路径。", 0
    except OSError as exc:
        return -3, f"无法启动编译器：{exc}", 0


def compile_source(source: str | os.PathLike, output_exe: str | os.PathLike,
                   settings: Dict, timeout: float = 120.0,
                   extra_flags: Sequence[str] = ()) -> Dict:
    """编译 C/C++ 源文件，返回统一的结果字典。"""
    source = Path(source)
    output_exe = Path(output_exe)

    if source.suffix.lower() == ".py":
        return {
            "status": "ok",
            "output": "Python 脚本无需编译。",
            "ms": 0,
            "exe": str(source),
            "command": f"{python_executable()} {source.name}",
            "warnings": 0,
        }

    compiler = str(settings.get("compiler") or "g++").strip() or "g++"
    raw_flags = str(settings.get("flags") or "").strip()
    flags = re.split(r"\s+", raw_flags) if raw_flags else []
    try:
        stack_mb = int(float(settings.get("stackMB", 0)))
    except (TypeError, ValueError):
        stack_mb = 0

    output_exe.parent.mkdir(parents=True, exist_ok=True)
    try:
        if output_exe.exists():
            output_exe.unlink()
    except OSError:
        pass

    def build(flag_list: List[str]) -> tuple:
        args = [compiler, str(source), "-o", str(output_exe)] + flag_list + list(extra_flags)
        if stack_mb > 0 and not any("--stack" in f for f in flag_list):
            args.append(f"-Wl,--stack={stack_mb * 1024 * 1024}")
        return args

    args = build(flags)
    compile_env: Optional[Dict[str, str]] = None
    try:
        temp_path = str(tmp_dir())
        compile_env = dict(os.environ)
        compile_env["TMP"] = temp_path
        compile_env["TEMP"] = temp_path
    except OSError:
        compile_env = None

    code, text, ms = _run_capture(args, source.parent, timeout, env=compile_env)

    # -static 在部分环境不可用，自动降级重试一次
    if code != 0 and any(f == "-static" for f in flags):
        lowered = text.lower()
        if "static" in lowered and ("unrecognized" in lowered or "cannot find" in lowered
                                    or "not supported" in lowered or "invalid" in lowered):
            retry_flags = [f for f in flags if f != "-static"]
            retry_args = build(retry_flags)
            code2, text2, ms2 = _run_capture(retry_args, source.parent, timeout, env=compile_env)
            text = text + "\n\n[已自动移除 -static 重试]\n" + text2
            ms += ms2
            args, code = retry_args, code2

    ok = code == 0 and output_exe.is_file()
    warnings = len(re.findall(r"\bwarning:", text, re.IGNORECASE))
    return {
        "status": "ok" if ok else "error",
        "output": text.strip() or ("编译成功" if ok else "编译失败（无输出）"),
        "ms": ms,
        "exe": str(output_exe),
        "command": subprocess.list2cmdline(args),
        "warnings": warnings,
    }


def probe_compiler(compiler: str) -> Dict:
    code, text, _ = _run_capture([compiler, "--version"], None, 8.0)
    first = (text.splitlines() or [""])[0].strip()
    return {"ok": code == 0, "path": compiler, "version": first if code == 0 else ""}


_testlib_cache: Dict[str, Optional[str]] = {}


def find_testlib(compiler: str) -> Optional[str]:
    """在编译器安装目录里寻找 testlib.h，返回其所在目录。"""
    key = str(compiler)
    if key in _testlib_cache:
        return _testlib_cache[key]

    candidates: List[Path] = []
    executable = shutil.which(compiler) or compiler
    try:
        base = Path(executable).resolve().parent
    except OSError:
        base = None

    if base is not None:
        for relative in (
            Path("..") / "include",
            Path("..") / "x86_64-w64-mingw32" / "include",
            Path("..") / "i686-w64-mingw32" / "include",
            Path("..") / "lib" / "gcc",
        ):
            candidates.append((base / relative).resolve())

    # g++ -E -v 打印的搜索路径里也可能有
    try:
        probe = subprocess.run(
            [compiler, "-E", "-x", "c++", "-", "-v"],
            input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )
        text = decode_bytes(probe.stderr or b"")
        collecting = False
        for line in text.splitlines():
            if line.startswith("#include <...>"):
                collecting = True
                continue
            if line.startswith("End of search list"):
                collecting = False
                continue
            if collecting and line.strip():
                stripped = line.strip()
                if stripped.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", stripped):
                    candidates.append(Path(stripped))
    except (OSError, subprocess.SubprocessError):
        pass

    found: Optional[str] = None
    for directory in candidates:
        try:
            if (directory / "testlib.h").is_file():
                found = str(directory)
                break
        except OSError:
            continue

    _testlib_cache[key] = found
    return found


# --------------------------------------------------------------------------- #
# 受限运行 —— Windows
# --------------------------------------------------------------------------- #

@dataclass
class RunOutcome:
    status: str = "OK"
    time_ms: float = 0.0
    cpu_ms: float = 0.0
    peak_bytes: int = 0
    exit_code: int = 0
    message: str = ""
    killed: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    work_bytes: int = 0


if IS_WIN:
    from ctypes import wintypes as _w

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    CREATE_ALWAYS = 2
    FILE_ATTRIBUTE_NORMAL = 0x80
    CREATE_SUSPENDED = 0x00000004
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESTDHANDLES = 0x00000100
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_BASIC_ACCOUNTING = 1
    JOB_OBJECT_EXTENDED_LIMIT = 9
    JOB_OBJECT_ASSOCIATE_COMPLETION_PORT = 7
    JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT = 9
    JOB_OBJECT_MSG_JOB_MEMORY_LIMIT = 10

    class _STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", _w.DWORD),
            ("lpReserved", _w.LPWSTR),
            ("lpDesktop", _w.LPWSTR),
            ("lpTitle", _w.LPWSTR),
            ("dwX", _w.DWORD),
            ("dwY", _w.DWORD),
            ("dwXSize", _w.DWORD),
            ("dwYSize", _w.DWORD),
            ("dwXCountChars", _w.DWORD),
            ("dwYCountChars", _w.DWORD),
            ("dwFillAttribute", _w.DWORD),
            ("dwFlags", _w.DWORD),
            ("wShowWindow", _w.WORD),
            ("cbReserved2", _w.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", _w.HANDLE),
            ("hStdOutput", _w.HANDLE),
            ("hStdError", _w.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", _w.HANDLE),
            ("hThread", _w.HANDLE),
            ("dwProcessId", _w.DWORD),
            ("dwThreadId", _w.DWORD),
        ]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", _w.DWORD),
            ("lpSecurityDescriptor", _w.LPVOID),
            ("bInheritHandle", _w.BOOL),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", _w.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", _w.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", _w.DWORD),
            ("SchedulingClass", _w.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", _w.DWORD),
            ("TotalProcesses", _w.DWORD),
            ("ActiveProcesses", _w.DWORD),
            ("TotalTerminatedProcesses", _w.DWORD),
        ]

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", _w.DWORD), ("dwHighDateTime", _w.DWORD)]

    class _JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
        _fields_ = [("CompletionKey", _w.LPVOID), ("CompletionPort", _w.HANDLE)]

    _k32.CreateJobObjectW.restype = _w.HANDLE
    _k32.CreateJobObjectW.argtypes = [_w.LPVOID, _w.LPCWSTR]
    _k32.SetInformationJobObject.restype = _w.BOOL
    _k32.SetInformationJobObject.argtypes = [_w.HANDLE, ctypes.c_int, _w.LPVOID, _w.DWORD]
    _k32.QueryInformationJobObject.restype = _w.BOOL
    _k32.QueryInformationJobObject.argtypes = [_w.HANDLE, ctypes.c_int, _w.LPVOID, _w.DWORD, _w.LPDWORD]
    _k32.AssignProcessToJobObject.restype = _w.BOOL
    _k32.AssignProcessToJobObject.argtypes = [_w.HANDLE, _w.HANDLE]
    _k32.TerminateJobObject.restype = _w.BOOL
    _k32.TerminateJobObject.argtypes = [_w.HANDLE, _w.UINT]
    _k32.TerminateProcess.restype = _w.BOOL
    _k32.TerminateProcess.argtypes = [_w.HANDLE, _w.UINT]
    _k32.CreateIoCompletionPort.restype = _w.HANDLE
    _k32.CreateIoCompletionPort.argtypes = [_w.HANDLE, _w.HANDLE, ctypes.c_size_t, _w.DWORD]
    _k32.GetQueuedCompletionStatus.restype = _w.BOOL
    _k32.GetQueuedCompletionStatus.argtypes = [
        _w.HANDLE, ctypes.POINTER(_w.DWORD), ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_void_p), _w.DWORD,
    ]
    _k32.CreateFileW.restype = _w.HANDLE
    _k32.CreateFileW.argtypes = [_w.LPCWSTR, _w.DWORD, _w.DWORD, ctypes.POINTER(_SECURITY_ATTRIBUTES),
                                 _w.DWORD, _w.DWORD, _w.HANDLE]
    _k32.CreateProcessW.restype = _w.BOOL
    _k32.CreateProcessW.argtypes = [_w.LPCWSTR, _w.LPWSTR, ctypes.POINTER(_SECURITY_ATTRIBUTES),
                                    ctypes.POINTER(_SECURITY_ATTRIBUTES), _w.BOOL, _w.DWORD, _w.LPVOID,
                                    _w.LPCWSTR, ctypes.POINTER(_STARTUPINFO), ctypes.POINTER(_PROCESS_INFORMATION)]
    _k32.ResumeThread.restype = _w.DWORD
    _k32.ResumeThread.argtypes = [_w.HANDLE]
    _k32.WaitForSingleObject.restype = _w.DWORD
    _k32.WaitForSingleObject.argtypes = [_w.HANDLE, _w.DWORD]
    _k32.GetExitCodeProcess.restype = _w.BOOL
    _k32.GetExitCodeProcess.argtypes = [_w.HANDLE, ctypes.POINTER(_w.DWORD)]
    _k32.CloseHandle.restype = _w.BOOL
    _k32.CloseHandle.argtypes = [_w.HANDLE]

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    NTSTATUS_MESSAGES = {
        0xC0000005: "非法内存访问 (ACCESS_VIOLATION)",
        0xC00000FD: "栈溢出 (STACK_OVERFLOW，递归过深或局部数组过大)",
        0xC0000094: "整数除零 (INT_DIVIDE_BY_ZERO)",
        0xC0000017: "内存不足 (NO_MEMORY)",
        0xC0000409: "栈缓冲区越界 (STACK_BUFFER_OVERRUN)",
        0xC0000374: "堆损坏 (HEAP_CORRUPTION)",
        0xC000013A: "被 Ctrl+C 中止",
    }

    def _open_handle(path: Path, access: int, creation: int, inherit: bool) -> int:
        sa = _SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
        sa.lpSecurityDescriptor = None
        sa.bInheritHandle = bool(inherit)
        handle = _k32.CreateFileW(
            str(path), access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            ctypes.byref(sa), creation, FILE_ATTRIBUTE_NORMAL, None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), f"无法打开文件：{path}")
        return handle

    class _Job:
        def __init__(self, enforce_bytes: int):
            self.handle = _k32.CreateJobObjectW(None, None)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObject 失败")
            self.mem_limit_hit = False
            self.port = None

            self._info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            self._info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            self._info.ProcessMemoryLimit = enforce_bytes
            _k32.SetInformationJobObject(
                self.handle, JOB_OBJECT_EXTENDED_LIMIT,
                ctypes.byref(self._info), ctypes.sizeof(self._info),
            )

            # 注册完成端口，用于可靠地捕获“进程超内存上限”事件
            port = _k32.CreateIoCompletionPort(_w.HANDLE(INVALID_HANDLE_VALUE), None, 0, 1)
            if port:
                assoc = _JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
                assoc.CompletionKey = ctypes.c_void_p(1)
                assoc.CompletionPort = port
                if _k32.SetInformationJobObject(
                    self.handle, JOB_OBJECT_ASSOCIATE_COMPLETION_PORT,
                    ctypes.byref(assoc), ctypes.sizeof(assoc),
                ):
                    self.port = port
                else:
                    _k32.CloseHandle(port)

        def assign(self, hprocess) -> bool:
            return bool(_k32.AssignProcessToJobObject(self.handle, hprocess))

        def poll_messages(self) -> None:
            if not self.port:
                return
            while True:
                code = _w.DWORD(0)
                key = ctypes.c_size_t(0)
                overlapped = ctypes.c_void_p()
                ok = _k32.GetQueuedCompletionStatus(
                    self.port, ctypes.byref(code), ctypes.byref(key),
                    ctypes.byref(overlapped), 0,
                )
                if not ok:
                    return
                if code.value in (JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT, JOB_OBJECT_MSG_JOB_MEMORY_LIMIT):
                    self.mem_limit_hit = True

        def stats(self) -> Dict[str, int]:
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            peak = 0
            if _k32.QueryInformationJobObject(
                self.handle, JOB_OBJECT_EXTENDED_LIMIT,
                ctypes.byref(info), ctypes.sizeof(info), None,
            ):
                peak = int(info.PeakJobMemoryUsed)
            acct = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            cpu_100ns = 0
            if _k32.QueryInformationJobObject(
                self.handle, JOB_OBJECT_BASIC_ACCOUNTING,
                ctypes.byref(acct), ctypes.sizeof(acct), None,
            ):
                cpu_100ns = int(acct.TotalUserTime) + int(acct.TotalKernelTime)
            return {"peak": peak, "cpu_ms": cpu_100ns / 10000.0}

        def terminate(self, code: int = 1) -> None:
            _k32.TerminateJobObject(self.handle, code)

        def close(self) -> None:
            if self.handle:
                _k32.CloseHandle(self.handle)
                self.handle = None
            if self.port:
                _k32.CloseHandle(self.port)
                self.port = None

    def _run_windows(argv: List[str], cwd: Path, in_path: Path, out_path: Path, err_path: Path,
                     limits: Limits, stop_event=None) -> RunOutcome:
        h_in = h_out = h_err = 0
        job = None
        pi = _PROCESS_INFORMATION()
        created = False
        resumed = False
        try:
            h_in = _open_handle(in_path, GENERIC_READ, OPEN_EXISTING, True)
            h_out = _open_handle(out_path, GENERIC_WRITE, CREATE_ALWAYS, True)
            h_err = _open_handle(err_path, GENERIC_WRITE, CREATE_ALWAYS, True)

            si = _STARTUPINFO()
            si.cb = ctypes.sizeof(_STARTUPINFO)
            si.dwFlags = STARTF_USESTDHANDLES
            si.hStdInput = h_in
            si.hStdOutput = h_out
            si.hStdError = h_err

            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
            ok = _k32.CreateProcessW(
                None, cmdline, None, None, True,
                CREATE_SUSPENDED | CREATE_NO_WINDOW, None, str(cwd),
                ctypes.byref(si), ctypes.byref(pi),
            )
            if not ok:
                raise OSError(ctypes.get_last_error(), "无法启动程序")
            created = True

            job = _Job(limits.enforce_bytes)
            job.assign(pi.hProcess)
            _k32.ResumeThread(pi.hThread)
            resumed = True

            started = time.perf_counter()
            killed = ""
            grace_ms = 25
            while True:
                rc = _k32.WaitForSingleObject(pi.hProcess, 3)
                job.poll_messages()
                if rc == WAIT_OBJECT_0:
                    break
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                if stop_event is not None and stop_event.is_set():
                    killed = "stop"
                    job.terminate()
                    _k32.WaitForSingleObject(pi.hProcess, 3000)
                    break
                if elapsed_ms > limits.time_ms + grace_ms:
                    killed = "tle"
                    job.terminate()
                    _k32.WaitForSingleObject(pi.hProcess, 3000)
                    break
                space = limits.space_bytes
                if space:
                    size = max(file_size(out_path), file_size(err_path))
                    if size > space:
                        killed = "ole"
                        job.terminate()
                        _k32.WaitForSingleObject(pi.hProcess, 3000)
                        break
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            job.poll_messages()
            stats = job.stats() if job else {"peak": 0, "cpu_ms": 0.0}
            mem_limit_hit = bool(job and job.mem_limit_hit)
            code = _w.DWORD(0)
            _k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            exit_code = int(code.value)

            outcome = RunOutcome(
                time_ms=elapsed_ms,
                cpu_ms=stats["cpu_ms"],
                peak_bytes=(max(stats["peak"], limits.memory_bytes) if mem_limit_hit
                            else stats["peak"]),
                exit_code=exit_code,
                killed=killed,
                stdout_bytes=file_size(out_path),
                stderr_bytes=file_size(err_path),
            )

            if killed == "tle":
                outcome.status, outcome.message = "TLE", f"超过时间限制 {limits.time_ms} ms"
            elif killed == "ole":
                outcome.status, outcome.message = "OLE", f"输出超过空间限制 {limits.space_mb} MB"
            elif killed == "stop":
                outcome.status, outcome.message = "STOPPED", "评测被手动中止"
            elif mem_limit_hit or stats["peak"] >= limits.memory_bytes:
                outcome.status = "MLE"
                if mem_limit_hit:
                    outcome.message = f"内存分配超过限制 {limits.memory_mb} MB"
                else:
                    outcome.message = (
                        f"峰值内存 {stats['peak'] / 1024 / 1024:.1f} MB，"
                        f"超过限制 {limits.memory_mb} MB"
                    )
            elif exit_code != 0:
                outcome.status = "RE"
                if exit_code in NTSTATUS_MESSAGES:
                    outcome.message = NTSTATUS_MESSAGES[exit_code]
                elif exit_code & 0xFFFFFFFF >= 0xC0000000:
                    outcome.message = f"异常退出 (0x{exit_code & 0xFFFFFFFF:08X})"
                else:
                    outcome.message = f"返回非零退出码 {exit_code}"
            else:
                outcome.status = "OK"
            return outcome
        finally:
            for handle in (h_in, h_out, h_err):
                if handle:
                    try:
                        _k32.CloseHandle(handle)
                    except Exception:
                        pass
            if created:
                if not resumed:
                    # 极端情况下 Job 创建失败，进程仍处于挂起状态，直接结束避免泄漏
                    try:
                        _k32.TerminateProcess(pi.hProcess, 1)
                    except Exception:
                        pass
                try:
                    _k32.CloseHandle(pi.hThread)
                except Exception:
                    pass
                try:
                    _k32.CloseHandle(pi.hProcess)
                except Exception:
                    pass
            if job is not None:
                job.close()


# --------------------------------------------------------------------------- #
# 受限运行 —— POSIX 兜底（开发调试用）
# --------------------------------------------------------------------------- #

def _run_posix(argv: List[str], cwd: Path, in_path: Path, out_path: Path, err_path: Path,
               limits: Limits, stop_event=None) -> RunOutcome:
    try:
        import resource  # noqa: F401
    except ImportError:
        resource = None

    def _limit():
        if resource is None:
            return None
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        except (ValueError, OSError):
            pass
        return None

    with open(in_path, "rb") as fin, open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
        started = time.perf_counter()
        proc = subprocess.Popen(list(argv), cwd=str(cwd), stdin=fin, stdout=fout,
                                stderr=ferr, preexec_fn=_limit)
        killed = ""
        while True:
            if proc.poll() is not None:
                break
            elapsed = (time.perf_counter() - started) * 1000.0
            if stop_event is not None and stop_event.is_set():
                killed = "stop"
                proc.kill()
                break
            if elapsed > limits.time_ms + 25:
                killed = "tle"
                proc.kill()
                break
            if limits.space_bytes and max(file_size(out_path), file_size(err_path)) > limits.space_bytes:
                killed = "ole"
                proc.kill()
                break
            time.sleep(0.003)
        proc.wait()
        elapsed = (time.perf_counter() - started) * 1000.0

    peak = 0
    if resource is not None:
        try:
            peak = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * 1024
        except Exception:
            peak = 0

    outcome = RunOutcome(
        time_ms=elapsed, cpu_ms=elapsed, peak_bytes=peak,
        exit_code=proc.returncode or 0, killed=killed,
        stdout_bytes=file_size(out_path), stderr_bytes=file_size(err_path),
    )
    if killed == "tle":
        outcome.status, outcome.message = "TLE", f"超过时间限制 {limits.time_ms} ms"
    elif killed == "ole":
        outcome.status, outcome.message = "OLE", f"输出超过空间限制 {limits.space_mb} MB"
    elif killed == "stop":
        outcome.status, outcome.message = "STOPPED", "评测被手动中止"
    elif peak and peak >= limits.memory_bytes:
        outcome.status, outcome.message = "MLE", "超过内存限制"
    elif outcome.exit_code != 0:
        outcome.status, outcome.message = "RE", f"返回非零退出码 {outcome.exit_code}"
    else:
        outcome.status = "OK"
    return outcome


def run_process(argv: List[str], cwd: Path, in_path: Path, out_path: Path, err_path: Path,
                limits: Limits, stop_event=None) -> RunOutcome:
    if IS_WIN:
        return _run_windows(argv, cwd, in_path, out_path, err_path, limits, stop_event)
    return _run_posix(argv, cwd, in_path, out_path, err_path, limits, stop_event)


# --------------------------------------------------------------------------- #
# 答案比对
# --------------------------------------------------------------------------- #

def _canonical_lines(text: str, trim: bool) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if trim:
        lines = [line.rstrip() for line in lines]
    return lines


def _preview(text: str, width: int = 200) -> str:
    if text is None:
        return ""
    text = text.replace("\t", "    ")
    if len(text) <= width:
        return text
    return text[:width] + " …"


def _first_diff_column(a: str, b: str) -> int:
    for index, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            return index + 1
    return min(len(a), len(b)) + 1


def compare_texts(expected: str, actual: str, mode: str = "trim") -> Dict:
    if mode == "tokens":
        exp_tokens = expected.split()
        act_tokens = actual.split()
        if exp_tokens == act_tokens:
            return {"equal": True}
        index = 0
        while index < min(len(exp_tokens), len(act_tokens)) and exp_tokens[index] == act_tokens[index]:
            index += 1
        reason = "第 %d 个单词不一致" % (index + 1)
        if index >= len(exp_tokens):
            reason = "输出比标准答案多出内容"
        elif index >= len(act_tokens):
            reason = "输出比标准答案缺少内容"
        return {
            "equal": False,
            "reason": reason,
            "line": None,
            "token": index + 1,
            "expected": _preview(exp_tokens[index] if index < len(exp_tokens) else ""),
            "actual": _preview(act_tokens[index] if index < len(act_tokens) else ""),
            "expectedLines": len(_canonical_lines(expected, True)),
            "actualLines": len(_canonical_lines(actual, True)),
        }

    trim = mode == "trim"
    exp_norm = expected.replace("\r\n", "\n").replace("\r", "\n")
    act_norm = actual.replace("\r\n", "\n").replace("\r", "\n")
    if mode == "strict" and exp_norm == act_norm:
        return {"equal": True}

    exp_lines = _canonical_lines(expected, trim)
    act_lines = _canonical_lines(actual, trim)

    for index in range(max(len(exp_lines), len(act_lines))):
        exp_line = exp_lines[index] if index < len(exp_lines) else None
        act_line = act_lines[index] if index < len(act_lines) else None
        if exp_line == act_line:
            continue
        if act_line is None:
            reason = "输出比标准答案少了第 %d 行" % (index + 1)
        elif exp_line is None:
            reason = "输出比标准答案多了第 %d 行" % (index + 1)
        else:
            reason = "第 %d 行不一致" % (index + 1)
            if mode == "strict" and exp_line.rstrip() == act_line.rstrip():
                reason += "（行末空格/换行差异）"
        return {
            "equal": False,
            "reason": reason,
            "line": index + 1,
            "column": _first_diff_column(exp_line or "", act_line or ""),
            "expected": _preview(exp_line or ""),
            "actual": _preview(act_line or ""),
            "expectedLines": len(exp_lines),
            "actualLines": len(act_lines),
        }

    if mode == "strict" and exp_norm != act_norm:
        return {
            "equal": False,
            "reason": "严格模式下行末空格或换行不一致",
            "line": len(exp_lines) if exp_lines else 1,
            "expected": _preview(exp_lines[-1] if exp_lines else ""),
            "actual": _preview(act_lines[-1] if act_lines else ""),
            "expectedLines": len(exp_lines),
            "actualLines": len(act_lines),
        }
    return {"equal": True}


def compare_files(expected_path: str | os.PathLike, actual_path: str | os.PathLike,
                  mode: str = "trim") -> Dict:
    limit = 16 * 1024 * 1024
    return compare_texts(
        read_text_file(expected_path, limit), read_text_file(actual_path, limit), mode
    )


# --------------------------------------------------------------------------- #
# Special Judge（testlib checker）
# --------------------------------------------------------------------------- #

def _clean_checker_message(text: str, limit: int = 1500) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)[:limit]


def _parse_points(message: str) -> Optional[float]:
    text = (message or "").strip()
    # testlib 会在消息前面加上 "points " / "relative-scoring " 前缀
    text = re.sub(r"^(?:points|relative-scoring)\s*[:：]?\s*", "", text, flags=re.IGNORECASE)
    match = re.match(r"(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def run_checker(checker_exe: str, in_path: str | os.PathLike, out_path: str | os.PathLike,
                ans_path: Optional[str | os.PathLike], artifact_dir: str | os.PathLike,
                work_root: str | os.PathLike, time_ms: int = CHECKER_TIME_MS,
                memory_mb: int = CHECKER_MEMORY_MB, stop_event=None) -> Dict:
    """运行 testlib 风格的 checker：checker <input> <output> <answer>。"""
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = artifact_dir / "checker_stdout.txt"
    stderr_path = artifact_dir / "checker_stderr.txt"
    empty_in = artifact_dir / "checker_stdin.txt"
    if not empty_in.exists():
        try:
            empty_in.write_bytes(b"")
        except OSError:
            pass

    answer: Path
    if ans_path and Path(ans_path).is_file():
        answer = Path(ans_path)
    else:
        answer = artifact_dir / "empty_answer.txt"
        try:
            answer.write_bytes(b"")
        except OSError:
            pass

    workdir = Path(work_root) / ("chk_" + uuid.uuid4().hex[:12])
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    limits = Limits(time_ms=max(1000, int(time_ms)), memory_mb=max(64, int(memory_mb)), space_mb=64)
    try:
        outcome = run_process(
            [checker_exe, str(in_path), str(out_path), str(answer)],
            workdir, empty_in, stdout_path, stderr_path, limits, stop_event,
        )
    except OSError as exc:
        return {"status": "CJF", "message": f"无法运行 checker：{exc}",
                "timeMs": 0.0, "exitCode": -1, "score": None}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    message = _clean_checker_message(read_text_file(stderr_path, 128 * 1024))
    if not message:
        message = _clean_checker_message(read_text_file(stdout_path, 128 * 1024))

    verdict = {
        "status": "CJF",
        "message": message,
        "score": None,
        "timeMs": round(outcome.time_ms, 2),
        "exitCode": outcome.exit_code,
        "verdict": "",
    }

    if outcome.killed == "tle":
        verdict["message"] = f"Checker 运行超时（{limits.time_ms} ms）"
        return verdict
    if outcome.killed == "ole":
        verdict["message"] = "Checker 输出过多"
        return verdict
    if outcome.killed == "stop":
        verdict["message"] = "Checker 运行被中止"
        return verdict
    if outcome.status == "MLE":
        verdict["message"] = "Checker 内存超限"
        return verdict

    # testlib 的 _pc(k) 会把部分分编码在消息里（partially correct (k)）
    partial = re.search(r"partially correct \((\d+)\)", message or "", re.IGNORECASE)
    if partial:
        verdict["status"] = "PC"
        verdict["verdict"] = "partially correct"
        verdict["score"] = float(partial.group(1))
        verdict["message"] = message
        return verdict

    code = outcome.exit_code
    if code == 0:
        verdict["status"] = "AC"
        verdict["message"] = message or "ok"
        verdict["verdict"] = "ok"
    elif code == 1:
        verdict["status"] = "WA"
        verdict["message"] = message or "wrong answer"
        verdict["verdict"] = "wrong answer"
    elif code == 2:
        verdict["status"] = "PE"
        verdict["message"] = message or "presentation error"
        verdict["verdict"] = "presentation error"
    elif code == 4:
        verdict["status"] = "PE"
        verdict["message"] = message or "dirty output"
        verdict["verdict"] = "dirty"
    elif code == 7:
        verdict["status"] = "PC"
        verdict["verdict"] = "points"
        verdict["score"] = _parse_points(message)
        if verdict["score"] is not None and verdict["score"] >= 1.0:
            verdict["status"] = "AC"
        verdict["message"] = message or "points"
    else:
        verdict["status"] = "CJF"
        verdict["verdict"] = "checker failed"
        if code == 3:
            verdict["message"] = message or "Checker 内部错误（fail）"
        elif code & 0xFFFFFFFF >= 0xC0000000:
            verdict["message"] = message or f"Checker 异常退出（0x{code & 0xFFFFFFFF:08X}）"
        else:
            verdict["message"] = message or f"未知的 Checker 退出码 {code}"
    return verdict


# --------------------------------------------------------------------------- #
# 单用例评测（供服务端与测试使用）
# --------------------------------------------------------------------------- #

def _is_excluded_file(name: str, in_name: str) -> bool:
    lowered = name.lower()
    if lowered == in_name.lower():
        return True
    if lowered in ("stdout.txt", "stderr.txt"):
        return True
    return False


def _scan_work_files(workdir: Path, in_name: str) -> List[Path]:
    result: List[Path] = []
    for entry in os.scandir(workdir):
        if entry.is_file(follow_symlinks=False) and not _is_excluded_file(entry.name, in_name):
            result.append(Path(entry.path))
    return result


def judge_case(argv: List[str], case: Dict, settings: Dict, artifact_dir: str | os.PathLike,
               work_root: str | os.PathLike, stop_event=None, checker: Optional[Dict] = None) -> Dict:
    """评测单个测试点，返回可直接展示的结果字典。"""
    limits = limits_from_settings(settings)
    mode = settings.get("compareMode", "trim")

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifact_dir / "stdout.txt"
    err_path = artifact_dir / "stderr.txt"

    workdir = Path(work_root) / ("run_" + uuid.uuid4().hex[:12])
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    in_path = Path(case["in"])
    in_name = in_path.name
    try:
        try:
            shutil.copyfile(in_path, workdir / in_name)
        except OSError:
            pass

        try:
            outcome = run_process(argv, workdir, in_path, out_path, err_path, limits, stop_event)
        except OSError as exc:
            outcome = RunOutcome(status="RE", message=f"无法运行程序：{exc}")

        message = outcome.message

        # 兼容使用 freopen 输出到文件的程序：stdout 为空时取唯一产物
        if outcome.status == "OK" and file_size(out_path) == 0:
            produced = [p for p in _scan_work_files(workdir, in_name) if p.stat().st_size > 0]
            if len(produced) == 1:
                try:
                    shutil.copyfile(produced[0], out_path)
                    message = f"程序通过文件输出（{produced[0].name}）"
                except OSError:
                    pass

        # 统计工作目录磁盘占用
        work_bytes = 0
        for path in _scan_work_files(workdir, in_name):
            work_bytes += file_size(path)
        outcome.work_bytes = work_bytes
        if (outcome.status == "OK" and limits.space_bytes
                and outcome.work_bytes > limits.space_bytes):
            outcome.status = "OLE"
            message = f"写入文件 {outcome.work_bytes / 1024 / 1024:.1f} MB，超过空间限制"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    result: Dict = {
        "status": outcome.status,
        "timeMs": round(outcome.time_ms, 2),
        "cpuMs": round(outcome.cpu_ms, 2),
        "memoryKB": int(outcome.peak_bytes / 1024),
        "exitCode": outcome.exit_code,
        "message": message,
        "stdoutBytes": file_size(out_path),
        "stderrBytes": file_size(err_path),
        "workBytes": work_bytes,
        "finishedAt": time.time(),
        "compare": None,
        "score": None,
        "artifact": str(artifact_dir),
    }

    def cap_artifacts() -> None:
        """避免一次 OLE / 大输出把结果目录撑爆：只保留前 512 KB。"""
        for path in (out_path, err_path):
            if file_size(path) <= 8 * 1024 * 1024:
                continue
            try:
                with open(path, "rb") as handle:
                    head = handle.read(512 * 1024)
                with open(path, "wb") as handle:
                    handle.write(head)
                    handle.write("\n\n... [输出过大，仅保留前 512 KB] ...\n".encode("utf-8"))
            except OSError:
                pass

    ans_path = case.get("ans")
    if outcome.status != "OK":
        cap_artifacts()
        return result

    # Special Judge：由 checker 给出结论（此时忽略“答案比较”设置）
    if checker:
        verdict = run_checker(
            checker["exe"], in_path, out_path, ans_path,
            artifact_dir, work_root,
            time_ms=int(checker.get("timeMs", CHECKER_TIME_MS)),
            memory_mb=int(checker.get("memoryMB", CHECKER_MEMORY_MB)),
            stop_event=stop_event,
        )
        result["checker"] = verdict
        result["status"] = verdict["status"]
        result["message"] = verdict["message"] or STATUS_LABEL.get(verdict["status"], "")
        if verdict.get("score") is not None:
            result["score"] = verdict["score"]
        cap_artifacts()
        return result

    if not ans_path or not Path(ans_path).is_file():
        result["status"] = "RUN"
        result["message"] = message or "无标准答案，仅记录运行结果"
        cap_artifacts()
        return result

    try:
        cmp_result = compare_files(ans_path, out_path, mode)
    except Exception as exc:  # noqa: BLE001
        cmp_result = {"equal": False, "reason": f"比对失败：{exc}"}
    result["compare"] = cmp_result
    if cmp_result.get("equal"):
        result["status"] = "AC"
    else:
        result["status"] = "WA"
        result["message"] = cmp_result.get("reason", "答案错误")
    cap_artifacts()
    return result
