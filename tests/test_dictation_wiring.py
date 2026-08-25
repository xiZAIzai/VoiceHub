"""V4/M11 集成层单测：config 深合并 / route_direct / deliver_local / 托盘与热键接线。"""

import json

from voicehub.config import Config, TargetConfig, deep_merge
from voicehub.orchestrator import Orchestrator
from voicehub.router import Router
from voicehub.state import StickyTarget
from voicehub.clipboard_monitor import ClipboardMonitor


# ---------- config：transcription 段 / local 深合并 / 环境变量 ----------

def test_transcription_defaults():
    tc = Config().transcription
    assert tc.engine == "shandianshuo"
    assert tc.api_key == ""
    assert tc.base_url.endswith("bigmodel_nostream")
    assert tc.resource_id == "volc.seedasr.sauc.duration"
    assert tc.trigger_key == "ctrl+alt+v"


def test_transcription_from_dict_with_bad_types_keeps_defaults():
    from voicehub.config import TranscriptionConfig

    tc = TranscriptionConfig.from_dict({
        "engine": "builtin", "sample_rate": "not-a-number",
        "vad_threshold": "bad", "max_duration_sec": 30,
    })
    assert tc.engine == "builtin"
    assert tc.sample_rate == 16000  # 坏值回退默认
    assert tc.max_duration_sec == 30.0


def test_config_local_json_deep_merges(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICEHUB_ASR_API_KEY", raising=False)
    seed = tmp_path / "config.json"
    seed.write_text(json.dumps({
        "transcription": {"engine": "shandianshuo", "vad_silence_ms": 1500},
    }), encoding="utf-8")
    local = tmp_path / "config.local.json"
    local.write_text(json.dumps({
        "transcription": {"engine": "builtin", "api_key": "Vx-local-key"},
    }), encoding="utf-8")
    cfg = Config.load(seed)
    assert cfg.transcription.engine == "builtin"  # local 覆盖
    assert cfg.transcription.vad_silence_ms == 1500  # 种子键保留（深合并非整体替换）
    assert cfg.transcription.api_key == "Vx-local-key"


def test_config_env_key_wins_over_local(tmp_path, monkeypatch):
    seed = tmp_path / "config.json"
    seed.write_text("{}", encoding="utf-8")
    (tmp_path / "config.local.json").write_text(
        json.dumps({"transcription": {"api_key": "local"}}), encoding="utf-8")
    monkeypatch.setenv("VOICEHUB_ASR_API_KEY", "env-key")
    assert Config.load(seed).transcription.api_key == "env-key"


def test_config_local_broken_json_falls_back_to_seed(tmp_path):
    seed = tmp_path / "config.json"
    seed.write_text(json.dumps({"server": {"port": 9000}}), encoding="utf-8")
    (tmp_path / "config.local.json").write_text("{broken", encoding="utf-8")
    cfg = Config.load(seed)
    assert cfg.server_port == 9000


def test_deep_merge_nested():
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}, "d": 4}) == \
        {"a": {"b": 9, "c": 2}, "d": 4}


# ---------- router：deliver_local 直通投递 ----------

class _Storage:
    def __init__(self):
        self.logs = []

    def insert(self, log):
        self.logs.append(log)


def _router(tmp_path=None, clipboard_write=None, storage=None):
    cfg = Config()
    cfg.targets = {
        "desktop": TargetConfig(key="desktop", name="台式机", hotkey="1", type="local"),
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
    }
    return Router(cfg, storage=storage, clipboard_write=clipboard_write)


def test_deliver_local_writes_clipboard():
    written = []

    def _write(text):
        written.append(text)
        return True

    r = _router(clipboard_write=_write)
    result = r.route("直通文本", "desktop", deliver_local=True)
    assert result["ok"] is True
    assert written == ["直通文本"]


def test_deliver_local_writer_failure_reported():
    def _bad(text):
        return False

    r = _router(clipboard_write=_bad)
    result = r.route("文本", "desktop", deliver_local=True)
    assert result["ok"] is False
    assert result["error"] == "clipboard write failed"


def test_deliver_local_without_writer_fails_cleanly():
    r = _router(clipboard_write=None)
    result = r.route("文本", "desktop", deliver_local=True)
    assert result["ok"] is False
    assert result["error"] == "no clipboard writer"


def test_local_without_deliver_local_keeps_adr5_behavior():
    """闪电说链路（默认）：local 只记录不写剪贴板。"""
    written = []

    r = _router(clipboard_write=lambda t: written.append(t) or True)
    result = r.route("文本", "desktop")  # deliver_local 缺省 False
    assert result["ok"] is True
    assert written == []


def test_deliver_local_metadata_persisted():
    storage = _Storage()
    r = _router(clipboard_write=lambda t: True, storage=storage)
    r.route("文本", "desktop", deliver_local=True,
            metadata={"source": "builtin", "record_ms": 123})
    assert storage.logs[0].metadata == {"source": "builtin", "record_ms": 123}


# ---------- orchestrator.route_direct ----------

