"""接收端单测：/paste 处理、心跳报文、广播目标、后端选择。"""
import json
import socket

from voicehub.receiver import Receiver


def _fake(ok: bool = True):
    return (lambda t: ok, lambda: ok)


def test_handle_paste_success():
    """写剪贴板 + 粘贴都成功时返回 ok。"""
    calls = {}

    def set_text(t: str) -> bool:
        calls["set"] = t
        return True

    def paste() -> bool:
        calls["paste"] = True
        return True

    r = Receiver("laptop", set_text=set_text, paste=paste)
    res = r.handle_paste({"text": "你好 world"})
    assert res["ok"] is True
    assert res["length"] == 8
    assert res["device"] == "laptop"
    assert calls["set"] == "你好 world"
    assert calls["paste"] is True


def test_handle_paste_empty():
    r = Receiver("laptop", set_text=_fake()[0], paste=_fake()[1])
    assert r.handle_paste({"text": ""})["ok"] is False


def test_paste_endpoint_http_layer():
    """回归测试：POST /paste 必须走 HTTP 层接受 JSON body。

    曾因 from __future__ import annotations + Request 在 make_app 内延迟导入，
    注解解析失败导致 req 被当成 query 参数，POST 恒 422（详见 CLAUDE.md 踩坑 3）。
    """
    from fastapi.testclient import TestClient

    calls = {}

    def set_text(t: str) -> bool:
        calls["set"] = t
        return True

    def paste() -> bool:
        return True

    r = Receiver("laptop", set_text=set_text, paste=paste)
    client = TestClient(r.make_app())
    resp = client.post("/paste", json={"text": "你好 world"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert calls["set"] == "你好 world"

    # /health 也顺带验证
    assert client.get("/health").json()["device"] == "laptop"


def test_handle_paste_clipboard_fail():
    r = Receiver("laptop", set_text=lambda t: False, paste=lambda: True)
    assert r.handle_paste({"text": "x"})["ok"] is False


def test_handle_paste_paste_fail():
    r = Receiver("laptop", set_text=lambda t: True, paste=lambda: False)
    assert r.handle_paste({"text": "x"})["ok"] is False


def test_heartbeat_packet():
    """心跳报文结构符合 ADR-2 协议。"""
    r = Receiver("laptop")
    assert json.loads(r._heartbeat_packet()) == {"svc": "voicehub", "name": "laptop", "port": 5050}


def test_broadcast_dual_targets(monkeypatch):
    """局域网 IP 时同时发有限广播和子网定向广播。"""
    r = Receiver("laptop")
    sent: list[tuple] = []

    class FakeSock:
        def setsockopt(self, *a):
            pass

        def sendto(self, payload, addr):
            sent.append((payload, addr))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    monkeypatch.setattr("voicehub.receiver.own_ip", lambda: "192.168.43.10")
    r._broadcast()
    addrs = [a for _, a in sent]
    assert ("255.255.255.255", 9898) in addrs
    assert ("192.168.43.255", 9898) in addrs
    assert json.loads(sent[0][0])["svc"] == "voicehub"


def test_broadcast_loopback_limited_only(monkeypatch):
    """回环地址时只发有限广播。"""
    r = Receiver("laptop")
    sent: list[tuple] = []

    class FakeSock:
        def setsockopt(self, *a):
            pass

        def sendto(self, payload, addr):
            sent.append(addr)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    monkeypatch.setattr("voicehub.receiver.own_ip", lambda: "127.0.0.1")
    r._broadcast()
    assert len(sent) == 1
    assert sent[0] == ("255.255.255.255", 9898)
