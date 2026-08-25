"""路径解析：统一 frozen（PyInstaller 打包）与源码运行的数据文件定位。

为什么需要（M6-① 打包需求）：双击 exe 启动时 CWD 不一定是 exe 所在目录
（开始菜单 / 注册表自启 / 服务方式启动时通常是 C:\\Windows\\System32），
config.json / db / 日志必须锚定 exe 目录，否则会写到系统目录或读不到配置。

约定：
- 源码运行：app_dir() = 当前工作目录（保持 v1 行为不变）。
- 打包运行（sys.frozen）：app_dir() = exe 所在目录，config.json 与 logs/
  都放在 exe 旁边，用户可直接编辑 / 查看日志。
- VOICEHUB_HOME 环境变量（V3/M10 AppImage 需求）：最高优先级显式指定数据目录。
  AppImage 内部挂载点只读，AppRun 启动器用它把数据指到 AppImage 旁边
  （便携式，与 Windows exe 同体验）或 ~/.config/voicehub（旁边不可写时回退）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    """应用运行目录：VOICEHUB_HOME > frozen 时 exe 所在目录 > 当前工作目录。"""
    env = os.environ.get("VOICEHUB_HOME")
    if env:  # AppImage 启动器注入
        return Path(env).resolve()
    if getattr(sys, "frozen", False):  # PyInstaller 打包后置位
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def default_config_path() -> Path:
    """默认 config.json 路径（app_dir 下，随 exe 分发、用户可编辑）。"""
    return app_dir() / "config.json"


def resolve_data_path(path: str | Path, base_dir: Path | None = None) -> Path:
    """解析数据文件路径（如 SQLite db）。

    相对路径拼接到 base_dir（默认 app_dir，调用方通常传 config 所在目录），
    绝对路径原样返回。返回值恒为绝对路径，行为不随 CWD 漂移。
    """
    p = Path(path)
    if p.is_absolute():
        return p
    base = base_dir if base_dir is not None else app_dir()
    return base / p
