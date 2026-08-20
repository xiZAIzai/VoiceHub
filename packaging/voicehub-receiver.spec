# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：接收端（笔记本）→ dist/VoiceHubReceiver/VoiceHubReceiver.exe。

构建（项目根目录执行 packaging/build_receiver.bat，或手动）：
  .venv/Scripts/pyinstaller packaging/voicehub-receiver.spec --noconfirm --distpath dist --workpath build

说明：
- console=True：接收端保留控制台窗口显示心跳/收文日志（关窗即停，笔记本端可见性好）。
- 默认参数 --name laptop；如 daemon 端 target key 不同，需建快捷方式加 --name <key>。
- conda DLL 收编原因见 voicehub-daemon.spec 注释（ffi.dll 供 keyboard/ctypes）。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)  # 项目根目录（packaging/ 的上一级）

# conda 系 Python 的依赖 DLL 在 Library\bin 而非 DLLs\，PyInstaller hook 找不到
_extra_binaries = []
for _dll in ("ffi.dll", "libffi-8.dll", "sqlite3.dll"):
    _p = os.path.join(sys.base_prefix, "Library", "bin", _dll)
    if os.path.exists(_p):
        _extra_binaries.append((_p, "."))

a = Analysis(
    [os.path.join(SPECPATH, "entry_receiver.py")],
    pathex=[ROOT],  # 让 voicehub 包在分析期可导入
    binaries=_extra_binaries,
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
    console=True,  # 接收端保留控制台：日志可见，关窗即停
    icon=os.path.join(ROOT, "assets", "voicehub.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="VoiceHubReceiver",  # 产物目录：dist/VoiceHubReceiver/
)
