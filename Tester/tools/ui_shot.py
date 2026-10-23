"""开发辅助：用 Edge 无头模式 + CDP 打开界面、执行 JS、截图。

仅在开发/验收时使用，不属于对拍器运行逻辑。

用法：
    python tools/ui_shot.py --url "http://127.0.0.1:18080/?k=..." \
        --eval "document.querySelector('tr[data-id=\"xxx\"]').click()" \
        --shot drawer.png
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


class WebSocket:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败")
            buffer += chunk
        self.buffer = buffer.split(b"\r\n\r\n", 1)[1]

    def _read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("连接已关闭")
            self.buffer += chunk
        out, self.buffer = self.buffer[:size], self.buffer[size:]
        return out

    def send(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        header = bytearray([0x80 | opcode, 0x80 | len(payload)])
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read_exact(8), "big")
            payload = self._read_exact(length)
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0x8:
                raise ConnectionError("服务端关闭连接")
            if opcode in (0x1, 0x0):
                return payload.decode("utf-8", "replace")
            # 其他帧忽略

    def call(self, message_id: int, method: str, params: dict | None = None) -> dict:
        self.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.recv())
            if message.get("id") == message_id:
                return message


def find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("找不到 Microsoft Edge")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_target(port: int, timeout: float = 25.0) -> dict:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as response:
                targets = json.loads(response.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page":
                    return target
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.3)
    raise SystemExit(f"等待浏览器调试端口超时：{last_error}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--shot", default="")
    parser.add_argument("--eval", action="append", default=[])
    parser.add_argument("--eval-file", default="", help="每行一个 JS 表达式（可避免命令行引号问题）")
    parser.add_argument("--wait", type=float, default=1.2)
    parser.add_argument("--size", default="1520,980")
    parser.add_argument("--profile", default="")
    args = parser.parse_args()

    expressions = list(args.eval)
    if args.eval_file:
        for line in Path(args.eval_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                expressions.append(line)

    edge = find_edge()
    port = free_port()
    profile = Path(args.profile) if args.profile else Path(os.environ.get("TEMP", ".")) / f"oitest-shot-{port}"
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            edge,
            "--headless=old",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-crash-reporter",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={args.size}",
            args.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        target = wait_for_target(port)
        ws = WebSocket(target["webSocketDebuggerUrl"])
        message_id = 0

        def evaluate(expression: str) -> dict:
            nonlocal message_id
            message_id += 1
            result = ws.call(message_id, "Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            })
            return result.get("result", {})

        message_id += 1
        ws.call(message_id, "Page.enable")
        message_id += 1
        ws.call(message_id, "Runtime.enable")

        # 等待页面加载及首次状态拉取
        deadline = time.time() + 20
        while time.time() < deadline:
            state = evaluate("document.readyState")
            if state.get("result", {}).get("value") == "complete":
                break
            time.sleep(0.2)
        time.sleep(args.wait)

        for expression in expressions:
            outcome = evaluate(expression)
            print("eval:", json.dumps(outcome, ensure_ascii=False)[:600])
            time.sleep(0.5)

        if args.shot:
            time.sleep(0.6)
            message_id += 1
            response = ws.call(message_id, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            data = response.get("result", {}).get("data")
            if not data:
                print("截图失败:", json.dumps(response, ensure_ascii=False)[:800])
                return 1
            Path(args.shot).write_bytes(base64.b64decode(data))
            print("saved", args.shot)

        message_id += 1
        ws.call(message_id, "Browser.close")
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
