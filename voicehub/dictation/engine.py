"""V4/M11 听写引擎：状态机 idle→recording→processing→idle（ADR-9）。

职责与线程模型：
- `toggle()` 由托盘菜单 / 热键 / Web 触发（可能来自不同线程），内部加锁串行化：
  idle → recording（开麦）；recording → processing（停麦 + 起 worker 线程做
  ASR + 直通路由）；processing 期间 toggle 忽略（防重复触发）。
- 会话令牌（generation）：每次 start/cancel 自增；worker 各步骤前校验，
  旧会话的迟到回调不会污染新会话（架构约束「异步流程必须带 token guard」）。
- 副作用全部注入（recorder / provider / route），纯逻辑可测；VAD 自动停止
  从音频回调线程经 `_on_auto_stop` 汇入（仅置请求，处理在 worker）。

对外状态（托盘 tooltip / 仪表盘用）：
- state: idle | recording | processing
- last_result: {"ok", "text"|"error", "elapsed_ms", ...} 最近一次结果（含失败原因）
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"


class DictationEngine:
    """听写引擎门面：组合 recorder + provider + 直通路由回调。"""

    def __init__(
        self,
        recorder,
        provider,
        route: Callable[[str, dict], dict],
        on_state_change: Optional[Callable[[str], None]] = None,
        max_duration_sec: float = 60.0,
    ) -> None:
        self._recorder = recorder
        self._provider = provider
        self._route = route
        self._on_state_change = on_state_change
        self._max_duration_sec = max_duration_sec
        # RLock：_set_state_locked 持锁触发 UI 回调，回调可能回读 state()
        self._lock = threading.RLock()
        self._state = STATE_IDLE
        self._gen = 0
        self._worker: Optional[threading.Thread] = None
        self._last_result: Optional[dict] = None

    # ---------- 查询 ----------
    def state(self) -> str:
        with self._lock:
            return self._state

    def last_result(self) -> Optional[dict]:
        with self._lock:
            return dict(self._last_result) if self._last_result else None

    def set_on_state_change(self, cb: Callable[[str], None]) -> None:
        """运行期挂状态回调（托盘标签跟随）；组装顺序在平台后端，晚于引擎构造。"""
        self._on_state_change = cb

    # ---------- 触发入口 ----------
    def toggle(self) -> str:
        """托盘/热键统一入口：返回触发后的状态（供 UI 即时反馈）。"""
        with self._lock:
            if self._state == STATE_IDLE:
                return self._start_locked()
            if self._state == STATE_RECORDING:
                return self._stop_and_process_locked()
            return self._state  # processing：忽略

    def cancel(self) -> None:
        """放弃当前录音（不送 ASR）。"""
        with self._lock:
            if self._state != STATE_RECORDING:
                return
            self._gen += 1  # 使可能迟到的自动停止失效
            try:
                self._recorder.stop()
            except Exception:  # noqa: BLE001
                logger.exception("取消录音时关麦异常")
            self._set_result_locked({"ok": False, "error": "已取消"})
            self._set_state_locked(STATE_IDLE)
            logger.info("听写已取消")

    # ---------- 状态迁移（持锁调用） ----------
    def _start_locked(self) -> str:
        self._gen += 1
        gen = self._gen
        try:
            self._recorder.start()
        except Exception as e:  # noqa: BLE001 - 无麦克风/声卡被占：不上状态，留原因
            logger.error("无法开始录音: %s", e)
            self._set_result_locked({"ok": False, "error": f"无法开始录音: {e}"})
            return self._state
        self._set_state_locked(STATE_RECORDING)
        if self._max_duration_sec > 0:
            threading.Timer(self._max_duration_sec, self._watchdog_stop, args=(gen,)).start()
        return self._state

    def _stop_and_process_locked(self) -> str:
        gen = self._gen
        self._set_state_locked(STATE_PROCESSING)
        self._worker = threading.Thread(
            target=self._process, args=(gen,), name="dictation-process", daemon=True)
        self._worker.start()
        return self._state

    def _set_state_locked(self, state: str) -> None:
        self._state = state
        cb = self._on_state_change
        if cb is not None:
            try:
                cb(state)
            except Exception:  # noqa: BLE001 - UI 回调异常不影响引擎
                logger.exception("on_state_change 回调异常")

    def _set_result_locked(self, result: dict) -> None:
        self._last_result = result

    # ---------- VAD 自动停止（音频回调线程 → 这里只置请求） ----------
    def request_stop(self, reason: str = "manual") -> None:
        """线程安全的「停止并送识别」请求（VAD / 看门狗 / 外部触发共用）。"""
        logger.info("停止录音请求: %s", reason)
        with self._lock:
            if self._state != STATE_RECORDING:
                return
            self._stop_and_process_locked()

    def _watchdog_stop(self, gen: int) -> None:
        with self._lock:
            if self._gen != gen or self._state != STATE_RECORDING:
                return
        self.request_stop("max_duration")

    # ---------- worker：ASR + 直通路由 ----------
    def _process(self, gen: int) -> None:
        started = time.monotonic()
        try:
            pcm = self._recorder.stop()
        except Exception as e:  # noqa: BLE001
            logger.exception("停止录音失败")
            with self._lock:
                if self._gen == gen:
                    self._set_result_locked({"ok": False, "error": f"停止录音失败: {e}"})
                    self._set_state_locked(STATE_IDLE)
            return
        if self._gen != gen:  # 会话已被取消/重启，丢弃
            logger.info("旧会话音频已丢弃（gen=%s）", gen)
            return
        if not pcm.strip(b"\x00"):
            with self._lock:
                if self._gen == gen:
                    self._set_result_locked({"ok": False, "error": "未采集到音频（静音）"})
                    self._set_state_locked(STATE_IDLE)
            return
        if hasattr(self._recorder, "had_speech") and not self._recorder.had_speech():
            # VAD 判定从未说话（lead-in 超时）：跳过 ASR，不浪费一次云端调用
            with self._lock:
                if self._gen == gen:
                    self._set_result_locked({"ok": False, "error": "未检测到语音（安静）"})
                    self._set_state_locked(STATE_IDLE)
            return
        elapsed_rec = int((time.monotonic() - started) * 1000)
        try:
            text = self._provider.transcribe(pcm).strip()
        except Exception as e:  # noqa: BLE001 - ASR 失败要落到结果与日志，不崩线程
            logger.error("ASR 失败: %s", e)
            with self._lock:
                if self._gen == gen:
                    self._set_result_locked({"ok": False, "error": str(e)})
                    self._set_state_locked(STATE_IDLE)
            return
        if self._gen != gen:
            return
        if not text:
            with self._lock:
                if self._gen == gen:
                    self._set_result_locked({"ok": False, "error": "识别结果为空"})
                    self._set_state_locked(STATE_IDLE)
            return
        result = self._route(text, {"record_ms": elapsed_rec})
        with self._lock:
            if self._gen == gen:
                self._set_result_locked({"ok": bool(result.get("ok")), "text": text,
                                         **{k: v for k, v in result.items()
                                            if k in ("target", "error", "log_id")}})
                self._set_state_locked(STATE_IDLE)
        logger.info("听写完成: %s 字 -> %s", len(text), result.get("target"))
