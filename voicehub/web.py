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
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .config import Config
from .settings import ConfigError, ConfigService
from .storage import Storage

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._config = config
        self._storage = storage
        self._sticky = sticky
        self._discovery = discovery
        self._hotkey = hotkey
        self._settings = settings

    def state(self) -> dict[str, Any]:
        return collect_state(self._config, self._sticky, self._discovery, self._hotkey)

    def build_app(self) -> FastAPI:
        app = FastAPI(title="VoiceHub Dashboard")

        @app.get("/")
        def index():
            from fastapi.responses import HTMLResponse

            return HTMLResponse(_INDEX_HTML)

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
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VoiceHub 仪表盘</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">
<div id="app" class="max-w-4xl mx-auto p-6" v-cloak>
  <header class="mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-bold">VoiceHub</h1>
      <p class="text-slate-400 text-sm">语音转写多设备分发 · 仪表盘</p>
    </div>
    <button @click="toggleSettings"
            class="bg-slate-700 hover:bg-slate-600 text-sm px-3 py-1.5 rounded">
      {{ showSettings ? '返回状态页' : '设置' }}
    </button>
  </header>

  <!-- 设置页（M6-③）：目标设备 / 去抖 / 超时可视化编辑，保存走 /api/config -->
  <section v-if="showSettings" class="space-y-4">
    <div v-if="!cfg" class="text-slate-500">配置加载中…</div>
    <template v-else>
      <div class="bg-slate-800 rounded-lg p-4">
        <h2 class="text-sm text-slate-400 mb-3">目标设备（热键 / 名称 / 固定端点，改后需重启生效）</h2>
        <div v-for="(t, key) in cfg.targets" :key="key" class="grid grid-cols-12 gap-2 mb-2 items-center">
          <label class="col-span-2 text-xs text-slate-500 self-center">{{ key }}</label>
          <input v-model="t.name" placeholder="名称" class="col-span-4 bg-slate-700 rounded px-2 py-1 text-sm">
          <div class="col-span-2 flex items-center gap-1">
            <span class="text-xs text-slate-500 font-mono">Alt+</span>
            <input v-model="t.hotkey" class="w-12 bg-slate-700 rounded px-2 py-1 text-sm text-center font-mono">
          </div>
          <input v-model="t.endpoint" placeholder="端点(可空,自动发现)" class="col-span-4 bg-slate-700 rounded px-2 py-1 text-sm">
        </div>
      </div>
      <div class="bg-slate-800 rounded-lg p-4">
        <h2 class="text-sm text-slate-400 mb-3">转写判定参数（保存后即时生效）</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="text-sm">去抖等待 stability_ms（毫秒）
            <input type="number" v-model.number="cfg.voicehub.stability_ms"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
          </label>
          <label class="text-sm">粘滞等待 pending_timeout_sec（秒，需覆盖最长听写）
            <input type="number" v-model.number="cfg.voicehub.pending_timeout_sec"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
          </label>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button @click="saveConfig" :disabled="saving"
                class="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded text-sm">
          {{ saving ? '保存中…' : '保存配置' }}
        </button>
        <span v-if="saveMsg" :class="saveMsg.ok ? 'text-emerald-400' : 'text-red-400'" class="text-sm">
          {{ saveMsg.text }}
        </span>
      </div>
    </template>
  </section>

  <section v-show="!showSettings" class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
    <div class="bg-slate-800 rounded-lg p-4">
      <h2 class="text-sm text-slate-400 mb-2">粘滞目标</h2>
      <div v-if="state.sticky.armed" class="text-emerald-400">
        等待转写 → {{ stickyTargetName }}
      </div>
      <div v-else class="text-slate-500">空闲（按 Alt+1/2/3/4 选目标）</div>
    </div>
    <div class="bg-slate-800 rounded-lg p-4">
      <h2 class="text-sm text-slate-400 mb-2">在线设备</h2>
      <ul>
        <li v-for="t in onlineTargets" :key="t.key" class="flex justify-between">
          <span>{{ t.name }}</span>
          <span class="text-emerald-400">{{ t.type === 'local' ? '本机' : (t.device.ip + ':' + t.device.port) }}</span>
        </li>
        <li v-if="!onlineTargets.length" class="text-slate-500">无在线设备</li>
      </ul>
    </div>
  </section>

  <section v-show="!showSettings" class="bg-slate-800 rounded-lg p-4 mb-6">
    <h2 class="text-sm text-slate-400 mb-2">目标绑定</h2>
    <table class="w-full text-sm">
      <thead class="text-slate-500"><tr>
        <th class="text-left">目标</th><th class="text-left">热键</th><th class="text-left">类型</th><th class="text-left">状态</th>
      </tr></thead>
      <tbody>
        <tr v-for="t in state.targets" :key="t.key" class="border-t border-slate-700">
          <td>{{ t.name }}</td>
          <td class="font-mono">Alt+{{ t.hotkey }}</td>
          <td>{{ t.type }}</td>
          <td :class="t.online ? 'text-emerald-400' : 'text-slate-500'">{{ t.online ? '在线' : '离线' }}</td>
        </tr>
      </tbody>
    </table>
  </section>

  <section v-show="!showSettings" class="bg-slate-800 rounded-lg p-4">
    <h2 class="text-sm text-slate-400 mb-2">最近转写</h2>
    <ul>
      <li v-for="log in logs" :key="log.id" class="border-t border-slate-700 py-2">
        <div class="text-sm">{{ log.processed_text }}</div>
        <div class="text-xs text-slate-500">→ {{ log.target_device }} · {{ log.created_at }}</div>
      </li>
      <li v-if="!logs.length" class="text-slate-500">暂无记录</li>
    </ul>
  </section>
</div>

<script>
const { createApp } = Vue;
createApp({
  data() { return {
    state: { sticky: { armed: false }, targets: [], hotkeys: {} },
    logs: [],
    // 设置页（M6-③）：cfg 为 /api/config 原始结构，编辑后整份 PUT 回传
    showSettings: false, cfg: null, saving: false, saveMsg: null,
  }; },
  computed: {
    stickyTargetName() {
      const t = this.state.targets.find(x => x.key === this.state.sticky.target_key);
      return t ? t.name : this.state.sticky.target_key;
    },
    onlineTargets() { return this.state.targets.filter(t => t.online); }
  },
  methods: {
    async refreshLogs() {
      const r = await fetch('/api/logs?limit=20');
      this.logs = await r.json();
    },
    async toggleSettings() {
      this.showSettings = !this.showSettings;
      if (this.showSettings && !this.cfg) {
        const r = await fetch('/api/config');
        this.cfg = await r.json();
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
