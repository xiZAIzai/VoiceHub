"""V4/M12-① 转写润色：LLM 后处理（OpenAI 兼容 chat/completions）。

四模式（2026-08-26 与用户定案）：
- off        关闭：ASR 原文直出，零延迟（默认）
- light      轻整理：短句输入场景，输出自然句子（只清洗不改写）
- structured 结构化整理：长口述/笔记场景，产出适合下游大模型的高信息密度文本
             （prompt 为用户提供版本，原文照录，勿改动措辞）
- custom     自定义：用户 prompt（config 配置）

铁律：润色失败（超时/报错/空结果）一律降级用 ASR 原文继续路由，
绝不挡住文字上屏——语音输入场景延迟体验优先。
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_LIGHT = "light"
MODE_STRUCTURED = "structured"
MODE_CUSTOM = "custom"
MODES = (MODE_OFF, MODE_LIGHT, MODE_STRUCTURED, MODE_CUSTOM)

LIGHT_PROMPT = (
    "你是语音转写清理器。清理下面的口语转录文本，输出自然的书面句子：\n"
    "只删除语气词（嗯、啊、那个、就是说等）、无意义重复与口误；\n"
    "规范标点与中英文术语书写；不增删任何实质内容、不总结、不分段、\n"
    "不改变语序结构。只输出清理后的文字。"
)

STRUCTURED_PROMPT = (
    "你是语音转写整理器。将口语转录文本整理为适合下游大模型理解的高信息密度书面文字：\n"
    "\n"
    "1. **清洗降噪**：删除语气词（嗯、啊、那个、就是说等）、无意义重复与口误；"
    "规范中英文术语书写与标点。\n"
    "2. **主题聚合**：将零散跳跃、穿插提及的内容，按语义归类合并到对应的板块中。\n"
    "3. **精炼润色**：理顺语序，语言保持精炼，但严禁增删实质事实、不改变原意。\n"
    "\n"
    "输出规范：按主题使用【主题名称】分段列出整理后的要点内容，"
    "不要输出任何前言、总结或额外解释。"
)


class PolishError(RuntimeError):
    """润色失败（网络/鉴权/响应异常）。"""


class Polisher:
    """润色器：mode + 凭证 + prompt 选择。post 可注入（单测免网络）。"""

    def __init__(
        self,
        mode: str = MODE_OFF,
        base_url: str = "https://api.deepseek.com/v1",
        api_key: str = "",
        model: str = "deepseek-v4-flash",
        custom_prompt: str = "",
        timeout_sec: float = 20.0,
        post: Optional[Callable[[str, dict, dict], dict]] = None,
    ) -> None:
        self.mode = mode if mode in MODES else MODE_OFF
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._custom_prompt = custom_prompt
        self._timeout = timeout_sec
        self._post = post or self._http_post

    # ---------- 状态 ----------
    def enabled(self) -> bool:
        """可用 = 模式非 off + 有凭证 +（custom 时 prompt 非空）。"""
        if self.mode == MODE_OFF or not self._api_key:
            return False
        if self.mode == MODE_CUSTOM and not self._custom_prompt.strip():
            return False
        return True

    def prompt(self) -> str:
        if self.mode == MODE_LIGHT:
            return LIGHT_PROMPT
        if self.mode == MODE_STRUCTURED:
            return STRUCTURED_PROMPT
        if self.mode == MODE_CUSTOM:
            return self._custom_prompt
        return ""

    # ---------- 执行 ----------
    def polish(self, text: str) -> str:
        """返回润色后文本；失败抛 PolishError（调用方降级用原文）。"""
        if not self.enabled() or not text.strip():
            return text
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.prompt()},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = self._post(url, payload, headers)
        try:
            out = str(resp["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise PolishError(f"润色响应格式异常: {e}") from e
        if not out:
            raise PolishError("润色结果为空")
        return out

    def _http_post(self, url: str, payload: dict, headers: dict) -> dict:
        import httpx

        try:
            with httpx.Client(trust_env=False, timeout=self._timeout) as client:
                r = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise PolishError(f"润色请求失败: {e}") from e
        if r.status_code != 200:
            raise PolishError(f"润色接口返回 {r.status_code}: {r.text[:120]}")
        return r.json()
