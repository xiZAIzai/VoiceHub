# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec（Linux）：接收端 → dist/VoiceHubReceiver/（one-dir，AppImage 素材）。

构建：bash packaging/build_linux.sh（含 AppImage 组装）。
说明：
- console=True 与 Windows 版哲学一致：接收端日志可见性好（前台跑，Ctrl+C 停）。
- 接收端不读 config.json（--name 等 CLI 参数），AppImage 无需播种配置。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "entry_receiver.py")],
    pathex=[ROOT],
    binaries=[],
    hiddenimports=[
        # uvicorn.run 动态导入 loop/protocol/lifespan 实现，全部收编最稳
        *collect_submodules("uvicorn"),
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
    name="VoiceHubReceiver",
    debug=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="VoiceHubReceiver",  # 产物目录：dist/VoiceHubReceiver/
)
