"""桌面通知（V4/M11 状态可视化）：org.freedesktop.Notifications 直写。

动机（2026-08-26 用户实测反馈）：听写触发后无任何可见反馈（闪电说有波形悬浮框，
M12 做悬浮窗前先用系统通知横幅兜底——录音开始/识别结果/失败原因全部可见）。

jeepney 仅 Linux 安装，且 SSID/服务器环境无会话总线——统一 try/except 降级为日志，
绝不阻塞调用方。发送函数可注入（单测无需真 DBus）。
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _dbus_notify(summary: str, body: str, timeout_ms: int, replaces_id: int) -> int:
    """经会话总线发通知；失败（无总线/无通知服务）返回 0 并落日志。"""
    try:
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.wrappers import DBusAddress, new_method_call
    except Exception as e:  # noqa: BLE001 - 依赖缺失属降级路径
        logger.debug("jeepney 不可用，桌面通知跳过: %s", e)
        return 0
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as e:  # noqa: BLE001 - 无桌面会话（SSH/服务）属降级路径
        logger.debug("无 DBus 会话总线，桌面通知跳过: %s", e)
        return 0
    try:
        addr = DBusAddress("/org/freedesktop/Notifications",
                           "org.freedesktop.Notifications", "org.freedesktop.Notifications")
        msg = new_method_call(
            addr, "Notify", "susssasa{sv}i",
            ("VoiceHub", replaces_id, "", summary, body, [], {}, timeout_ms))
        reply = conn.send_and_get_reply(msg)
        return int(reply.body[0])
    except Exception:  # noqa: BLE001 - UKUI 等面板通知服务差异大，失败只降级
        logger.debug("桌面通知发送失败", exc_info=True)
        return 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


class DesktopNotifier:
    """通知发送器（replaces_id 串联同一会话的横幅更新）。"""

    def __init__(self, sender: Optional[Callable[[str, str, int, int], int]] = None) -> None:
        self._sender = sender or _dbus_notify
        self._last_id = 0

    def send(self, summary: str, body: str = "", timeout_ms: int = 4000) -> int:
        """发横幅；同一 Notifier 实例连续发送会替换上一条（不刷屏）。"""
        try:
            nid = int(self._sender(summary, body, timeout_ms, self._last_id))
        except Exception:  # noqa: BLE001 - 通知失败绝不拖垮引擎/托盘回调
            logger.debug("桌面通知异常", exc_info=True)
            return 0
        self._last_id = nid
        return nid
