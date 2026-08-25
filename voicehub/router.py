"""路由编排（ADR-1/2/5）：把一条转写文本按目标类型分发到本地或远端。

单向数据流：转写文本 → route() → 解析端点 → 推送/记录 → 返回结果。
- local：桌面端。闪电说链路（ADR-5）只记录（闪电说已自动粘贴）；
  builtin 直通链路（ADR-9，deliver_local=True）由 VoiceHub 写系统剪贴板完成投递。
- network_http：查设备发现（或手动 endpoint）取 /paste，HTTP 推送文本。

storage / discovery / transport 均通过接口注入，便于单测与副作用隔离。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .config import Config, TargetConfig
from .storage import Storage, TranscriptLog

logger = logging.getLogger(__name__)


class Router:
    """路由分发器。"""

    def __init__(
        self,
        config: Config,
        discovery: Optional[object] = None,
        transport: Optional[object] = None,
        storage: Optional[Storage] = None,
        now: Callable[[], float] = time.time,
        clipboard_write: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._config = config
        self._discovery = discovery
        self._transport = transport
        self._storage = storage
        self._now = now
        self._clipboard_write = clipboard_write

    def target_by_key(self, key: str) -> Optional[TargetConfig]:
        return self._config.targets.get(key)

    def route(
        self,
        text: str,
        target_key: str,
        raw_text: Optional[str] = None,
        latency_ms: int = 0,
        metadata: Optional[dict] = None,
        deliver_local: bool = False,
    ) -> dict:
        """把文本路由到目标；返回结果 dict（含 ok / error / target / type）。"""
        started = self._now()
        target = self.target_by_key(target_key)
        if target is None:
            return self._finish(target_key, False, "unknown target", text, raw_text, latency_ms)

        ok, error = self._dispatch(target, text, deliver_local)
        if not ok:
            # 诊断日志：路由失败原因必须可见（如 no transport / target offline / push failed）
            logger.warning("路由失败: target=%s error=%s", target_key, error)
        result = self._finish(target_key, ok, error, text, raw_text, latency_ms, target, metadata)
        result["elapsed_ms"] = int((self._now() - started) * 1000)
        return result

    # ---------- 分发 ----------
    def _dispatch(self, target: TargetConfig, text: str,
                  deliver_local: bool = False) -> tuple[bool, Optional[str]]:
        if target.type == "local":
            if not deliver_local:
                # ADR-5：闪电说链路，桌面端由闪电说完成粘贴，VoiceHub 只记录。
                return True, None
            # ADR-9：builtin 直通链路，写系统剪贴板完成本地投递（Wayland 注入留 M12+）
            if self._clipboard_write is None:
                return False, "no clipboard writer"
            if self._clipboard_write(text):
                return True, None
            return False, "clipboard write failed"
        if target.type != "network_http":
            return False, f"unsupported type: {target.type}"
        endpoint = self._resolve_endpoint(target)
        if endpoint is None:
            return False, "target offline (no endpoint)"
        if self._transport is None:
            return False, "no transport"
        if self._transport.push(endpoint, text):
            return True, None
        return False, "push failed"

    def _resolve_endpoint(self, target: TargetConfig) -> Optional[str]:
        if target.endpoint:
            return target.endpoint
        if self._discovery is not None:
            return self._discovery.resolve_endpoint(target)
        return None

    # ---------- 记录 ----------
    def _finish(
        self,
        target_key: str,
        ok: bool,
        error: Optional[str],
        text: str,
        raw_text: Optional[str],
        latency_ms: int,
        target: Optional[TargetConfig] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        result = {
            "ok": ok,
            "target": target_key,
            "type": target.type if target else None,
        }
        if error:
            result["error"] = error
        if self._storage is not None:
            log = TranscriptLog(
                processed_text=text,
                target_device=target.name if target else target_key,
                raw_text=raw_text,
                latency_ms=latency_ms,
                is_routed_successfully=ok,
            )
            if metadata:
                log.set_metadata(**metadata)
            self._storage.insert(log)
            result["log_id"] = log.id
        return result
