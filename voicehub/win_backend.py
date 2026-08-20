"""Windows 平台后端：全局热键 + 剪贴板监听（WM_CLIPBOARDUPDATE）+ 托盘 + 自启。

本模块仅 Windows 下运行（main.py 在 sys.platform == "win32" 时才 import）。
所有 Windows 依赖（keyboard / pystray / PIL / ctypes）都在函数内延迟 import，
保证非 Windows 平台 import 本模块不崩溃。

职责：
- 全局热键：keyboard.add_hotkey 注册 Alt+N，回调编排层 select_target。
- 剪贴板监听：ctypes 建隐藏窗口 + AddClipboardFormatListener，收到
  WM_CLIPBOARDUPDATE 回调编排层 on_clipboard_change（ADR-4 事件驱动）。
- 托盘：pystray 图标，菜单提供"打开仪表盘 / 退出"。
- 自启：注册表 Run 键（HKCU）。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002


# ---------- 全局热键 ----------
class HotkeyBackend:
    """用 keyboard 库注册全局热键，绑定到编排层。"""

    def __init__(self) -> None:
        self._handles: list = []

    def register_all(self, components) -> None:
        import keyboard  # 仅 Windows

        for key, target in components.config.targets.items():
            combo = f"{components.config.trigger_key}+{target.hotkey}"
            handle = keyboard.add_hotkey(combo, lambda k=key: components.orchestrator.select_target(k))
            self._handles.append(handle)
            logger.info("已注册热键: %s -> %s", combo, target.name)

    def unregister_all(self) -> None:
        import keyboard  # 仅 Windows

        for handle in self._handles:
            keyboard.remove_hotkey(handle)
        self._handles.clear()


# ---------- 剪贴板监听（ctypes） ----------
class ClipboardListener:
    """WM_CLIPBOARDUPDATE 监听：隐藏窗口 + AddClipboardFormatListener。

    关键约束：Win32 窗口有「线程亲和性」——窗口在哪条线程创建，其消息就投递到
    哪条线程的队列。因此「注册类 + 建窗口 + AddClipboardFormatListener +
    GetMessageW 消息循环」必须全部放在同一条线程里；否则 PostMessage 的
    WM_CLIPBOARDUPDATE 会投递到建窗线程的队列，而消息循环线程收不到。
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._running = False
        self._hwnd: Optional[int] = None
        self._user32 = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="clipboard-listener", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._user32 = user32
        LRESULT = wintypes.LPARAM  # LRESULT == LONG_PTR == LPARAM（wintypes 无 LRESULT）

        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HANDLE),
            ]

        # 显式声明函数签名，避免 64 位句柄被 ctypes 默认按 32 位 int 截断
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.AddClipboardFormatListener.restype = wintypes.BOOL
        user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.GetMessageW.restype = ctypes.c_long
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                self._callback()
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc_ref = WNDPROC(_wnd_proc)  # 局部引用，消息循环期间保持存活
        class_name = "VoiceHubClipboardListener"

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = wnd_proc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            logger.error("注册剪贴板监听窗口类失败（错误码 %s）", ctypes.get_last_error())
            self._running = False
            return

        hwnd = user32.CreateWindowExW(
            0, class_name, class_name, 0, 0, 0, 0, 0,
            None, None, wc.hInstance, None)
        if not hwnd:
            logger.error("创建剪贴板监听窗口失败（错误码 %s）", ctypes.get_last_error())
            self._running = False
            return
        self._hwnd = hwnd
        if not user32.AddClipboardFormatListener(hwnd):
            logger.error("AddClipboardFormatListener 失败（错误码 %s）", ctypes.get_last_error())
            self._running = False
            return
        logger.info("剪贴板监听已启动（WM_CLIPBOARDUPDATE）")

        # 消息循环（与建窗口同线程）
        msg = wintypes.MSG()
        while self._running:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.DestroyWindow(hwnd)

    def stop(self) -> None:
        self._running = False
        if self._hwnd and self._user32:
            # 跨线程 PostMessage 唤醒阻塞中的 GetMessageW，让其返回后退出循环
            self._user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)


