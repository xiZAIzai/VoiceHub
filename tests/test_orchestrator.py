"""编排层单测：热键选目标 → 剪贴板变化 → 路由，全链路状态流转。"""

from voicehub.config import Config, TargetConfig
from voicehub.clipboard_monitor import ClipboardMonitor
from voicehub.orchestrator import Orchestrator
from voicehub.router import Router
from voicehub.state import StickyTarget


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Clipboard:
    def __init__(self) -> None:
        self.text = "基线"

    def read(self):
        return self.text


class _FakeTransport:
    def __init__(self) -> None:
        self.pushed: list[tuple] = []

    def push(self, endpoint, text):
        self.pushed.append((endpoint, text))
        return True


class _Discovery:
    def resolve_endpoint(self, target):
        return f"http://10.0.0.5:5050/paste"


def _make_orchestrator(clock, clip, transport=None):
    cfg = Config()
    cfg.targets = {
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
        "tablet": TargetConfig(key="tablet", name="平板", hotkey="3", type="network_http"),
    }
    sticky = StickyTarget(pending_timeout_sec=30.0, now=clock)
    monitor = ClipboardMonitor(read_text=clip.read, on_text=lambda t: None,
                               stability_ms=600, pending_timeout_sec=30.0, now=clock)
    router = Router(cfg, discovery=_Discovery(),
                    transport=transport or _FakeTransport())
    return Orchestrator(cfg, sticky, monitor, router), sticky, monitor


def test_full_flow_hotkey_to_route():
    clock = _Clock()
    clip = _Clipboard()
    transport = _FakeTransport()
    orch, sticky, monitor = _make_orchestrator(clock, clip, transport)

    # 1. 按 Alt+2 选笔记本
    assert orch.select_target("laptop") is True
    assert sticky.is_armed() is True
    assert monitor.is_armed() is True

    # 2. 闪电说写出新文本
    clip.text = "今天天气不错"
    orch.on_clipboard_change()
    clock.t += 0.7  # 去抖就绪

    # 3. 判定并路由
    result = orch.poll_settle()
    assert result is not None
    assert result["ok"] is True
    assert result["target"] == "laptop"
    assert transport.pushed == [("http://10.0.0.5:5050/paste", "今天天气不错")]
    # 一次性消费，粘滞清空
    assert sticky.is_armed() is False


def test_unknown_target_rejected():
    clock = _Clock()
    clip = _Clipboard()
    orch, sticky, monitor = _make_orchestrator(clock, clip)
    assert orch.select_target("nonexistent") is False
    assert sticky.is_armed() is False
    assert monitor.is_armed() is False


def test_poll_settle_before_debounce_returns_none():
    clock = _Clock()
    clip = _Clipboard()
    orch, _, _ = _make_orchestrator(clock, clip)
    orch.select_target("laptop")
    clip.text = "新文本"
    orch.on_clipboard_change()
    clock.t += 0.3  # 去抖未到
    assert orch.poll_settle() is None


def test_clipboard_unchanged_no_route():
    """剪贴板内容与基线相同则不路由。"""
    clock = _Clock()
    clip = _Clipboard()
    orch, sticky, _ = _make_orchestrator(clock, clip)
    orch.select_target("laptop")
    orch.on_clipboard_change()  # 内容没变
    clock.t += 0.7
    assert orch.poll_settle() is None
    assert sticky.is_armed() is False  # monitor 已消费并解除武装
