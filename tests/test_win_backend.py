"""Windows 后端纯逻辑单测（M6-⑤）：自启命令构造（源码 / frozen 两种模式）。

注册表读写属系统副作用，走真机脚本验证（见 TASKS.md M6-⑤ 记录），不进单测。
"""
import sys
from pathlib import Path

from voicehub.win_backend import _autostart_command


def test_autostart_command_source_mode():
    """源码运行：python.exe -m voicehub.main（保持 v1 命令形态）。"""
    cmd = _autostart_command()
    assert cmd.endswith('-m voicehub.main')
    assert cmd.startswith('"')
    assert sys.executable in cmd


def test_autostart_command_frozen_mode(tmp_path, monkeypatch):
    """打包运行：直接启动 exe，不带 -m 参数（exe 不识别会 argparse 失败）。"""
    exe = tmp_path / "VoiceHub.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    cmd = _autostart_command()
    assert cmd == f'"{Path(exe)}"'
    assert "-m" not in cmd
