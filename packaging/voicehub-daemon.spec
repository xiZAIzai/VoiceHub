# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：台式机 daemon → dist/VoiceHub/VoiceHub.exe（one-dir）。

构建（项目根目录执行 packaging/build_daemon.bat，或手动）：
  .venv/Scripts/pyinstaller packaging/voicehub-daemon.spec --noconfirm --distpath dist --workpath build

说明：
- one-dir 而非 one-file：托盘常驻程序启动快、杀软误报率低，分发时整目录打 zip。
- console=False：托盘程序无控制台，日志由 main._setup_logging 落 exe 目录 logs/。
- config.json 不打进 exe（用户可编辑），由构建脚本拷贝到 dist/VoiceHub/ 同级。
- 路径统一基于 SPECPATH（spec 所在目录），与执行 pyinstaller 时的 CWD 无关。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)  # 项目根目录（packaging/ 的上一级）

# conda 系 Python 的部分依赖 DLL 在 Library\bin 而非 DLLs\，PyInstaller hook 找不到，
# 需手动收编，否则打包后 import 报 "DLL load failed"：
# - sqlite3.dll：_sqlite3.pyd 依赖（storage）
# - ffi.dll：conda 自编译的 _ctypes.pyd 依赖（keyboard 热键 / click / ctypes 全挂）；
#   python.org 版叫 libffi-8.dll，两者都探测，存在哪个收哪个
_extra_binaries = []
for _dll in ("sqlite3.dll", "ffi.dll", "libffi-8.dll"):
    _p = os.path.join(sys.base_prefix, "Library", "bin", _dll)
    if os.path.exists(_p):
        _extra_binaries.append((_p, "."))

a = Analysis(
    [os.path.join(SPECPATH, "entry_daemon.py")],
    pathex=[ROOT],  # 让 voicehub 包在分析期可导入
    binaries=_extra_binaries,
    hiddenimports=[
        # pystray 按平台动态导入托盘后端，静态分析不可见
        "pystray._win32",
        # uvicorn.run 动态导入 loop/protocol/lifespan 实现，全部收编最稳
        *collect_submodules("uvicorn"),
        # pywebview 按平台动态导入 GUI 后端；pythonnet 的 clr 是 .NET 绑定入口
        *collect_submodules("webview"),
        "clr",
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
    console=False,
    icon=os.path.join(ROOT, "assets", "voicehub.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="VoiceHub",  # 产物目录：dist/VoiceHub/
)
