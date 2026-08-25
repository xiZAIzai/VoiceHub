"""配置模块单测：默认值、config.json 加载、热键映射。"""
import json

from voicehub.config import Config, load


def test_default_config():
    """无 config.json 时字段取默认值。"""
    cfg = Config()
    assert cfg.server_host == "127.0.0.1"
    assert cfg.server_port == 8765  # 默认避开 8000（openKylin kytensor/Triton 占用）
    assert cfg.pending_timeout_sec == 30.0
    assert cfg.stability_ms == 600
    assert cfg.targets == {}


def test_load_config(tmp_path):
    """加载自定义 config.json，字段正确映射。"""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "server": {"host": "0.0.0.0", "port": 9000},
        "voicehub": {"pending_timeout_sec": 20, "stability_ms": 500},
        "targets": {
            "laptop": {"name": "笔记本", "hotkey": "2", "type": "network_http",
                       "endpoint": "http://192.168.1.5:5050/paste"}
        }
    }), encoding="utf-8")
    cfg = Config.load(cfg_file)
    assert cfg.server_host == "0.0.0.0"
    assert cfg.server_port == 9000
    assert cfg.pending_timeout_sec == 20.0
    assert cfg.stability_ms == 500
    assert "laptop" in cfg.targets
    laptop = cfg.targets["laptop"]
    assert laptop.name == "笔记本"
    assert laptop.hotkey == "2"
    assert laptop.type == "network_http"
    assert laptop.endpoint == "http://192.168.1.5:5050/paste"
    assert cfg.target_by_hotkey("2").key == "laptop"


def test_target_by_hotkey_missing():
    assert Config().target_by_hotkey("9") is None


def test_load_helper(tmp_path):
    """load() 便捷入口与 Config.load 等价。"""
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text("{}", encoding="utf-8")
    assert load(cfg_file).server_port == 8765
