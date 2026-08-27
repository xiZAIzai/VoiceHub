"""Linux（X11）平台后端：剪贴板轮询监听（xclip）+ 全局热键（pynput）。

V3/M8 第一版（ADR-7 占位，按 X11 假设先行开发，WSL 可测）：
- 剪贴板：X11 无系统级变化通知（对比 Windows 的 WM_CLIPBOARDUPDATE），
  用 xclip 周期读取模拟事件：武装期间发现与基线不同的文本 → 转调
  orchestrator.on_clipboard_change，与 Windows 事件语义一致（ADR-4 逻辑复用）。
- 热键：pynput GlobalHotKeys 监听 Alt+N → 编排层 select_target
  （「按下即录」语义依赖闪电说同样吃 Alt 触发键）；
  pynput / DISPLAY 不可用时降级为日志提示，仪表盘照常可用。
- 托盘：SNI 直写（linux_tray，2026-08-25 openKylin 实机补齐，替代 M8 的
  「无托盘」降级决策）——Wayland 会话下 XEmbed 托盘不可用，走 DBus 协议；
  无 watcher 时同样降级。原生窗口维持不做，仪表盘走浏览器。

依赖：xclip（系统包，apt install xclip）；pynput 走 requirements 的 linux 标记。
平台依赖均延迟 import，非 Linux 平台 import 本模块不崩溃。
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
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


def xclip_write_text(text: str) -> bool:
    """写 X11 CLIPBOARD 选区（builtin 直通链路的本地投递，V4/ADR-9）。

    坑：xclip 写入后会 fork 守护进程持有选区（X11 剪贴板需要活客户端），
    若 capture_output 捕获管道，守护进程占着管道导致 run() 等到超时误报失败
    （openKylin 实测：报失败但剪贴板实际已写入）。stdout/stderr 必须 DEVNULL。
    """
    try:
        p = subprocess.run(
            ["xclip", "-selection", "clipboard", "-i"],
            input=text, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=2, encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("xclip 写入失败（未安装/无 X 会话）")
        return False
    return p.returncode == 0


# 粘贴目标窗口（录音开始/停止时捕获；防悬浮框抢焦与中途换位）
_paste_target: dict = {"wid": None}


def _xdotool(*args: str):
    """执行 xdotool 子命令，失败返回空串。独立成函数便于测试注入。"""
    try:
        p = subprocess.run(["xdotool", *args],
                           capture_output=True, timeout=3)
        return p.stdout.decode(errors="replace").strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def capture_paste_target() -> None:
    """记录当前活动窗口 id 为粘贴目标。

    2026-08-27 用户实测补充：录音中用户可能点击别的位置再停止——调用方应在
    停止时刻再捕获一次，让「最后落点」优先于「开始落点」。守卫：活动窗口属于
    本进程（= 悬浮框抢到焦点）时不覆盖真实目标，避免把文字贴回自己的悬浮框。
    """
    wid = _xdotool("getactivewindow")
    if not wid:
        return  # 取不到保持原值
    pid_out = _xdotool("getwindowpid", wid)
    try:
        own = int(pid_out) == os.getpid()
    except ValueError:
        own = False
    if own:
        logger.debug("活动窗口是本进程悬浮框，保留原目标 %s", _paste_target["wid"])
        return
    _paste_target["wid"] = wid
    logger.debug("粘贴目标窗口: %s", _paste_target["wid"])


def wl_copy_write_text(text: str) -> bool:
    """wl-copy 写 Wayland 原生剪贴板（跨侧可见性实测优于 xclip，2026-08-27：
    kylin-wlcom 下 xclip 内容对原生应用不可见——用户「Ctrl+V 不能用」根因）。"""
    try:
        p = subprocess.run(["wl-copy"], input=text,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, encoding="utf-8")
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("wl-copy 不可用")
        return False


def write_clipboard_text(text: str) -> bool:
    """统一剪贴板写入入口：Wayland 会话下 wl-copy+xclip 双写（各覆盖一侧，
    至少一路成功即成）；纯 X 会话仅 xclip。"""
    results = []
    if os.environ.get("WAYLAND_DISPLAY"):
        try:
            import shutil

            if shutil.which("wl-copy"):
                results.append(wl_copy_write_text(text))
        except Exception:  # noqa: BLE001 - 探测失败按无 wl-copy
            pass
    results.append(xclip_write_text(text))
    return any(results)


def xdotool_paste() -> bool:
    """在目标窗口模拟 Ctrl+V（V4 听写「贴到光标处」，对齐闪电说体验）。

    优先定向录音开始时捕获的窗口；无捕获/失败回退当前活动窗口。
    仅对 XWayland 窗口生效（Wayland 原生窗口收不到合成键），失败由 Router
    降级为「仅剪贴板」并标注。
    """
    time.sleep(0.15)  # xclip 守护进程接管选区需要一拍
    wid = _paste_target.get("wid")

    def _send_key(args):
        return bool(_xdotool(*args))

    # 链式尝试（2026-08-27 用户实测 --window 直接发键在 wlcom 下无效）：
    # ① 窗口存在则先激活（XWayland 窗口激活会拉起合成器焦点）再全局发键；
    # ② 不激活直接定向发键；③ 回退当前活动窗口全局键
    if wid and _send_key(["windowactivate", "--sync", wid]):
        time.sleep(0.08)
        if _send_key(["key", "--clearmodifiers", "ctrl+v"]):
            return True
    if wid and _send_key(["key", "--window", wid, "--clearmodifiers", "ctrl+v"]):
        return True
    if _send_key(["getactivewindow"]) and \
            _send_key(["key", "--clearmodifiers", "ctrl+v"]):
        return True
    logger.debug("光标粘贴全链失败（目标窗=%s；Wayland 原生窗口为已知边界）", wid)
    return False


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


# pynput GlobalHotKeys 修饰键集合（其余键名原样传递，如 'v' / 'f1'）
_PYNPUT_MODIFIERS = {"ctrl", "alt", "altgr", "shift", "cmd", "win", "meta"}


def to_pynput_combo(spec: str) -> str:
    """'ctrl+alt+v' → '<ctrl>+<alt>+v'（修饰键加尖括号）。纯逻辑可单测。"""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        return ""
    return "+".join(f"<{p}>" if p in _PYNPUT_MODIFIERS else p for p in parts)


class PynputHotkeyBackend:
    """pynput 全局热键（X11）：Alt+N → select_target；听写触发键 → engine.toggle。

    register_all 返回 False 表示降级（无 DISPLAY / 无 pynput），
    调用方照常运行其余功能（托盘菜单仍可触发听写）。
    系统快捷键（--dictate 通道）已注册时停用 pynput 听写键，防止双触发。
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
        # V4/M11：builtin 听写独立触发键（ADR-9：与闪电说 Alt 解耦，避免撞车）；
        # UKUI 系统快捷键已注册时跳过（同一按键会双触发：CLI + X11 抓键各一次）
        dictation = getattr(components, "dictation", None)
        if dictation is not None:
            if _system_shortcut_registered():
                logger.info("检测到 UKUI 系统快捷键已注册，pynput 听写热键停用")
            else:
                combo = to_pynput_combo(components.config.transcription.trigger_key)
                if combo:
                    hotkeys[combo] = dictation.toggle
                    logger.info("已注册听写热键: %s", combo)
        self._listener = keyboard.GlobalHotKeys(hotkeys)
        self._listener.start()  # listener 自带监听线程
        logger.info("已注册 Linux 热键: %s", [c for c, _ in mapping])
        return True

    def unregister_all(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def start_linux_backend(components) -> None:
    """Linux 后端总入口：剪贴板轮询 + 热键/托盘（均可降级）+ 主线程等待退出。

    主线程等待对象从「永久 Event」改为共享 stop：托盘「退出」或 Ctrl+C 均能收口。
    V4/M11：builtin 引擎启用时托盘带「开始听写」菜单 + 独立热键（ADR-9 双通道）。
    """
    poller = X11ClipboardPoller(components.monitor, components.orchestrator.on_clipboard_change)
    poller.start()

    hotkeys = PynputHotkeyBackend()
    hotkeys.register_all(components)

    stop = threading.Event()
    tray = _build_tray(components, stop)
    tray.start()
    _wire_dictation(components, tray)

    logger.info("Linux 后端已启动（仪表盘: http://%s:%.0f）",
                components.config.server_host, components.config.server_port)
    try:
        stop.wait()  # 托盘「退出」置位；Ctrl+C 在主线程打断本等待
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出")
    finally:
        tray.stop()
        poller.stop()
        hotkeys.unregister_all()
        components.discovery.stop()
        components.storage.close()
        logger.info("VoiceHub 已退出")


def _build_tray(components, stop: threading.Event):
    """组装托盘：菜单「打开仪表盘」开浏览器（对位 Windows 版），「退出」停主循环。"""
    import webbrowser

    from .linux_tray import LinuxTray

    url = f"http://{components.config.server_host}:{components.config.server_port}"
    dictation = getattr(components, "dictation", None)
    return LinuxTray(
        on_open=lambda: webbrowser.open(url),
        on_quit=stop.set,
        on_dictate=dictation.toggle if dictation is not None else None,
    )


def _system_shortcut_registered() -> bool:
    """UKUI 系统快捷键（--dictate 通道）是否已由本产品注册。失败按未注册处理。"""
    try:
        from .ukui_shortcut import find_dictate_slot

        return find_dictate_slot() is not None
    except Exception:  # noqa: BLE001
        return False


def _wire_dictation(components, tray) -> None:
    """听写引擎状态 → 可见反馈全家桶（对齐闪电说体感，2026-08-26 用户反馈）：

    - 波形悬浮框（录音中屏幕底部跳动，再触发一次消失）
    - 托盘：菜单标签 开/停 + 图标变红 + Title 标注
    - 桌面通知：识别结果 / 失败原因（开始提示由悬浮框承担，不再重复弹）
    """
    dictation = getattr(components, "dictation", None)
    if dictation is None:
        return
    from .dictation.overlay import WaveformOverlay
    from .notify import DesktopNotifier

    notifier = DesktopNotifier()
    overlay = WaveformOverlay()
    recorder = getattr(dictation, "recorder", None)
    if recorder is not None and hasattr(recorder, "set_level_callback"):
        recorder.set_level_callback(overlay.update_level)

    def _refocus_later(wid: str) -> None:
        """把焦点还给用户原本的窗口（悬浮框弹出抢焦的补救）。"""
        def _run():
            time.sleep(0.4)  # 等 tk 窗口完成映射与抢焦动作后再归还
            if _xdotool("windowactivate", "--sync", wid):
                logger.debug("焦点已归还给 %s", wid)

        threading.Thread(target=_run, name="dictation-refocus", daemon=True).start()

    def _on_state(state: str) -> None:
        recording = state == "recording"
        tray.set_dictation_state(recording)
        if recording:
            # 先记开始落点，悬浮框随后才弹（会抢焦）——弹后立即把焦点还回去
            capture_paste_target()
            overlay.show()
            start_wid = _paste_target.get("wid")
            if start_wid:
                _refocus_later(start_wid)
        elif state == "processing":
            # 用户可能在录音期间点了新的位置：停止时刻的落点优先
            capture_paste_target()
        else:
            overlay.hide()
            if state == "idle":
                result = dictation.last_result()
                if not result:
                    return
                if result.get("ok"):
                    text = result.get("text", "")
                    notifier.send(f"识别完成（{len(text)} 字）",
                                  text if len(text) <= 80 else text[:80] + "…")
                else:
                    notifier.send("听写未成功", str(result.get("error", "未知原因")))

    dictation.set_on_state_change(_on_state)
