"""平板接收端（Android/Termux）：纯标准库实现，避免 Termux 装不了 wheel 的问题。

Termux 的 pip 装不了 manylinux 轮子（bionic libc），FastAPI/uvicorn 需从源码编译、
代价高，所以这里复用 Receiver 的粘滞/心跳逻辑，但把 HTTP 换成 stdlib 的
http.server 提供 POST /paste。

粘贴后端（ADR-5 平板侧）：
- 写剪贴板：termux-clipboard-set（Termux:API）。
- 粘贴到当前焦点：有 root 时 su -c "input keyevent 279"（KEYCODE_PASTE）注入；
  无 root 时退回"复制 + 提示手动长按粘贴"。
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import DISCOVERY_PORT, RECEIVER_PORT
from .receiver import Receiver

logger = logging.getLogger(__name__)


def _termux_backend():
    """返回 (set_text, paste) 两个可调用，供 Receiver 注入。"""

    def set_text(text: str) -> bool:
        p = subprocess.run(["termux-clipboard-set"], input=text.encode("utf-8"), check=False)
        return p.returncode == 0

    def paste() -> bool:
        # 有 root：注入 KEYCODE_PASTE(279) 到当前焦点
        try:
            p = subprocess.run(["su", "-c", "input keyevent 279"], check=False, timeout=5)
            if p.returncode == 0:
                return True
        except Exception:  # noqa: BLE001 - su 不可用也走手动粘贴
            pass
        subprocess.run(["termux-toast", "-s", "已复制，请长按粘贴"], check=False)
        return False

    return set_text, paste


class TabletReceiver(Receiver):
    """Termux 平板接收端：继承 Receiver，HTTP 换成纯标准库实现。"""

    def __init__(
        self,
        name: str,
        host: str = "0.0.0.0",
        port: int = RECEIVER_PORT,
        discovery_port: int = DISCOVERY_PORT,
        heartbeat_interval_sec: float = 4.0,
    ) -> None:
        set_text, paste = _termux_backend()
        super().__init__(name, host, port, discovery_port, heartbeat_interval_sec,
                         set_text, paste)

    def _make_handler(self):
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/health":
                    self._reply(200, {"ok": True, "device": owner.name})
                else:
                    self._reply(404, {"ok": False, "error": "not found"})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply(400, {"ok": False, "error": "bad json"})
                    return
                self._reply(200, owner.handle_paste(payload))

            def _reply(self, code: int, obj: dict) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args) -> None:
                logger.debug("http: %s", fmt % args)

        return _Handler

    def run_http(self) -> None:
        server = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        server.serve_forever()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="VoiceHub 平板接收端（Termux，纯标准库）")
    parser.add_argument("--name", default="tablet", help="报到名称，需与 daemon config 的 target key 一致")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=RECEIVER_PORT)
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT)
    parser.add_argument("--interval", type=float, default=4.0, help="心跳广播间隔（秒）")
    args = parser.parse_args(argv)
    recv = TabletReceiver(args.name, args.host, args.port, args.discovery_port, args.interval)
    logger.info("平板接收端启动: %s @ %s:%s", args.name, args.host, args.port)
    recv.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        recv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
