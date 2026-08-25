# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec（Linux）：daemon → dist/VoiceHub/（one-dir，AppImage 素材）。

构建：bash packaging/build_linux.sh（含 AppImage 组装，不建议单独跑本 spec）。
说明：
- Linux 托盘：SNI 直写（linux_tray，jeepney）；console=True 沿用：从终端启动可见日志，
  frozen 日志另落 VOICEHUB_HOME/logs/（AppRun 注入，见 paths.py / AppImage 只读约束）。
- hiddenimports：uvicorn 动态子模块（沿 Windows 版 422/收编教训）；
  pynput 的 X11 后端为动态导入，一并收编——但注意 pynput>=1.8 起 import 期即连
  X server，headless CI（无 DISPLAY）上 collect_submodules("pynput") 会静默拿到
  空表（仅留 WARNING，构建不失败），v0.3.0 Release 的 AppImage 即因此缺
  pynput.keyboard._xorg 导致热键全灭。故模块名显式写死，不依赖构建期能否 import。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)  # 项目根目录（packaging/ 的上一级）

a = Analysis(
    [os.path.join(SPECPATH, "entry_daemon.py")],
    pathex=[ROOT],  # 让 voicehub 包在分析期可导入
    binaries=[],
    datas=[(os.path.join(ROOT, "assets", "voicehub.png"), "assets")],  # 托盘图标
    hiddenimports=[
        *collect_submodules("uvicorn"),
        # pynput 平台后端（Linux 相关；名字写死的理由见文件头注释）
        "pynput",
        "pynput._util",
        "pynput._util.xorg",
        "pynput._util.xorg_keysyms",
        "pynput._util.uinput",
        "pynput.keyboard",
        "pynput.keyboard._base",
        "pynput.keyboard._xorg",
        "pynput.keyboard._uinput",
        "pynput.keyboard._dummy",
        "pynput.mouse",
        "pynput.mouse._base",
        "pynput.mouse._xorg",
        "pynput.mouse._dummy",
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
