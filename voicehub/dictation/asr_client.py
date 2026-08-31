"""V4/M11 云 ASR 客户端：火山豆包 openspeech WebSocket v3 二进制协议（ADR-9）。

协议（2026-08-25 spike 实测定案，官方文档 docs/6561/1354869）：
- 端点默认 `sauc/bigmodel_nostream`（流式发送、收尾统一返回，准确率优先）。
- 鉴权：新版控制台仅需 `X-Api-Key`（无 appid）+ `X-Api-Resource-Id`。
- 二进制帧：4B header(version=1,size=1 | type<<4|flags | serial<<4|compression | 0x00)
  + full client request: 4B size + gzip(JSON)   type=0x1 flags=0x0 serial=JSON comp=gzip
  + audio only:          4B size + raw PCM       type=0x2 flags=0x0/0b0010(末包) raw
  + server response:     4B seq + 4B size + gzip(JSON)   type=0x9
  + error:               4B code + 4B size + JSON        type=0xF

帧编解码为纯函数（单测直接喂字节流）；WS 连接工厂可注入（单测无需网络）。
"""
from __future__ import annotations

import gzip
import json
import logging
import struct
import time
import uuid
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
DEFAULT_RESOURCE_ID = "volc.seedasr.sauc.duration"  # 豆包流式语音识别 2.0（小时版）
SAMPLE_RATE = 16000

# websocket-client 超时异常（发送阶段排空用，短超时属正常轮空）
try:  # websocket 延迟导入，模块加载期探测一次
    from websocket import WebSocketTimeoutException

    _TIMEOUT_ERRORS = (WebSocketTimeoutException,)
except ImportError:  # 无依赖环境（单测注入假连接）退化为通用超时
    import socket as _socket

    _TIMEOUT_ERRORS = (_socket.timeout,)


