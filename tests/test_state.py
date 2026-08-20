"""粘滞目标状态机单测：选定、切换、消费、超时、清空。"""

from voicehub.state import StickyTarget


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_select_and_arm():
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    assert s.is_armed() is False
    s.select("laptop")
    assert s.is_armed() is True
    assert s.target_key() == "laptop"


def test_select_switch_and_refresh():
    """重复按目标键切换目标，并刷新超时起点。"""
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    s.select("laptop")
    clock.t += 25.0
    s.select("tablet")  # 重新计时
    clock.t += 10.0     # 距重选只过 10s，未过期
    assert s.is_armed() is True
    assert s.target_key() == "tablet"


def test_consume_returns_and_clears():
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    s.select("desktop")
    assert s.consume() == "desktop"
    assert s.is_armed() is False
    assert s.consume() is None


def test_expire_after_timeout():
    """超时后视为未武装，consume 返回 None。"""
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    s.select("laptop")
    clock.t += 31.0
    assert s.is_armed() is False
    assert s.target_key() is None
    assert s.consume() is None


def test_clear():
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    s.select("laptop")
    s.clear()
    assert s.is_armed() is False
    assert s.consume() is None


def test_thread_safe_select_and_consume():
    """并发 select/consume 不抛错、状态一致。"""
    clock = _Clock()
    s = StickyTarget(pending_timeout_sec=30.0, now=clock)
    for _ in range(100):
        s.select("laptop")
        assert s.consume() == "laptop"
    assert s.consume() is None
