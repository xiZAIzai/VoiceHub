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
import os
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
class TranscriptionConfig:
    """V4/ADR-9 自建转写内核（builtin 引擎）配置。

    - `engine`：shandianshuo（默认，剪贴板监听链路）| builtin（自建录音 → 云 ASR → 直通）。
    - 密钥安全：种子 config.json 恒不放密钥；真实值走同目录 config.local.json
      （深度合并覆盖）或环境变量 `VOICEHUB_ASR_API_KEY`（最高优先级）。
    - 端点/资源默认值即 2026-08-25 spike 实测结论（豆包语音 openspeech WS v3）。
    """

    engine: str = "shandianshuo"
    provider: str = "volcengine_sauc"
    base_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    resource_id: str = "volc.seedasr.sauc.duration"
    api_key: str = ""
    language: str = "auto"
    # builtin 独立触发键（pynput/keyboard 组合格式，空 = 禁用热键，托盘菜单仍可用）
    trigger_key: str = "ctrl+alt+v"
    # 无粘滞目标时直通路由的默认目标；空 = 第一个 local 目标
    default_target: str = ""
    # 录音参数
    sample_rate: int = 16000
    # VAD：静音自动停（说过话后连续静音判定结束）；lead_in 为从未说话的最长等待
    vad_silence_ms: int = 1500
    vad_threshold: float = 0.012
    vad_lead_in_ms: int = 10000
    max_duration_sec: float = 60.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranscriptionConfig":
        def _get(key: str, cast, default):
            v = data.get(key, default)
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        cfg = cls()
        cfg.engine = str(data.get("engine", cfg.engine))
        cfg.provider = str(data.get("provider", cfg.provider))
        cfg.base_url = str(data.get("base_url", cfg.base_url))
        cfg.resource_id = str(data.get("resource_id", cfg.resource_id))
        cfg.api_key = str(data.get("api_key", cfg.api_key))
        cfg.language = str(data.get("language", cfg.language))
        cfg.trigger_key = str(data.get("trigger_key", cfg.trigger_key))
        cfg.default_target = str(data.get("default_target", cfg.default_target))
        cfg.sample_rate = _get("sample_rate", int, cfg.sample_rate)
        cfg.vad_silence_ms = _get("vad_silence_ms", int, cfg.vad_silence_ms)
        cfg.vad_threshold = _get("vad_threshold", float, cfg.vad_threshold)
        cfg.vad_lead_in_ms = _get("vad_lead_in_ms", int, cfg.vad_lead_in_ms)
        cfg.max_duration_sec = _get("max_duration_sec", float, cfg.max_duration_sec)
        return cfg


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：override 覆盖 base，dict 深合并、其余类型整体替换。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    """全局配置，字段均有默认值，缺失 config.json 也能运行。"""

    server_host: str = "127.0.0.1"
    # 默认 8765：8000 是 Triton（openKylin 自带 AI 推理服务 kytensor 即用）等
    # 常见服务端口，2026-08-25 openKylin 实机实测 bind 冲突（uvicorn address in use）。
    server_port: int = 8765
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
    # V4/ADR-9 自建转写内核
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "Config":
        cfg = cls()
        p = Path(path)
        raw: dict[str, Any] = {}
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
        # config.local.json（gitignore）：同目录本地覆盖（放密钥等敏感值），深度合并
        local = p.parent / "config.local.json"
        if local.exists():
            try:
                raw = deep_merge(raw, json.loads(local.read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                # 本地配置损坏不阻塞启动（退回仅种子配置），但要可见
                import logging
                logging.getLogger(__name__).warning("config.local.json 解析失败，已忽略: %s", e)
        # 环境变量密钥最高优先级（CI/容器场景）
        env_key = os.environ.get("VOICEHUB_ASR_API_KEY")
        if env_key:
            raw = deep_merge(raw, {"transcription": {"api_key": env_key}})
        cfg._apply(raw)
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
        tc = raw.get("transcription")
        if isinstance(tc, dict) and tc:
            self.transcription = TranscriptionConfig.from_dict(tc)

    def target_by_hotkey(self, hotkey: str) -> TargetConfig | None:
        """按热键数字查目标，如 '2' -> laptop。"""
        for t in self.targets.values():
            if t.hotkey == hotkey:
                return t
        return None


def load(path: str | Path = "config.json") -> Config:
    """便捷入口。"""
    return Config.load(path)
