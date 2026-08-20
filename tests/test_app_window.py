"""原生窗口控制器单测（M6-④）：关窗否决 / 退出放行 / 回退逻辑 / 端口就绪等待 / 延迟隐藏。"""
import socket
import sys
import threading
import time

from voicehub.app_window import (
    WindowController, make_closing_handler, start_app_window, wait_for_port,
)


class _FakeWindow:
    """webview 窗口替身：记录 show/hide/destroy 调用。"""

    def __init__(self) -> None:
        self.shown = 0
        self.hidden = 0
        self.destroyed = 0

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1

    def destroy(self) -> None:
        self.destroyed += 1


def test_show_without_window_returns_false():
    """未 attach 窗口时 show 返回 False（托盘菜单据此回退浏览器）。"""
    assert WindowController().show() is False


def test_show_with_window_forwards():
    win = _FakeWindow()
    c = WindowController()
    c.attach(win)
    assert c.show() is True
    assert win.shown == 1


def test_closing_veto_decision():
    """普通关窗：判定为否决（隐藏动作由 handler 延迟执行，见下）。"""
    win = _FakeWindow()
    c = WindowController()
    c.attach(win)
    assert c.should_veto_close() is True
    assert win.hidden == 0  # 判定阶段不隐藏（FormClosing 内 Hide 会被吞）
    assert win.destroyed == 0


def test_closing_handler_defers_hide():
    """回归（点 X 关不掉/误退出）：否决时返回 False（pywebview 语义）+ 延迟隐藏。"""
    win = _FakeWindow()
    c = WindowController()
    c.attach(win)
    handler = make_closing_handler(c, win)
    assert handler() is False  # pywebview：False 才会取消窗口关闭
    assert win.hidden == 0     # 事件内不隐藏
    time.sleep(0.3)            # 等 50ms 定时器触发
    assert win.hidden == 1     # 事件结束后隐藏
    assert win.destroyed == 0  # 窗口未销毁（程序驻留）


def test_closing_handler_exit_allows_close():
    """退出流程：返回 True 放行销毁（pywebview 语义），且不触发隐藏。"""
    win = _FakeWindow()
    c = WindowController()
    c.attach(win)
    c.request_quit()
    handler = make_closing_handler(c, win)
    assert handler() is True  # pywebview：True = 放行关闭
    time.sleep(0.2)
    assert win.hidden == 0
    assert win.destroyed == 1  # request_quit 已转发 destroy


def test_quit_after_request_passes_through():
    """托盘退出：request_quit 后关窗放行（否决标志解除 + destroy 转发）。"""
    win = _FakeWindow()
    c = WindowController()
    c.attach(win)
    c.request_quit()
    assert c.should_veto_close() is False
    assert win.destroyed == 1


def test_start_app_window_falls_back_when_no_pywebview(monkeypatch, tmp_path):
    """未安装 pywebview：返回 False，调用方回退原有等待/浏览器模式。"""
    monkeypatch.setitem(sys.modules, "webview", None)  # import webview → ImportError
    import threading

    assert start_app_window("http://127.0.0.1:8000",
                            threading.Event()) is False


def test_wait_for_port_ready():
    """端口已监听：立即返回 True（窗口等待服务器就绪的场景）。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert wait_for_port("127.0.0.1", port, timeout=2.0) is True
    finally:
        srv.close()


def test_wait_for_port_timeout():
    """无人监听的端口：超时后返回 False（不让窗口无限等）。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))  # 只占端口不 listen，保证连接被拒/超时
    port = srv.getsockname()[1]
    srv.close()
    # 关闭后端口空闲：本地连接被立即拒绝，wait 快速走完重试直到超时
    assert wait_for_port("127.0.0.1", port, timeout=1.0) is False
