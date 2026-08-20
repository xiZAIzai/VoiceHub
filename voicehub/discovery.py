"""设备发现模块（ADR-2）：UDP 心跳报到 + 在线设备表 + 子网扫描兜底。

- 接收端（laptop/tablet）每 3~5s 向子网广播 UDP 报到包，daemon 监听并维护在线设备表。
- 路由时查在线表取当前 IP；目标有手动 endpoint 时优先用手动值（固定网络覆盖）。
- 兜底：周期性扫描当前 /24 子网的 receiver_port，发现未广播的设备（记为 source='scan'）。
- 时钟可注入（now），便于单测超时离线逻辑。
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import time
from typing import Callable, Optional

from .config import DISCOVERY_PORT, HEARTBEAT_SVC, RECEIVER_PORT

logger = logging.getLogger(__name__)


def own_ip() -> str:
    """取本机当前局域网 IP（通过 UDP connect 探测路由，不真正发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@dataclass
class DeviceInfo:
    """在线设备条目。"""

    name: str
    ip: str
    port: int
    last_seen: float
    source: str  # 'heartbeat' | 'scan'

    @property
    def endpoint(self) -> str:
        return f"http://{self.ip}:{self.port}/paste"


class Discovery:
    """设备发现服务：UDP 监听 + 注册表 + 子网扫描兜底。"""

    def __init__(
        self,
        receiver_port: int = RECEIVER_PORT,
        offline_timeout_sec: float = 12.0,
        scan_interval_sec: float = 20.0,
        discovery_port: int = DISCOVERY_PORT,
        now: Callable[[], float] = time,
    ) -> None:
        self._receiver_port = receiver_port
        self._offline_timeout = offline_timeout_sec
        self._scan_interval = scan_interval_sec
        self._discovery_port = discovery_port
        self._now = now
        self._lock = threading.Lock()
        self._devices: dict[str, DeviceInfo] = {}
        self._stop = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        self._scan_thread: Optional[threading.Thread] = None

    # ---------- 注册表 ----------
    def _register(self, info: DeviceInfo) -> None:
        with self._lock:
            self._devices[info.name] = info

    def get(self, name: str) -> Optional[DeviceInfo]:
        """按名字取在线设备；已过离线超时的视为不存在。"""
        cutoff = self._now() - self._offline_timeout
        with self._lock:
            d = self._devices.get(name)
            if d and d.last_seen >= cutoff:
                return d
        return None

    def online_devices(self) -> list[DeviceInfo]:
        cutoff = self._now() - self._offline_timeout
        with self._lock:
            return [d for d in self._devices.values() if d.last_seen >= cutoff]

    def resolve_endpoint(self, target) -> Optional[str]:
        """返回目标当前可用的 /paste 端点：手动 endpoint > 发现的设备 > None。"""
        if target.endpoint:
            return target.endpoint
        dev = self.get(target.key)
        if dev:
            return dev.endpoint
        return None

    # ---------- UDP 心跳监听 ----------
    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self._discovery_port))
        except OSError as e:
            logger.error("设备发现 UDP 监听失败（端口 %s）: %s", self._discovery_port, e)
            return
        sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self._handle_packet(data, addr)
        sock.close()

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if msg.get("svc") != HEARTBEAT_SVC or not msg.get("name"):
            return
        self._register(DeviceInfo(
            name=str(msg["name"]),
            ip=str(addr[0]),
            port=int(msg.get("port", self._receiver_port)),
            last_seen=self._now(),
            source="heartbeat",
        ))
        logger.debug("设备报到: %s @ %s:%s", msg["name"], addr[0], msg.get("port"))

    # ---------- 子网扫描兜底 ----------
    def scan_once(self) -> int:
        """扫一遍 /24 子网的 receiver_port，返回发现的设备数（本机回环时跳过）。"""
        ip = own_ip()
        if ip.startswith("127."):
            return 0
        base = ip.rsplit(".", 1)[0]
        found: list[str] = []

        def probe(i: int) -> None:
            target = f"{base}.{i}"
            try:
                with socket.create_connection((target, self._receiver_port), timeout=0.25):
                    found.append(target)
            except OSError:
                pass

        with ThreadPoolExecutor(max_workers=32) as ex:
            list(ex.map(probe, range(1, 255)))
        for ip_addr in found:
            self._register(DeviceInfo(
                name=ip_addr, ip=ip_addr, port=self._receiver_port,
                last_seen=self._now(), source="scan",
            ))
        if found:
            logger.info("子网扫描发现 %d 台设备: %s", len(found), found)
        return len(found)

    def _scan_loop(self) -> None:
        while not self._stop.wait(self._scan_interval):
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001 - 扫描失败不影响主流程
                logger.exception("子网扫描异常")

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._stop.clear()
        self._listener_thread = threading.Thread(target=self._listen_loop, name="discovery-udp", daemon=True)
        self._listener_thread.start()
        self._scan_thread = threading.Thread(target=self._scan_loop, name="discovery-scan", daemon=True)
        self._scan_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._listener_thread:
            self._listener_thread.join(timeout=2)
