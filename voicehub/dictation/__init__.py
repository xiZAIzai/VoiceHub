"""V4 自建转写内核（ADR-9）：录音 → 云 ASR → orchestrator 直通路由。

- vad.py：纯逻辑语音活动检测（静音自动停）。
- recorder.py：sounddevice 采集 int16 PCM（延迟导入，可注入）。
- asr_client.py：火山豆包 openspeech WS v3 客户端 + AsrProvider 协议。
- engine.py：状态机 idle→recording→processing（会话令牌防旧回调污染）。
"""
from .engine import DictationEngine, STATE_IDLE, STATE_PROCESSING, STATE_RECORDING
from .vad import VadTracker

__all__ = [
    "DictationEngine",
    "STATE_IDLE",
    "STATE_PROCESSING",
    "STATE_RECORDING",
    "VadTracker",
]
