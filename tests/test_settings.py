"""配置读写服务单测（M6-③）：读 / 校验 / 原子写回 / 热应用 / 重启判定。"""
import json

import pytest

from voicehub.settings import ConfigError, ConfigService


class _FakeMonitor:
    """剪贴板监控替身：实现 apply_params 与真实 ClipboardMonitor 同协议。"""

    def __init__(self) -> None:
        self.stability_ms = 600
        self.pending_timeout_sec = 30.0

    def apply_params(self, stability_ms=None, pending_timeout_sec=None) -> None:
        if stability_ms is not None:
            self.stability_ms = stability_ms
        if pending_timeout_sec is not None:
            self.pending_timeout_sec = pending_timeout_sec


class _FakeSticky:
    """粘滞目标替身：pending_timeout 为公有属性。"""

    def __init__(self) -> None:
        self.pending_timeout = 30.0


def _base_raw() -> dict:
    return {
        "server": {"host": "127.0.0.1", "port": 8000},
        "voicehub": {"stability_ms": 600, "pending_timeout_sec": 30},
        "shandianshuo": {"trigger_key": "alt"},
        "targets": {
            "desktop": {"name": "台式机", "hotkey": "1", "type": "local"},
            "laptop": {"name": "笔记本", "hotkey": "2", "type": "network_http"},
        },
        "storage": {"db_path": "v.db"},
    }


@pytest.fixture()
def env(tmp_path):
    """建好 config.json 与服务（含热应用替身），返回 (path, service, monitor, sticky)。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_base_raw(), ensure_ascii=False), encoding="utf-8")
    monitor, sticky = _FakeMonitor(), _FakeSticky()
    svc = ConfigService(path, monitor=monitor, sticky=sticky)
    return path, svc, monitor, sticky


def test_get_returns_raw_dict(env):
    path, svc, _, _ = env
    assert svc.get() == json.loads(path.read_text(encoding="utf-8"))


def test_update_hot_fields_applied_without_restart(env):
    """仅改热应用字段：写盘 + 组件即时生效 + 不要求重启。"""
    path, svc, monitor, sticky = env
    raw = svc.get()
    raw["voicehub"]["stability_ms"] = 300
    raw["voicehub"]["pending_timeout_sec"] = 300
    result = svc.update(raw)
    assert result["ok"] is True
    assert result["need_restart"] is False
    assert monitor.stability_ms == 300
    assert monitor.pending_timeout_sec == 300
    assert sticky.pending_timeout == 300
    # 写盘生效
    assert json.loads(path.read_text(encoding="utf-8"))["voicehub"]["stability_ms"] == 300


def test_update_targets_requires_restart(env):
    """改目标（热键/名称）需重启生效，但不阻塞写盘。"""
    path, svc, monitor, _ = env
    raw = svc.get()
    raw["targets"]["laptop"]["hotkey"] = "4"
    result = svc.update(raw)
    assert result["ok"] is True
    assert result["need_restart"] is True
    assert any(p.startswith("targets") for p in result["changed"])
    # 热应用字段未变，不应触发组件更新
    assert monitor.stability_ms == 600


def test_update_invalid_value_rejected_and_file_intact(env):
    """类型非法：拒绝并保持原文件不动。"""
    path, svc, _, _ = env
    before = path.read_text(encoding="utf-8")
    raw = svc.get()
    raw["voicehub"]["stability_ms"] = "abc"
    with pytest.raises(ConfigError):
        svc.update(raw)
    assert path.read_text(encoding="utf-8") == before


def test_update_duplicate_hotkeys_rejected(env):
    """两个目标热键相同会导致路由歧义，必须拒绝。"""
    path, svc, _, _ = env
    before = path.read_text(encoding="utf-8")
    raw = svc.get()
    raw["targets"]["laptop"]["hotkey"] = "1"  # 与 desktop 冲突
    with pytest.raises(ConfigError):
        svc.update(raw)
    assert path.read_text(encoding="utf-8") == before


def test_update_atomic_no_tmp_leftover(env):
    """写回走临时文件 + 原子替换，不留 .tmp。"""
    path, svc, _, _ = env
    raw = svc.get()
    raw["voicehub"]["stability_ms"] = 250
    svc.update(raw)
    assert not list(path.parent.glob("*.tmp"))
    assert json.loads(path.read_text(encoding="utf-8"))["voicehub"]["stability_ms"] == 250
