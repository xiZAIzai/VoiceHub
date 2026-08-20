"""粘滞目标状态机（ADR-1，零侵入方案）。

- IDLE：无粘滞目标，语音转写只落库不路由。
- ARMED：用户按了目标热键（如 Alt+2 选笔记本），等待下一次转写。
  重复按目标键可随时切换目标并刷新超时；超时未收到转写则自动回 IDLE。
- 路由：转写到达时 consume() 取出目标并清空，router 据此投递。

时钟可注入（now），便于单测超时逻辑。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from time import time
from typing import Callable, Optional


@dataclass
class StickyState:
    """一次粘滞目标会话：目标 key + 开始时间。"""

    target_key: str
    armed_at: float


class StickyTarget:
    """粘滞目标状态机（线程安全）。"""

    def __init__(
        self,
        pending_timeout_sec: float = 30.0,
        now: Callable[[], float] = time,
    ) -> None:
        self.pending_timeout = pending_timeout_sec
        self._now = now
        self._lock = threading.Lock()
        self._state: Optional[StickyState] = None

    # ---------- 查询 ----------
    def target_key(self) -> Optional[str]:
        """当前粘滞目标 key（未过期）；无则 None。"""
        with self._lock:
            if self._state is None or self._expired(self._state):
                return None
            return self._state.target_key

    def is_armed(self) -> bool:
        with self._lock:
            return self._state is not None and not self._expired(self._state)

    def armed_at(self) -> Optional[float]:
        """目标选定的时间戳（用于仪表盘展示等待时长）。"""
        with self._lock:
            return self._state.armed_at if self._state else None

    # ---------- 事件 ----------
    def select(self, target_key: str) -> None:
        """按目标热键：选定目标并刷新超时；重复按可切换目标。"""
        with self._lock:
            self._state = StickyState(target_key=target_key, armed_at=self._now())

    def consume(self) -> Optional[str]:
        """转写到达：取出粘滞目标并清空；无有效目标则 None。"""
        with self._lock:
            if self._state is None or self._expired(self._state):
                self._state = None
                return None
            key = self._state.target_key
            self._state = None
            return key

    def clear(self) -> None:
        """手动清空（如超时巡检或用户取消）。"""
        with self._lock:
            self._state = None

    # ---------- 内部 ----------
    def _expired(self, state: StickyState) -> bool:
        return self._now() - state.armed_at > self.pending_timeout
