"""设备发现单测：心跳注册、超时离线、端点解析、扫描回环跳过。"""
import json
import socket
import threading
import time

from voicehub.config import TargetConfig
from voicehub.discovery import DeviceInfo, Discovery

_TEST_UDP_PORT = 19898


def _send_heartbeat(name: str, port: int, target_port: int) -> None:
    """向发现端口发送一条报到包（重试以规避监听线程未就绪）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    payload = json.dumps({"svc": "voicehub", "name": name, "port": port}).encode()
    for _ in range(20):
        s.sendto(payload, ("127.0.0.1", target_port))
        time.sleep(0.05)
    s.close()


def _wait_for(pred, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class _FakeClock:
    """可手动拨快的时钟，用于测试离线超时。"""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_heartbeat_registration():
    """收到 UDP 报到包后登记设备并可解析端点。"""
    disc = Discovery(discovery_port=_TEST_UDP_PORT)
    disc.start()
    try:
        _send_heartbeat("laptop", 5050, _TEST_UDP_PORT)
        assert _wait_for(lambda: disc.get("laptop") is not None), "未在超时内收到报到"
        dev = disc.get("laptop")
        assert dev is not None
        assert dev.ip == "127.0.0.1"
        assert dev.port == 5050
        assert dev.source == "heartbeat"
        assert dev.endpoint == "http://127.0.0.1:5050/paste"
    finally:
        disc.stop()


def test_offline_expiry():
    """超过离线超时后设备视为不存在。"""
    clock = _FakeClock()
    disc = Discovery(offline_timeout_sec=10.0, now=clock)
    disc._register(DeviceInfo(name="laptop", ip="1.2.3.4", port=5050, last_seen=clock.t, source="heartbeat"))
    assert disc.get("laptop") is not None
    clock.t += 11.0
    assert disc.get("laptop") is None
    assert disc.online_devices() == []


def test_resolve_endpoint():
    """端点解析：手动 endpoint 优先，其次发现结果。"""
    clock = _FakeClock()
    disc = Discovery(now=clock)
    laptop = TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http")
    assert disc.resolve_endpoint(laptop) is None
    disc._register(DeviceInfo(name="laptop", ip="10.0.0.5", port=5050, last_seen=clock.t, source="heartbeat"))
    assert disc.resolve_endpoint(laptop) == "http://10.0.0.5:5050/paste"
    fixed = TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http",
                         endpoint="http://10.0.0.9:5050/paste")
    assert disc.resolve_endpoint(fixed) == "http://10.0.0.9:5050/paste"


def test_scan_skipped_on_loopback(monkeypatch):
    """本机为回环地址时跳过扫描。"""
    disc = Discovery()
    monkeypatch.setattr("voicehub.discovery.own_ip", lambda: "127.0.0.1")
    assert disc.scan_once() == 0


def test_ignore_garbage_packet():
    """垃圾 UDP 包被忽略，不影响注册表。"""
    disc = Discovery()
    disc._handle_packet(b"not-json", ("1.2.3.4", 5050))
    disc._handle_packet(json.dumps({"foo": "bar"}).encode(), ("1.2.3.4", 5050))
    assert disc.online_devices() == []


def test_broadcast_socket_thread_safe_registry():
    """并发 register 不丢数据。"""
    clock = _FakeClock()
    disc = Discovery(now=clock)
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            for j in range(100):
                disc._register(DeviceInfo(name=f"dev{i}", ip=f"1.2.3.{i}", port=5050,
                                          last_seen=clock.t, source="heartbeat"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(disc.online_devices()) == 8
