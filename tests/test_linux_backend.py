"""linux_backend 单测：xclip 读取容错 + 轮询事件语义 + 热键映射构造。

全部用注入/模拟实现，不依赖真实 X 会话，Windows / Linux 双平台 CI 均可跑。
"""
from unittest import mock

from voicehub.config import Config, TargetConfig
from voicehub.linux_backend import (
    X11ClipboardPoller,
    build_hotkey_map,
    xclip_read_text,
)


class FakeMonitor:
    """只提供 is_armed() 的假监控器（X11ClipboardPoller 仅用到该接口）。"""

    def __init__(self) -> None:
        self.armed = False

    def is_armed(self) -> bool:
        return self.armed


# ---------- xclip_read_text ----------

def test_xclip_read_text_success_with_utf8():
    cp = mock.Mock(returncode=0, stdout="中文-abc")
    with mock.patch("voicehub.linux_backend.subprocess.run", return_value=cp) as run:
        assert xclip_read_text() == "中文-abc"
    args = run.call_args
    assert args.args[0] == ["xclip", "-selection", "clipboard", "-o"]
    assert args.kwargs.get("encoding") == "utf-8"  # 无 LANG 环境下中文不乱码
    assert args.kwargs.get("timeout") is not None


def test_xclip_read_text_empty_selection_returns_none():
    # 空选区：xclip 非零退出且无输出 → None
    assert xclip_read_text.__name__  # 占位防误删 import
    cp = mock.Mock(returncode=1, stdout="")
    with mock.patch("voicehub.linux_backend.subprocess.run", return_value=cp):
        assert xclip_read_text() is None
    # 零退出但空输出 → None
    cp2 = mock.Mock(returncode=0, stdout="")
    with mock.patch("voicehub.linux_backend.subprocess.run", return_value=cp2):
        assert xclip_read_text() is None


def test_xclip_read_text_missing_binary_or_timeout_returns_none():
    import subprocess as sp

    with mock.patch("voicehub.linux_backend.subprocess.run", side_effect=OSError("no xclip")):
        assert xclip_read_text() is None
    with mock.patch("voicehub.linux_backend.subprocess.run",
                    side_effect=sp.TimeoutExpired(cmd="xclip", timeout=2)):
        assert xclip_read_text() is None


# ---------- X11ClipboardPoller 事件语义 ----------

def _poller(read_queue: list, events: list) -> tuple[X11ClipboardPoller, FakeMonitor]:
    """构造带可编程读取序列与事件记录的 poller（读一次弹出一次）。"""
    seq = list(read_queue)

    def read() -> object:
        return seq.pop(0) if seq else seq  # 耗尽后恒返回 []（占位，不影响断言）

    monitor = FakeMonitor()
    poller = X11ClipboardPoller(monitor, on_change=lambda: events.append(1),
                                read_text=lambda: read())
    return poller, monitor


def test_tick_not_armed_never_reads_clipboard():
    read = mock.Mock(return_value="x")
    monitor = FakeMonitor()
    poller = X11ClipboardPoller(monitor, on_change=mock.Mock(), read_text=read)
    poller.tick()
    read.assert_not_called()


def test_armed_edge_snapshots_baseline_without_event():
    poller, monitor = _poller(["基线文本", "基线文本"], [])
    monitor.armed = True
    poller.tick()  # 武装沿：登记基线，不触发
    poller.tick()  # 内容同基线：不触发
    assert poller._prev_armed is True
    assert poller._last == "基线文本"


def test_change_fires_event_exactly_once():
    poller, monitor = _poller(["a", "a", "b", "b"], events := [])
    monitor.armed = True
    poller.tick()  # 基线 a
    poller.tick()  # 仍 a，无事件
    poller.tick()  # 变 b → 事件
    poller.tick()  # 仍 b，不重复
    assert len(events) == 1


def test_failed_read_none_does_not_fire_then_new_text_fires():
    poller, monitor = _poller(["a", None, "c"], events := [])
    monitor.armed = True
    poller.tick()  # 基线 a
    poller.tick()  # 读取失败 None：不触发
    poller.tick()  # 恢复并变为 c：触发
    assert len(events) == 1


def test_rearm_resets_reference_avoids_phantom_event():
    # 第一轮：基线 a → 变 b 触发；解除武装后再武装，剪贴板仍是 b：
    # 武装沿应以当前 b 为参照，不得把 b 当作"变化"再触发一次
    poller, monitor = _poller(["a", "b", "b"], events := [])
    monitor.armed = True
    poller.tick()
    poller.tick()
    assert len(events) == 1
    monitor.armed = False
    poller.tick()  # 解除武装：清武装沿标记
    monitor.armed = True
    poller.tick()  # 再武装：登记 b 为新参照，无事件
    assert len(events) == 1


# ---------- 热键映射 ----------

def test_build_hotkey_map_alt_targets():
    cfg = Config(targets={
        "desktop": TargetConfig(key="desktop", name="台式机", hotkey="1", type="local"),
        "laptop": TargetConfig(key="laptop", name="笔记本", hotkey="2", type="network_http"),
    })
    assert build_hotkey_map("alt", cfg.targets) == [("<alt>+1", "desktop"), ("<alt>+2", "laptop")]


def test_build_hotkey_map_custom_trigger():
    t = TargetConfig(key="x", name="X", hotkey="3", type="network_http")
    assert build_hotkey_map("ctrl", {"x": t}) == [("<ctrl>+3", "x")]
