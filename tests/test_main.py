"""主入口组装单测：build_components 组件齐全、热键绑定、编排联动。"""
import json

from voicehub.main import build_components
from voicehub.transport import HttpPusher


def _write_config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "server": {"host": "127.0.0.1", "port": 8000},
        "storage": {"db_path": str(tmp_path / "v.db")},
        "targets": {
            "desktop": {"name": "台式机", "hotkey": "1", "type": "local"},
            "laptop": {"name": "笔记本", "hotkey": "2", "type": "network_http"},
            "tablet": {"name": "平板", "hotkey": "3", "type": "network_http"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    return cfg


def test_build_components_assembles_all(tmp_path):
    cfg_path = _write_config(tmp_path)
    c = build_components(str(cfg_path))
    assert c.config.server_port == 8000
    assert len(c.config.targets) == 3
    assert c.storage is not None
    assert c.discovery is not None
    assert c.sticky is not None
    assert c.monitor is not None
    assert c.router is not None
    # 回归：曾漏注入 transport，导致 daemon 内所有远程路由恒失败（no transport）
    assert isinstance(c.router._transport, HttpPusher)
    assert c.orchestrator is not None
    assert c.dashboard is not None
    c.storage.close()


def test_hotkeys_bound_from_config(tmp_path):
    cfg_path = _write_config(tmp_path)
    c = build_components(str(cfg_path))
    assert c.hotkeys.bindings() == {
        "desktop": "alt+1",
        "laptop": "alt+2",
        "tablet": "alt+3",
    }
    c.storage.close()


def test_orchestrator_select_target(tmp_path):
    """编排层与热键联动：select_target 对合法目标生效。"""
    cfg_path = _write_config(tmp_path)
    c = build_components(str(cfg_path))
    assert c.orchestrator.select_target("laptop") is True
    assert c.sticky.target_key() == "laptop"
    assert c.orchestrator.select_target("ghost") is False
    c.storage.close()
