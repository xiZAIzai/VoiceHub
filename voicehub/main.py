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
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from .clipboard_monitor import ClipboardMonitor, win32_read_text
from .config import Config
from .discovery import Discovery
from .hotkey import HotkeyRegistry
from .orchestrator import Orchestrator
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


def build_components(config_path: str = "config.json") -> Components:
    """加载配置并组装全部组件，绑定热键/剪贴板/路由/仪表盘。"""
    config = Config.load(config_path)
    storage = Storage(config.db_path, retention_days=config.retention_days)
    discovery = Discovery(
        receiver_port=config.receiver_port,
        offline_timeout_sec=config.offline_timeout_sec,
        scan_interval_sec=config.scan_interval_sec,
        discovery_port=config.discovery_port,
    )
    sticky = StickyTarget(pending_timeout_sec=config.pending_timeout_sec)
    # transport 必须注入：Router 不带 transport 时所有远程路由都会以 "no transport" 失败
    router = Router(config, discovery=discovery, transport=HttpPusher(), storage=storage)
    hotkeys = HotkeyRegistry()

    # 剪贴板监控：Windows 读 win32 剪贴板，其他平台用空实现（不可用即不监听）
    if sys.platform == "win32":
        read_text = win32_read_text
    else:
        read_text = lambda: None  # noqa: E731 - 非 Windows 平台不监听剪贴板

    def _on_text(text: str) -> None:
        logger.info("检测到转写文本（%d 字）", len(text))

    monitor = ClipboardMonitor(
        read_text=read_text,
        on_text=_on_text,
        stability_ms=config.stability_ms,
        pending_timeout_sec=config.pending_timeout_sec,
    )
    orchestrator = Orchestrator(config, sticky, monitor, router)
    dashboard = Dashboard(config, storage, sticky, discovery, hotkeys)

    # 注册目标热键：热键回调 → 编排层 select_target
    for key, target in config.targets.items():
        hotkeys.register(key, target.hotkey, lambda k=key: orchestrator.select_target(k))

    return Components(
        config=config, storage=storage, discovery=discovery, sticky=sticky,
        monitor=monitor, router=router, hotkeys=hotkeys,
        orchestrator=orchestrator, dashboard=dashboard,
    )


def run_web(config: Config, dashboard: Dashboard) -> None:
    """启动 FastAPI 仪表盘（阻塞当前线程）。"""
    import uvicorn

    uvicorn.run(dashboard.build_app(), host=config.server_host,
                port=config.server_port, log_level="warning")


def run_windows_backend(components: Components) -> None:
    """Windows 平台后端：全局热键 + 剪贴板监听 + 托盘（仅 Windows）。"""
    from .win_backend import start_windows_backend

    start_windows_backend(components)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="VoiceHub 语音转写多设备分发")
    parser.add_argument("--config", default="config.json", help="config.json 路径")
    parser.add_argument("--no-web", action="store_true", help="不启动仪表盘（仅 CLI）")
    args = parser.parse_args(argv)

    try:
        components = build_components(args.config)
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
    else:
        logger.info("非 Windows 平台：仅仪表盘 + 设备发现运行，Ctrl+C 退出")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
