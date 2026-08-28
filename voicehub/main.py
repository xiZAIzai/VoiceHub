"""主入口：配置加载、组件组装、平台后端启动（Windows 热键/剪贴板/托盘）。

分层职责（CLAUDE.md §2.2）：
- 入口只做组装与生命周期，业务流程在 orchestrator / router 里。
- Windows 特有部分（全局热键、WM_CLIPBOARDUPDATE、托盘、自启）按平台守卫，
  仅 Windows 下实例化；跨平台逻辑（config/storage/discovery/router/web）可全平台运行。

启动顺序：加载配置 → 组装组件 → 起 Web 仪表盘 → （Windows）起热键/剪贴板/托盘。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .clipboard_monitor import ClipboardMonitor, win32_read_text
from .config import Config
from .discovery import Discovery
from .hotkey import HotkeyRegistry
from .orchestrator import Orchestrator
from .paths import app_dir, default_config_path, resolve_data_path
from .credentials import CredentialsService
from .settings import ConfigService
from .router import Router
from .state import StickyTarget
from .storage import Storage
from .transport import HttpPusher
from .web import Dashboard

logger = logging.getLogger(__name__)


@dataclass
class Components:
    """组装完成后的组件集合，供平台后端与测试引用。"""

    config: Config
    storage: Storage
    discovery: Discovery
    sticky: StickyTarget
    monitor: ClipboardMonitor
    router: Router
    hotkeys: HotkeyRegistry
    orchestrator: Orchestrator
    dashboard: Dashboard
    dictation: Optional[object] = None  # V4/M11 builtin 听写引擎（engine=builtin 时存在）


def build_components(config_path: str | Path = "config.json") -> Components:
    """加载配置并组装全部组件，绑定热键/剪贴板/路由/仪表盘。"""
    config_path = Path(config_path)
    config = Config.load(config_path)
    # db 相对路径锚定 config 所在目录：exe 模式下 CWD 不可靠（M6 打包需求）
    db_path = resolve_data_path(config.db_path, base_dir=config_path.parent)
    storage = Storage(db_path, retention_days=config.retention_days)
    discovery = Discovery(
        receiver_port=config.receiver_port,
        offline_timeout_sec=config.offline_timeout_sec,
        scan_interval_sec=config.scan_interval_sec,
        discovery_port=config.discovery_port,
    )
    sticky = StickyTarget(pending_timeout_sec=config.pending_timeout_sec)
    # transport 必须注入：Router 不带 transport 时所有远程路由都会以 "no transport" 失败；
    # clipboard_write 供 builtin 直通链路本地投递（V4/ADR-9）；paste_at_cursor 贴到光标
    clipboard_write = _clipboard_writer()
    router = Router(config, discovery=discovery, transport=HttpPusher(), storage=storage,
                    clipboard_write=clipboard_write, paste_at_cursor=_cursor_paster())
    hotkeys = HotkeyRegistry()

    # 剪贴板监控：Windows 读 win32 剪贴板，Linux 读 xclip（V3/M8），
    # 其他平台用空实现（不可用即不监听）
    if sys.platform == "win32":
        read_text = win32_read_text
    elif sys.platform.startswith("linux"):
        from .linux_backend import xclip_read_text

        read_text = xclip_read_text
    else:
        read_text = lambda: None  # noqa: E731 - 非 Windows/Linux 平台不监听剪贴板

    def _on_text(text: str) -> None:
        logger.info("检测到转写文本（%d 字）", len(text))

    monitor = ClipboardMonitor(
        read_text=read_text,
        on_text=_on_text,
        stability_ms=config.stability_ms,
        pending_timeout_sec=config.pending_timeout_sec,
    )
    orchestrator = Orchestrator(config, sticky, monitor, router)
    # 设置页（M6-③）：config.json 唯一写入方，去抖/超时参数可热应用
    config_service = ConfigService(config_path, monitor=monitor, sticky=sticky)
    # V4/M12：凭证自助填写（写 gitignored 的 config.local.json，key 永不出服务）
    credentials = CredentialsService(config_path)

    # V4/M11：builtin 听写引擎（录音 → 云 ASR → orchestrator 直通路由）
    dictation = build_dictation(config, orchestrator)

    dashboard = Dashboard(config, storage, sticky, discovery, hotkeys,
                          settings=config_service, dictation=dictation,
                          credentials=credentials)

    # 注册目标热键：热键回调 → 编排层 select_target
    for key, target in config.targets.items():
        hotkeys.register(key, target.hotkey, lambda k=key: orchestrator.select_target(k))

    return Components(
        config=config, storage=storage, discovery=discovery, sticky=sticky,
        monitor=monitor, router=router, hotkeys=hotkeys,
        orchestrator=orchestrator, dashboard=dashboard, dictation=dictation,
    )


def _clipboard_writer():
    """平台剪贴板写入器（builtin 直通链路本地投递）；未知平台返回 None。"""
    if sys.platform == "win32":
        from .win_backend import win32_write_text

        return win32_write_text
    if sys.platform.startswith("linux"):
        from .linux_backend import write_clipboard_text

        return write_clipboard_text
    return None


def _cursor_paster():
    """光标处粘贴器（听写文本自动上屏）；无实现平台返回 None（仅剪贴板模式）。"""
    if sys.platform.startswith("linux"):
        from .linux_backend import xdotool_paste

        return xdotool_paste
    return None


def build_dictation(config: Config, orchestrator: Orchestrator):
    """组装听写引擎；engine != builtin 或依赖/密钥缺失时返回 None（其余功能照常）。

    密钥来源：config.local.json / 环境变量（见 Config.load），种子 config 恒不含。
    """
    if config.transcription.engine != "builtin":
        return None
    tc = config.transcription
    has_credential = tc.api_key or (tc.app_key and tc.access_key)
    if not has_credential:
        logger.warning("transcription.engine=builtin 但未配置 ASR 凭证"
                       "（api_key 或 app_key+access_key，走 config.local.json 或环境变量），"
                       "听写不可用")
        return None
    try:
        from .dictation import DictationEngine, VadTracker
        from .dictation.asr_client import VolcengineSaucClient
        from .dictation.recorder import MicrophoneRecorder

        vad = VadTracker(
            silence_ms=tc.vad_silence_ms,
            threshold=tc.vad_threshold,
            lead_in_ms=tc.vad_lead_in_ms,
            max_duration_ms=int(tc.max_duration_sec * 1000),
        )
        recorder = MicrophoneRecorder(sample_rate=tc.sample_rate, vad=vad)
        provider = VolcengineSaucClient(
            api_key=tc.api_key,
            app_key=tc.app_key,
            access_key=tc.access_key,
            base_url=tc.base_url,
            resource_id=tc.resource_id,
            language=tc.language,
        )

        def _route(text: str, metadata: dict) -> dict:
            return orchestrator.route_direct(text, metadata=metadata)

        pc = config.polish
        polisher = None
        if pc.mode != "off" and pc.api_key:
            from .dictation.polisher import Polisher

            polisher = Polisher(
                mode=pc.mode, base_url=pc.base_url, api_key=pc.api_key,
                model=pc.model, custom_prompt=pc.custom_prompt,
                timeout_sec=pc.timeout_sec)
            logger.info("润色已启用（模式=%s，模型=%s）", pc.mode, pc.model)
        engine = DictationEngine(
            recorder, provider, _route, max_duration_sec=tc.max_duration_sec,
            polisher=polisher)
        recorder.set_auto_stop_callback(engine.request_stop)
        logger.info("builtin 听写引擎已启用（%s，资源 %s）",
                    tc.base_url.rsplit("/", 1)[-1], tc.resource_id)
        return engine
    except Exception:  # noqa: BLE001 - 引擎组装失败不拖垮主程序
        logger.exception("听写引擎组装失败（sounddevice/websocket 依赖缺失?）")
        return None


def run_web(config: Config, dashboard: Dashboard) -> None:
    """启动 FastAPI 仪表盘（阻塞当前线程）。

    - log_config=None：不让 uvicorn 走自己的 dictConfig（其 ColourizedFormatter
      调 sys.stdout.isatty()，无控制台时崩溃），日志统一走根 logger（文件）。
    - 线程内异常必须落日志：dashboard 线程死掉 = 仪表盘白屏"无法访问"，
      静默死亡是事故（2026-08-20 白屏事故根因之一）。
    """
    import uvicorn

    try:
        uvicorn.run(dashboard.build_app(), host=config.server_host,
                    port=config.server_port, log_level="warning",
                    log_config=None)
    except Exception:  # noqa: BLE001 - dashboard 线程死亡必须可见
        logger.exception("仪表盘线程异常退出（端口 %s）", config.server_port)


def run_windows_backend(components: Components) -> None:
    """Windows 平台后端：全局热键 + 剪贴板监听 + 托盘（仅 Windows）。"""
    from .win_backend import start_windows_backend

    start_windows_backend(components)


def _setup_logging() -> None:
    """日志初始化：源码运行输出到控制台；打包（frozen/windowed）落 exe 目录日志文件。

    windowed exe 双击启动时无控制台句柄，sys.stdout/stderr 为 None —— 任何库
    调 isatty()/fileno() 都会 AttributeError（实测 uvicorn 的 ColourizedFormatter
    因此崩溃，dashboard 线程静默死亡 → 窗口白屏"无法访问"）。这里先用 devnull
    兜底再初始化日志。
    """
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    if getattr(sys, "frozen", False):
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        # 日志写文件，方便排查问题
        log_dir = app_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=fmt,
                            handlers=[logging.FileHandler(
                                log_dir / "voicehub.log", encoding="utf-8")])
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)


def run_dictate_cli(config_path: str | Path) -> int:
    """--dictate：触发运行中实例的听写开关后退出。

    Wayland 下 X11 全局热键不可靠（pynput 仅 XWayland 聚焦时收得到键），
    这是给 UKUI 系统快捷键 / wlr 绑定工具用的稳态入口（ADR-9 触发第三通道）。
    """
    import httpx

    config = Config.load(config_path)
    url = f"http://{config.server_host}:{config.server_port}/api/dictate/toggle"
    try:
        r = httpx.post(url, timeout=5, trust_env=False)
        data = r.json()
    except Exception as e:  # noqa: BLE001 - CLI 场景错误直接打印
        print(f"触发失败（实例未运行?）: {e}")
        return 1
    if data.get("ok"):
        print(f"已触发听写: {data.get('state')}")
        return 0
    print(f"触发失败: {data.get('error')}")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="VoiceHub 语音转写多设备分发")
    parser.add_argument("--config", default=None,
                        help="config.json 路径（默认：exe 目录 / 当前目录下 config.json）")
    parser.add_argument("--no-web", action="store_true", help="不启动仪表盘（仅 CLI）")
    parser.add_argument("--dictate", action="store_true",
                        help="触发运行中实例的听写开关后退出（供系统快捷键调用）")
    args = parser.parse_args(argv)
    config_path = args.config if args.config else default_config_path()
    if args.dictate:
        return run_dictate_cli(config_path)

    try:
        components = build_components(config_path)
    except Exception as e:  # noqa: BLE001 - 组装失败要显式上报
        logger.error("组装失败: %s", e)
        return 1

    components.discovery.start()
    logger.info("VoiceHub 启动，目标 %d 个", len(components.config.targets))

    if not args.no_web:
        threading.Thread(target=run_web, args=(components.config, components.dashboard),
                         name="dashboard", daemon=True).start()

    if sys.platform == "win32":
        run_windows_backend(components)  # 阻塞：热键/托盘消息循环
    elif sys.platform.startswith("linux"):
        from .linux_backend import start_linux_backend

        start_linux_backend(components)  # 阻塞：xclip 轮询 + pynput 热键（V3/M8）
    else:
        logger.info("非 Windows/Linux 平台：仅仪表盘 + 设备发现运行，Ctrl+C 退出")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
