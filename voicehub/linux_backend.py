"""Linux（X11）平台后端：剪贴板轮询监听（xclip）+ 全局热键（pynput）。

V3/M8 第一版（ADR-7 占位，按 X11 假设先行开发，WSL 可测）：
- 剪贴板：X11 无系统级变化通知（对比 Windows 的 WM_CLIPBOARDUPDATE），
  用 xclip 周期读取模拟事件：武装期间发现与基线不同的文本 → 转调
  orchestrator.on_clipboard_change，与 Windows 事件语义一致（ADR-4 逻辑复用）。
- 热键：pynput GlobalHotKeys 监听 Alt+N → 编排层 select_target
  （「按下即录」语义依赖闪电说同样吃 Alt 触发键）；
  pynput / DISPLAY 不可用时降级为日志提示，仪表盘照常可用。
- 无托盘/原生窗口：仪表盘走浏览器（PLAN.md M8 降级决策）。

依赖：xclip（系统包，apt install xclip）；pynput 走 requirements 的 linux 标记。
平台依赖均延迟 import，非 Linux 平台 import 本模块不崩溃。
"""
from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 轮询间隔：检测延迟 ≈ 本值 + stability_ms。仅武装期间轮询（空闲零开销），
# M7 定案后如需调优可提升为 config 字段。
POLL_INTERVAL_SEC = 0.3


def xclip_read_text() -> Optional[str]:
    """读 X11 CLIPBOARD 选区文本；空选区/失败返回 None（win32_read_text 的 xclip 等价物）。

    - 显式 encoding="utf-8"：xclip 输出恒为 UTF-8，systemd 等环境无 LANG 时
      text=True 的本地码页解码会把中文读坏。
    - 空选区时 xclip 以非零码退出且无输出，属正常现象按 None 处理。
    """
    try:
        p = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, timeout=2, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("xclip 读取失败（未安装/无 X 会话）")
        return None
    if p.returncode != 0:
        return None
    return p.stdout or None


class X11ClipboardPoller:
    """剪贴板轮询器：把 X11 剪贴板变化翻译成 on_change 事件。

    与 Windows 事件监听的语义对接：
    - 未武装时不读剪贴板（空闲零 subprocess 开销）。
    - 武装沿（False→True）时把当前内容登记为「上次快照」，与
      monitor.arm() 的基线快照同义，避免基线本身被误判为变化
      （否则会立即触发一次假事件，把粘滞目标白白消费掉）。
    - 武装期间内容与上次快照不同 → 调 on_change（对应 WM_CLIPBOARDUPDATE）。
    """

    def __init__(
        self,
        monitor,  # ClipboardMonitor（只用其 is_armed()）
        on_change: Callable[[], None],
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        read_text: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self._monitor = monitor
        self._on_change = on_change
        self._interval = poll_interval_sec
        self._read_text = read_text or xclip_read_text
        self._last: Optional[str] = None
        self._prev_armed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="x11-clipboard-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        """单次轮询周期（独立成方法便于单测，不需要真实 X 会话）。"""
        armed = self._monitor.is_armed()
        if not armed:
            self._prev_armed = False
            return
        if not self._prev_armed:
            # 武装沿：登记基线参照，不触发事件
            self._last = self._read_text()
            self._prev_armed = True
            return
        text = self._read_text()
        if text != self._last:
            self._last = text
            if text is not None:
                self._on_change()

    def _run(self) -> None:
        logger.info("剪贴板轮询已启动（xclip，间隔 %.2fs）", self._interval)
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 轮询线程异常必须可见且不杀线程
                logger.exception("剪贴板轮询 tick 异常")


# ---------- 全局热键 ----------
def build_hotkey_map(trigger_key: str, targets) -> list[tuple[str, str]]:
    """构造 [(pynput 组合键, 目标 key)]，如 ('<alt>+2', 'laptop')。纯逻辑可单测。"""
    return [(f"<{trigger_key}>+{t.hotkey}", t.key) for t in targets.values()]


class PynputHotkeyBackend:
    """pynput 全局热键（X11）：Alt+N → 编排层 select_target。

    register_all 返回 False 表示降级（无 DISPLAY / 无 pynput），
    调用方照常运行其余功能。
    """

    def __init__(self) -> None:
        self._listener = None

    def register_all(self, components) -> bool:
        try:
            from pynput import keyboard
        except Exception as e:  # noqa: BLE001 - 依赖缺失/无 X 会话属预期降级路径
            logger.warning("pynput 不可用，Linux 热键未注册: %s", e)
            return False
        mapping = build_hotkey_map(components.config.trigger_key, components.config.targets)
        hotkeys = {combo: (lambda k=key: components.orchestrator.select_target(k))
                   for combo, key in mapping}
        self._listener = keyboard.GlobalHotKeys(hotkeys)
        self._listener.start()  # listener 自带监听线程
        logger.info("已注册 Linux 热键: %s", [c for c, _ in mapping])
        return True

    def unregister_all(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def start_linux_backend(components) -> None:
    """Linux 后端总入口：剪贴板轮询 + 热键（可降级）+ 主线程等待退出。"""
    poller = X11ClipboardPoller(components.monitor, components.orchestrator.on_clipboard_change)
    poller.start()

    hotkeys = PynputHotkeyBackend()
    hotkeys.register_all(components)

    logger.info("Linux 后端已启动（仪表盘: http://%s:%.0f）",
                components.config.server_host, components.config.server_port)
    try:
        threading.Event().wait()  # 阻塞主线程，Ctrl+C 退出
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出")
    finally:
        poller.stop()
        hotkeys.unregister_all()
        components.discovery.stop()
        components.storage.close()
        logger.info("VoiceHub 已退出")
