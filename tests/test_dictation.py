"""V4/M11 听写内核单测：VAD / ASR 帧协议 / 引擎状态机 / 录音器纯函数。"""

import gzip
import json
import struct
import threading

import pytest

from voicehub.dictation import VadTracker
from voicehub.dictation.asr_client import (
    AsrError,
    VolcengineSaucClient,
    build_request_payload,
    encode_audio_packet,
    encode_full_request,
    parse_frame,
)
from voicehub.dictation.engine import (
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_RECORDING,
    DictationEngine,
)
from voicehub.dictation.recorder import MicrophoneRecorder, pcm_rms


# ---------- VAD ----------

def _frames(ms: int, frame_ms: int = 20) -> int:
    return ms // frame_ms


def test_vad_silence_after_speech_stops():
    v = VadTracker(silence_ms=1000, threshold=0.01, lead_in_ms=10000,
                   max_duration_ms=60000)
    reason = None
    for _ in range(_frames(500)):
        reason = v.feed(0.05)  # 说话
    assert reason is None and v.has_spoken()
    for _ in range(_frames(999)):
        reason = v.feed(0.001)  # 静音 999ms，未到
    assert reason is None
    assert v.feed(0.001) == "silence"  # 第 1000ms 静音触发


def test_vad_leadin_timeout_when_never_speaking():
    v = VadTracker(silence_ms=1000, threshold=0.01, lead_in_ms=2000,
                   max_duration_ms=60000)
    reason = None
    for _ in range(_frames(2000)):
        reason = v.feed(0.0)
    assert reason == "no_speech"


def test_vad_max_duration_wins_over_silence():
    v = VadTracker(silence_ms=60000, threshold=0.01, lead_in_ms=10000,
                   max_duration_ms=2000)
    reason = None
    for _ in range(_frames(2000)):
        reason = v.feed(0.05)
    assert reason == "max_duration"


def test_vad_stops_only_once():
    v = VadTracker(silence_ms=1000, threshold=0.01, lead_in_ms=10000,
                   max_duration_ms=60000)
    v.feed(0.05)
    for _ in range(_frames(1000)):
        r = v.feed(0.0)
    assert r == "silence"
    assert v.feed(0.9) == "silence"  # 已停止后恒返回同一原因


# ---------- 录音器纯函数 ----------

def test_pcm_rms_silent_and_loud():
    silent = b"\x00\x00" * 160
    assert pcm_rms(silent) == 0.0
    loud = struct.pack("<h", 32767) * 160
    assert pcm_rms(loud) == pytest.approx(32767 / 32768, abs=1e-9)
    assert pcm_rms(b"") == 0.0


def test_pcm_rms_odd_bytes_safe():
    assert pcm_rms(b"\x01\x02\x03") >= 0.0  # 单字节尾巴安全丢弃


# ---------- ASR 帧协议 ----------

def test_encode_full_request_header_layout():
    frame = encode_full_request({"user": {"uid": "t"}})
    assert frame[0] == 0x11          # version=1, header size=1
    assert frame[1] == 0x10          # full client request, flags=0
    assert frame[2] == 0x11          # serialization=JSON, compression=gzip
    assert frame[3] == 0x00
    size = struct.unpack(">I", frame[4:8])[0]
    assert len(frame) == 8 + size
    assert gzip.decompress(frame[8:]) == json.dumps({"user": {"uid": "t"}}).encode()


def test_encode_audio_packet_flags():
    normal = encode_audio_packet(b"\x01\x02", last=False)
    assert normal[1] == 0x20 and normal[2] == 0x00
    assert normal[4:8] == struct.pack(">I", 2)
    last = encode_audio_packet(b"", last=True)
    assert last[1] == 0x22  # 末包负包 flags=0b0010


def _response_frame(text: str, flags: int, seq: int = 1) -> bytes:
    payload = gzip.compress(json.dumps({"result": {"text": text}}).encode())
    header = bytes([0x11, (0x9 << 4) | flags, 0x11, 0x00])
    return header + struct.pack(">I", seq) + struct.pack(">I", len(payload)) + payload


def _error_frame(code: int, message: str) -> bytes:
    payload = json.dumps({"message": message}).encode()
    return (bytes([0x11, 0xF0, 0x01, 0x00]) + struct.pack(">I", code)
            + struct.pack(">I", len(payload)) + payload)


