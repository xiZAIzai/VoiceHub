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

    config = components.config

    def _exit(icon, item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开仪表盘", lambda icon, item: _open_dashboard(config)),
        pystray.MenuItem("退出", _exit),
    )
    icon = pystray.Icon("voicehub", _make_tray_icon(), "VoiceHub", menu)
    icon.run_detached()
    logger.info("托盘已启动")


# ---------- 自启 ----------
def install_autostart() -> bool:
    """写入 HKCU Run 键实现开机自启。"""
    import sys
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, "VoiceHub", 0, winreg.REG_SZ,
                          f'"{sys.executable}" -m voicehub.main')
    finally:
        winreg.CloseKey(key)
    return True


def start_windows_backend(components) -> None:
    """Windows 后端总入口：热键 + 剪贴板监听 + 托盘（主线程等退出信号）。"""
    hotkeys = HotkeyBackend()
    hotkeys.register_all(components)

    listener = ClipboardListener(components.orchestrator.on_clipboard_change)
    listener.start()

    stop_event = threading.Event()
    try:
        run_tray(components, stop_event)
        # 主线程可中断地等待退出：托盘「退出」设 stop_event，或 Ctrl+C 抛 KeyboardInterrupt
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出")
    finally:
        listener.stop()
        hotkeys.unregister_all()
        components.discovery.stop()
        components.storage.close()
        logger.info("VoiceHub 已退出")
