"""V4/M11 语音活动检测（纯逻辑，TDD）。

能量法：说话判定 = 音频块 RMS 超过阈值。自动停止条件（满足其一）：
1. 已说过话，且连续静音达到 `silence_ms`（说完自动收尾，免再按一次）。
2. 从未说过话，持续超过 `lead_in_ms`（按了触发键又不开口，安静放弃，不消耗 ASR）。
3. 总时长超过 `max_duration_ms`（长语音硬上限）。

时间轴由「帧数 × frame_ms」驱动（不依赖墙钟），喂入确定性数据即可复现判定。
"""
from __future__ import annotations

from typing import Optional


class VadTracker:
    """逐帧喂 RMS，判定是否应自动停止录音。"""

    def __init__(
        self,
        silence_ms: int = 1500,
        threshold: float = 0.012,
        lead_in_ms: int = 10000,
        max_duration_ms: int = 60000,
        frame_ms: int = 20,
    ) -> None:
        self.frame_ms = frame_ms
        self._silence_frames = max(1, silence_ms // frame_ms)
        self._lead_in_frames = max(1, lead_in_ms // frame_ms)
        self._max_frames = max(1, max_duration_ms // frame_ms)
        self._threshold = threshold
        self._frames = 0
        self._consecutive_silence = 0
        self._has_spoken = False
        self._stopped = False
        self._stop_reason: Optional[str] = None

    # ---------- 事件 ----------
    def feed(self, rms: float) -> Optional[str]:
        """喂入一帧 RMS（16bit 归一化振幅）。触发自动停止时返回原因，其余返回 None。"""
        if self._stopped:
            return self._stop_reason
        self._frames += 1
        if rms >= self._threshold:
            self._has_spoken = True
            self._consecutive_silence = 0
        else:
            self._consecutive_silence += 1
        reason = self._check()
        if reason:
            self._stopped = True
            self._stop_reason = reason
        return self._stop_reason

    # ---------- 查询 ----------
    def has_spoken(self) -> bool:
        return self._has_spoken

    def elapsed_ms(self) -> int:
        return self._frames * self.frame_ms

    # ---------- 判定 ----------
    def _check(self) -> Optional[str]:
        if self._frames >= self._max_frames:
            return "max_duration"
        if self._has_spoken and self._consecutive_silence >= self._silence_frames:
            return "silence"
        if not self._has_spoken and self._frames >= self._lead_in_frames:
            return "no_speech"
        return None