# ---------- 托盘 ----------
def _make_tray_icon():
    from PIL import Image, ImageDraw  # 仅 Windows

    img = Image.new("RGB", (64, 64), (37, 99, 235))
    d = ImageDraw.Draw(img)
    d.ellipse((16, 16, 48, 48), fill=(255, 255, 255))
    return img


def _open_dashboard(config) -> None:
    webbrowser.open(f"http://{config.server_host}:{config.server_port}")


def run_tray(components, stop_event: threading.Event) -> None:
    """启动托盘（非阻塞）：后台线程跑图标，「退出」菜单触发 stop_event + stop。"""
    import pystray  # 仅 Windows

    from . import app_window

    config = components.config

    def _exit(icon, item):
        stop_event.set()
        icon.stop()

    def _open_main(icon, item):
        """打开主窗口：原生窗口优先，未启用/已销毁则回退系统浏览器。"""
        if not app_window.show_main_window():
            _open_dashboard(config)

    def _toggle_autostart(icon, item):
        """托盘开关开机自启：按当前注册状态取反（M6-⑤）。"""
        if is_autostart_enabled():
            remove_autostart()
        else:
            install_autostart()

    menu = pystray.Menu(
        pystray.MenuItem("打开主窗口", _open_main),
        pystray.MenuItem("开机自启", _toggle_autostart,
                         checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem("退出", _exit),
    )
    icon = pystray.Icon("voicehub", _make_tray_icon(), "VoiceHub", menu)
    icon.run_detached()
    logger.info("托盘已启动")


# ---------- 自启 ----------
_AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE_NAME = "VoiceHub"


def _autostart_command() -> str:
    """构造自启命令：打包运行直接启 exe，源码运行用 -m voicehub.main。

    frozen 下带 -m 会导致 exe argparse 报错退出（exe 不认识该参数），必须区分。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m voicehub.main'


def is_autostart_enabled() -> bool:
    """查询 HKCU Run 键里是否已注册 VoiceHub 自启。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_RUN_KEY) as key:
            winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
            return True
    except OSError:
        return False


def install_autostart() -> bool:
    """写入 HKCU Run 键实现开机自启。"""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_RUN_KEY,
                        0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ,
                          _autostart_command())
    logger.info("开机自启已注册: %s", _autostart_command())
    return True


def remove_autostart() -> bool:
    """删除 HKCU Run 键里的自启注册（未注册时静默成功）。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
    except FileNotFoundError:
        pass
    logger.info("开机自启已移除")
    return True


def start_windows_backend(components) -> None:
    """Windows 后端总入口：热键 + 剪贴板监听 + 托盘 + 原生主窗口（主线程）。"""
    from . import app_window

    hotkeys = HotkeyBackend()
    hotkeys.register_all(components)

    listener = ClipboardListener(components.orchestrator.on_clipboard_change)
    listener.start()

    stop_event = threading.Event()
    try:
        run_tray(components, stop_event)
        # 主线程优先跑原生窗口（M6-④）：阻塞至托盘退出；关窗仅隐藏不退程序。
        # pywebview 不可用/启动失败时回退原等待循环（托盘/热键照常）。
        url = f"http://{components.config.server_host}:{components.config.server_port}"
        if not app_window.wait_for_port(components.config.server_host,
                                        components.config.server_port, timeout=5.0):
            logger.warning("仪表盘端口 %.0f 就绪超时，窗口可能白屏",
                           components.config.server_port)
        if not app_window.start_app_window(url, stop_event):
            while not stop_event.is_set():
                time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出")
    finally:
        listener.stop()
        hotkeys.unregister_all()
        components.discovery.stop()
        components.storage.close()
        _exit_forcefully_if_threads_linger()


def _exit_forcefully_if_threads_linger() -> None:
    """清理后若仍有非 daemon 线程驻留（pywebview/pythonnet/pystray 会留），
    进程无法自然退出 → 记录线程名后强制结束（资源已在调用方关闭）。

    2026-08-20 白屏事故伴生问题：托盘退出后进程变僵尸驻留，占着下一次启动的端口。
    """
    linger = [t.name for t in threading.enumerate()
              if t is not threading.main_thread() and not t.daemon]
    logger.info("VoiceHub 已退出")
    if linger:
        logger.info("残留非 daemon 线程 %s，强制结束进程", linger)
        logging.shutdown()
        os._exit(0)
