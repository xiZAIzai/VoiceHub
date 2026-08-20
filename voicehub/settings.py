"""配置读写服务（M6-③）：config.json 的读取 / 校验 / 原子写回 / 热应用。

设计：
- 前端「设置」页通过 GET/PUT /api/config 编辑配置，本服务是唯一写入方。
- 校验复用 Config._apply（类型转换失败即非法），另加业务规则（热键唯一等）。
- 原子写回：先写 .tmp 再 os.replace，避免写一半崩溃留下残缺配置。
- 热应用：仅低风险参数（去抖 / 粘滞超时）即时生效到运行中组件；
  其余变更（目标 / 热键 / 端口 / db 路径等）返回 need_restart，由前端提示重启。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from .config import Config

logger = logging.getLogger(__name__)

# 可热应用的配置路径（改完即时生效，无需重启）
HOT_PATHS = {
    ("voicehub", "stability_ms"),
    ("voicehub", "pending_timeout_sec"),
}


class ConfigError(ValueError):
    """配置校验失败（类型非法 / 业务规则冲突），由 Web 层转 400。"""


class ConfigService:
    """config.json 读写服务：持有文件路径与可热应用的组件引用。"""

    def __init__(self, path: str | Path, monitor: Optional[object] = None,
                 sticky: Optional[object] = None) -> None:
        self._path = Path(path)
        self._monitor = monitor
        self._sticky = sticky

    # ---------- 读 ----------
    def get(self) -> dict[str, Any]:
        """读取 config.json 原始字典（保持结构原样，未知键原样透传）。"""
        if self._path.exists():
            return json.loads(self._path.read_text(encoding="utf-8"))
        return {}

    # ---------- 写 ----------
    def update(self, new_raw: dict[str, Any]) -> dict[str, Any]:
        """校验 + 原子写回 + 热应用。

        返回 {"ok": True, "need_restart": bool, "changed": [...]}；
        校验失败抛 ConfigError（文件保持原样）。
        """
        old_raw = self.get()
        cfg = self._validate(new_raw)
        self._write_atomic(new_raw)
        changed = self._changed_paths(old_raw, new_raw)
        self._apply_hot(cfg, changed)
        need_restart = any(p not in HOT_PATHS for p in changed)
        logger.info("配置已更新: changed=%s need_restart=%s", changed, need_restart)
        return {"ok": True, "need_restart": need_restart, "changed": sorted(
            ".".join(p) for p in changed)}

    def _validate(self, raw: dict[str, Any]) -> Config:
        """结构/类型校验（复用 Config._apply）+ 业务规则，非法抛 ConfigError。"""
        if not isinstance(raw, dict):
            raise ConfigError("配置必须是 JSON 对象")
        cfg = Config()
        try:
            cfg._apply(raw)  # noqa: SLF001 - 复用既有解析做类型校验
        except (TypeError, ValueError) as e:
            raise ConfigError(f"配置字段非法: {e}") from e
        hotkeys = [t.hotkey for t in cfg.targets.values()]
        if len(hotkeys) != len(set(hotkeys)):
            raise ConfigError("多个目标使用了相同热键（Alt+N 冲突）")
        for t in cfg.targets.values():
            if t.type not in ("local", "network_http"):
                raise ConfigError(f"目标 {t.key} 类型非法: {t.type}（应为 local/network_http）")
        return cfg

    def _write_atomic(self, raw: dict[str, Any]) -> None:
        """临时文件 + 原子替换写回。"""
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def _changed_paths(self, old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, ...]]:
        """深度对比，返回变更的路径元组列表（顶层键粒度展开到子键）。"""
        changed: list[tuple[str, ...]] = []

        def _walk(a: Any, b: Any, prefix: tuple[str, ...]) -> None:
            if type(a) is not type(b):
                changed.append(prefix)
                return
            if isinstance(a, dict):
                for k in set(a) | set(b):
                    _walk(a.get(k), b.get(k), (*prefix, k))
            elif a != b:
                changed.append(prefix)

        _walk(old, new, ())
        return changed

    def _apply_hot(self, cfg: Config, changed: list[tuple[str, ...]]) -> None:
        """把热应用字段即时更新到运行中组件（去抖 / 粘滞超时）。"""
        paths = set(changed)
        if self._monitor is not None:
            self._monitor.apply_params(  # type: ignore[attr-defined]
                stability_ms=cfg.stability_ms if ("voicehub", "stability_ms") in paths else None,
                pending_timeout_sec=(cfg.pending_timeout_sec
                                     if ("voicehub", "pending_timeout_sec") in paths else None))
        if self._sticky is not None and ("voicehub", "pending_timeout_sec") in paths:
            self._sticky.pending_timeout = cfg.pending_timeout_sec  # type: ignore[attr-defined]
