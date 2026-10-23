"""OI 对拍器 —— 启动入口。

启动本地服务后，用一个独立的 Chromium/Edge 应用窗口承载界面；
如果没有可用的 Chromium 内核浏览器，则回退到系统默认浏览器。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import APP_NAME, __version__  # noqa: E402
from app.paths import data_dir, log_path  # noqa: E402
from app.server import Context, create_server  # noqa: E402
from app.session import Session  # noqa: E402
from app.store import Store  # noqa: E402

IDLE_TIMEOUT = 180.0
SESSION_FILE = data_dir() / "session.json"


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #

def setup_logging() -> None:
    try:
        path = log_path()
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.write_text("", encoding="utf-8")
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = None

    if stream is not None:
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream

    def excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] unhandled: {text}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = excepthook


# --------------------------------------------------------------------------- #
# 浏览器
# --------------------------------------------------------------------------- #

def find_chromium() -> str | None:
    env = os.environ
    roots = [
        env.get("ProgramFiles(x86)"),
        env.get("ProgramFiles"),
        env.get("LOCALAPPDATA"),
        env.get("ProgramW6432"),
    ]
    relative = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"Microsoft\Edge Beta\Application\msedge.exe",
        r"Microsoft\Edge Dev\Application\msedge.exe",
    ]
    for root in roots:
        if not root:
            continue
        for rel in relative:
            candidate = Path(root) / rel
            if candidate.is_file():
                return str(candidate)
    return None


def launch_ui(url: str, app_window: bool = True) -> subprocess.Popen | None:
    if app_window:
        exe = find_chromium()
        if exe:
            profile = data_dir() / "browser"
            args = [
                exe,
                f"--app={url}",
                "--window-size=1520,960",
                "--window-position=80,60",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,MediaRouter",
                "--disable-background-networking",
                "--disable-sync",
                "--hide-crash-restore-bubble",
                "--disable-breakpad",
            ]
            try:
                return subprocess.Popen(args, close_fds=True)
            except OSError:
                pass
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------- #
# 单实例
# --------------------------------------------------------------------------- #

def load_session() -> dict | None:
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port, token = data.get("port"), data.get("token")
    if not isinstance(port, int) or not isinstance(token, str):
        return None
    url = f"http://127.0.0.1:{port}/api/ping?k={token}"
    try:
        with urllib.request.urlopen(url, timeout=0.8) as response:
            if response.status == 200:
                data["url"] = f"http://127.0.0.1:{port}/?k={token}"
                return data
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None


def write_session(port: int, token: str) -> None:
    try:
        SESSION_FILE.write_text(
            json.dumps({"port": port, "token": token, "pid": os.getpid(), "version": __version__}),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_session() -> None:
    try:
        SESSION_FILE.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="OI 对拍器", description="信息学竞赛本地对拍与评测工具")
    parser.add_argument("--port", type=int, default=0, help="监听端口（0 表示自动选择）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不打开界面")
    parser.add_argument("--browser", action="store_true", help="使用默认浏览器标签页而不是应用窗口")
    parser.add_argument("--keep-alive", action="store_true", help="界面关闭后仍保持后台服务")
    parser.add_argument("--new", action="store_true", help="强制启动新实例")
    parser.add_argument("--open", default="", help="启动后立即打开的题目文件夹")
    parser.add_argument("--check", action="store_true", help="仅自检后退出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()

    if args.check:
        print(f"{APP_NAME} {__version__}")
        print(f"python: {sys.executable} {sys.version.split()[0]}")
        print(f"data:   {data_dir()}")
        print(f"ui:     {ROOT / 'web'}")
        return 0

    existing = None if args.new else load_session()
    if existing:
        print(f"已有实例在运行：{existing['url']}", flush=True)
        if not args.no_browser:
            launch_ui(existing["url"], not args.browser)
        return 0

    store = Store()
    session = Session(store)
    token = secrets.token_urlsafe(18)
    httpd, ctx = create_server(session, token, port=args.port)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?k={token}"
    write_session(port, token)

    if args.open:
        try:
            session.open_folder(args.open)
        except Exception as exc:  # noqa: BLE001
            print(f"打开文件夹失败：{exc}", file=sys.stderr)

    browser_holder: dict = {"proc": None}
    stopping = threading.Event()

    def shutdown(reason: str = "") -> None:
        if stopping.is_set():
            return
        stopping.set()
        try:
            session.shutdown()
        except Exception:  # noqa: BLE001
            pass
        proc = browser_holder.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        clear_session()
        try:
            httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass

    ctx.shutdown_cb = shutdown

    def watchdog() -> None:
        if args.keep_alive:
            return
        while not stopping.is_set():
            time.sleep(3)
            idle = time.time() - ctx.last_ping
            if idle > IDLE_TIMEOUT and not session.judging.get("running"):
                print(f"界面已关闭 {idle:.0f}s，自动退出。")
                shutdown("idle")
                return

    if not args.no_browser:
        def open_later() -> None:
            time.sleep(0.25)
            browser_holder["proc"] = launch_ui(url, not args.browser)
        threading.Thread(target=open_later, name="launcher", daemon=True).start()

    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()

    print(f"{APP_NAME} v{__version__} 服务地址：{url}", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        shutdown("keyboard")
    finally:
        stopping.set()
        clear_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
