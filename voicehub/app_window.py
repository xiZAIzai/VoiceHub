"""pywebview 原生窗口（M6-④，ADR-3 预留路径 / ADR-6 路线③）。

- 启动时以原生窗口打开仪表盘（替代浏览器标签页，「软件感」）。
- 「关窗不退程序」：普通关窗 = 隐藏并否决销毁；程序退出统一走托盘菜单
  （request_quit 解除否决后 destroy，让 webview.start() 返回）。
- pywebview 缺失 / 启动异常时返回 False 回退：调用方保持原等待循环，
  托盘「打开主窗口」也回退系统浏览器，功能不受影响。
- webview 的 import 放在函数内：非 Windows / 未装 pywebview 时模块仍可导入。
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """等待 HTTP 端口可连（dashboard 就绪）。超时返回 False，不抛异常。

    用途：窗口加载 URL 前先等服务器起来，避免「先白屏再恢复」的观感。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class _WebViewWindow(Protocol):
    """webview 窗口的最小接口（供类型提示与替身测试）。"""

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def destroy(self) -> None: ...


class WindowController:
    """原生窗口控制器：纯状态逻辑，show/否决/退出转发，便于单测。"""

    def __init__(self) -> None:
        self._window: Optional[_WebViewWindow] = None
        self._exiting = False

    def attach(self, window: _WebViewWindow) -> None:
        self._window = window
        self._exiting = False

    def detach(self) -> None:
        self._window = None

    def show(self) -> bool:
        """显示窗口；无窗口（未启动/已销毁）返回 False 供托盘回退浏览器。"""
        if self._window is None:
            return False
        self._window.show()
        return True

    def should_veto_close(self) -> bool:
        """closing 事件回调的判定：普通关窗否决（隐藏由 handler 延迟执行）；退出放行。

        注意不能在这里直接 hide：WinForms 的 FormClosing 里 Cancel=True 会把
        同事件内的 Hide() 一起吞掉（窗口留在原地关不掉），必须延迟到事件之后。
        """
        return not self._exiting

    def request_quit(self) -> None:
        """托盘「退出」调用：解除否决并销毁窗口，webview.start() 随之返回。"""
        self._exiting = True
        if self._window is not None:
            self._window.destroy()


# 模块级单例：托盘菜单与主线程启动共用同一控制器
CONTROLLER = WindowController()


def show_main_window() -> bool:
    """托盘菜单「打开主窗口」入口，失败由调用方回退浏览器。"""
    return CONTROLLER.show()


def make_closing_handler(controller: WindowController, window: "_WebViewWindow"):
    """构造 closing 事件处理器。

    pywebview 6.x 的实际语义（读 event.py 源码定案，与文档直觉相反）：
    `Event.set()` 收集各 handler 返回值，**只有 handler 返回 False 才会取消窗口关闭**；
    返回 True = 放行销毁。（2026-08-20 事故：返回 True 导致点 X 窗口被销毁、
    start() 返回、整个 daemon 跟着退出。）

    另：FormClosing 事件内 Cancel 后同时 Hide() 会被吞掉（窗口留在原地），故隐藏
    用 50ms 定时器延迟到事件结束后执行。
    """

    def _on_closing() -> bool:
        veto = controller.should_veto_close()  # True = 要保住窗口（隐藏驻留）
        if veto:
            t = threading.Timer(0.05, window.hide)
            t.daemon = True
            t.start()
            return False  # pywebview 语义：False = 取消本次关闭
        return True       # 退出流程：放行销毁，让 start() 返回走统一清理

    return _on_closing


def start_app_window(url: str, stop_event: threading.Event,
                     controller: Optional[WindowController] = None) -> bool:
    """以原生窗口打开仪表盘（阻塞调用线程直至窗口销毁）。

    返回 False 表示 pywebview 不可用或窗口启动失败，调用方回退原有等待模式。
    """
    controller = controller if controller is not None else CONTROLLER
    try:
        import webview
    except Exception:  # noqa: BLE001 - 未安装或运行时缺失均回退
        logger.info("未安装 pywebview，跳过原生窗口（回退浏览器/托盘模式）")
        return False

    window = webview.create_window("VoiceHub", url, width=1120, height=780)
    controller.attach(window)
    window.events.closing += make_closing_handler(controller, window)

    def _watch_stop() -> None:
        """托盘退出（stop_event）→ 销毁窗口，让 webview.start() 返回。"""
        stop_event.wait()
        try:
            controller.request_quit()
        except Exception:  # noqa: BLE001 - 窗口尚未完成启动时 destroy 会抛异常
            logger.exception("销毁原生窗口失败（可能尚未启动完成）")

    threading.Thread(target=_watch_stop, name="app-window-stop", daemon=True).start()
    logger.info("原生窗口启动: %s", url)
    try:
        webview.start()  # 阻塞直至窗口 destroy
    except Exception:  # noqa: BLE001 - WebView2 运行时缺失等
        logger.exception("原生窗口启动失败，回退托盘模式")
        controller.detach()
        return False
    controller.detach()
    logger.info("原生窗口已退出")
    return True
