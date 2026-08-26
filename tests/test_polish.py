"""V4/M12-① 润色单测：模式/prompt 选择、注入 post、引擎降级、config 解析。"""

import pytest

from voicehub.config import Config
from voicehub.dictation.engine import DictationEngine
from voicehub.dictation.polisher import (
    LIGHT_PROMPT,
    STRUCTURED_PROMPT,
    PolishError,
    Polisher,
)
from tests.test_dictation import _FakeProvider, _FakeRecorder, _wait_idle


# ---------- Polisher 纯逻辑 ----------

def _ok_post(content="润色结果"):
    def post(url, payload, headers):
        assert payload["messages"][0]["content"]  # system prompt 非空
        assert "chat/completions" in url
        return {"choices": [{"message": {"content": content}}]}
    return post


def test_mode_enablement_rules():
    assert Polisher(mode="off", api_key="k").enabled() is False
    assert Polisher(mode="light", api_key="k").enabled() is True
    assert Polisher(mode="light", api_key="").enabled() is False
    assert Polisher(mode="custom", api_key="k", custom_prompt="").enabled() is False
    assert Polisher(mode="custom", api_key="k", custom_prompt="我的规则").enabled() is True
    assert Polisher(mode="不存在的模式", api_key="k").mode == "off"  # 非法回退


def test_prompt_selection():
    assert Polisher(mode="light").prompt() == LIGHT_PROMPT
    assert Polisher(mode="structured").prompt() == STRUCTURED_PROMPT
    assert "【主题名称】" in STRUCTURED_PROMPT and "下游大模型" in STRUCTURED_PROMPT
    assert "不增删任何实质内容" in LIGHT_PROMPT
    assert Polisher(mode="custom", custom_prompt="我的规则").prompt() == "我的规则"


def test_polish_returns_content_and_passes_raw_text():
    captured = {}

    def post(url, payload, headers):
        captured["user"] = payload["messages"][1]["content"]
        captured["auth"] = headers["Authorization"]
        return {"choices": [{"message": {"content": "  干净的结果  "}}]}

    p = Polisher(mode="light", api_key="sk-test", post=post)
    assert p.polish("嗯那个原始文本") == "干净的结果"
    assert captured["user"] == "嗯那个原始文本"
    assert captured["auth"] == "Bearer sk-test"


def test_polish_http_error_raises():
    def bad(url, payload, headers):
        raise PolishError("润色接口返回 401")

    p = Polisher(mode="light", api_key="k", post=bad)
    with pytest.raises(PolishError):
        p.polish("文本")


def test_polish_empty_content_raises():
    p = Polisher(mode="light", api_key="k", post=_ok_post(content="  "))
    with pytest.raises(PolishError):
        p.polish("文本")


# ---------- 引擎接入（成功 / 降级） ----------

class _FixedPolisher:
    def __init__(self, result=None, error=None):
        self.mode = "light"
        self._result = result
        self._error = error

    def enabled(self):
        return True

    def polish(self, text):
        if self._error:
            raise self._error
        return self._result


def _engine_with(polisher, routed):
    def _route(text, metadata):
        routed.append((text, metadata))
        return {"ok": True, "target": "desktop"}

    return DictationEngine(_FakeRecorder(), _FakeProvider("嗯那个原始文本"),
                           _route, polisher=polisher)


def test_engine_polishes_before_route():
    routed = []
    engine = _engine_with(_FixedPolisher(result="干净的整理结果"), routed)
    engine.toggle(); engine.toggle()
    _wait_idle(engine)
    text, meta = routed[0]
    assert text == "干净的整理结果"          # 路由拿到润色文本
    assert meta["raw_text"] == "嗯那个原始文本"  # 原文随元数据传递（落库专用列）
    assert meta["polish"] == "light"
    result = engine.last_result()
    assert result["text"] == "干净的整理结果"
    assert result["raw_text"] == "嗯那个原始文本"


def test_engine_polish_failure_falls_back_to_raw():
    routed = []
    engine = _engine_with(_FixedPolisher(error=PolishError("超时")), routed)
    engine.toggle(); engine.toggle()
    _wait_idle(engine)
    text, meta = routed[0]
    assert text == "嗯那个原始文本"  # 铁律：失败降级原文，不挡上屏
    assert meta["polish"] == "failed"


def test_engine_polish_empty_result_falls_back():
    routed = []
    engine = _engine_with(_FixedPolisher(result="   "), routed)
    engine.toggle(); engine.toggle()
    _wait_idle(engine)
    assert routed[0][0] == "嗯那个原始文本"
    assert routed[0][1]["polish"] == "failed"


# ---------- config 解析 ----------

def test_polish_config_defaults_and_parse(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICEHUB_POLISH_API_KEY", raising=False)
    seed = tmp_path / "config.json"
    seed.write_text("{}", encoding="utf-8")
    cfg = Config.load(seed)
    assert cfg.polish.mode == "off"  # 默认关闭
    assert cfg.polish.api_key == ""

    seed.write_text('{"polish": {"mode": "structured", "model": "m1"}}', encoding="utf-8")
    cfg = Config.load(seed)
    assert cfg.polish.mode == "structured" and cfg.polish.model == "m1"
    assert cfg.polish.base_url.endswith("/v1")  # 其余键保默认


def test_polish_env_key_wins(tmp_path, monkeypatch):
    seed = tmp_path / "config.json"
    seed.write_text('{"polish": {"api_key": "local"}}', encoding="utf-8")
    monkeypatch.setenv("VOICEHUB_POLISH_API_KEY", "env-key")
    assert Config.load(seed).polish.api_key == "env-key"
