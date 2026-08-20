"""路径解析单测：frozen（PyInstaller 打包）与源码运行两种模式的数据文件定位。

背景（M6-① 打包需求）：双击 exe 启动时 CWD 不一定是 exe 目录（开始菜单/注册表
自启时通常是 System32），config.json / db / logs 必须锚定 exe 所在目录。
"""
import sys
from pathlib import Path

from voicehub.paths import app_dir, default_config_path, resolve_data_path


def test_app_dir_source_mode_is_cwd():
    """源码运行：保持 v1 行为，等于当前工作目录。"""
    assert app_dir() == Path.cwd()


def test_app_dir_frozen_mode_is_exe_dir(tmp_path, monkeypatch):
    """打包运行：锚定 sys.executable（exe）所在目录，与 CWD 无关。"""
    exe = tmp_path / "VoiceHub.exe"
    exe.touch()
    monkeypatch.chdir(tmp_path)  # CWD 与 exe 目录相同无区分度，切到别处
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert app_dir() == tmp_path


def test_default_config_path_under_app_dir(tmp_path, monkeypatch):
    """默认 config.json 路径 = app_dir()/config.json。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "VoiceHub.exe"))
    assert default_config_path() == tmp_path / "config.json"


def test_resolve_data_path_relative_joins_base(tmp_path):
    """相对数据路径（如 db）拼接到 base 目录（config 所在目录）。"""
    assert resolve_data_path("voice_memory.db", base_dir=tmp_path) == tmp_path / "voice_memory.db"


def test_resolve_data_path_absolute_kept(tmp_path):
    """绝对路径原样返回，不拼接。"""
    abs_db = tmp_path / "abs.db"
    assert resolve_data_path(abs_db, base_dir=tmp_path / "other") == abs_db