class _ByteReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def __call__(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        assert len(chunk) == n, "测试数据不足"
        self._pos += n
        return chunk


def test_parse_frame_response_and_error():
    kind, flags, payload = parse_frame(_ByteReader(_response_frame("你好", 0b0011)))
    assert kind == "response" and flags == 0b0011
    assert payload["result"]["text"] == "你好"
    kind, code, payload = parse_frame(_ByteReader(_error_frame(45000001, "invalid key")))
    assert kind == "error" and code == 45000001
    assert payload["message"] == "invalid key"


def test_build_request_payload_language():
    assert "language" not in build_request_payload("auto")
    assert build_request_payload("zh")["request"]["language"] == "zh"


# ---------- ASR 客户端（假 WS 连接） ----------

class _FakeWs:
    """按脚本回放服务端行为的假连接；sent 记录全部发送帧。

    超时语义对齐真实 websocket-client：短超时（发送阶段排空）无数据抛
    WebSocketTimeoutException；长超时（等待最终结果）无数据视为连接关闭。
    """

    def __init__(self, script: bytes) -> None:
        self.sent: list[bytes] = []
        self._script = script
        self._pos = 0
        self._timeout = 30.0
        self.gate_sends: int | None = None  # 发满 N 帧前不吐响应（模拟真时序）
        self.closed = False

    def send_binary(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self) -> bytes:
        chunk = self._script[self._pos:self._pos + 4096]
        if chunk and self.gate_sends is not None and len(self.sent) < self.gate_sends:
            chunk = b""  # 未到放行时机：走无数据分支
        if not chunk:
            if self._timeout <= 0.5:
                from websocket import WebSocketTimeoutException

                raise WebSocketTimeoutException("timed out")
            raise AsrError("连接在响应途中关闭")
        self._pos += len(chunk)
        return chunk

    def settimeout(self, t: float) -> None:
        self._timeout = t

    def close(self) -> None:
        self.closed = True


def _client_with(ws: _FakeWs) -> VolcengineSaucClient:
    return VolcengineSaucClient(
        api_key="test-key", connect=lambda url, headers: ws,
        chunk_pause_sec=0.0)


def test_client_transcribe_returns_final_text():
    pcm = b"\x11\x22" * 16000  # 1 秒
    script = _response_frame("你好", 0b0001, 1) + _response_frame("你好世界", 0b0011, 2)
    ws = _FakeWs(script)
    # 服务端在收满全部音频（full request + 块 + 末包）后才回帧
    ws.gate_sends = 1 + (len(pcm) + 6399) // 6400 + 1
    text = _client_with(ws).transcribe(pcm)
    assert text == "你好世界"
    # 帧 0 是 full request（gzip JSON），其后音频块 + 末包负包
    assert ws.sent[0][:2] == b"\x11\x10"
    assert ws.sent[-1][1] == 0x22 and ws.sent[-1][4:8] == b"\x00\x00\x00\x00"
    audio = b"".join(f[8:] for f in ws.sent[1:-1])
    assert audio == pcm
    assert ws.closed


def test_client_auth_error_raises_without_retry():
    ws = _FakeWs(_error_frame(45000001, "invalid key"))
    with pytest.raises(AsrError) as ei:
        _client_with(ws).transcribe(b"\x00\x00" * 1600)
    assert "45000001" in str(ei.value)


def test_client_retries_once_on_transport_error():
    class _FlakyFactory:
        def __init__(self) -> None:
            self.calls = 0
            self.good = _FakeWs(_response_frame("重试成功", 0b0011))

        def __call__(self, url, headers):
            self.calls += 1
            if self.calls == 1:
                raise OSError("connection reset")
            return self.good

    client = VolcengineSaucClient(api_key="k", connect=_FlakyFactory(),
                                  chunk_pause_sec=0.0)
    assert client.transcribe(b"\x00\x00" * 3200) == "重试成功"


def test_client_handshake_rejected_maps_to_asr_error():
    import websocket

    class _Rejected:
        def __call__(self, url, headers):
            raise websocket.WebSocketBadStatusException(
                "Handshake status 401", None, None, None, None)

    client = VolcengineSaucClient(api_key="k", connect=_Rejected())
    with pytest.raises(AsrError) as ei:
        client.transcribe(b"\x00\x00" * 1600)
    assert "401" in str(ei.value)


# ---------- 引擎状态机（假 recorder / provider / route） ----------

class _FakeRecorder:
    def __init__(self, pcm: bytes = b"\x01\x02" * 1600) -> None:
        self.pcm = pcm
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> bytes:
        self.stopped += 1
        return self.pcm


class _FakeProvider:
    def __init__(self, text: str = "识别文本") -> None:
        self.text = text
        self.calls: list[bytes] = []

    def transcribe(self, pcm: bytes) -> str:
        self.calls.append(pcm)
        return self.text


class _RecorderThatFails:
    def start(self) -> None:
        raise RuntimeError("无麦克风")

    def stop(self) -> bytes:
        return b""


def _engine(recorder, provider, routed: list):
    def _route(text, metadata):
        routed.append((text, metadata))
        return {"ok": True, "target": "desktop"}

    return DictationEngine(recorder, provider, _route)


def _wait_idle(engine, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while engine.state() != STATE_IDLE:
        assert time.monotonic() < deadline, "引擎未回到 idle"
        time.sleep(0.01)


def test_engine_toggle_cycle_routes_text():
    routed = []
    rec, prov = _FakeRecorder(), _FakeProvider("你好世界")
    engine = _engine(rec, prov, routed)
    assert engine.state() == STATE_IDLE

    assert engine.toggle() == STATE_RECORDING
    assert rec.started == 1
    assert engine.toggle() == STATE_PROCESSING
    _wait_idle(engine)

    text, meta = routed[0]
    assert text == "你好世界"
    assert set(meta) == {"record_ms", "raw_text", "polish"}  # M12-① 起固定三键
    assert meta["raw_text"] == "你好世界" and meta["polish"] == "off"  # 未开润色
    assert prov.calls == [rec.pcm]
    result = engine.last_result()
    assert result["ok"] is True and result["text"] == "你好世界"
    assert result["target"] == "desktop"


def test_engine_state_change_callback_fires():
    states = []
    rec, prov = _FakeRecorder(), _FakeProvider()
    engine = DictationEngine(rec, prov, lambda t, m: {"ok": True},
                             on_state_change=states.append)
    engine.toggle()  # -> recording
    engine.toggle()  # -> processing
    _wait_idle(engine)
    assert states == [STATE_RECORDING, STATE_PROCESSING, STATE_IDLE]


def test_engine_recorder_failure_stays_idle_with_reason():
    engine = _engine(_RecorderThatFails(), _FakeProvider(), [])
    assert engine.toggle() == STATE_IDLE
    assert "无法开始录音" in engine.last_result()["error"]


def test_engine_cancel_discards_audio():
    routed = []
    rec, prov = _FakeRecorder(), _FakeProvider()
    engine = _engine(rec, prov, routed)
    engine.toggle()
    engine.cancel()
    assert engine.state() == STATE_IDLE
    assert rec.stopped == 1 and routed == [] and prov.calls == []


def test_engine_silent_pcm_short_circuits():
    routed = []
    rec = _FakeRecorder(pcm=b"\x00\x00" * 1600)  # 全零
    engine = _engine(rec, _FakeProvider(), routed)
    engine.toggle()
    engine.toggle()
    _wait_idle(engine)
    assert routed == []
    assert "静音" in engine.last_result()["error"]


def test_engine_request_stop_from_vad_thread():
    routed = []
    engine = _engine(_FakeRecorder(), _FakeProvider("VAD 停止"), routed)
    engine.toggle()
    # 模拟 VAD 回调（来自音频线程）
    threading.Thread(target=lambda: engine.request_stop("silence"), daemon=True).start()
    _wait_idle(engine)
    assert routed and routed[0][0] == "VAD 停止"


def test_engine_toggle_ignored_while_processing():
    rec, prov = _FakeRecorder(b"\x01\x02" * 16000), _FakeProvider("慢识别")
    engine = _engine(rec, prov, [])
    engine.toggle()
    assert engine.toggle() == STATE_PROCESSING
    assert engine.toggle() == STATE_PROCESSING  # 处理中忽略重复触发
    assert rec.started == 1
    _wait_idle(engine, timeout=5)


def test_engine_asr_failure_lands_in_last_result():
    class _BadProvider:
        def transcribe(self, pcm):
            raise AsrError("ASR 握手被拒: HTTP 401")

    engine = _engine(_FakeRecorder(), _BadProvider(), [])
    engine.toggle()
    engine.toggle()
    _wait_idle(engine)
    assert engine.last_result()["ok"] is False
    assert "401" in engine.last_result()["error"]


class _RecorderNoSpeech(_FakeRecorder):
    """带 had_speech()=False 的假录音器（VAD 判定从未说话）。"""

    def had_speech(self) -> bool:
        return False


def test_engine_skips_asr_when_vad_says_no_speech():
    routed = []
    prov = _FakeProvider()
    engine = _engine(_RecorderNoSpeech(), prov, routed)
    engine.toggle()
    engine.toggle()
    _wait_idle(engine)
    assert routed == [] and prov.calls == []  # ASR 未被调用
    assert "未检测到语音" in engine.last_result()["error"]


# ---------- 双鉴权方案（2026-08-26 旧版控制台凭证实测定案） ----------

def test_client_headers_legacy_scheme_preferred():
    client = VolcengineSaucClient(api_key="new-key", app_key="1234567890",
                                  access_key="legacy-token")
    h = client._headers()
    assert h["X-Api-App-Key"] == "1234567890"
    assert h["X-Api-Access-Key"] == "legacy-token"
    assert "X-Api-Key" not in h  # 旧版方案时不发新版头
    assert h["X-Api-Resource-Id"] == "volc.seedasr.sauc.duration"


def test_client_headers_new_console_scheme():
    client = VolcengineSaucClient(api_key="new-key")
    h = client._headers()
    assert h["X-Api-Key"] == "new-key"
    assert "X-Api-App-Key" not in h and "X-Api-Access-Key" not in h


def test_client_missing_credentials_raises():
    with pytest.raises(AsrError):
        VolcengineSaucClient()
    with pytest.raises(AsrError):
        VolcengineSaucClient(app_key="only-app-key")


def test_engine_cancel_sets_cancelled_result():
    engine = _engine(_FakeRecorder(), _FakeProvider(), [])
    engine.toggle()
    engine.cancel()
    assert engine.last_result() == {"ok": False, "error": "已取消"}


def test_vad_reset_clears_session_state():
    """openKylin 实测事故回归：静音计数跨会话残留导致新会话秒停。"""
    v = VadTracker(silence_ms=1000, threshold=0.01, lead_in_ms=10000,
                   max_duration_ms=60000)
    v.feed(0.05)
    for _ in range(75):  # 1.5s 静音 → 触发 silence 停止
        v.feed(0.0)
    assert v.feed(0.0) == "silence"
    v.reset()
    assert v.has_spoken() is False and v.elapsed_ms() == 0
    # 重置后新会话不会立即停止
    for _ in range(100):
        assert v.feed(0.0) is None


def test_client_long_audio_flood_of_frames():
    """长语音回归（2026-08-26 用户实测）：服务端逐块回帧，超 64 帧且发送期间
    需持续排空，否则 socket 缓冲堵塞死锁 / 帧数上限误报。"""
    pcm = b"\x11\x22" * 16000 * 10  # 10 秒
    script = b"".join(
        _response_frame("段", 0b0001, seq=i) for i in range(200)) + \
        _response_frame("长语音完整结果", 0b0011, seq=999)
    ws = _FakeWs(script)
    text = _client_with(ws).transcribe(pcm)
    assert text == "长语音完整结果"
    assert ws.closed


def test_vad_silence_disabled():
    """silence_ms<=0：说完静音也不自动停（用户偏好手动截断），仅受总上限约束。"""
    v = VadTracker(silence_ms=0, threshold=0.01, lead_in_ms=10000,
                   max_duration_ms=60000)
    v.feed(0.05)
    reason = None
    for _ in range(2000):  # 40s 静音（< 60s 总上限）
        reason = v.feed(0.0)
        if reason:
            break
    assert reason is None  # 静音停被禁用


def test_overlay_place_x_follows_cursor():
    from voicehub.dictation.overlay import place_x

    assert place_x(960, 1920) == 960 - 140          # 光标居中
    assert place_x(10, 1920) == 0                    # 左缘夹紧
    assert place_x(1910, 1920) == 1920 - 280         # 右缘夹紧
    # 双屏合并 3840：光标在右屏中段 → 窗口落在右屏内，不再骑缝
    assert place_x(2880, 3840) == 2880 - 140


def test_overlay_bar_height_agc():
    """自适应增益：小电平说话也满幅（2026-08-26 波形不动根因回归）。"""
    from voicehub.dictation.overlay import bar_height

    quiet_speech = 0.02
    assert bar_height(quiet_speech, peak=quiet_speech) >= 40   # 峰值电平=满幅
    assert bar_height(quiet_speech / 2, peak=quiet_speech) >= 20  # 半峰值≈半幅
    assert bar_height(0.0002, peak=0.02) == 3                  # 底噪=最小条
    assert bar_height(0.9, peak=0.02) == bar_height(0.02, peak=0.02)  # 超峰截断
    assert bar_height(0.5, peak=0) > 3                          # peak 异常兜底


def test_overlay_peak_tracks_and_decays():
    from voicehub.dictation.overlay import WaveformOverlay

    o = WaveformOverlay()
    o.update_level(0.05)  # 说话
    assert abs(o._peak - 0.05) < 1e-9  # noqa: SLF001 - 峰值跟随
    for _ in range(200):   # 4 秒静音
        o.update_level(0.0)
    assert o._peak <= 0.02 + 1e-9  # noqa: SLF001 - 回落到地板
