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
        dictation: Optional[object] = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._sticky = sticky
        self._discovery = discovery
        self._hotkey = hotkey
        self._settings = settings
        self._dictation = dictation

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
      <!-- V4/M12-①：转写润色（LLM 后处理，四模式） -->
      <div class="bg-slate-800 rounded-lg p-4" v-if="cfg.polish">
        <h2 class="text-sm text-slate-400 mb-3">转写润色（保存后重启程序生效）</h2>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <label class="text-sm">模式
            <select v-model="cfg.polish.mode"
                    class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
              <option value="off">关闭（原文直出，零延迟）</option>
              <option value="light">轻整理（短句输入，输出自然句子）</option>
              <option value="structured">结构化整理（长口述，喂下游大模型）</option>
              <option value="custom">自定义 prompt</option>
            </select>
          </label>
          <label class="text-sm">模型
            <input v-model="cfg.polish.model"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1 font-mono text-xs">
          </label>
          <label class="text-sm">接口地址 base_url
            <input v-model="cfg.polish.base_url"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1 font-mono text-xs">
          </label>
        </div>
        <label v-if="cfg.polish.mode === 'custom'" class="text-sm block mt-3">
          自定义 prompt
          <textarea v-model="cfg.polish.custom_prompt" rows="5"
                    class="w-full mt-1 bg-slate-700 rounded px-2 py-1 text-xs font-mono"></textarea>
        </label>
        <p class="text-xs text-slate-500 mt-2">
          润色失败（超时/报错）自动降级为原文直出，不会挡住文字上屏；
          API Key 不在此配置（走 config.local.json 或环境变量 VOICEHUB_POLISH_API_KEY）。
          原文与润色结果双双落库，仪表盘最近转写中可见对照。
        </p>
      </div>
      <!-- V4/M12：听写引擎切换 + 录音参数（保存后重启程序生效） -->
      <div class="bg-slate-800 rounded-lg p-4" v-if="cfg.transcription">
        <h2 class="text-sm text-slate-400 mb-3">听写引擎与录音参数（保存后重启程序生效）</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="text-sm">转写引擎
            <select v-model="cfg.transcription.engine"
                    class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
              <option value="shandianshuo">闪电说（剪贴板监听链路）</option>
              <option value="builtin">自建内核（本项目 · 云端 ASR）</option>
            </select>
          </label>
          <label class="text-sm">识别语言
            <select v-model="cfg.transcription.language"
                    class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
              <option value="auto">自动检测</option>
              <option value="zh">中文</option>
              <option value="en">英文</option>
            </select>
          </label>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          <label class="text-sm">静音自动结束（毫秒，0=关闭）
            <input type="number" v-model.number="cfg.transcription.vad_silence_ms"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
          </label>
          <label class="text-sm">单段最长录音（秒）
            <input type="number" v-model.number="cfg.transcription.max_duration_sec"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
          </label>
          <label class="text-sm">未开口放弃等待（毫秒）
            <input type="number" v-model.number="cfg.transcription.vad_lead_in_ms"
                   class="w-full mt-1 bg-slate-700 rounded px-2 py-1">
          </label>
        </div>
        <p class="text-xs text-slate-500 mt-2">
          静音自动结束 = 说完连续静音多久自动送识别（0 = 仅手动停止，推荐）；
          未开口放弃 = 按下后一直不说多久自动放弃（不消耗云端调用）。
          切引擎需重启；两套引擎共用 Alt+N 目标粘滞。
        </p>
      </div>
      <!-- V4/M11：听写系统快捷键一键注册（Wayland 下唯一可靠的全局触发） -->
      <div class="bg-slate-800 rounded-lg p-4" v-if="shortcut.supported">
        <h2 class="text-sm text-slate-400 mb-3">听写快捷键（系统级，注册后任何界面下都生效）</h2>
        <div class="flex items-center gap-3 flex-wrap">
          <input v-model="shortcut.binding" placeholder="如 Ctrl+Alt+V"
                 class="bg-slate-700 rounded px-3 py-1.5 text-sm font-mono w-44">
          <button @click="registerShortcut" :disabled="shortcut.busy"
                  class="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded text-sm">
            {{ shortcut.registered ? '更新注册' : '一键注册' }}
          </button>
          <button v-if="shortcut.registered" @click="removeShortcut"
                  class="bg-slate-600 hover:bg-slate-500 px-3 py-1.5 rounded text-sm">移除</button>
          <span class="text-sm" :class="shortcut.msgOk ? 'text-emerald-400' : 'text-red-400'">
            {{ shortcut.msg }}
          </span>
        </div>
        <p class="text-xs text-slate-500 mt-2">
          注册即写入系统快捷键（等效于控制中心「自定义快捷键」），按一下开始听写、再按一下结束；
          录音中屏幕底部有波形悬浮框，识别结果自动粘贴到光标处。
        </p>
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
        <div v-if="log.raw_text && log.raw_text !== log.processed_text"
             class="text-xs text-slate-600 mt-0.5">原文：{{ log.raw_text }}</div>
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
    // V4/M11：UKUI 听写系统快捷键一键注册
    shortcut: { supported: false, registered: false, binding: 'Ctrl+Alt+V',
                msg: '', msgOk: true, busy: false },
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
        if (this.cfg && !this.cfg.polish) this.cfg.polish = {};
        if (this.cfg && this.cfg.polish) {
          if (!this.cfg.polish.mode) this.cfg.polish.mode = 'off';
          if (!this.cfg.polish.model) this.cfg.polish.model = 'deepseek-v4-flash';
          if (!this.cfg.polish.base_url) this.cfg.polish.base_url = 'https://api.deepseek.com/v1';
          if (this.cfg.polish.custom_prompt === undefined) this.cfg.polish.custom_prompt = '';
        }
        if (this.cfg && !this.cfg.transcription) this.cfg.transcription = {};
        if (this.cfg && this.cfg.transcription && !this.cfg.transcription.engine) {
          this.cfg.transcription.engine = 'shandianshuo';
        }
        if (this.cfg && this.cfg.transcription) {
          if (this.cfg.transcription.vad_silence_ms === undefined) this.cfg.transcription.vad_silence_ms = 0;
          if (this.cfg.transcription.max_duration_sec === undefined) this.cfg.transcription.max_duration_sec = 300;
          if (this.cfg.transcription.vad_lead_in_ms === undefined) this.cfg.transcription.vad_lead_in_ms = 10000;
        }
      }
      if (this.showSettings) this.refreshShortcut();
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
