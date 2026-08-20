"""传输层：HTTP 客户端，向目标接收端 POST /paste 推送文本（ADR-2/5）。

副作用（网络请求）独立成类，router 依赖接口注入，单测可替换为假实现。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class HttpPusher:
    """向接收端 /paste 推送文本的 HTTP 客户端。"""

    def __init__(self, timeout_sec: float = 3.0) -> None:
        self._timeout = timeout_sec

    def push(self, endpoint: str, text: str) -> bool:
        """POST {"text": text} 到 endpoint，返回接收端是否确认成功。"""
        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("ok") is True
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("推送失败 %s: %s", endpoint, e)
            return False
