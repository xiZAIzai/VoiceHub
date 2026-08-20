"""路由编排单测：本地记录、网络推送、离线、未知目标、日志落库。"""
from voicehub.config import Config, TargetConfig
from voicehub.router import Router


def _config() -> Config:
    cfg = Config()
    cfg.targets = {
        "desktop": TargetConfig(key="desktop", name="台式机", hotkey="1", type="local"),
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
        "tablet": TargetConfig(key="tablet", name="平板", hotkey="3", type="network_http"),
    }
    return cfg


class _FakeDiscovery:
    def __init__(self, endpoints: dict) -> None:
        self._endpoints = endpoints

    def resolve_endpoint(self, target):
        return self._endpoints.get(target.key)


class _FakeTransport:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.pushed: list[tuple] = []

    def push(self, endpoint: str, text: str) -> bool:
        self.pushed.append((endpoint, text))
        return self.ok


def test_local_target_records_success():
    """本地目标：只记录成功，不推送。"""
    r = Router(_config(), transport=_FakeTransport())
    res = r.route("你好", "desktop")
    assert res["ok"] is True
    assert res["type"] == "local"


def test_network_push_uses_discovery_endpoint():
    """网络目标：用发现的端点推送。"""
    disc = _FakeDiscovery({"laptop": "http://10.0.0.5:5050/paste"})
    transport = _FakeTransport(ok=True)
    r = Router(_config(), discovery=disc, transport=transport)
    res = r.route("转写文本", "laptop")
    assert res["ok"] is True
    assert res["target"] == "laptop"
    assert transport.pushed == [("http://10.0.0.5:5050/paste", "转写文本")]


def test_network_push_manual_endpoint_priority():
    """手动 endpoint 优先于发现结果。"""
    cfg = _config()
    cfg.targets["laptop"].endpoint = "http://192.168.1.9:5050/paste"
    disc = _FakeDiscovery({"laptop": "http://10.0.0.5:5050/paste"})
    transport = _FakeTransport(ok=True)
    r = Router(cfg, discovery=disc, transport=transport)
    r.route("x", "laptop")
    assert transport.pushed[0][0] == "http://192.168.1.9:5050/paste"


def test_target_offline_fails():
    """无端点（离线）时推送失败。"""
    disc = _FakeDiscovery({})
    r = Router(_config(), discovery=disc, transport=_FakeTransport())
    res = r.route("x", "laptop")
    assert res["ok"] is False
    assert res["error"] == "target offline (no endpoint)"


def test_push_failure_reported():
    """推送被拒时返回失败。"""
    disc = _FakeDiscovery({"laptop": "http://10.0.0.5:5050/paste"})
    r = Router(_config(), discovery=disc, transport=_FakeTransport(ok=False))
    res = r.route("x", "laptop")
    assert res["ok"] is False
    assert res["error"] == "push failed"


def test_unknown_target():
    r = Router(_config())
    res = r.route("x", "nonexistent")
    assert res["ok"] is False
    assert res["error"] == "unknown target"


def test_log_persisted(tmp_path):
    """有 storage 时路由结果落库，字段正确。"""
    from voicehub.storage import Storage

    cfg = _config()
    storage = Storage(tmp_path / "v.db")
    r = Router(cfg, discovery=_FakeDiscovery({"laptop": "http://10.0.0.5:5050/paste"}),
               transport=_FakeTransport(ok=True), storage=storage)
    res = r.route("记录我", "laptop", raw_text="原始", latency_ms=120, metadata={"win": "chrome"})
    assert "log_id" in res
    row = storage.get(res["log_id"])
    assert row["processed_text"] == "记录我"
    assert row["target_device"] == "笔记本"
    assert row["is_routed_successfully"] == 1
    storage.close()
