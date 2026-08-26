"""编排层：把热键 / 剪贴板 / 路由串成单向数据流（纯逻辑，可测）。

流程（ADR-1/4/5）：
1. select_target(key)   —— 用户按 Alt+N：粘滞目标选定 + 剪贴板基线快照。
2. on_clipboard_change() —— 闪电说写出新文本触发剪贴板变化，刷新去抖计时。
3. poll_settle()         —— 去抖就绪后读取文本，消费粘滞目标并路由。

Windows 平台层（main.py）负责真正的热键 hook / 剪贴板消息 / 托盘，并把
事件转调到这里；驱动线程周期性调用 poll_settle 直到命中或超时。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional  # noqa: UP045 - 与存量代码 typing 风格保持一致

from .config import Config
from .clipboard_monitor import ClipboardMonitor
from .router import Router
from .state import StickyTarget

logger = logging.getLogger(__name__)


class Orchestrator:
    """核心编排器：持有粘滞状态、剪贴板监控、路由。"""

    def __init__(
        self,
        config: Config,
        sticky: StickyTarget,
        monitor: ClipboardMonitor,
        router: Router,
    ) -> None:
        self._config = config
        self._sticky = sticky
        self._monitor = monitor
        self._router = router
        self._driver_thread: Optional[threading.Thread] = None

    # ---------- 事件入口 ----------
    def select_target(self, target_key: str) -> bool:
        """热键回调：选定目标并开始监听剪贴板。未知目标返回 False。"""
        if self._router.target_by_key(target_key) is None:
            logger.warning("未知目标热键: %s", target_key)
            return False
        self._sticky.select(target_key)
        self._monitor.arm()
        logger.info("选定目标 %s，等待转写", target_key)
        return True

    def on_clipboard_change(self) -> None:
        """剪贴板变化事件：刷新去抖计时，并确保驱动线程在跑。"""
        self._monitor.notify_change()
        if self._driver_thread is None or not self._driver_thread.is_alive():
            self._driver_thread = threading.Thread(
                target=self._settle_driver, name="clipboard-settle", daemon=True)
            self._driver_thread.start()

    def poll_settle(self) -> Optional[dict]:
        """单步判定：去抖就绪则读取并路由；返回路由结果或 None。"""
        was_armed = self._monitor.is_armed()
        text = self._monitor.settle_if_ready()
        if text is not None:
            target_key = self._sticky.consume()
            if target_key is None:
                logger.warning("剪贴板命中但无粘滞目标（可能已超时）")
                return None
            return self._router.route(text, target_key)
        # monitor 已消费（内容与基线相同或超时），同步清空粘滞保持一致
        if was_armed and not self._monitor.is_armed():
            self._sticky.clear()
        return None

    # ---------- 直通路径（V4/ADR-9 builtin 引擎） ----------
    def route_direct(self, text: str, metadata: Optional[dict] = None) -> dict:
        """自建转写内核的直通路由：不经剪贴板监听链路（ADR-4/ADR-5 痛点免疫）。

        目标解析：粘滞目标优先（用户先按了 Alt+N 的既有习惯不变），
        无粘滞则退 config.transcription.default_target，再退第一个 local 目标。
        落库 metadata 带 source=builtin 与闪电说来源区分。
        """
        target_key = self._sticky.consume()
        if target_key is None:
            target_key = self._default_direct_target()
        meta = dict(metadata or {})
        raw_text = meta.pop("raw_text", None)  # 原文走 DB 专用列，不进扩展元数据
        meta = {"source": "builtin", **meta}
        if target_key is None:
            logger.warning("直通路由无可用目标（未配置 targets）")
            return {"ok": False, "error": "no target", "target": None}
        return self._router.route(text, target_key, raw_text=raw_text,
                                  metadata=meta, deliver_local=True)

    def _default_direct_target(self) -> Optional[str]:
        tc = self._config.transcription
        if tc.default_target and tc.default_target in self._config.targets:
            return tc.default_target
        for key, t in self._config.targets.items():
            if t.type == "local":
                return key
        return next(iter(self._config.targets), None)

    # ---------- 驱动 ----------
    def _settle_driver(self) -> None:
        """后台循环调用 poll_settle，直到命中或未武装退出。"""
        while True:
            result = self.poll_settle()
            if result is not None:
                logger.info("路由完成: %s -> %s", result.get("ok"), result.get("target"))
                return
            if not self._monitor.is_armed():
                return
            time.sleep(0.1)
