"""热键注册表单测：解析、注册、冲突、派发、激活。"""

from voicehub.hotkey import HotkeyRegistry, parse_hotkey


def test_parse_with_modifier():
    assert parse_hotkey("alt+2").combo() == "alt+2"
    assert parse_hotkey("Alt+2").combo() == "alt+2"
    assert parse_hotkey("alt + 2").combo() == "alt+2"


def test_parse_default_modifier():
    assert parse_hotkey("2").combo() == "alt+2"
    assert parse_hotkey("3").combo() == "alt+3"


def test_parse_invalid():
    assert parse_hotkey("") is None
    assert parse_hotkey("+2") is None
    assert parse_hotkey("alt+") is None


def test_register_and_dispatch():
    r = HotkeyRegistry()
    called = []
    assert r.register("laptop", "2", lambda: called.append("laptop")) is True
    assert r.dispatch("laptop") is True
    assert called == ["laptop"]
    assert r.bindings() == {"laptop": "alt+2"}


def test_conflict_rejected():
    r = HotkeyRegistry()
    assert r.register("laptop", "2", lambda: None) is True
    assert r.register("tablet", "2", lambda: None) is False


def test_same_key_reregister_allowed():
    """同名覆盖不算冲突。"""
    r = HotkeyRegistry()
    assert r.register("laptop", "2", lambda: None) is True
    assert r.register("laptop", "3", lambda: None) is True
    assert r.bindings() == {"laptop": "alt+3"}


def test_invalid_spec_rejected():
    r = HotkeyRegistry()
    assert r.register("laptop", "", lambda: None) is False
    assert "laptop" not in r.bindings()


def test_unregister_removes_binding():
    r = HotkeyRegistry()
    r.register("laptop", "2", lambda: None)
    r.unregister("laptop")
    assert r.bindings() == {}
    assert r.dispatch("laptop") is False
