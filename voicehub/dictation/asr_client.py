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
        chunk_ms: int = 200,
        chunk_pause_sec: float = 0.02,
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
            self._send_audio(ws, pcm)
            return self._read_final(ws)
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

    def _send_audio(self, ws, pcm: bytes) -> None:
        for i in range(0, len(pcm), self._chunk_bytes):
            ws.send_binary(encode_audio_packet(pcm[i:i + self._chunk_bytes], False))
            if self._chunk_pause > 0:
                time.sleep(self._chunk_pause)
        ws.send_binary(encode_audio_packet(b"", True))  # 末包负包

    def _read_final(self, ws) -> str:
        ws.settimeout(self._recv_timeout)
        # 流式缓冲：ws.recv() 一次返回一条完整消息（可能含多帧/超量字节），
        # 必须跨 read(n) 累积切片，否则消息尾部会被当「下一帧」丢弃
        buf = bytearray()

        def read(n: int) -> bytes:
            while len(buf) < n:
                chunk = ws.recv()
                if not chunk:
                    raise AsrError("连接在响应途中关闭")
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        texts: list[str] = []
        for _ in range(64):
            kind, a, b = parse_frame(read)
            if kind == "error":
                raise AsrError(f"ASR 服务端错误 [{a}]: {b}", code=a)
            text = ((b or {}).get("result") or {}).get("text", "")
            if text:
                texts.append(text)
            if a & 0b0010:  # 最后一包：nostream 分段为累计式，最终帧即完整文本
                return texts[-1] if texts else ""
        raise AsrError("响应帧数超限，未收到最终结果")

    def _websocket_connect(self, url: str, headers: dict):
        import websocket  # websocket-client（延迟导入与探针一致）

        try:
            return websocket.create_connection(
                url, header=headers, timeout=self._connect_timeout, suppress_origin=True)
        except websocket.WebSocketBadStatusException as e:
            # 401 = key 无效/资源未开通（spike 实测错误形态）
            raise AsrError(f"ASR 握手被拒: HTTP {e.status_code}（检查 api_key / resource_id）",
                           code=e.status_code) from e
