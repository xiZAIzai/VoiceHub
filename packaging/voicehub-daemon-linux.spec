# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec（Linux）：daemon → dist/VoiceHub/（one-dir，AppImage 素材）。

构建：bash packaging/build_linux.sh（含 AppImage 组装，不建议单独跑本 spec）。
说明：
- Linux 无托盘/原生窗口（M8 降级决策），console=True：从终端启动可见日志，
  frozen 日志另落 VOICEHUB_HOME/logs/（AppRun 注入，见 paths.py / AppImage 只读约束）。
- hiddenimports：uvicorn 动态子模块（沿 Windows 版 422/收编教训）；
  pynput 的 X11 后端为动态导入，一并收编。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)  # 项目根目录（packaging/ 的上一级）

a = Analysis(
    [os.path.join(SPECPATH, "entry_daemon.py")],
    pathex=[ROOT],  # 让 voicehub 包在分析期可导入
    binaries=[],
    hiddenimports=[
        *collect_submodules("uvicorn"),
        *collect_submodules("pynput"),
    ],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceHub",
    debug=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="VoiceHub",  # 产物目录：dist/VoiceHub/
)
