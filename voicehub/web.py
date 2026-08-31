"""Web 仪表盘（ADR-3）：FastAPI 单文件 HTML + 状态 API + WebSocket 推送 + 配置读写。

- GET /        : 单文件 HTML（Tailwind + Vue CDN，无构建链）。
- GET /api/state : 聚合状态（粘滞目标 / 在线设备 / 绑定热键 / 目标）。
- GET /api/logs  : 历史转写记录。
- GET/PUT /api/config : 配置读取 / 保存（M6-③ 设置页，写回走 ConfigService）。
- WS /ws        : 每 2s 推送一次状态快照，前端只读订阅。

collect_state 为纯函数，便于单测；FastAPI 端点薄封装依赖。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .config import Config
from .settings import ConfigError, ConfigService
from .storage import Storage
from . import __version__

logger = logging.getLogger(__name__)

# 前端 vendor 依赖（随包分发，浏览器零外网请求；断网/无代理也不再白屏）
_VENDOR_FILES = {"tailwind.js", "vue.global.prod.js"}


def _static_vendor_dir():
    """vendor 目录：PyInstaller 解包目录优先（spec datas 收编），源码跑回包目录。"""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent
    for candidate in (root / "voicehub" / "static" / "vendor", root / "static" / "vendor"):
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent / "static" / "vendor"


def collect_state(
    config: Config,
    sticky: Optional[object] = None,
    discovery: Optional[object] = None,
    hotkey: Optional[object] = None,
) -> dict[str, Any]:
    """聚合一份只读状态快照，供 API 与 WebSocket 复用。"""
    targets: list[dict[str, Any]] = []
    online_by_key: dict[str, dict[str, Any]] = {}
    if discovery is not None:
        for d in discovery.online_devices():
            online_by_key[d.name] = {
                "name": d.name, "ip": d.ip, "port": d.port, "source": d.source,
            }
    for key, t in config.targets.items():
        is_local = t.type == "local"
        targets.append({
            "key": t.key,
            "name": t.name,
            "hotkey": t.hotkey,
            "type": t.type,
            "endpoint": t.endpoint,
            # local 目标是本机，不参与网络发现，恒为在线
            "online": is_local or key in online_by_key,
            "device": online_by_key.get(key) if not is_local else
                      {"name": t.name, "ip": "本机", "port": None, "source": "local"},
        })
    return {
        "sticky": {
            "armed": sticky.is_armed() if sticky else False,
            "target_key": sticky.target_key() if sticky else None,
            "armed_at": sticky.armed_at() if sticky else None,
        },
        "targets": targets,
        "hotkeys": hotkey.bindings() if hotkey else {},
        "server": {"host": config.server_host, "port": config.server_port},
    }


class Dashboard:
    """仪表盘服务：持有依赖并构造 FastAPI 应用。"""

    def __init__(
        self,
        config: Config,
        storage: Optional[Storage] = None,
        sticky: Optional[object] = None,
        discovery: Optional[object] = None,
        hotkey: Optional[object] = None,
        settings: Optional[ConfigService] = None,
        dictation: Optional[object] = None,
        credentials: Optional[object] = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._sticky = sticky
        self._discovery = discovery
        self._hotkey = hotkey
        self._settings = settings
        self._dictation = dictation
        self._credentials = credentials

    def state(self) -> dict[str, Any]:
        return collect_state(self._config, self._sticky, self._discovery, self._hotkey)

    def build_app(self) -> FastAPI:
        app = FastAPI(title="VoiceHub Dashboard")

        @app.get("/")
        def index():
            from fastapi.responses import HTMLResponse

            return HTMLResponse(_INDEX_HTML)

        @app.get("/static/vendor/{name}")
        def vendor_js(name: str):
            """前端依赖本地化（Tailwind/Vue vendor 文件，随包分发）。

            2026-08-31 卡顿定案：页面引公网 CDN，直连（浏览器不走代理）时两个
            脚本 ~8s 白屏，且断网即白屏。白名单命名杜绝路径穿越。
            """
            from fastapi.responses import FileResponse

            if name not in _VENDOR_FILES:
                from fastapi.responses import JSONResponse

                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            return FileResponse(_static_vendor_dir() / name, media_type="application/javascript")

        @app.get("/api/state")
        def api_state():
            return self.state()

        @app.get("/api/logs")
        def api_logs(limit: int = 50, category: Optional[str] = None):
            if self._storage is None:
                return []
            return self._storage.recent(limit=limit, category=category)

        @app.get("/api/config")
        def api_config_get():
            """设置页读取：返回 config.json 原始结构。"""
            if self._settings is None:
                return {"ok": False, "error": "本实例未启用配置服务"}
            return self._settings.get()

        @app.post("/api/dictate/toggle")
        def api_dictate_toggle():
            """听写触发（V4/M11 Wayland 热键方案）：系统快捷键调 CLI --
            dictate，CLI 回调本端点；也可供自动化/测试使用。"""
            if self._dictation is None:
                return {"ok": False, "error": "builtin 听写引擎未启用"}
            state = self._dictation.toggle()
            return {"ok": True, "state": state}

        @app.get("/api/dictate/status")
        def api_dictate_status():
            """听写引擎当前状态 + 最近一次结果。"""
            if self._dictation is None:
                return {"ok": False, "state": "disabled"}
            result = self._dictation.last_result()
            return {"ok": True, "state": self._dictation.state(),
                    "last_result": result}

        @app.get("/api/dictate/shortcut")
        def api_shortcut_get():
            """查询 UKUI 系统快捷键注册状态（非 Linux/UKUI 返回 supported=false）。"""
            import sys

            if not sys.platform.startswith("linux"):
                return {"supported": False}
            from .ukui_shortcut import find_dictate_slot

            found = find_dictate_slot()
            return {"supported": True, "registered": found is not None,
                    **(found or {})}

        @app.post("/api/dictate/shortcut")
        async def api_shortcut_register(req: Request):
            """一键注册听写系统快捷键（写 UKUI gsettings，即时生效）。"""
            import sys

            if not sys.platform.startswith("linux"):
                return {"ok": False, "error": "仅 Linux/UKUI 支持"}
            from .ukui_shortcut import register as reg

            body = await req.json()
            return reg(binding=str(body.get("binding", "Ctrl+Alt+V")))

        @app.get("/api/credentials")
        def api_credentials_get():
            """凭证配置状态（脱敏：仅是否已配置 + 尾 4 位，key 永不出服务）。"""
            if self._credentials is None:
                return {"ok": False}
            return {"ok": True, "credentials": self._credentials.status()}

        @app.post("/api/credentials")
        async def api_credentials_post(req: Request):
            """自助填写 API 凭证（写 gitignored 的 config.local.json，重启生效）。"""
            if self._credentials is None:
                return {"ok": False, "error": "本实例未启用凭证服务"}
            return self._credentials.update(await req.json())

        @app.delete("/api/dictate/shortcut")
        def api_shortcut_unregister():
            import sys

            if not sys.platform.startswith("linux"):
                return {"ok": False, "error": "仅 Linux/UKUI 支持"}
            from .ukui_shortcut import unregister as unreg

            return unreg()

        @app.put("/api/config")
        async def api_config_put(req: Request):
            """设置页保存：校验 + 原子写回 + 热应用；非法配置返回 400。"""
            if self._settings is None:
                return {"ok": False, "error": "本实例未启用配置服务"}
            try:
                return self._settings.update(await req.json())
            except ConfigError as e:
                return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            await websocket.accept()
            try:
                while True:
                    await websocket.send_json(self.state())
                    await asyncio.sleep(2.0)
            except WebSocketDisconnect:
                pass

        return app


_INDEX_HTML = """<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VoiceHub 仪表盘</title>
  <!-- 前端依赖随包本地分发（/static/vendor）：不走公网 CDN，断网/无代理秒开 -->
  <script src="/static/vendor/tailwind.js"></script>
  <script src="/static/vendor/vue.global.prod.js"></script>
  <style>
    /* 三主题（深色 / 浅色 / 护眼）：语义变量驱动，Tailwind 只管布局 */
    :root, :root[data-theme="dark"] {
      --bg: #0b1020; --bg-soft: #0f1628;
      --card: rgba(255,255,255,.04); --card-border: rgba(255,255,255,.09);
      --text: #e6eaf2; --muted: #97a0b5; --faint: #6b7590;
      --input-bg: rgba(255,255,255,.06); --input-border: rgba(255,255,255,.13);
      --ok: #34d399; --err: #f87171;
      --shadow: 0 8px 26px rgba(0,0,0,.32);
      color-scheme: dark;
    }
    :root[data-theme="light"] {
      --bg: #f4f6fb; --bg-soft: #eef1f8;
      --card: #ffffff; --card-border: #e2e6f0;
      --text: #1c2333; --muted: #5c667a; --faint: #9aa3b8;
      --input-bg: #f7f8fc; --input-border: #d8deea;
      --ok: #059669; --err: #dc2626;
      --shadow: 0 5px 18px rgba(23,43,99,.07);
      color-scheme: light;
    }
    :root[data-theme="sepia"] {
      --bg: #c9e4cc; --bg-soft: #bddbbf;
      --card: #dcEEDF; --card-border: #aed2b1;
      --text: #22331f; --muted: #4f6450; --faint: #7e9380;
      --input-bg: #ebf6ec; --input-border: #a8ccab;
      --ok: #15803d; --err: #b91c1c;
      --shadow: 0 5px 16px rgba(47,79,47,.10);
      color-scheme: light;
    }
    [v-cloak] { display: none; }
    body {
      background: var(--bg); color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                   "Microsoft YaHei", "Noto Sans SC", sans-serif;
      transition: background .25s ease, color .25s ease;
    }
    :root[data-theme="dark"] body {
      background:
        radial-gradient(700px 420px at 12% -6%, rgba(99,102,241,.16), transparent 60%),
        radial-gradient(640px 420px at 92% 4%, rgba(34,211,238,.08), transparent 60%),
        var(--bg);
      background-attachment: fixed;
    }
    .vh-card {
      background: var(--card); border: 1px solid var(--card-border);
      border-radius: 14px; padding: 18px 20px; box-shadow: var(--shadow);
    }
    .vh-title {
      display: flex; align-items: center; gap: 8px;
      font-size: 13px; font-weight: 600; letter-spacing: .4px;
      color: var(--muted); margin-bottom: 14px;
    }
    .vh-title svg { color: var(--accent, #6366f1); flex: none; }
    :root { --accent: #818cf8; }
    :root[data-theme="light"] { --accent: #4f46e5; }
    :root[data-theme="sepia"] { --accent: #3d7a44; }
    .vh-hint { font-size: 12px; color: var(--faint); line-height: 1.7; }
    .vh-label { display: block; font-size: 13px; color: var(--text); }
    .vh-input {
      width: 100%; margin-top: 4px;
      background: var(--input-bg); border: 1px solid var(--input-border);
      border-radius: 8px; padding: 7px 10px; font-size: 13.5px; color: var(--text);
      outline: none; transition: border-color .15s ease;
    }
    .vh-input:focus { border-color: var(--accent); }
    /* 下拉弹出列表跟随主题（否则深色主题下白底浅字看不清） */
    select.vh-input option { background: var(--bg-soft); color: var(--text); }
    .vh-input.font-mono { font-size: 12.5px; }
    .vh-btn {
      display: inline-flex; align-items: center; gap: 6px;
      background: var(--input-bg); border: 1px solid var(--input-border);
      color: var(--text); padding: 7px 14px; border-radius: 9px;
      font-size: 13.5px; font-weight: 500; cursor: pointer;
      transition: border-color .15s ease, transform .12s ease;
    }
    .vh-btn:hover { border-color: var(--accent); }
    .vh-btn:active { transform: translateY(1px); }
    .vh-btn:disabled { opacity: .5; cursor: not-allowed; }
    .vh-btn-primary {
      background: linear-gradient(120deg, #6366f1, #0ea5e9);
      border: none; color: #fff;
      box-shadow: 0 4px 16px rgba(79,70,229,.35);
    }
    .vh-btn-primary:hover { filter: brightness(1.08); border: none; }
    .vh-ok { color: var(--ok); } .vh-err { color: var(--err); }
    .vh-faint { color: var(--faint); }
    .vh-pill {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 12px; padding: 3px 10px; border-radius: 999px;
      border: 1px solid var(--card-border); background: var(--input-bg);
    }
    .vh-pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--faint); }
    .vh-pill.on .dot { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
    .vh-savebar {
      position: sticky; bottom: 12px; display: flex; align-items: center; gap: 12px;
      padding: 10px 14px; border-radius: 12px;
      background: var(--card); border: 1px solid var(--card-border);
      backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    }
    details.vh-card summary { cursor: pointer; user-select: none; list-style: none; }
    details.vh-card summary::-webkit-details-marker { display: none; }
    details.vh-card summary .vh-caret { transition: transform .15s ease; display: inline-block; }
    details.vh-card[open] summary .vh-caret { transform: rotate(90deg); }
    .vh-table { width: 100%; font-size: 13.5px; }
    .vh-table th { text-align: left; color: var(--faint); font-weight: 500; padding: 6px 8px; }
    .vh-table td { padding: 7px 8px; border-top: 1px solid var(--card-border); }
    .vh-logo { display: flex; align-items: center; gap: 10px; }
    .vh-logo .name {
      font-size: 20px; font-weight: 800; letter-spacing: .3px;
      background: linear-gradient(120deg, #818cf8, #22d3ee);
      -webkit-background-clip: text; background-clip: text;
      -webkit-text-fill-color: transparent; color: transparent;
    }
  </style>
</head>
<body class="min-h-screen">
<div id="app" class="max-w-4xl mx-auto p-6" v-cloak>
  <header class="mb-6 flex items-center justify-between">
    <div class="vh-logo">
      <svg width="34" height="34" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="rgba(129,140,248,.14)" stroke="rgba(129,140,248,.45)"/><rect x="13" y="6" width="6" height="13" rx="3" fill="#818cf8"/><path d="M9 15a7 7 0 0 0 14 0" stroke="#22d3ee" stroke-width="2.4" fill="none" stroke-linecap="round"/><path d="M16 22v4" stroke="#22d3ee" stroke-width="2.4" stroke-linecap="round"/></svg>
      <div>
        <div class="name">VoiceHub</div>
        <div class="text-xs vh-faint">单点语音输入，多端分发</div>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <button class="vh-btn" @click="cycleTheme" :title="'当前主题：' + themeLabel">
        <svg v-if="theme === 'dark'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>
        <svg v-else-if="theme === 'light'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>
        <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
        {{ themeLabel }}
      </button>
      <button class="vh-btn" @click="toggleSettings">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>
        {{ showSettings ? '返回状态页' : '设置' }}
      </button>
    </div>
  </header>

  <!-- 设置页（M6-③）：目标 / 引擎 / 润色 / 凭证 / 快捷键 / 高级，保存走 /api/config -->
  <section v-if="showSettings" class="space-y-4">
    <div v-if="!cfg" class="vh-faint">配置加载中…</div>
    <template v-else>
      <div class="vh-card">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>
          目标设备（热键 / 名称 / 固定端点，改后需重启生效）
        </h2>
        <div v-for="(t, key) in cfg.targets" :key="key" class="grid grid-cols-12 gap-2 mb-2 items-center">
          <label class="col-span-2 text-xs vh-faint self-center font-mono">{{ key }}</label>
          <input v-model="t.name" placeholder="名称" class="col-span-4 vh-input">
          <div class="col-span-2 flex items-center gap-1">
            <span class="text-xs vh-faint font-mono">Alt+</span>
            <input v-model="t.hotkey" class="w-12 vh-input text-center font-mono">
          </div>
          <input v-model="t.endpoint" placeholder="端点(可空,自动发现)" class="col-span-4 vh-input">
        </div>
      </div>
      <div class="vh-card">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12h4l2-8 4 16 2-6 2 4h2"/></svg>
          转写判定参数（保存后即时生效）
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="vh-label">去抖等待 stability_ms（毫秒）
            <input type="number" v-model.number="cfg.voicehub.stability_ms" class="vh-input">
          </label>
          <label class="vh-label">粘滞等待 pending_timeout_sec（秒，需覆盖最长听写）
            <input type="number" v-model.number="cfg.voicehub.pending_timeout_sec" class="vh-input">
          </label>
        </div>
        <p class="vh-hint mt-3">
          去抖等待：文本写入剪贴板后稳定多久才判定为一次完整输入——太小会把一句话截成多段，太大则出字慢（默认 600ms）。
          粘滞等待：按 Alt+N 选中目标后的收文窗口期，期内写入剪贴板的文本都会发给该目标——需覆盖一次听写的最长耗时（默认 30s）。
        </p>
      </div>
      <!-- V4/M12-①：转写润色（多厂商预设 + 四模式） -->
      <div class="vh-card" v-if="cfg.polish">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/></svg>
          转写润色（保存后重启程序生效）
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="vh-label">润色模式
            <select v-model="cfg.polish.mode" class="vh-input">
              <option value="off">关闭（原文直出，零延迟）</option>
              <option value="light">轻整理（短句输入，输出自然句子）</option>
              <option value="structured">结构化整理（长口述，喂下游大模型）</option>
              <option value="custom">自定义 prompt</option>
            </select>
          </label>
          <label class="vh-label">模型厂商
            <select v-model="cfg.polish.provider" @change="onPolishProvider" class="vh-input">
              <option value="deepseek">DeepSeek（深度求索）</option>
              <option value="kimi">Kimi（月之暗面）</option>
              <option value="zhipu">智谱 GLM</option>
              <option value="qwen">通义千问（阿里云）</option>
              <option value="ark">火山方舟（豆包）</option>
              <option value="openai">OpenAI</option>
              <option value="custom">自定义 OpenAI 兼容</option>
            </select>
          </label>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <label class="vh-label">接口地址 base_url
            <input v-model="cfg.polish.base_url" class="vh-input font-mono">
          </label>
          <label class="vh-label">模型
            <input v-model="cfg.polish.model" class="vh-input font-mono">
          </label>
        </div>
        <label v-if="cfg.polish.mode === 'custom'" class="vh-label block mt-3">
          自定义 prompt
          <textarea v-model="cfg.polish.custom_prompt" rows="5" class="vh-input font-mono"></textarea>
        </label>
        <p class="vh-hint mt-3">
          选厂商自动带出接口地址与默认模型，两项均可手改（火山方舟填 ep- 开头的接入点 ID）；
          Key 在下方「API 凭证」卡填写。润色失败（超时/报错）自动降级为原文直出，不会挡住文字上屏；
          原文与润色结果双双落库，「最近转写」中可见对照。
        </p>
      </div>
      <!-- V4/M12：API 凭证自助填写（仅自建内核需要；闪电说引擎下整卡隐藏，已存凭证不受影响） -->
      <div class="vh-card" v-if="cred.supported && cfg.transcription && cfg.transcription.engine === 'builtin'">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21 2-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
          API 凭证（自建内核所需 · 保存到本机 config.local.json，重启程序生效）
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="vh-label">转写 API Key
            <span class="text-xs vh-faint" v-if="cfg.transcription.provider === 'openai_compat'">OpenAI 兼容供应商（硅基流动 / Groq 等）</span>
            <span class="text-xs vh-faint" v-else>豆包新版控制台（用旧版则填下面两项）</span>
            <input type="password" v-model="cred.transcription_api_key" placeholder="API Key"
                   class="vh-input font-mono">
            <span class="text-xs" :class="cred.has.transcription_api_key ? 'vh-ok' : 'vh-faint'">
              {{ credTransKeyStatus }}</span>
          </label>
          <label class="vh-label">润色 API Key
            <span class="text-xs vh-faint">DeepSeek / Kimi / GLM 等，按所选厂商</span>
            <input type="password" v-model="cred.polish_api_key" placeholder="sk-..."
                   class="vh-input font-mono">
            <span class="text-xs" :class="cred.has.polish_api_key ? 'vh-ok' : 'vh-faint'">
              {{ cred.has.polish_api_key ? '已配置 ' + cred.has.polish_api_key : '未配置' }}</span>
          </label>
          <template v-if="cfg.transcription.provider === 'volcengine_sauc'">
            <label class="vh-label">豆包 APP ID（旧版控制台，选填）
              <input type="password" v-model="cred.transcription_app_key" placeholder="数字 APP ID"
                     class="vh-input font-mono">
              <span class="text-xs" :class="cred.has.transcription_app_key ? 'vh-ok' : 'vh-faint'">
                {{ cred.has.transcription_app_key ? '已配置 ' + cred.has.transcription_app_key : '未配置' }}</span>
            </label>
            <label class="vh-label">豆包 Access Token（旧版控制台，选填）
              <input type="password" v-model="cred.transcription_access_key" placeholder="Access Token"
                     class="vh-input font-mono">
              <span class="text-xs" :class="cred.has.transcription_access_key ? 'vh-ok' : 'vh-faint'">
                {{ cred.has.transcription_access_key ? '已配置 ' + cred.has.transcription_access_key : '未配置' }}</span>
            </label>
          </template>
        </div>
        <div class="flex items-center gap-3 mt-3">
          <button @click="saveCreds" :disabled="cred.busy" class="vh-btn vh-btn-primary">
            保存凭证
          </button>
          <span class="text-sm" :class="cred.msgOk ? 'vh-ok' : 'vh-err'">{{ cred.msg }}</span>
        </div>
        <p class="vh-hint mt-2">
          凭证只写本机 config.local.json（已被 gitignore 保护，永不上传/入库），留空的项不改动，回显只显示尾 4 位。
          转写鉴权二选一：新版控制台填「转写 API Key」，或旧版控制台填 APP ID + Access Token，任意一种已配置即可用。
          切换引擎 / 供应商只是隐藏本卡，已保存的凭证不会被清除。
        </p>
      </div>
      <!-- V4/M12：听写引擎 + 转写供应商 + 录音参数（保存后重启程序生效） -->
      <div class="vh-card" v-if="cfg.transcription">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 19v3"/></svg>
          听写引擎与录音参数（保存后重启程序生效）
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="vh-label">转写引擎
            <select v-model="cfg.transcription.engine" class="vh-input">
              <option value="shandianshuo">闪电说（剪贴板监听链路）</option>
              <option value="builtin">自建内核（本项目 · 云端 ASR）</option>
            </select>
          </label>
          <label class="vh-label" v-if="cfg.transcription.engine === 'builtin'">转写供应商
            <select v-model="cfg.transcription.provider" @change="onAsrProvider" class="vh-input">
              <option value="volcengine_sauc">火山豆包（WebSocket 流式直连）</option>
              <option value="openai_compat">OpenAI 兼容转写（硅基流动 / Groq / 本地 Whisper 等）</option>
            </select>
          </label>
          <label class="vh-label">识别语言
            <select v-model="cfg.transcription.language" class="vh-input">
              <option value="auto">自动检测</option>
              <option value="zh">中文</option>
              <option value="en">英文</option>
            </select>
          </label>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4"
             v-if="cfg.transcription.engine === 'builtin' && cfg.transcription.provider === 'openai_compat'">
          <label class="vh-label">转写接口地址 base_url
            <input v-model="cfg.transcription.base_url" class="vh-input font-mono">
          </label>
          <label class="vh-label">转写模型
            <input v-model="cfg.transcription.model" class="vh-input font-mono">
          </label>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          <label class="vh-label">静音自动结束（毫秒，0=关闭）
            <input type="number" v-model.number="cfg.transcription.vad_silence_ms" class="vh-input">
          </label>
          <label class="vh-label">单段最长录音（秒）
            <input type="number" v-model.number="cfg.transcription.max_duration_sec" class="vh-input">
          </label>
          <label class="vh-label">未开口放弃等待（毫秒）
            <input type="number" v-model.number="cfg.transcription.vad_lead_in_ms" class="vh-input">
          </label>
        </div>
        <p class="vh-hint mt-3">
          静音自动结束 = 说完连续静音多久自动送识别（0 = 仅手动停止，推荐）；
          未开口放弃 = 按下后一直不说多久自动放弃（不消耗云端调用）。
          「OpenAI 兼容转写」凭「转写 API Key」即可用（硅基流动 / Groq / OpenAI / 本地 Whisper server），
          闪电说不可用的平台（如 openKylin）选「自建内核」。切引擎/供应商需重启；两套引擎共用 Alt+N 目标粘滞。
        </p>
        <p class="vh-hint mt-1" v-if="cfg.transcription.engine === 'shandianshuo'">
          当前引擎为「闪电说」：转写与润色均由闪电说自身完成，无需配置任何 API 凭证；
          已保存过的凭证仍留在本机 config.local.json，切回自建内核时自动恢复可见。
        </p>
      </div>
      <!-- V4/M11：听写系统快捷键一键注册（Wayland 下唯一可靠的全局触发） -->
      <div class="vh-card" v-if="shortcut.supported">
        <h2 class="vh-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/></svg>
          听写快捷键（系统级，注册后任何界面下都生效）
        </h2>
        <div class="flex items-center gap-3 flex-wrap">
          <input v-model="shortcut.binding" placeholder="如 Ctrl+Alt+V"
                 class="vh-input font-mono" style="width: 11rem">
          <button @click="registerShortcut" :disabled="shortcut.busy" class="vh-btn vh-btn-primary">
            {{ shortcut.registered ? '更新注册' : '一键注册' }}
          </button>
          <button v-if="shortcut.registered" @click="removeShortcut" class="vh-btn">移除</button>
          <span class="text-sm" :class="shortcut.msgOk ? 'vh-ok' : 'vh-err'">
            {{ shortcut.msg }}
          </span>
        </div>
        <p class="vh-hint mt-2">
          注册即写入系统快捷键（等效于控制中心「自定义快捷键」），按一下开始听写、再按一下结束；
          录音中屏幕上有波形悬浮框，识别结果自动进剪贴板。
        </p>
      </div>
      <!-- 高级设置：默认收起，普通用户无需触碰 -->
      <details class="vh-card" v-if="cfg.transcription">
        <summary class="vh-title">
          <span class="vh-caret">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m9 6 6 6-6 6"/></svg>
          </span>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>
          高级设置（一般无需改动）
        </summary>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <label class="vh-label">仪表盘端口
            <input type="number" v-model.number="cfg.server.port" class="vh-input font-mono">
            <span class="text-xs vh-faint">改后需重启，浏览器访问 http://127.0.0.1:新端口</span>
          </label>
          <label class="vh-label">录音采样率
            <input type="number" v-model.number="cfg.transcription.sample_rate" class="vh-input font-mono">
          </label>
          <template v-if="cfg.transcription.provider === 'volcengine_sauc'">
            <label class="vh-label">ASR 端点 base_url
              <input v-model="cfg.transcription.base_url" class="vh-input font-mono">
            </label>
            <label class="vh-label">火山资源 ID resource_id
              <input v-model="cfg.transcription.resource_id" class="vh-input font-mono">
            </label>
          </template>
        </div>
        <p class="vh-hint mt-3">
          端点 / 资源 ID 仅供调试火山系新接口；OpenAI 兼容供应商的端点与模型在上方「听写引擎与录音参数」卡。
          所有配置也可直接编辑程序旁的 config.json。
        </p>
      </details>
      <div class="vh-savebar">
        <button @click="saveConfig" :disabled="saving" class="vh-btn vh-btn-primary">
          {{ saving ? '保存中…' : '保存配置' }}
        </button>
        <span v-if="saveMsg" :class="saveMsg.ok ? 'vh-ok' : 'vh-err'" class="text-sm">
          {{ saveMsg.text }}
        </span>
      </div>
    </template>
  </section>

  <section v-show="!showSettings" class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="vh-card">
      <h2 class="vh-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
        粘滞目标
      </h2>
      <div v-if="state.sticky.armed" class="flex items-center gap-2">
        <span class="vh-pill on"><span class="dot"></span>等待转写</span>
        <span class="text-sm">{{ stickyTargetName }}</span>
      </div>
      <div v-else class="vh-faint text-sm">空闲（按 Alt+1/2/3/4 选目标）</div>
    </div>
    <div class="vh-card">
      <h2 class="vh-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/></svg>
        在线设备
      </h2>
      <ul class="text-sm">
        <li v-for="t in onlineTargets" :key="t.key" class="flex justify-between py-0.5">
          <span>{{ t.name }}</span>
          <span class="vh-ok">{{ t.type === 'local' ? '本机' : (t.device.ip + ':' + t.device.port) }}</span>
        </li>
        <li v-if="!onlineTargets.length" class="vh-faint">无在线设备</li>
      </ul>
    </div>
  </section>

  <section v-show="!showSettings" class="vh-card mb-4">
    <h2 class="vh-title">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
      目标绑定
    </h2>
    <table class="vh-table">
      <thead><tr>
        <th>目标</th><th>热键</th><th>类型</th><th>状态</th>
      </tr></thead>
      <tbody>
        <tr v-for="t in state.targets" :key="t.key">
          <td>{{ t.name }}</td>
          <td class="font-mono">Alt+{{ t.hotkey }}</td>
          <td class="vh-faint">{{ t.type }}</td>
          <td :class="t.online ? 'vh-ok' : 'vh-faint'">{{ t.online ? '在线' : '离线' }}</td>
        </tr>
      </tbody>
    </table>
  </section>

  <section v-show="!showSettings" class="vh-card">
    <h2 class="vh-title">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v4l2.5 2.5"/><circle cx="12" cy="12" r="10"/></svg>
      最近转写
    </h2>
    <ul class="text-sm">
      <li v-for="log in logs" :key="log.id" class="py-2" style="border-top: 1px solid var(--card-border)">
        <div>{{ log.processed_text }}</div>
        <div v-if="log.raw_text && log.raw_text !== log.processed_text"
             class="text-xs vh-faint mt-0.5">原文：{{ log.raw_text }}</div>
        <div class="text-xs vh-faint">→ {{ log.target_device }} · {{ log.created_at }}</div>
      </li>
      <li v-if="!logs.length" class="vh-faint">暂无记录</li>
    </ul>
  </section>

  <footer class="text-center text-xs vh-faint mt-6">
    VoiceHub v__VOICEHUB_VERSION__ · 单点语音输入，多端分发
  </footer>
</div>

<script>
const { createApp } = Vue;

// 润色厂商预设（OpenAI 兼容 chat/completions）：选厂商自动带出端点与默认模型，均可手改
const POLISH_PRESETS = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash' },
  kimi:     { base_url: 'https://api.moonshot.cn/v1', model: 'kimi-k2-0905-preview' },
  zhipu:    { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4.7' },
  qwen:     { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3-max' },
  ark:      { base_url: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-seed-1-6-250615' },
  openai:   { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  custom:   null,
};
// 按 base_url 反查厂商（老用户升级后下拉能停在正确项）
const POLISH_PROVIDER_BY_URL = {};
for (const [k, v] of Object.entries(POLISH_PRESETS)) {
  if (v) POLISH_PROVIDER_BY_URL[v.base_url] = k;
}

// 转写供应商预设（builtin 引擎）
const ASR_PRESETS = {
  volcengine_sauc: { base_url: 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream' },
  openai_compat: { base_url: 'https://api.siliconflow.cn/v1', model: 'FunAudioLLM/SenseVoiceSmall' },
};

const THEMES = [
  { id: 'dark', label: '深色' },
  { id: 'light', label: '浅色' },
  { id: 'sepia', label: '护眼' },
];

createApp({
  data() { return {
    state: { sticky: { armed: false }, targets: [], hotkeys: {} },
    logs: [],
    // 设置页（M6-③）：cfg 为 /api/config 原始结构，编辑后整份 PUT 回传
    showSettings: false, cfg: null, saving: false, saveMsg: null,
    // 主题（localStorage 持久化，深色/浅色/护眼循环）
    theme: 'dark',
    // V4/M11：UKUI 听写系统快捷键一键注册
    shortcut: { supported: false, registered: false, binding: 'Ctrl+Alt+V',
                msg: '', msgOk: true, busy: false },
    // V4/M12：API 凭证（脱敏状态回显，保存只发非空项）
    cred: { supported: false, busy: false, msg: '', msgOk: true,
            has: {}, transcription_api_key: '', transcription_app_key: '',
            transcription_access_key: '', polish_api_key: '' },
  }; },
  computed: {
    stickyTargetName() {
      const t = this.state.targets.find(x => x.key === this.state.sticky.target_key);
      return t ? t.name : this.state.sticky.target_key;
    },
    onlineTargets() { return this.state.targets.filter(t => t.online); },
    themeLabel() {
      const t = THEMES.find(x => x.id === this.theme);
      return t ? t.label : '深色';
    },
    // 转写 API Key 状态：新版未配但旧版双头已就绪时明确告知可不填
    credTransKeyStatus() {
      if (this.cred.has.transcription_api_key) return '已配置 ' + this.cred.has.transcription_api_key;
      if (this.cred.has.transcription_app_key && this.cred.has.transcription_access_key) {
        return '未配置（旧版 APP ID+Token 已就绪，可不填）';
      }
      return '未配置';
    },
  },
  methods: {
    applyTheme() {
      document.documentElement.dataset.theme = this.theme;
      localStorage.setItem('vh-theme', this.theme);
    },
    cycleTheme() {
      const i = THEMES.findIndex(x => x.id === this.theme);
      this.theme = THEMES[(i + 1) % THEMES.length].id;
      this.applyTheme();
    },
    onPolishProvider() {
      const p = POLISH_PRESETS[this.cfg.polish.provider];
      if (p) {
        this.cfg.polish.base_url = p.base_url;
        this.cfg.polish.model = p.model;
      }
    },
    onAsrProvider() {
      const p = ASR_PRESETS[this.cfg.transcription.provider];
      if (p && this.cfg.transcription.base_url !== p.base_url) {
        this.cfg.transcription.base_url = p.base_url;
      }
      if (this.cfg.transcription.provider === 'openai_compat' && p.model) {
        this.cfg.transcription.model = p.model;
      }
    },
    async refreshLogs() {
      const r = await fetch('/api/logs?limit=20');
      this.logs = await r.json();
    },
    async toggleSettings() {
      this.showSettings = !this.showSettings;
      if (this.showSettings && !this.cfg) {
        const r = await fetch('/api/config');
        this.cfg = await r.json();
        if (!this.cfg.server) this.cfg.server = { host: '127.0.0.1', port: 8765 };
        if (this.cfg && !this.cfg.polish) this.cfg.polish = {};
        if (this.cfg && this.cfg.polish) {
          if (!this.cfg.polish.mode) this.cfg.polish.mode = 'off';
          if (!this.cfg.polish.provider) this.cfg.polish.provider = 'deepseek';
          if (!this.cfg.polish.model) this.cfg.polish.model = 'deepseek-v4-flash';
          if (!this.cfg.polish.base_url) this.cfg.polish.base_url = 'https://api.deepseek.com/v1';
          if (this.cfg.polish.custom_prompt === undefined) this.cfg.polish.custom_prompt = '';
          // 老配置按 base_url 反查厂商，下拉停在正确项
          const detected = POLISH_PROVIDER_BY_URL[this.cfg.polish.base_url];
          if (detected) this.cfg.polish.provider = detected;
        }
        if (this.cfg && !this.cfg.transcription) this.cfg.transcription = {};
        if (this.cfg && this.cfg.transcription) {
          if (!this.cfg.transcription.engine) this.cfg.transcription.engine = 'shandianshuo';
          if (!this.cfg.transcription.provider) this.cfg.transcription.provider = 'volcengine_sauc';
          if (!this.cfg.transcription.model) this.cfg.transcription.model =
              ASR_PRESETS[this.cfg.transcription.provider].model || 'whisper-1';
          if (this.cfg.transcription.vad_silence_ms === undefined) this.cfg.transcription.vad_silence_ms = 0;
          if (this.cfg.transcription.max_duration_sec === undefined) this.cfg.transcription.max_duration_sec = 300;
          if (this.cfg.transcription.vad_lead_in_ms === undefined) this.cfg.transcription.vad_lead_in_ms = 10000;
        }
      }
      if (this.showSettings) this.refreshShortcut();
      if (this.showSettings) this.refreshCreds();
    },
    async refreshCreds() {
      try {
        const r = await fetch('/api/credentials');
        const d = await r.json();
        this.cred.supported = !!d.ok;
        const has = {};
        for (const [domain, fields] of Object.entries(d.credentials || {})) {
          for (const [f, st] of Object.entries(fields)) {
            if (st.set) has[domain + '_' + f] = st.tail;
          }
        }
        this.cred.has = has;
      } catch (e) { this.cred.supported = false; }
    },
    async saveCreds() {
      this.cred.busy = true;
      try {
        const payload = { transcription: {}, polish: {} };
        if (this.cred.transcription_api_key) payload.transcription.api_key = this.cred.transcription_api_key;
        if (this.cred.transcription_app_key) payload.transcription.app_key = this.cred.transcription_app_key;
        if (this.cred.transcription_access_key) payload.transcription.access_key = this.cred.transcription_access_key;
        if (this.cred.polish_api_key) payload.polish.api_key = this.cred.polish_api_key;
        const r = await fetch('/api/credentials', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const d = await r.json();
        this.cred.msgOk = !!d.ok;
        this.cred.msg = d.ok ? '已保存：' + (d.updated || []).join(', ') + '（重启程序生效）'
                             : '保存失败：' + (d.error || '未知错误');
        if (d.ok) {
          this.cred.transcription_api_key = this.cred.transcription_app_key =
              this.cred.transcription_access_key = this.cred.polish_api_key = '';
          await this.refreshCreds();
        }
      } catch (e) {
        this.cred.msg = '保存失败：' + e;
        this.cred.msgOk = false;
      } finally {
        this.cred.busy = false;
      }
    },
    async refreshShortcut() {
      try {
        const r = await fetch('/api/dictate/shortcut');
        const d = await r.json();
        this.shortcut.supported = !!d.supported;
        this.shortcut.registered = !!d.registered;
        if (d.registered && d.binding) {
          // GTK 加速器显示化：<Ctrl><Alt>v → Ctrl+Alt+V
          const parts = d.binding.split('>').filter(Boolean).map(s => s.replace('<', ''));
          const last = parts.pop() || '';
          this.shortcut.binding = parts.join('+') + '+' +
              (last.length === 1 ? last.toUpperCase() : last);
        }
        if (d.registered) this.shortcut.msg = '已注册：' + d.binding;
      } catch (e) { this.shortcut.supported = false; }
    },
    async registerShortcut() {
      this.shortcut.busy = true;
      try {
        const r = await fetch('/api/dictate/shortcut', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ binding: this.shortcut.binding }),
        });
        const d = await r.json();
        if (d.ok) {
          this.shortcut.registered = true;
          this.shortcut.msg = '已注册：' + d.binding + '（立即生效，试试吧）';
          this.shortcut.msgOk = true;
        } else {
          this.shortcut.msg = '注册失败：' + (d.error || '未知错误');
          this.shortcut.msgOk = false;
        }
      } catch (e) {
        this.shortcut.msg = '注册失败：' + e;
        this.shortcut.msgOk = false;
      } finally {
        this.shortcut.busy = false;
      }
    },
    async removeShortcut() {
      this.shortcut.busy = true;
      try {
        await fetch('/api/dictate/shortcut', { method: 'DELETE' });
        this.shortcut.registered = false;
        this.shortcut.msg = '已移除';
      } finally {
        this.shortcut.busy = false;
      }
    },
    async saveConfig() {
      this.saving = true; this.saveMsg = null;
      try {
        const r = await fetch('/api/config', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.cfg),
        });
        const data = await r.json();
        if (r.ok && data.ok) {
          this.saveMsg = data.need_restart
            ? { ok: true, text: '已保存；目标/热键等变更重启程序后生效' }
            : { ok: true, text: '已保存并生效' };
        } else {
          this.saveMsg = { ok: false, text: '保存失败：' + (data.error || r.status) };
        }
      } catch (e) {
        this.saveMsg = { ok: false, text: '保存失败：' + e };
      } finally {
        this.saving = false;
      }
    }
  },
  mounted() {
    const savedTheme = localStorage.getItem('vh-theme');
    if (savedTheme && THEMES.some(t => t.id === savedTheme)) this.theme = savedTheme;
    this.applyTheme();
    const connect = () => {
      const ws = new WebSocket(`ws://${location.host}/ws`);
      ws.onmessage = (e) => { this.state = JSON.parse(e.data); this.refreshLogs(); };
      ws.onclose = () => setTimeout(connect, 3000);
    };
    connect();
    this.refreshLogs();
  }
}).mount('#app');
</script>
</body>
</html>
"""

# 版本号注入：页脚展示与发布版本保持一致（避免 f-string 转义整页 HTML）。
_INDEX_HTML = _INDEX_HTML.replace("__VOICEHUB_VERSION__", __version__)
