"""配置模块：加载并校验 config.json，提供全项目共享配置。

对应 PRD §6 的 config.json 结构，并按 ADR 修订：
- ADR-4：原 `shandianshuo.polling_timeout_sec`（6s 当总超时不成立）改为
  `voicehub.pending_timeout_sec`（粘滞目标过期，默认 30s）+ `voicehub.stability_ms`（去抖，默认 600）。
- ADR-2：`targets` 不再强制写 endpoint，由设备发现解析当前 IP；`endpoint` 可手动写死覆盖。
- ADR-3：`server.host` 默认 127.0.0.1（仪表盘仅本机访问，安全）。

协议常量（DISCOVERY_PORT / RECEIVER_PORT / HEARTBEAT_SVC）是接收端与 daemon 共用的约定，
接收端自包含单文件里应保持同一套值。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 协议常量
DISCOVERY_PORT = 9898  # UDP 报到广播端口
RECEIVER_PORT = 5050   # 接收端 HTTP 服务端口
HEARTBEAT_SVC = "voicehub"  # 报到包 svc 标识


@dataclass
class TargetConfig:
    """单个目标设备配置。

    - `key` 为 config targets 中的键，同时作为设备发现的注册名（name）。
    - `hotkey` 为 Alt+N 里的数字（'1'/'2'/'3'/'4'）。
    - `endpoint` 可省略：默认由设备发现解析当前 IP；写死后固定网络场景可覆盖发现结果。
    """

    key: str
    name: str
    hotkey: str
    type: str  # 'local' | 'network_http'
    endpoint: str | None = None

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> "TargetConfig":
        return cls(
            key=key,
            name=str(data.get("name", key)),
            hotkey=str(data.get("hotkey", "0")),
            type=str(data.get("type", "network_http")),
            endpoint=data.get("endpoint"),
        )


@dataclass
class Config:
    """全局配置，字段均有默认值，缺失 config.json 也能运行。"""

    server_host: str = "127.0.0.1"
    server_port: int = 8000
    trigger_key: str = "alt"
    # ADR-4 转录判定
    pending_timeout_sec: float = 30.0
    stability_ms: int = 600
    # ADR-2 设备发现
    heartbeat_interval_sec: float = 4.0
    offline_timeout_sec: float = 12.0
    scan_interval_sec: float = 20.0
    discovery_port: int = DISCOVERY_PORT
    receiver_port: int = RECEIVER_PORT
    # storage
    db_path: str = "voice_memory.db"
    retention_days: int = 90
    targets: dict[str, TargetConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "Config":
        cfg = cls()
        p = Path(path)
        if p.exists():
            cfg._apply(json.loads(p.read_text(encoding="utf-8")))
        return cfg

    def _apply(self, raw: dict[str, Any]) -> None:
        server = raw.get("server", {})
        self.server_host = str(server.get("host", self.server_host))
        self.server_port = int(server.get("port", self.server_port))
        shandian = raw.get("shandianshuo", {})
        self.trigger_key = str(shandian.get("trigger_key", self.trigger_key))
        vh = raw.get("voicehub", {})
        self.pending_timeout_sec = float(vh.get("pending_timeout_sec", self.pending_timeout_sec))
        self.stability_ms = int(vh.get("stability_ms", self.stability_ms))
        self.heartbeat_interval_sec = float(vh.get("heartbeat_interval_sec", self.heartbeat_interval_sec))
        self.offline_timeout_sec = float(vh.get("offline_timeout_sec", self.offline_timeout_sec))
        self.scan_interval_sec = float(vh.get("scan_interval_sec", self.scan_interval_sec))
        self.discovery_port = int(vh.get("discovery_port", self.discovery_port))
        self.receiver_port = int(vh.get("receiver_port", self.receiver_port))
        storage = raw.get("storage", {})
        self.db_path = str(storage.get("db_path", self.db_path))
        self.retention_days = int(storage.get("retention_days", self.retention_days))
        for key, data in dict(raw.get("targets", {})).items():
            self.targets[key] = TargetConfig.from_dict(key, data)

    def target_by_hotkey(self, hotkey: str) -> TargetConfig | None:
        """按热键数字查目标，如 '2' -> laptop。"""
        for t in self.targets.values():
            if t.hotkey == hotkey:
                return t
        return None


def load(path: str | Path = "config.json") -> Config:
    """便捷入口。"""
    return Config.load(path)
