"""V4/M11 麦克风录音器：sounddevice(PortAudio) 回调式采集 int16 PCM。

设计：
- 采集 / VAD / 自动停止判定全部在 PortAudio 回调线程内完成（追加速度要快），
  自动停止通过 `on_auto_stop` 回调交回引擎线程处理（回调里只置事件，不做重活）。
- sounddevice 延迟 import（打包环境缺 PortAudio 时引擎降级为「听写不可用」，
  其余功能不受影响）；`open_stream` 可注入假实现，单测无需真实声卡。
- 只支持 16kHz / 单声道 / int16（ASR 输入格式，spike 定案）。
"""
from __future__ import annotations

import logging
import subprocess
import threading
from array import array
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def pcm_rms(pcm: bytes) -> float:
    """int16 小端 PCM 块的 RMS（归一化到 [-1,1] 振幅）。纯函数可单测。"""
    if not pcm:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return (total / len(samples)) ** 0.5 / 32768.0


class MicrophoneRecorder:
    """一次 start()/stop() 生命周期 = 一段完整 PCM（线程安全由引擎层串行保证）。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 20,
        vad: Optional[object] = None,
        on_auto_stop: Optional[Callable[[str], None]] = None,
        open_stream: Optional[Callable] = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._block_ms = block_ms
        self._vad = vad
        self._on_auto_stop = on_auto_stop
        self._open_stream = open_stream or self._open_sounddevice_stream
        self._buffer = bytearray()
        self._stream = None
        self._lock = threading.Lock()
        self._auto_stop_fired = False
        self._on_level: Optional[Callable[[float], None]] = None

    def set_auto_stop_callback(self, cb: Callable[[str], None]) -> None:
        """后挂自动停止回调（引擎在构造 recorder 之后才存在，避免循环依赖）。"""
        self._on_auto_stop = cb

    def set_level_callback(self, cb: Callable[[float], None]) -> None:
        """后挂电平回调（悬浮框波形用；音频线程调用，接收方须自带线程安全）。"""
        self._on_level = cb

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """打开输入流开始采集；失败（无麦克风/无 PortAudio）抛 RuntimeError。"""
        blocksize = self._sample_rate * self._block_ms // 1000
        self._buffer = bytearray()
        self._auto_stop_fired = False
        if self._vad is not None and hasattr(self._vad, "reset"):
            self._vad.reset()  # 会话间状态残留会导致秒停（见 VadTracker.reset）
        try:
            self._stream = self._open_stream(blocksize, self._on_audio)
        except Exception as e:  # noqa: BLE001 - 声卡/驱动问题统一走引擎降级
            raise RuntimeError(f"无法打开麦克风输入: {e}") from e
        logger.info("录音已开始（%dHz 单声道，%dms 块）", self._sample_rate, self._block_ms)

    def stop(self) -> bytes:
        """关闭输入流并返回整段 PCM（含自动停止后未及时关闭的尾部）。"""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - 关流失败不影响取数据
                logger.exception("关闭输入流异常")
        with self._lock:
            pcm = bytes(self._buffer)
            self._buffer = bytearray()
        logger.info("录音已停止，共 %.2fs / %d 字节", len(pcm) / 2 / self._sample_rate, len(pcm))
        return pcm

    def is_recording(self) -> bool:
        return self._stream is not None

    def had_speech(self) -> bool:
        """本次录音是否检测到过语音（无 VAD 时恒 True，由引擎决定是否送 ASR）。"""
        return True if self._vad is None else bool(self._vad.has_spoken())

    # ---------- 回调（PortAudio 线程） ----------
    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            logger.debug("音频回调状态: %s", status)
        pcm = bytes(indata)
        with self._lock:
            self._buffer.extend(pcm)
        rms = pcm_rms(pcm)
        if self._on_level is not None:
            try:
                self._on_level(rms)
            except Exception:  # noqa: BLE001 - 悬浮框回调异常不能杀音频线程
                logger.exception("on_level 回调异常")
        if self._vad is None or self._auto_stop_fired:
            return
        reason = self._vad.feed(rms)
        if reason is not None:
            self._auto_stop_fired = True
            if self._on_auto_stop is not None:
                try:
                    self._on_auto_stop(reason)
                except Exception:  # noqa: BLE001 - 引擎回调异常不能杀音频线程
                    logger.exception("on_auto_stop 回调异常")

    def _open_sounddevice_stream(self, blocksize: int, callback: Callable):
        try:
            import sounddevice  # 延迟导入：打包环境可能缺 PortAudio
        except OSError as e:
            # PortAudio 系统库缺失（openKylin 实测）：Linux 回退 arecord 子进程后端
            stream = _try_alsa_stream(self._sample_rate, blocksize, callback)
            if stream is not None:
                return stream
            raise RuntimeError(f"PortAudio 不可用且无 arecord 回退: {e}") from e
        return sounddevice.RawInputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            blocksize=blocksize,
            callback=callback,
        )


class _AlsaPcmStream:
    """arecord 子进程后端：stdout 读线程模拟 PortAudio 回调。

    动机（2026-08-25 openKylin 实机）：PortAudio 系统库缺失且无 sudo 安装；
    ALSA 是 Linux 音频最底层公共依赖，arecord 拉原始 PCM 零额外 so 依赖，
    也免去 AppImage 收编 PortAudio（M13 打包友好）。接口对齐
    sounddevice.RawInputStream 的 close()（录音数据经 callback 回灌）。
    """

    def __init__(self, sample_rate: int, block_frames: int, on_data: Callable[[bytes], None]) -> None:
        import subprocess

        self._proc = subprocess.Popen(
            ["arecord", "-q", "-N", "-t", "raw", "-f", "S16_LE",
             "-r", str(sample_rate), "-c", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._block_bytes = block_frames * 2
        self._on_data = on_data
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="alsa-pcm-reader", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        stdout = self._proc.stdout
        while not self._stop.is_set():
            chunk = stdout.read(self._block_bytes)
            if not chunk:
                break  # 进程退出/管道关闭
            self._on_data(chunk)

    def close(self) -> None:
        self._stop.set()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:  # type: ignore[name-defined]
            self._proc.kill()
        self._thread.join(timeout=2)


def _try_alsa_stream(sample_rate: int, block_frames: int,
                     callback: Callable) -> Optional[_AlsaPcmStream]:
    """arecord 可用时返回子进程流（回调签名桥接到 PortAudio 形态），否则 None。"""
    import shutil

    if not shutil.which("arecord"):
        return None

    def _on_data(pcm: bytes) -> None:
        callback(pcm, len(pcm) // 2, None, None)

    try:
        return _AlsaPcmStream(sample_rate, block_frames, _on_data)
    except OSError as e:
        logger.warning("arecord 后端启动失败: %s", e)
        return None