class AsrError(RuntimeError):
    """ASR 调用失败（含服务端错误码）。"""

    def __init__(self, message: str, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code


# ---------- 帧编解码（纯函数，勿改字节布局） ----------

def frame_header(msg_type: int, flags: int, serial: int, comp: int) -> bytes:
    return bytes([0x11, (msg_type << 4) | flags, (serial << 4) | comp, 0x00])


def encode_full_request(payload: dict) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    return frame_header(0x1, 0x0, 0x1, 0x1) + struct.pack(">I", len(body)) + body


def encode_audio_packet(pcm: bytes, last: bool) -> bytes:
    return frame_header(0x2, 0b0010 if last else 0x0, 0x0, 0x0) + struct.pack(">I", len(pcm)) + pcm


def parse_frame(read: Callable[[int], bytes]):
    """从 read(n) 读一帧：返回 ("response", flags, dict) / ("error", code, dict)。

    服务端响应：header + seq(4B) + size(4B) + gzip JSON；
    错误帧：header + code(4B) + size(4B) + JSON。
    """
    h = read(4)
    msg_type, flags = h[1] >> 4, h[1] & 0xF
    first = struct.unpack(">I", read(4))[0]  # response=sequence / error=code
    size = struct.unpack(">I", read(4))[0]
    payload = read(size)
    if msg_type == 0xF:
        return ("error", first, json.loads(_maybe_gunzip(payload)))
    return ("response", flags, json.loads(_maybe_gunzip(payload)))


def _maybe_gunzip(data: bytes) -> bytes:
    try:
        return gzip.decompress(data)
    except OSError:
        return data


def build_request_payload(language: str = "auto") -> dict:
    """full client request 的 JSON 载荷（与 spike 探针一致的最小稳定形态）。"""
    request: dict = {"model_name": "bigmodel", "enable_punc": True, "enable_itn": True}
    if language and language != "auto":
        request["language"] = language
    return {
        "user": {"uid": "voicehub"},
        "audio": {"format": "pcm", "rate": SAMPLE_RATE, "bits": 16, "channel": 1},
        "request": request,
    }


# ---------- Provider 抽象 ----------

class AsrProvider(Protocol):
    """转写提供方接口：PCM 进、文本出。新增厂商实现本协议即可接入引擎。"""

    def transcribe(self, pcm: bytes) -> str: ...


class VolcengineSaucClient:
    """火山豆包 sauc WS v3 客户端（默认 nostream 模式）。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_URL,
        resource_id: str = DEFAULT_RESOURCE_ID,
        language: str = "auto",
        connect_timeout_sec: float = 10.0,
        recv_timeout_sec: float = 30.0,
        # nostream 模式上传 pacing 无讲究（服务端收完才识别），大块快发把
        # 上传耗时从音频时长量级压到亚秒（2026-08-27 实测 10s 音频 4.7→2.2s）
        chunk_ms: int = 500,
        chunk_pause_sec: float = 0.0,
        connect: Optional[Callable[[str, dict], object]] = None,
        app_key: str = "",
        access_key: str = "",
    ) -> None:
        # 鉴权二选一（2026-08-26 实测）：旧版控制台 app_key+access_key 优先，
        # 新版控制台 api_key 单头；都没有时调用即抛错
        self._api_key = api_key
        self._app_key = app_key
        self._access_key = access_key
        if not (self._app_key and self._access_key) and not self._api_key:
            raise AsrError("缺少 ASR 凭证（api_key 或 app_key+access_key）")
        self._url = base_url
        self._resource_id = resource_id
        self._language = language
        self._connect_timeout = connect_timeout_sec
        self._recv_timeout = recv_timeout_sec
        self._chunk_bytes = SAMPLE_RATE * 2 * chunk_ms // 1000
        self._chunk_pause = chunk_pause_sec
        self._connect = connect or self._websocket_connect

    # ---------- 对外 ----------
    def transcribe(self, pcm: bytes) -> str:
        """PCM → 文本。连接层瞬时失败重试一次；鉴权/协议错误直接抛 AsrError。"""
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                return self._transcribe_once(pcm)
            except AsrError:
                raise  # 鉴权/服务端明确拒绝：重试无意义
            except Exception as e:  # noqa: BLE001 - 网络/超时类重试一次
                last_err = e
                logger.warning("ASR 连接失败（第 %s 次）: %s", attempt, e)
        raise AsrError(f"ASR 连接失败: {last_err}") from last_err

    # ---------- 内部 ----------
    def _transcribe_once(self, pcm: bytes) -> str:
        ws = self._connect(self._url, self._headers())
        try:
            ws.send_binary(encode_full_request(build_request_payload(self._language)))
            return self._send_and_read(ws, pcm)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _headers(self) -> dict:
        headers = {"X-Api-Resource-Id": self._resource_id,
                   "X-Api-Connect-Id": str(uuid.uuid4())}
        if self._app_key and self._access_key:  # 旧版控制台（2026-08-26 实测可用）
            headers["X-Api-App-Key"] = self._app_key
            headers["X-Api-Access-Key"] = self._access_key
        else:  # 新版控制台单头
            headers["X-Api-Key"] = self._api_key
        return headers

    def _send_and_read(self, ws, pcm: bytes) -> str:
        """边发边读：长音频时服务端逐块回帧，只发不读会塞满 socket 缓冲导致
        死锁（2026-08-26 用户实测：长语音无结果且无报错的根因）；同时取消
        响应帧数上限，长度约束交给 recv 超时。"""
        buf = bytearray()
        texts: list[str] = []

        def _drain() -> str | None:
            """读掉当前已到达的响应帧；返回最终文本、None（暂无更多数据）；
            错误直接抛 AsrError。"""
            while True:
                try:
                    msg = ws.recv()
                except _TIMEOUT_ERRORS:
                    return None  # 短超时内无新数据（发送阶段正常）
                if not msg:
                    raise AsrError("连接在响应途中关闭")
                buf.extend(msg)
                while True:
                    frame = _try_parse(buf)
                    if frame is None:
                        break
                    kind, a, payload = frame
                    if kind == "error":
                        raise AsrError(f"ASR 服务端错误 [{a}]: {json.loads(payload)}", code=a)
                    text = ((json.loads(payload) or {}).get("result") or {}).get("text", "")
                    if text:
                        texts.append(text)
                    if a & 0b0010:  # 最后一包：nostream 分段累计式，最终帧即全文
                        return texts[-1] if texts else ""

        ws.settimeout(0.05)  # 发送阶段：每块后短暂排空响应
        for i in range(0, len(pcm), self._chunk_bytes):
            ws.send_binary(encode_audio_packet(pcm[i:i + self._chunk_bytes], False))
            final = _drain()
            if final is not None:
                return final  # nostream 在音频 >15s 时可能提前返回完整结果
            if self._chunk_pause > 0:
                time.sleep(self._chunk_pause)
        ws.send_binary(encode_audio_packet(b"", True))  # 末包负包
        ws.settimeout(self._recv_timeout)
        deadline = time.monotonic() + self._recv_timeout
        while True:
            ws.settimeout(max(0.5, deadline - time.monotonic()))
            final = _drain()
            if final is not None:
                return final
            if time.monotonic() >= deadline:
                raise AsrError("超时未收到最终识别结果")

    def _read_final(self, ws) -> str:  # noqa: RET503 - 兼容保留（旧路径已并入 _send_and_read）
        return self._send_and_read(ws, b"")

    def _websocket_connect(self, url: str, headers: dict):
        import websocket  # websocket-client（延迟导入与探针一致）

        try:
            return websocket.create_connection(
                url, header=headers, timeout=self._connect_timeout, suppress_origin=True)
        except websocket.WebSocketBadStatusException as e:
            # 401 = key 无效/资源未开通（spike 实测错误形态）
            raise AsrError(f"ASR 握手被拒: HTTP {e.status_code}（检查 api_key / resource_id）",
                           code=e.status_code) from e


def _try_parse(buf: bytearray):
    """从缓冲区解出一帧；不完整返回 None（消耗掉的字节从 buf 删除）。"""
    if len(buf) < 4:
        return None
    msg_type, flags = buf[1] >> 4, buf[1] & 0xF
    if msg_type == 0xF:
        if len(buf) < 12:
            return None
        size = struct.unpack(">I", bytes(buf[8:12]))[0]
        if len(buf) < 12 + size:
            return None
        code = struct.unpack(">I", bytes(buf[4:8]))[0]
        payload = bytes(buf[12:12 + size])
        del buf[:12 + size]
        return ("error", code, payload)
    if len(buf) < 12:
        return None
    size = struct.unpack(">I", bytes(buf[8:12]))[0]
    if len(buf) < 12 + size:
        return None
    payload = bytes(buf[12:12 + size])
    del buf[:12 + size]
    return ("response", flags, _maybe_gunzip(payload))


# ---------- OpenAI 兼容转写（/audio/transcriptions，第二厂商适配） ----------

def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1,
               sampwidth: int = 2) -> bytes:
    """裸 PCM（16k 单声道 s16le）转 WAV 字节流（纯函数，供 multipart 上传）。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class OpenAICompatAsrClient:
    """OpenAI 兼容转写客户端：POST {base_url}/audio/transcriptions。

    一套协议覆盖多家：OpenAI(whisper-1)、Groq(whisper-large-v3)、
    硅基流动(SenseVoice)、本地 whisper server 等；构造参数可注入，
    post 可替换（单测无网络）。录音已是 16k 单声道，内存转 WAV 上传，
    不落盘。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "FunAudioLLM/SenseVoiceSmall",
        language: str = "auto",
        timeout_sec: float = 60.0,
        sample_rate: int = SAMPLE_RATE,
        post: Optional[Callable] = None,
    ) -> None:
        if not api_key:
            raise AsrError("缺少 ASR 凭证（api_key）")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._language = language
        self._timeout = timeout_sec
        self._sample_rate = sample_rate
        self._post = post

    def transcribe(self, pcm: bytes) -> str:
        import json as _json

        post = self._post
        if post is None:
            post = self._httpx_post
        fields = {"model": self._model, "response_format": "json"}
        if self._language and self._language != "auto":
            fields["language"] = self._language
        status, body = post(
            f"{self._base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            fields=fields,
            filename="audio.wav",
            content_type="audio/wav",
            data=pcm_to_wav(pcm, self._sample_rate),
        )
        if status != 200:
            raise AsrError(f"ASR 请求失败: HTTP {status}: {body[:200]}", code=status)
        try:
            return str(_json.loads(body).get("text", "")).strip()
        except ValueError as e:
            raise AsrError(f"ASR 响应非 JSON: {body[:200]}") from e

    def _httpx_post(self, url, headers, fields, filename, content_type, data):
        """httpx multipart 上传（返回 (status, text)）；trust_env=False 同润色
        （本机 socks 代理会拦直连，2026-08-26 实测）。"""
        import httpx

        files = {"file": (filename, data, content_type)}
        try:
            with httpx.Client(trust_env=False, timeout=self._timeout) as client:
                resp = client.post(url, headers=headers, data=fields, files=files)
        except httpx.HTTPError as e:
            raise AsrError(f"ASR 请求异常: {e}") from e
        return resp.status_code, resp.text
