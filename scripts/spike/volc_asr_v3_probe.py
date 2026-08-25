"""V4/M11 spike：火山豆包 ASR（openspeech WebSocket v3）连通性探针。

2026-08-25 spike 产物（结论详见 TASKS.md M11-①）：协议按官方文档
docs/6561/1354869 实现，已实测到鉴权层；M11 的 asr_client 可直接移植本文件的
帧编解码（full_request / audio_packet / parse_response）。

用法：
    python scripts/spike/volc_asr_v3_probe.py [ws_url] [resource_id]

密钥从 config.local.json 的 transcription.api_key 读取（不进参数/不打印），
鉴权头按新版控制台方案：X-Api-Key（无 appid）。
默认打 bigmodel_nostream（录完统一返回，准确率优先，M11 首选端点）。
"""
from __future__ import annotations

import gzip
import json
import math
import os
import struct
import sys
import time
import uuid
from pathlib import Path

import websocket  # pip install websocket-client

DEFAULT_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
DEFAULT_RESOURCE = "volc.seedasr.sauc.duration"  # 豆包流式语音识别 2.0（小时版）
RATE = 16000


def load_key() -> str:
    cfg = json.loads((Path(__file__).resolve().parents[2] / "config.local.json").read_text())
    key = cfg["transcription"]["api_key"]
    print(f"key={key[:6]}...(len={len(key)})")
    return key


# ---- v3 二进制协议（勿改：服务端按字节布局解析） ----
# header: 0x11(version=1,size=1) | type<<4|flags | serial<<4|compression | 0x00
# full client request: type=0x1 flags=0x0 serial=JSON(0x1) comp=gzip(0x1)
# audio only:          type=0x2 flags=0x0/0b0010(末包) serial=none comp=none(raw PCM)
# server response:     type=0x9 ... + seq(4B) + size(4B) + gzip JSON
# error:               type=0xF ... + code(4B) + size(4B) + JSON

def _header(msg_type: int, flags: int, serial: int, comp: int) -> bytes:
    return bytes([0x11, (msg_type << 4) | flags, (serial << 4) | comp, 0x00])


def full_request(payload: dict) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    return _header(0x1, 0x0, 0x1, 0x1) + struct.pack(">I", len(body)) + body


def audio_packet(pcm: bytes, last: bool) -> bytes:
    return _header(0x2, 0b0010 if last else 0x0, 0x0, 0x0) + struct.pack(">I", len(pcm)) + pcm


def _recv_exact(ws, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = ws.recv()
        if not chunk:
            raise EOFError("ws closed")
        buf += chunk
    return buf


def parse_response(ws):
    h = _recv_exact(ws, 4)
    msg_type, flags = h[1] >> 4, h[1] & 0xF
    if msg_type == 0xF:
        code = struct.unpack(">I", _recv_exact(ws, 4))[0]
        size = struct.unpack(">I", _recv_exact(ws, 4))[0]
        return ("error", code, json.loads(_recv_exact(ws, size)))
    _recv_exact(ws, 4)  # sequence
    size = struct.unpack(">I", _recv_exact(ws, 4))[0]
    return ("response", flags, json.loads(_recv_exact(ws, size)))


def sine_pcm(seconds: float = 1.0) -> bytes:
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / RATE)))
        for i in range(int(RATE * seconds))
    )


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    resource = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_RESOURCE
    key = load_key()

    headers = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": resource,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    try:
        ws = websocket.create_connection(url, header=headers, timeout=10, suppress_origin=True)
    except websocket.WebSocketBadStatusException as e:
        print(f"握手被拒: HTTP {e.status_code}（鉴权失败多为 key 无效，见 TASKS.md M11-①）")
        return 2

    print("握手 OK，发送 full request + 1s 正弦音频 + 末包")
    req = {
        "user": {"uid": "voicehub-probe"},
        "audio": {"format": "pcm", "rate": RATE, "bits": 16, "channel": 1},
        "request": {"model_name": "bigmodel", "enable_punc": True, "enable_itn": True},
    }
    try:
        ws.send_binary(full_request(req))
        pcm = sine_pcm(1.0)
        half = len(pcm) // 2
        ws.send_binary(audio_packet(pcm[:half], False))
        time.sleep(0.1)
        ws.send_binary(audio_packet(pcm[half:], False))
        ws.send_binary(audio_packet(b"", True))
        ws.settimeout(15)
        for i in range(6):
            kind, a, b = parse_response(ws)
            if kind == "error":
                print(f"服务端错误: code={a} msg={b}")
                return 3
            text = (b.get("result") or {}).get("text", "")
            print(f"响应#{i} flags={a:#06x} text={text!r}")
            if a & 0b0010:
                print("✅ 鉴权+协议全链路通")
                return 0
        return 4
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    for k in list(os.environ):  # 直连国内端点，绕开 shell 的 socks 代理
        if "proxy" in k.lower():
            del os.environ[k]
    raise SystemExit(main())
