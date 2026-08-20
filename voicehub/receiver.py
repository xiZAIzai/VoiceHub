"""接收端（笔记本/平板通用）：HTTP 收文 + 粘贴到当前焦点 + UDP 心跳广播。

- HTTP: POST /paste {"text": "..."} → 写入剪贴板并模拟 Ctrl+V 粘贴到当前焦点窗口。
- UDP:  每 heartbeat_interval_sec 向子网广播 {svc:"voicehub", name, port} 报到，
        daemon 据此自动发现本设备（ADR-2）。有限广播 + 子网定向广播双发。
- 粘贴后端按平台注入：Windows=pywin32+keyboard，macOS=pbcopy+osascript，Linux=xclip+xdotool。
  测试时可注入假后端；Termux 平板见 tablet 分支（ADR-5 需要 root 注入）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from typing import Callable, Optional

from .config import DISCOVERY_PORT, HEARTBEAT_SVC, RECEIVER_PORT
from .discovery import own_ip

# fastapi 必须在模块级导入：本文件有 from __future__ import annotations，
# 路由函数的 `req: Request` 注解是字符串，FastAPI 经 get_type_hints 在模块全局
# 解析；若在 make_app 内延迟导入，解析不到会被当成 query 参数，POST /paste 恒 422。
# Termux 平板复用本模块的心跳函数但不装 fastapi，故 try-import 回退为 None。
try:
    from fastapi import FastAPI, Request
except ImportError:  # pragma: no cover - 仅无 fastapi 的平板环境走到
    FastAPI = Request = None

logger = logging.getLogger(__name__)


# ---------- UDP 心跳广播（桌面端与平板端复用） ----------
def heartbeat_packet(name: str, port: int) -> bytes:
    """构造 ADR-2 协议报到报文。"""
    return json.dumps({"svc": HEARTBEAT_SVC, "name": name, "port": port},
                      ensure_ascii=False).encode("utf-8")


def broadcast_heartbeat(name: str, port: int, discovery_port: int) -> None:
    """有限广播 + 子网定向广播双发，兼容不同手机热点的网段差异。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    payload = heartbeat_packet(name, port)
    targets = ["255.255.255.255"]
    ip = own_ip()
    if not ip.startswith("127."):
        targets.append(f"{ip.rsplit('.', 1)[0]}.255")
    for target in targets:
        try:
            sock.sendto(payload, (target, discovery_port))
        except OSError:
            logger.debug("心跳广播失败: %s", target)
    sock.close()


# ---------- 平台粘贴后端：返回 (set_text, paste) 两个可调用 ----------
def _win_backend() -> tuple[Callable[[str], bool], Callable[[], bool]]:
    import win32clipboard  # 仅 Windows
    import keyboard

    def set_text(text: str) -> bool:
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("写剪贴板失败")
            return False

    def paste() -> bool:
        try:
            keyboard.press_and_release("ctrl+v")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("模拟 Ctrl+V 失败")
            return False

    return set_text, paste


def _mac_backend() -> tuple[Callable[[str], bool], Callable[[], bool]]:
    import subprocess

    def set_text(text: str) -> bool:
        p = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        return p.returncode == 0

    def paste() -> bool:
        script = 'tell application "System Events" to keystroke "v" using command down'
        p = subprocess.run(["osascript", "-e", script], check=False)
        return p.returncode == 0

    return set_text, paste


def _linux_backend() -> tuple[Callable[[str], bool], Callable[[], bool]]:
    """X11 桌面（xclip/xsel 写剪贴板，xdotool 触发粘贴）。"""
    import shutil
    import subprocess

    def set_text(text: str) -> bool:
        if shutil.which("xclip"):
            p = subprocess.run(["xclip", "-selection", "clipboard"],
                               input=text.encode("utf-8"), check=False)
            return p.returncode == 0
        if shutil.which("xsel"):
            p = subprocess.run(["xsel", "--clipboard", "--input"],
                               input=text.encode("utf-8"), check=False)
            return p.returncode == 0
        return False

    def paste() -> bool:
        if not shutil.which("xdotool"):
            return False
        p = subprocess.run(["xdotool", "key", "ctrl+v"], check=False)
        return p.returncode == 0

    return set_text, paste


