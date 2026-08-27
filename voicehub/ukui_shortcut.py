"""UKUI 系统快捷键一键注册（V4/M11：听写热键的 Wayland 根治通道）。

openKylin（UKUI）自定义快捷键存储（2026-08-26 实机逆向确认）：
- relocatable schema `org.ukui.control-center.keybinding`（键：name / action / binding）
- 路径 `/org/ukui/desktop/keybindings/custom<N>/`（N 从 0 递增取空位）
- binding 为 GTK 加速器格式（如 `<Ctrl><Alt>v`）
通过 gsettings 命令行读写（免 python-gi 依赖）；ukui-settings-daemon 监听
dconf 变化即时生效（无需重启）。

本模块仅 Linux 使用；全部操作 try/except 降级，绝不抛给调用方。
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA = "org.ukui.control-center.keybinding"
DIR_PATH = "/org/ukui/desktop/keybindings"
MAX_SLOTS = 32


def _gsettings(*args: str) -> Optional[str]:
    """执行 gsettings 命令；失败返回 None（只读操作失败属降级路径）。"""
    try:
        p = subprocess.run(["gsettings", *args], capture_output=True,
                           timeout=5, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def _slot_path(n: int) -> str:
    return f"{SCHEMA}:{DIR_PATH}/custom{n}/"


def to_gtk_accel(spec: str) -> str:
    """'Ctrl+Alt+V' → '<Ctrl><Alt>v'（UKUI 控制中心同款大小写惯例）。纯函数可单测。"""
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    out = ""
    mods = {"ctrl", "control", "alt", "shift", "super", "win", "meta"}
    for p in parts:
        low = p.lower()
        if low in mods:
            name = "ctrl" if low == "control" else ("super" if low in ("win", "meta") else low)
            out += f"<{name.capitalize()}>"
        else:
            out += low  # 主键小写（'V'→'v'，'F9'→'f9'）
    return out


DESKTOP_ID = "voicehub-dictate.desktop"


TRIGGER_SCRIPT = "~/.local/share/voicehub/dictate-trigger.sh"


def make_trigger_script(port: int, fallback_cmd: str) -> str:
    """生成毫秒级触发脚本：HTTP 直打运行中实例。

    每次按 Alt+9 完整拉起 AppImage 要挂载镜像+初始化 Python（实测 2~4s），
    用户体感「再按一下不能立马关闭」；curl 直连 <100ms。实例不在时回退
    镜像直启。返回脚本绝对路径；失败抛 OSError 由调用方兜底。
    """
    import os

    path = os.path.expanduser(TRIGGER_SCRIPT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n"
                "# VoiceHub 听写触发器（注册时生成，勿手改）\n"
                "curl -s -m 3 -X POST "
                "http://127.0.0.1:" + str(port) + "/api/dictate/toggle >/dev/null\n"
                "[ $? -eq 0 ] || exec " + fallback_cmd + " --dictate\n")
    os.chmod(path, 0o755)
    return path


def _configured_port():
    """读安装目录 config.json 的仪表盘端口；读不到返回 None。"""
    import json
    import sys
    from pathlib import Path

    if os.environ.get("APPIMAGE"):
        base = Path(os.environ["APPIMAGE"]).parent
    elif getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parents[1]
    try:
        cfg = json.loads((base / "config.json").read_text(encoding="utf-8"))
        port = int(cfg.get("server", {}).get("port", 0))
        return port or None
    except (OSError, ValueError):
        return None


def dictation_command() -> str:
    """听写触发命令（写入 desktop 文件 Exec 行）：优先快速脚本。"""
    import os
    import sys

    appimage = os.environ.get("APPIMAGE")  # AppRun 环境下由启动器注入
    if appimage:
        fallback = '"' + appimage + '"'
    elif getattr(sys, "frozen", False):
        fallback = '"' + sys.executable + '"'
    else:
        fallback = "'" + sys.executable + "' -m voicehub.main"

    port = _configured_port()
    if port is not None:
        try:
            return make_trigger_script(port, fallback)
        except OSError as e:
            logger.warning("触发脚本生成失败，回退镜像直启: %s", e)
    if appimage:
        return '"' + appimage + '" --dictate'
    if getattr(sys, "frozen", False):
        return '"' + sys.executable + '" --dictate'
    return "'" + sys.executable + "' -m voicehub.main --dictate"


def desktop_file_path() -> str:
    """听写 desktop 文件路径（~/.local/share/applications/，GDesktopAppInfo 标准搜索路径）。"""
    import os

    return os.path.expanduser(f"~/.local/share/applications/{DESKTOP_ID}")


def write_desktop_file() -> Optional[str]:
    """生成/刷新听写 desktop 文件；返回路径，失败 None。

    UKUI 自定义快捷键的 action 经 g_desktop_app_info_new_from_filename 解析
    （2026-08-26 用户实测定位：控制中心 UI 是「选择程序」，裸命令串不被识别），
    故必须落到 desktop 文件，Exec 里携带 --dictate 参数。
    """
    import os

    path = desktop_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=VoiceHub 听写\n"
            "Comment=开始/停止语音听写\n"
            f"Exec={dictation_command()}\n"
            "NoDisplay=true\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        logger.warning("desktop 文件写入失败: %s", e)
        return None
    return path


def find_dictate_slot() -> Optional[dict]:
    """找到已注册的听写快捷键槽位；无则 None。返回 {slot, binding, action}。"""
    for n in range(MAX_SLOTS):
        action = _gsettings("get", _slot_path(n), "action")
        if action is None:
            continue
        # 兼容两种 action 形态：desktop 文件路径（新）/ 含 --dictate 的命令串（旧）
        if DESKTOP_ID in action or "--dictate" in action:
            binding = (_gsettings("get", _slot_path(n), "binding") or "''").strip("'")
            return {"slot": n, "binding": binding, "action": action.strip("'")}
    return None


def register(binding: str = "Ctrl+Alt+V", name: str = "VoiceHub 听写") -> dict:
    """注册（或更新）听写快捷键；返回 {ok, slot, binding, action, error?}。"""
    accel = to_gtk_accel(binding)
    if accel.count("<") < 1 or not accel.split(">")[-1]:
        return {"ok": False, "error": f"非法快捷键: {binding}（需修饰键+主键，如 Ctrl+Alt+V）"}
    desktop = write_desktop_file()
    if desktop is None:
        return {"ok": False, "error": "desktop 文件写入失败（权限?）"}
    existing = find_dictate_slot()
    slot = existing["slot"] if existing else _free_slot()
    if slot is None:
        return {"ok": False, "error": "无可用自定义快捷键槽位"}
    results = [
        _gsettings("set", _slot_path(slot), "name", f"'{name}'"),
        _gsettings("set", _slot_path(slot), "action", f"'{desktop}'"),
        _gsettings("set", _slot_path(slot), "binding", f"'{accel}'"),
    ]
    if any(r is None for r in results):
        return {"ok": False, "error": "gsettings 写入失败（非 UKUI 桌面?）"}
    _restart_ukui_daemon()  # custom 清单守护进程启动时枚举（2026-08-26 实测）
    logger.info("UKUI 系统快捷键已注册: %s -> %s (custom%s)", accel, desktop, slot)
    return {"ok": True, "slot": slot, "binding": accel, "action": desktop}


def _restart_ukui_daemon() -> None:
    """重启 ukui-settings-daemon 使新注册的 custom 快捷键被枚举。

    守护进程被会话管理器自动拉起（2026-08-26 实测 ~4s 无感恢复），自定义快捷键
    仅在启动时枚举（运行期 dconf watch 未覆盖 custom 路径新增场景）。
    """
    import subprocess

    try:
        p = subprocess.run(["pgrep", "-x", "ukui-settings-daemon"],
                           capture_output=True, timeout=5, encoding="utf-8")
        pids = [line for line in (p.stdout or "").split() if line]
        for pid in pids:
            subprocess.run(["kill", pid], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("ukui-settings-daemon 重启跳过")


def unregister() -> dict:
    """移除听写快捷键注册。"""
    existing = find_dictate_slot()
    if existing is None:
        return {"ok": True, "slot": None, "note": "未注册"}
    slot = existing["slot"]
    _gsettings("set", _slot_path(slot), "binding", "''")
    _gsettings("set", _slot_path(slot), "action", "''")
    _gsettings("set", _slot_path(slot), "name", "''")
    logger.info("UKUI 系统快捷键已移除 (custom%s)", slot)
    return {"ok": True, "slot": slot}


def _free_slot() -> Optional[int]:
    for n in range(MAX_SLOTS):
        action = _gsettings("get", _slot_path(n), "action")
        binding = _gsettings("get", _slot_path(n), "binding")
        if (action in (None, "''", "", "@as []") and binding in (None, "''", "")):
            # gsettings get 一个从未 set 过的 relocatable 键会失败 → None，即空槽
            return n
    return None
