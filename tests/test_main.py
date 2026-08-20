"""主入口组装单测：build_components 组件齐全、热键绑定、编排联动。"""
import json
import logging
import sys
from pathlib import Path

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


def test_build_components_resolves_relative_db_next_to_config(tmp_path):
    """相对 db_path 解析到 config 所在目录（M6 打包：exe 模式下 CWD 不可靠）。"""
    cfg_path = _write_config(tmp_path)
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["storage"]["db_path"] = "voice_memory.db"  # 相对路径
    cfg_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    c = build_components(str(cfg_path))
    assert Path(c.storage.db_path) == tmp_path / "voice_memory.db"
    c.storage.close()


def test_setup_logging_gives_frozen_noconsole_streams(tmp_path, monkeypatch):
    """回归（白屏事故）：windowed exe 双击启动时 stdout/stderr 为 None，
    uvicorn 日志配置调 sys.stdout.isatty() 直接崩 → dashboard 静默死亡。
    _setup_logging 必须先给 None 流兜底。"""
    import io

    from voicehub.main import _setup_logging

    saved_handlers = logging.root.handlers[:]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "VoiceHub.exe"))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        _setup_logging()
        assert sys.stdout is not None and sys.stderr is not None
        # 兜底流可写且 isatty/fileno 可调用不炸（uvicorn ColourizedFormatter 的调用路径；
        # 注意 Windows 的 nul 设备 isatty() 返回 True，属正常字符设备行为）
        sys.stdout.write("")
        sys.stdout.isatty()
        sys.stdout.fileno()
    finally:
        logging.root.handlers[:] = saved_handlers
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


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
