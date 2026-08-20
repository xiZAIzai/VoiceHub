"""全局热键（ADR-1）：Alt+1/2/3/4 目标选择，注册表 + 键位解析。

- 纯逻辑层：热键字符串解析、目标映射、冲突校验，可跨平台单测。
- 平台层：Windows 用 keyboard 库注册热键（挂到 main.py 组装），此处提供
  register()/unregister() 薄封装，把 key → 回调 的绑定与解析解耦。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hotkey:
    """一条热键绑定：修饰键 + 主键。"""

    modifier: str = "alt"
    key: str = "1"

    def combo(self) -> str:
        return f"{self.modifier}+{self.key}"


def parse_hotkey(spec: str) -> Optional[Hotkey]:
    """解析 'alt+2' 或 '2'（默认 alt）形式；非法则 None。"""
    spec = spec.strip().lower()
    if "+" in spec:
        mod, _, key = spec.partition("+")
        mod, key = mod.strip(), key.strip()
        if not mod or not key:
            return None
        return Hotkey(modifier=mod, key=key)
    if spec:
        return Hotkey(key=spec)
    return None


class HotkeyRegistry:
    """目标 key → 热键 的注册表，负责绑定与回调派发。"""

    def __init__(self) -> None:
        self._bindings: dict[str, Hotkey] = {}
        self._callbacks: dict[str, Callable[[], None]] = {}

    def register(self, target_key: str, hotkey_spec: str, callback: Callable[[], None]) -> bool:
        """注册一个目标热键；组合冲突或重复则拒绝并返回 False。"""
        hk = parse_hotkey(hotkey_spec)
        if hk is None:
            logger.error("非法热键: %s", hotkey_spec)
            return False
        combo = hk.combo()
        for k, existing in self._bindings.items():
            if existing.combo() == combo and k != target_key:
                logger.error("热键冲突: %s 已被 %s 占用", combo, k)
                return False
        self._bindings[target_key] = hk
        self._callbacks[target_key] = callback
        return True

    def unregister(self, target_key: str) -> None:
        self._bindings.pop(target_key, None)
        self._callbacks.pop(target_key, None)

    def dispatch(self, target_key: str) -> bool:
        """按目标 key 派发回调；未注册返回 False。"""
        cb = self._callbacks.get(target_key)
        if cb is None:
            return False
        cb()
        return True

    def bindings(self) -> dict[str, str]:
        """返回 {target_key: combo} 快照，供仪表盘展示。"""
        return {k: v.combo() for k, v in self._bindings.items()}
