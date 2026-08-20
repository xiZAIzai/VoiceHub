"""平板接收端单测：Termux 粘贴后端、HTTP /paste 全链路。"""
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from voicehub.tablet_server import TabletReceiver


class _FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def test_termux_set_text(monkeypatch):
    """set_text 调用 termux-clipboard-set 写入。"""
    calls: list = []

    def fake_run(cmd, input=b"", check=False, timeout=None):
        calls.append((cmd, input))
        return _FakeProc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    import voicehub.tablet_server as ts
    backend = ts._termux_backend()
    assert backend[0]("你好") is True
    assert calls[0][0] == ["termux-clipboard-set"]
    assert calls[0][1] == "你好".encode("utf-8")


def test_termux_paste_with_root(monkeypatch):
    """有 root 时粘贴成功。"""
    calls: list = []

    def fake_run(cmd, check=False, timeout=None):
        calls.append(cmd)
        return _FakeProc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    import voicehub.tablet_server as ts
    _, paste = ts._termux_backend()
    assert paste() is True
    assert calls[0] == ["su", "-c", "input keyevent 279"]


def test_termux_paste_without_root(monkeypatch):
    """无 root 时提示手动粘贴并返回失败。"""
    calls: list = []

    def fake_run(cmd, check=False, timeout=None):
        calls.append(cmd)
        if cmd[0] == "su":
            return _FakeProc(1)
        return _FakeProc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    import voicehub.tablet_server as ts
    _, paste = ts._termux_backend()
    assert paste() is False
    assert ["su", "-c", "input keyevent 279"] in calls
    assert any(c[0] == "termux-toast" for c in calls)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_paste_roundtrip():
    """启动 TabletReceiver（假后端），POST /paste 全链路可用。"""
    port = _free_port()
    calls: dict = {}

    recv = TabletReceiver("tablet", host="127.0.0.1", port=port, heartbeat_interval_sec=60)
    recv._set_text = lambda t: (calls.setdefault("set", t), True)[1]
    recv._paste = lambda: (calls.__setitem__("paste", True), True)[1]
    recv.start()
    try:
        # 等 HTTP 服务就绪
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.05)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/paste",
            data=json.dumps({"text": "平板测试"}).encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["device"] == "tablet"
        assert body["length"] == 4
        assert calls["set"] == "平板测试"
        assert calls["paste"] is True
    finally:
        recv.stop()
