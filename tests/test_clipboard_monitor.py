"""剪贴板监控单测：基线快照、去抖、变化判定、过期、一次性消费。"""

from voicehub.clipboard_monitor import ClipboardMonitor


class _Clipboard:
    """模拟剪贴板 + 可拨快时钟。"""

    def __init__(self, initial: str | None) -> None:
        self.text = initial
        self.t = 0.0

    def read(self):
        return self.text

    def __call__(self) -> float:
        return self.t


def test_no_change_before_baseline():
    """剪贴板内容与基线相同，不触发。"""
    cb = _Clipboard("旧的")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, now=cb)
    m.arm()
    cb.text = "旧的"  # 没变化
    m.notify_change()
    cb.t += 0.7
    assert m.settle_if_ready() is None
    assert hits == []


def test_change_after_debounce_triggers():
    """内容变化且去抖就绪后命中。"""
    cb = _Clipboard("基线")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, now=cb)
    m.arm()
    cb.text = "新转写"
    m.notify_change()
    # 去抖未到，不应触发
    cb.t += 0.3
    assert m.settle_if_ready() is None
    # 去抖就绪
    cb.t += 0.4
    assert m.settle_if_ready() == "新转写"
    assert hits == ["新转写"]


def test_debounce_restarts_on_rapid_change():
    """多次变化刷新去抖窗口，只有稳定后判定。"""
    cb = _Clipboard("基线")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, stability_ms=600, now=cb)
    m.arm()
    cb.text = "中间态1"
    m.notify_change()
    cb.t += 0.4
    cb.text = "中间态2"
    m.notify_change()  # 去抖重计
    cb.t += 0.4  # 距上次变化 0.4s，未到 0.6s
    assert m.settle_if_ready() is None
    cb.t += 0.3  # 距上次 0.7s
    assert m.settle_if_ready() == "中间态2"
    assert hits == ["中间态2"]


def test_one_shot_consumption():
    """命中后解除武装，不再重复触发。"""
    cb = _Clipboard("基线")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, now=cb)
    m.arm()
    cb.text = "一次"
    m.notify_change()
    cb.t += 0.7
    assert m.settle_if_ready() == "一次"
    assert m.settle_if_ready() is None
    assert hits == ["一次"]


def test_pending_timeout_disarms():
    """超时未检测到变化，自动放弃。"""
    cb = _Clipboard("基线")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, pending_timeout_sec=30.0, now=cb)
    m.arm()
    cb.t += 31.0
    assert m.settle_if_ready() is None
    assert m.is_armed() is False


def test_notify_before_arm_ignored():
    """未武装时通知被忽略。"""
    cb = _Clipboard("基线")
    hits: list[str] = []
    m = ClipboardMonitor(read_text=cb.read, on_text=hits.append, now=cb)
    m.notify_change()
    assert m.settle_if_ready() is None
