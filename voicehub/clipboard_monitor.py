"""剪贴板监控（ADR-4）：事件驱动检测 + 基线快照 + 稳定性去抖。

- 基线快照：选定粘滞目标时记录当前剪贴板内容，作为"闪电说尚未写出"的参照。
- 检测：Windows 用 AddClipboardFormatListener + WM_CLIPBOARDUPDATE 消息通知；
        通知只调 notify_change()，真正判定交给 settle_if_ready()。
- 去抖：剪贴板写入可能有多次中间态，stability_ms 内无新变化才判定为最终文本。
- 过期：pending_timeout_sec 内未检测到变化则放弃本次等待，自动解除武装。

纯逻辑核心与平台层分离，读剪贴板 / 时钟均可注入，WSL 下可完整单测状态机。
"""
from __future__ import annotations

import threading
from time import time
from typing import Callable, Optional

logger = __import__("logging").getLogger(__name__)


class ClipboardMonitor:
    """粘滞窗口内的剪贴板变化检测器（一次性消费）。"""

    def __init__(
        self,
        read_text: Callable[[], Optional[str]],
        on_text: Callable[[str], None],
        stability_ms: float = 600.0,
        pending_timeout_sec: float = 30.0,
        now: Callable[[], float] = time,
    ) -> None:
        self._read_text = read_text
        self._on_text = on_text
        self._stability_sec = stability_ms / 1000.0
        self._pending_timeout = pending_timeout_sec
        self._now = now
        self._lock = threading.Lock()
        self._baseline: Optional[str] = None
        self._armed = False
        self._armed_at: Optional[float] = None
        self._last_change_at: Optional[float] = None
        self._settled = False

    # ---------- 生命周期 ----------
    def arm(self) -> None:
        """选定粘滞目标时调用：记录基线，开始监听。"""
        with self._lock:
            self._baseline = self._read_text()
            self._armed = True
            self._armed_at = self._now()
            self._last_change_at = None
            self._settled = False

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def rebase_baseline_if_armed(self, snapshot: Optional[str]) -> None:
        """武装期间把基线重锚到指定快照（V4 防抖守卫）。

        场景：builtin 听写完成会把文本写入系统剪贴板；若此刻闪电说链路
        恰在武装中，旧基线 ≠ 新剪贴板会触发一次假 settle（告警噪声）。
        直通路由完成后调用本方法以本次写入为新基线，settle 判等即静默。
        未武装时是 no-op（幂等，调用方无需判态）。
        """
        with self._lock:
            if self._armed:
                self._baseline = snapshot
                self._last_change_at = None
                self._settled = False

    def snapshot_now(self) -> Optional[str]:
        """读当前剪贴板快照（rebase 配套，避免调用方依赖底层读取实现）。"""
        return self._read_text()

    def apply_params(self, stability_ms: Optional[float] = None,
                     pending_timeout_sec: Optional[float] = None) -> None:
        """设置页热应用入口（M6-③）：只更新传入的参数，None 表示不变。"""
        with self._lock:
            if stability_ms is not None:
                self._stability_sec = stability_ms / 1000.0
            if pending_timeout_sec is not None:
                self._pending_timeout = pending_timeout_sec

    def is_armed(self) -> bool:
        with self._lock:
            return self._armed

    # ---------- 事件 ----------
    def notify_change(self) -> None:
        """平台层检测到剪贴板变化时调用，刷新去抖计时。"""
        with self._lock:
            if self._armed:
                self._last_change_at = self._now()
                self._settled = False

    def settle_if_ready(self) -> Optional[str]:
        """去抖就绪后读取并判定文本；返回命中的文本（已回调），否则 None。"""
        with self._lock:
            if not self._armed or self._settled:
                return None
            now = self._now()
            if now - self._armed_at > self._pending_timeout:
                self._armed = False
                return None
            if self._last_change_at is None:
                return None
            if now - self._last_change_at < self._stability_sec:
                return None
            text = self._read_text()
            self._settled = True
            self._armed = False
        if text is not None and text != self._baseline:
            self._on_text(text)
            return text
        return None


def win32_read_text() -> Optional[str]:
    """Windows 读取剪贴板文本（仅在 Windows 可用）。"""
    import win32clipboard  # 仅 Windows

    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001 - 剪贴板被占用等
        logger.exception("读取剪贴板失败")
        return None
