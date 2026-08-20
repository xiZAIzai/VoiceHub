"""仪表盘单测：collect_state 聚合、API 端点、历史查询。"""
from fastapi.testclient import TestClient

from voicehub.config import Config, TargetConfig
from voicehub.discovery import DeviceInfo
from voicehub.hotkey import HotkeyRegistry
from voicehub.state import StickyTarget
from voicehub.storage import Storage
from voicehub.web import Dashboard, collect_state


class _FakeDiscovery:
    def __init__(self, devices: list) -> None:
        self._devices = devices

    def online_devices(self):
        return self._devices


def test_collect_state_aggregation():
    cfg = Config()
    cfg.targets = {
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
    }
    sticky = StickyTarget(pending_timeout_sec=30.0)
    sticky.select("laptop")
    disc = _FakeDiscovery([DeviceInfo(name="laptop", ip="10.0.0.5", port=5050,
                                      last_seen=0, source="heartbeat")])
    hk = HotkeyRegistry()
    hk.register("laptop", "2", lambda: None)
    state = collect_state(cfg, sticky, disc, hk)
    assert state["sticky"]["armed"] is True
    assert state["sticky"]["target_key"] == "laptop"
    assert state["targets"][0]["online"] is True
    assert state["targets"][0]["device"]["ip"] == "10.0.0.5"
    assert state["hotkeys"] == {"laptop": "alt+2"}


def test_local_target_always_online():
    """local 类型目标不参与网络发现，恒为在线（本机）。"""
    cfg = Config()
    cfg.targets = {
        "desktop": TargetConfig(key="desktop", name="台式机", hotkey="1", type="local"),
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
    }
    disc = _FakeDiscovery([])  # 无任何设备广播心跳
    state = collect_state(cfg, None, disc, None)
    by_key = {t["key"]: t for t in state["targets"]}
    assert by_key["desktop"]["online"] is True
    assert by_key["desktop"]["device"]["source"] == "local"
    assert by_key["laptop"]["online"] is False


def test_dashboard_state_endpoint():
    cfg = Config()
    disc = _FakeDiscovery([])
    app = Dashboard(cfg, discovery=disc).build_app()
    client = TestClient(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["sticky"]["armed"] is False


def test_dashboard_logs_endpoint(tmp_path):
    from voicehub.storage import TranscriptLog

    cfg = Config()
    storage = Storage(tmp_path / "v.db")
    storage.insert(TranscriptLog(processed_text="第一条", target_device="laptop"))
    app = Dashboard(cfg, storage=storage).build_app()
    client = TestClient(app)
    resp = client.get("/api/logs?limit=10")
    assert resp.status_code == 200
    assert resp.json()[0]["processed_text"] == "第一条"
    storage.close()


def test_dashboard_index_serves_html():
    app = Dashboard(Config()).build_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "VoiceHub" in resp.text