def _orch(storage=None, clipboard_write=None):
    cfg = Config()
    cfg.targets = {
        "desktop": TargetConfig(key="desktop", name="台式机", hotkey="1", type="local"),
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
    }
    sticky = StickyTarget(pending_timeout_sec=30.0)
    monitor = ClipboardMonitor(read_text=lambda: None, on_text=lambda t: None,
                               stability_ms=100, pending_timeout_sec=30.0)
    router = Router(cfg, storage=storage, clipboard_write=clipboard_write)
    return Orchestrator(cfg, sticky, monitor, router), sticky


def test_route_direct_defaults_to_first_local_target():
    written = []
    orch, _ = _orch(clipboard_write=lambda t: written.append(t) or True)
    result = orch.route_direct("自建引擎文本")
    assert result["ok"] is True and result["target"] == "desktop"
    assert written == ["自建引擎文本"]


def test_route_direct_consumes_sticky_target_first():
    written = []
    orch, sticky = _orch(clipboard_write=lambda t: written.append(t) or True)
    sticky.select("laptop")  # 用户先按了 Alt+2：粘滞目标优先
    result = orch.route_direct("去笔记本")
    assert result["target"] == "laptop"
    assert sticky.is_armed() is False
    # 笔记本是 network_http：不写本地剪贴板（无 transport 注入则失败，但已离开 local 分支）
    assert written == []


def test_route_direct_respects_configured_default_target():
    written = []
    orch, _ = _orch(clipboard_write=lambda t: written.append(t) or True)
    orch._config.transcription.default_target = "laptop"  # noqa: SLF001 - 测试注入
    result = orch.route_direct("去笔记本")
    assert result["target"] == "laptop"


def test_route_direct_no_targets_returns_error():
    cfg = Config()
    sticky = StickyTarget()
    monitor = ClipboardMonitor(read_text=lambda: None, on_text=lambda t: None,
                               stability_ms=100, pending_timeout_sec=30.0)
    orch = Orchestrator(cfg, sticky, monitor, Router(cfg))
    result = orch.route_direct("无处可去")
    assert result["ok"] is False and result["error"] == "no target"


def test_route_direct_metadata_marks_builtin(tmp_path):
    storage = _Storage()
    written = []
    orch, _ = _orch(storage=storage, clipboard_write=lambda t: written.append(t) or True)
    orch.route_direct("文本", metadata={"record_ms": 500})
    meta = storage.logs[0].metadata
    assert meta["source"] == "builtin" and meta["record_ms"] == 500


# ---------- 托盘菜单 / 热键组合 ----------

def test_menu_model_dictate_item_optional():
    from voicehub.linux_tray import MENU_ID_DICTATE, MenuModel

    fired = []
    plain = MenuModel(on_open=lambda: None, on_quit=lambda: None)
    assert plain.item(MENU_ID_DICTATE) is None  # 未注入不出现，向后兼容

    with_dictate = MenuModel(on_open=lambda: None, on_quit=lambda: None,
                             on_dictate=lambda: fired.append(1))
    assert with_dictate.item(MENU_ID_DICTATE)["label"] == "开始听写"
    with_dictate.activate(MENU_ID_DICTATE)
    assert fired == [1]
    assert with_dictate.set_label(MENU_ID_DICTATE, "停止听写") is True
    assert with_dictate.item(MENU_ID_DICTATE)["label"] == "停止听写"
    assert with_dictate.set_label(999, "x") is False


def test_tray_set_dictation_state_flips_label():
    from voicehub.linux_tray import MENU_ID_DICTATE, LinuxTray

    tray = LinuxTray(on_open=lambda: None, on_quit=lambda: None,
                     on_dictate=lambda: None)
    tray.set_dictation_state(True)
    assert tray._menu.item(MENU_ID_DICTATE)["label"] == "停止听写"  # noqa: SLF001
    tray.set_dictation_state(False)
    assert tray._menu.item(MENU_ID_DICTATE)["label"] == "开始听写"  # noqa: SLF001


def test_to_pynput_combo():
    from voicehub.linux_backend import to_pynput_combo

    assert to_pynput_combo("ctrl+alt+v") == "<ctrl>+<alt>+v"
    assert to_pynput_combo("Ctrl + Alt + V") == "<ctrl>+<alt>+v"
    assert to_pynput_combo("f9") == "f9"
    assert to_pynput_combo("shift+f2") == "<shift>+f2"
    assert to_pynput_combo("") == ""
    assert to_pynput_combo("+++") == ""


# ---------- main.build_dictation 组装 ----------

def test_build_dictation_requires_builtin_engine_and_key():
    from voicehub.main import build_dictation

    cfg = Config()
    assert build_dictation(cfg, None) is None  # engine=shandianshuo 默认

    cfg.transcription.engine = "builtin"
    assert build_dictation(cfg, None) is None  # 无 key 降级（None，不抛）


def test_build_dictation_assembles_engine(tmp_path, monkeypatch):
    from voicehub.main import build_dictation

    monkeypatch.delenv("VOICEHUB_ASR_API_KEY", raising=False)
    cfg = Config()
    cfg.transcription.engine = "builtin"
    cfg.transcription.api_key = "test-key"
    engine = build_dictation(cfg, None)
    assert engine is not None
    assert engine.state() == "idle"
