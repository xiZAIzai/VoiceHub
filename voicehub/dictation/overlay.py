"""V4/M11 听写录音悬浮框（对齐闪电说的波形小矩形）。

用户需求（2026-08-26）：触发听写后要有常驻可见的动态波形框（非一次性弹窗），
录音中实时跳动、再触发一次消失。实现：tkinter（标准库，AppImage 收编成本最低）
无边框置顶小窗，画布画 RMS 柱状波形；音频回调线程经线程安全队列喂电平，
tk 主循环 30ms 刷新。Wayland 会话下走 XWayland 显示（置顶尽力而为）。

降级：无 DISPLAY / tk 不可用时 show()/hide() 只走日志，不阻塞引擎。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_WIDTH, _HEIGHT, _BARS = 280, 84, 40


def bar_height(level: float, peak: float, max_px: int = _HEIGHT - 36) -> int:
    """波形条高度（自适应增益）：电平相对滚动峰值归一化，说话再小声也满幅跳动。

    2026-08-26 用户实测「波形不动」根因：麦克风电平绝对值很低（说话 RMS ~0.02），
    线性画法只有 3px 底线，看起来一排死条；改为除以滚动峰值后小电平也能撑满。
    """
    if peak <= 0:
        peak = 0.02
    norm = min(1.0, level / peak)
    return max(3, int(norm * max_px))


def place_x(cursor_x: int, screen_w: int, win_w: int = _WIDTH) -> int:
    """窗口横坐标：跟随鼠标所在区域居中并夹在屏幕内（纯函数可单测）。

    多显示器（X 合并屏）下「屏幕中心」会落在两屏接缝（2026-08-26 用户实测
    「卡在两个屏幕之间」），改为围绕当前光标位置居中。
    """
    x = cursor_x - win_w // 2
    return max(0, min(x, screen_w - win_w))


class WaveformOverlay:
    """录音波形悬浮框：show() 显示 / update_level() 喂电平 / hide() 关闭。"""

    def __init__(self, title: str = "正在听写…") -> None:
        self._title = title
        self._levels: "queue.Queue[float]" = queue.Queue(maxsize=64)
        self._thread: Optional[threading.Thread] = None
        self._root = None
        self._canvas = None
        self._bars: list[float] = [0.0] * _BARS
        self._peak = 0.02  # 滚动峰值（AGC）：静音下限 0.02，随说话音量自适应
        self._shown = threading.Event()

    # ---------- 对外 API（引擎/后端线程调用） ----------
    def show(self) -> None:
        if self._shown.is_set():
            return
        try:
            import tkinter  # noqa: F401 - 探测可用性
        except Exception as e:  # noqa: BLE001
            logger.debug("tkinter 不可用，悬浮框降级关闭: %s", e)
            return
        self._shown.set()
        self._thread = threading.Thread(target=self._run, name="dictation-overlay", daemon=True)
        self._thread.start()

    def hide(self) -> None:
        self._shown.clear()
        root, self._root = self._root, None
        if root is not None:
            try:
                root.quit()  # 唤醒 mainloop；窗口销毁在 tk 线程做
            except Exception:  # noqa: BLE001
                pass

    def update_level(self, rms: float) -> None:
        """音频回调线程喂电平（0.0~1.0，超出会截断）；队列满直接丢（保实时）。

        同时维护滚动峰值（每块衰减 0.5%，~22%/s）：停止说话约 3 秒后峰值
        回落到地板，波形自然趴下；开口即重新撑满（中途换气 1 秒只缩 ~22%，
        不影响观感）。
        """
        rms = max(0.0, min(1.0, rms))
        self._peak = max(self._peak * 0.995, rms, 0.02)
        try:
            self._levels.put_nowait(rms)
        except queue.Full:
            pass

    # ---------- tk 线程主体 ----------
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:  # noqa: BLE001
            return
        try:
            root = tk.Tk()
        except Exception as e:  # noqa: BLE001 - 无 DISPLAY 属降级路径
            logger.debug("无显示环境，悬浮框未显示: %s", e)
            self._shown.clear()
            return
        self._root = root
        root.overrideredirect(True)  # 无边框
        try:
            root.attributes("-topmost", True)  # X11 置顶（Wayland 尽力而为）
        except tk.TclError:
            pass
        # 跟随鼠标所在区域定位（多屏接缝修复），底部偏上 120px
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        try:
            cursor_x = root.winfo_pointerx()
        except Exception:  # noqa: BLE001 - 无指针环境回退屏幕中心
            cursor_x = sw // 2
        x, y = place_x(cursor_x, sw), sh - _HEIGHT - 120
        root.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")
        self._bind_drag(root)
        root.configure(bg="#1e1e2e")
        canvas = tk.Canvas(root, width=_WIDTH, height=_HEIGHT, bg="#1e1e2e",
                           highlightthickness=0)
        canvas.pack()
        canvas.create_text(_WIDTH // 2, 16, text=self._title, fill="#cdd6f4",
                           font=("Noto Sans CJK SC", 11, "bold"))
        self._canvas = canvas
        self._draw()
        root.after(30, self._tick)
        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._canvas = None
            logger.debug("悬浮框已关闭")

    def _bind_drag(self, root) -> None:
        """无边框窗口手动拖动（按住任意处移动）。"""
        state = {"dx": 0, "dy": 0}

        def press(e):
            state["dx"], state["dy"] = e.x, e.y

        def drag(e):
            x = root.winfo_x() + e.x - state["dx"]
            y = root.winfo_y() + e.y - state["dy"]
            root.geometry(f"+{x}+{y}")

        root.bind("<Button-1>", press)
        root.bind("<B1-Motion>", drag)

    def _tick(self) -> None:
        root, canvas = self._root, self._canvas
        if root is None or canvas is None:
            return
        drained = False
        while True:
            try:
                level = self._levels.get_nowait()
            except queue.Empty:
                break
            self._bars.append(level)
            del self._bars[0]
            drained = True
        if not drained:
            self._bars.append(self._bars[-1] * 0.7)  # 静默衰减，波形自然回落
            del self._bars[0]
        if not self._shown.is_set():
            return  # mainloop 由 hide() 的 quit 退出，这里不再续帧
        self._draw()
        root.after(30, self._tick)

    def _draw(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.delete("wave")
        bw = _WIDTH // _BARS
        for i, level in enumerate(self._bars):
            h = bar_height(level, self._peak)
            x0 = i * bw + 2
            norm = level / max(self._peak, 0.02)
            color = "#f38ba8" if norm > 0.6 else "#89b4fa"  # 近峰值粉红、低电平蓝
            canvas.create_rectangle(x0, _HEIGHT - 10 - h, x0 + bw - 3, _HEIGHT - 10,
                                    fill=color, outline="", tags="wave")
