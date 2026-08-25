"""linux_tray 纯逻辑单测：pixmap 转换、菜单模型、SNI 属性表。

DBus 收发循环不在此覆盖（需真实会话总线），由 smoke_appimage.sh 冒烟 +
openKylin 实机（UKUI StatusNotifierWatcher 在线）人工验证。
"""
import pytest

from voicehub.linux_tray import (MENU_ID_OPEN, MENU_ID_QUIT, MENU_ID_ROOT,
                                 MenuModel, argb32_pixmap, sni_properties)


def test_argb32_pixmap_pixel_order():
    """单像素 RGBA→ARGB32 网络字节序：alpha 移到高位。"""
    px = argb32_pixmap(bytes([255, 0, 0, 128]), 1, 1)  # R=255 G=0 B=0 A=128
    assert px == (1, 1, b"\x80\xff\x00\x00")


def test_argb32_pixmap_size_mismatch():
    with pytest.raises(ValueError):
        argb32_pixmap(b"\x00" * 8, 2, 2)


def test_menu_model_layout_and_actions():
    """布局树：根 0 + 打开/退出两个子项；点击分发按 id 路由，未知 id 不炸。"""
    calls: list[str] = []
    menu = MenuModel(on_open=lambda: calls.append("open"),
                     on_quit=lambda: calls.append("quit"))
    root_id, root_props, children = menu.layout_tree()
    assert root_id == MENU_ID_ROOT
    assert root_props == {"children-display": "submenu"}
    assert [c[0] for c in children] == [MENU_ID_OPEN, MENU_ID_QUIT]
    assert children[0][1]["label"] == "打开仪表盘"
    assert children[1][1]["label"] == "退出"
    assert children[0][1]["enabled"] is True

    assert menu.activate(MENU_ID_OPEN) is True
    assert menu.activate(MENU_ID_QUIT) is True
    assert calls == ["open", "quit"]
    assert menu.activate(999) is False


def test_sni_properties_shape():
    """属性表：签名与值配对，Menu 指向 DBusMenu 对象路径，空图标降级为空数组。"""
    pixmap = (2, 2, b"\x00" * 16)
    props = sni_properties("VoiceHub", "/MenuBar", pixmap, "desc")
    assert props["Category"] == ("s", "ApplicationStatus")
    assert props["Status"] == ("s", "Active")
    assert props["Menu"] == ("o", "/MenuBar")
    assert props["ItemIsMenu"] == ("b", False)
    assert props["IconPixmap"] == ("a(iiay)", [pixmap])
    assert props["ToolTip"] == ("(s(iiay)ss)", ("", pixmap, "VoiceHub", "desc"))

    empty = sni_properties("VoiceHub", "/MenuBar", None)
    assert empty["IconPixmap"] == ("a(iiay)", [])
    assert empty["ToolTip"][1][1] == (0, 0, b"")