def default_backend() -> tuple[Callable[[str], bool], Callable[[], bool]]:
    """按当前平台选择后端；Termux 平板单独分支。"""
    if os.environ.get("TERMUX_VERSION"):
        raise NotImplementedError("平板粘贴需 Termux + root 注入，见 tablet_server.py")
    if os.name == "nt":
        return _win_backend()
    if sys.platform == "darwin":
        return _mac_backend()
    if sys.platform.startswith("linux"):
        return _linux_backend()
    raise RuntimeError(f"不支持的平台: {sys.platform}")


class Receiver:
    """接收端服务：HTTP 收文 + 粘贴 + UDP 心跳广播。"""

    def __init__(
        self,
        name: str,
        host: str = "0.0.0.0",
        port: int = RECEIVER_PORT,
        discovery_port: int = DISCOVERY_PORT,
        heartbeat_interval_sec: float = 4.0,
        set_text: Optional[Callable[[str], bool]] = None,
        paste: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.discovery_port = discovery_port
        self.heartbeat_interval = heartbeat_interval_sec
        self._set_text = set_text
        self._paste = paste
        self._stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._http_thread: Optional[threading.Thread] = None

    # ---------- HTTP 收文 ----------
    def handle_paste(self, payload: dict) -> dict:
        """处理一条 /paste 请求：写剪贴板 + 粘贴，返回结果。"""
        text = payload.get("text", "")
        if not text:
            return {"ok": False, "error": "empty text"}
        if self._set_text is not None and not self._set_text(text):
            return {"ok": False, "error": "clipboard write failed"}
        if self._paste is not None and not self._paste():
            return {"ok": False, "error": "paste failed"}
        return {"ok": True, "length": len(text), "device": self.name}

    def make_app(self):
        """构造 FastAPI 应用（fastapi 缺失说明是平板等纯标准库环境，显式报错）。"""
        if FastAPI is None:
            raise RuntimeError("当前环境未安装 fastapi，无法启动接收端 HTTP 服务")

        app = FastAPI(title=f"VoiceHub receiver {self.name}")

        @app.get("/health")
        def health():
            return {"ok": True, "device": self.name}

        @app.post("/paste")
        async def paste(req: Request):
            return self.handle_paste(await req.json())

        return app

    def run_http(self) -> None:
        import uvicorn

        uvicorn.run(self.make_app(), host=self.host, port=self.port, log_level="warning")

    # ---------- UDP 心跳 ----------
    def _heartbeat_packet(self) -> bytes:
        return heartbeat_packet(self.name, self.port)

    def _broadcast(self) -> None:
        """有限广播 + 子网定向广播双发，兼容不同手机热点的网段差异。"""
        broadcast_heartbeat(self.name, self.port, self.discovery_port)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            try:
                self._broadcast()
            except Exception:  # noqa: BLE001
                logger.exception("心跳广播异常")

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="receiver-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        self._http_thread = threading.Thread(
            target=self.run_http, name="receiver-http", daemon=True)
        self._http_thread.start()

    def stop(self) -> None:
        self._stop.set()


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="VoiceHub 接收端：收文并粘贴到当前焦点")
    parser.add_argument("--name", default="laptop", help="报到名称，需与 daemon config 的 target key 一致")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=RECEIVER_PORT)
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT)
    parser.add_argument("--interval", type=float, default=4.0, help="心跳广播间隔（秒）")
    args = parser.parse_args(argv)
    try:
        set_text, paste = default_backend()
    except Exception as e:  # noqa: BLE001
        logger.error("初始化粘贴后端失败: %s", e)
        return 1
    recv = Receiver(args.name, args.host, args.port, args.discovery_port,
                    args.interval, set_text, paste)
    logger.info("接收端启动: %s @ %s:%s（心跳端口 %s）", args.name, args.host,
                args.port, args.discovery_port)
    recv.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        recv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
