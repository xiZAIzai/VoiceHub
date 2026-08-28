"""API 凭证写入服务（V4/M12）：给普通用户的自助填 key 通道。

安全设计（对应「key 绝不入库」红线）：
- 唯一写入目标 = config.json 同目录的 config.local.json（gitignore 覆盖），
  与既有 Config.load 深度合并语义对接；绝不触碰被版本库跟踪的 config.json。
- 允许写入的字段白名单：transcription.{api_key,app_key,access_key} +
  polish.api_key —— 其余键一律忽略（防止经 HTTP 往 local 塞任意配置）。
- 读取只返回脱敏状态（是否已配置 + 尾 4 位），完整 key 永不出服务。
- 落盘 chmod 600（尽力而为，Windows 忽略）；临时文件 + os.replace 原子替换。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 字段白名单：域 -> 允许写入的 key 字段
ALLOWED = {
    "transcription": ("api_key", "app_key", "access_key"),
    "polish": ("api_key",),
}


class CredentialsService:
    """config.local.json 的凭证专用读写器（与 ConfigService 的 config.json 分离）。"""

    def __init__(self, config_path: str | Path) -> None:
        self._path = Path(config_path).resolve().parent / "config.local.json"

    # ---------- 读（脱敏） ----------
    def status(self) -> dict[str, dict[str, dict[str, Any]]]:
        """各白名单字段的脱敏状态：{domain: {field: {set, tail}}}。"""
        data = self._read()
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for domain, fields in ALLOWED.items():
            out[domain] = {}
            for f in fields:
                val = str(((data.get(domain) or {}).get(f)) or "")
                out[domain][f] = {
                    "set": bool(val),
                    "tail": ("…" + val[-4:]) if len(val) >= 8 else "",
                }
        return out

    # ---------- 写（合并 + 原子 + 600） ----------
    def update(self, payload: dict[str, dict[str, str]]) -> dict[str, Any]:
        """合并写入非空凭证；返回 {ok, updated:[...]}。非法域/字段静默忽略。"""
        data = self._read()
        updated: list[str] = []
        for domain, fields in ALLOWED.items():
            incoming = payload.get(domain) or {}
            slot = dict(data.get(domain) or {})
            for f in fields:
                if f in incoming:
                    val = str(incoming[f]).strip()
                    if val:
                        slot[f] = val
                        updated.append(f"{domain}.{f}")
            if slot:
                data[domain] = slot
        if not updated:
            return {"ok": False, "error": "没有可写入的凭证字段（需非空）"}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass  # Windows 等：尽力而为
        except OSError as e:
            logger.warning("凭证写入失败: %s", e)
            return {"ok": False, "error": f"写入失败: {e}"}
        logger.info("凭证已更新: %s（config.local.json）", ", ".join(updated))
        return {"ok": True, "updated": updated}

    # ---------- 内部 ----------
    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as e:
            logger.warning("config.local.json 解析失败（保持不覆盖）: %s", e)
            return {}
