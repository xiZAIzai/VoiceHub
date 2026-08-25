"""Linux 托盘：DBus StatusNotifierItem（SNI）+ DBusMenu 直写实现（V3 随用随修）。

背景（2026-08-25 openKylin SP2 实机）：M8 曾定案「Linux 无托盘，仪表盘走浏览器」；
实机使用发现程序完全不可见。且 openKylin 为 Wayland 会话（kylin-wlcom），pystray 的
xorg 后端（XEmbed 托盘）不可用、appindicator 后端需把 GTK 整套打进 AppImage。实测
UKUI 面板运行 org.kde.StatusNotifierWatcher（SNI 宿主在线），故用 jeepney（纯 Python
DBus 库）直写协议，零 GTK 依赖、AppImage 友好：

- 菜单对位 Windows 托盘精简版：打开仪表盘 / 退出（左键单击 = 打开仪表盘）；
- 无 watcher / 无会话总线 / 无 jeepney：降级为日志提示，其余功能照常（沿热键降级先例）；
- 线程模型：专用 daemon 线程跑 jeepney 阻塞收发循环，stop() 或菜单「退出」结束。

协议参考：
- SNI: https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/
- DBusMenu: https://github.com/AyatanaIndicators/libdbusmenu/blob/master/docs/dbus-menu.xml
"""
from __future__ import annotations

import logging
import os
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SNI_IFACE = "org.kde.StatusNotifierItem"
SNI_WATCHER = "org.kde.StatusNotifierWatcher"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
MENU_IFACE = "com.canonical.dbusmenu"

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

MENU_ID_ROOT = 0
MENU_ID_OPEN = 10
MENU_ID_QUIT = 90

ICON_SIZE_PX = 32

# dbusmenu 条目属性名 → DBus 签名（label/enabled/visible 是标准三件套）
_MENU_PROP_SIG = {"label": "s", "enabled": "b", "visible": "b", "children-display": "s"}


# ---------- 纯逻辑（可单测，不依赖 jeepney/PIL） ----------
def argb32_pixmap(rgba: bytes, width: int, height: int) -> tuple[int, int, bytes]:
    """RGBA 字节串（PIL Image.tobytes() 顺序）→ SNI IconPixmap 的 (w, h, ARGB32)。

    SNI 规范要求像素为网络字节序 uint32：alpha 在高位，R/G/B 依次在低位。
    """
    if len(rgba) != width * height * 4:
        raise ValueError(f"RGBA 长度 {len(rgba)} 与 {width}x{height} 不符")
    out = bytearray(width * height * 4)
    for i in range(width * height):
        r, g, b, a = rgba[i * 4:(i + 1) * 4]
        struct.pack_into(">I", out, i * 4, (a << 24) | (r << 16) | (g << 8) | b)
    return (width, height, bytes(out))


class MenuModel:
    """托盘菜单纯逻辑：条目属性 + 点击分发（与 DBus 细节解耦）。"""

    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None],
                 label_open: str = "打开仪表盘", label_quit: str = "退出") -> None:
        base = {"enabled": True, "visible": True}
        self._items: dict[int, dict[str, Any]] = {
            MENU_ID_OPEN: {"label": label_open, **base},
            MENU_ID_QUIT: {"label": label_quit, **base},
        }
        self._actions: dict[int, Callable[[], None]] = {
            MENU_ID_OPEN: on_open, MENU_ID_QUIT: on_quit,
        }

    def layout_tree(self) -> tuple[int, dict[str, Any], list]:
        """dbusmenu 布局树（纯 Python 表示；DBus 层负责 Variant 包装与递归嵌套）。"""
        children = [(i, dict(props), []) for i, props in sorted(self._items.items())]
        return (MENU_ID_ROOT, {"children-display": "submenu"}, children)

    def item(self, item_id: int) -> Optional[dict[str, Any]]:
        return self._items.get(item_id)

    def activate(self, item_id: int) -> bool:
        act = self._actions.get(item_id)
        if act is None:
            logger.warning("托盘菜单未知条目: %s", item_id)
            return False
        act()
        return True


def sni_properties(title: str, menu_path: str,
                   pixmap: Optional[tuple[int, int, bytes]],
                   description: str = "") -> dict[str, tuple[str, Any]]:
    """SNI 属性表：属性名 → (DBus 签名, 纯 Python 值)；Properties.Get/GetAll 共用。"""
    return {
        "Category": ("s", "ApplicationStatus"),
        "Id": ("s", "org.VoiceHub.tray"),
        "Title": ("s", title),
        "Status": ("s", "Active"),
        "WindowId": ("u", 0),
        "IconName": ("s", ""),
        "IconPixmap": ("a(iiay)", [pixmap] if pixmap else []),
        "AttentionIconName": ("s", ""),
        "AttentionIconPixmap": ("a(iiay)", []),
        "OverlayIconName": ("s", ""),
        "OverlayIconPixmap": ("a(iiay)", []),
        "ToolTip": ("(s(iiay)ss)", ("", pixmap or (0, 0, b""), title, description)),
        "Menu": ("o", menu_path),
        "ItemIsMenu": ("b", False),
    }


# ---------- 图标 ----------
def _icon_file_candidates() -> list[Path]:
    """图标候选路径：frozen 用 PyInstaller datas 收编的 assets/，源码用仓库 assets/。"""
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(Path(base) / "assets" / "voicehub.png")
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "voicehub.png")
    return cands


def load_icon_pixmap(size: int = ICON_SIZE_PX) -> Optional[tuple[int, int, bytes]]:
    """加载托盘图标 → ARGB32 pixmap；assets 缺失时 PIL 兜底画蓝底白点（同 Windows 款）。"""
    try:
        from PIL import Image, ImageDraw
    except Exception as e:  # noqa: BLE001 - 缺 Pillow 属降级路径（图标空白但托盘可用）
        logger.warning("Pillow 不可用，托盘图标为空: %s", e)
        return None
    for cand in _icon_file_candidates():
        try:
            img = Image.open(cand).convert("RGBA").resize((size, size))
            return argb32_pixmap(img.tobytes(), size, size)
        except OSError:
            continue
    img = Image.new("RGBA", (size, size), (37, 99, 235, 255))
    m = size // 4
    ImageDraw.Draw(img).ellipse((m, m, size - m, size - m), fill=(255, 255, 255, 255))
    return argb32_pixmap(img.tobytes(), size, size)


def _import_dbus() -> SimpleNamespace:
    """延迟导入 jeepney（requirements 仅 linux 标记安装），打成一个命名空间传参。

    jeepney 0.9 起变体值就是普通 (签名, 值) 元组（Variant 仅是内部类型描述符），
    因此本模块不 import Variant，一律传二元组。
    """
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import (HeaderFields, MessageType,  # noqa: F401
                                  DBusAddress, new_error, new_method_call,
                                  new_method_return)

    return SimpleNamespace(HeaderFields=HeaderFields, MessageType=MessageType,
                           open_dbus_connection=open_dbus_connection,
                           DBusAddress=DBusAddress, new_error=new_error,
                           new_method_call=new_method_call,
                           new_method_return=new_method_return)


class LinuxTray:
    """SNI 托盘句柄：start() 起后台线程，stop() 关闭；所有降级路径只留日志不抛出。"""

    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None],
                 title: str = "VoiceHub", description: str = "语音转写多设备分发") -> None:
        self._menu = MenuModel(on_open=on_open, on_quit=on_quit)
        self._title = title
        self._description = description
        self._pixmap: Optional[tuple[int, int, bytes]] = None
        self._revision = 1  # dbusmenu 版本号（菜单静态，恒 1 即可）
        self._bus_name = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dbus: Optional[SimpleNamespace] = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """起托盘线程（图标加载失败不阻塞，仅影响图标显示）。"""
        self._pixmap = load_icon_pixmap()
        self._thread = threading.Thread(target=self._run, name="linux-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2)

    # ---------- DBus 线程主体 ----------
    def _run(self) -> None:
        try:
            self._dbus = db = _import_dbus()
        except Exception as e:  # noqa: BLE001 - 依赖缺失属预期降级路径
            logger.warning("jeepney 不可用，Linux 托盘未启动: %s", e)
            return
        try:
            conn = db.open_dbus_connection(bus="SESSION")
        except Exception as e:  # noqa: BLE001 - 无桌面会话（SSH/纯 tty）属降级路径
            logger.warning("无法连接 DBus 会话总线，托盘未启动: %s", e)
            return
        try:
            if self._claim_name(conn) and self._register_with_watcher(conn):
                logger.info("Linux 托盘已启动（SNI: %s）", self._bus_name)
                self._serve(conn)
        except Exception:  # noqa: BLE001 - 托盘线程死掉必须可见，但不拖垮主程序
            logger.exception("托盘线程异常退出")
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info("Linux 托盘已退出")

    def _claim_name(self, conn) -> bool:
        """申请唯一总线名 org.kde.StatusNotifierItem-<pid>-1（SNI 约定命名）。"""
        db = self._dbus
        self._bus_name = f"{SNI_IFACE}-{os.getpid()}-1"
        msg = db.new_method_call(
            db.DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus",
                           "org.freedesktop.DBus"),
            "RequestName", "su", (self._bus_name, 4))  # 4 = DO_NOT_QUEUE
        code = conn.send_and_get_reply(msg).body[0]
        if code not in (1, 4):  # 1 新属主 / 4 已是属主；2/3 = 名字被占
            logger.warning("申请总线名失败（返回 %s），托盘未启动", code)
            return False
        return True

    def _register_with_watcher(self, conn) -> bool:
        """向面板 watcher 报到；无 SNI 宿主（返回错误）时降级。"""
        db = self._dbus
        watcher = db.DBusAddress("/StatusNotifierWatcher", SNI_WATCHER, SNI_WATCHER)
        msg = db.new_method_call(watcher, "RegisterStatusNotifierItem", "s",
                                 (self._bus_name,))
        try:
            conn.send_and_get_reply(msg)
        except Exception as e:  # noqa: BLE001 - 无 watcher 属降级路径
            logger.warning("注册 StatusNotifierWatcher 失败（桌面无 SNI 托盘宿主?）: %s", e)
            return False
        return True

    def _serve(self, conn) -> None:
        """收发循环：0.5s 超时轮询，保证 stop() 两拍内退出。"""
        while not self._stop.is_set():
            try:
                msg = conn.receive(timeout=0.5)
            except TimeoutError:
                continue
            except OSError:
                break  # 连接被关闭
            if msg.header.message_type != self._dbus.MessageType.method_call:
                continue
            reply = self._dispatch(msg)
            if reply is not None:
                conn.send_message(reply)

    # ---------- 消息分发 ----------
    def _dispatch(self, msg):
        db = self._dbus
        f = msg.header.fields
        path = f.get(db.HeaderFields.path)
        iface = f.get(db.HeaderFields.interface) or ""
        member = f.get(db.HeaderFields.member) or ""
        try:
            if path == ITEM_PATH:
                return self._dispatch_item(msg, iface, member)
            if path == MENU_PATH:
                return self._dispatch_menu(msg, iface, member)
            return db.new_error(msg, "org.freedesktop.DBus.Error.UnknownObject")
        except Exception:  # noqa: BLE001 - 单条消息异常必须可见且不杀线程
            logger.exception("托盘处理 %s.%s 异常", iface, member)
            return db.new_error(msg, "org.freedesktop.DBus.Error.Failed",
                                "s", ("internal error",))

    def _dispatch_item(self, msg, iface: str, member: str):
        """StatusNotifierItem 对象：Properties + Activate/SecondaryActivate/Scroll。"""
        db = self._dbus
        if member == "Ping":  # org.freedesktop.DBus.Peer
            return db.new_method_return(msg)
        if member == "Introspect":  # 部分宿主会先 Introspect
            return db.new_method_return(msg, "s", ("<node></node>",))
        if iface == PROPS_IFACE:
            props = sni_properties(self._title, MENU_PATH, self._pixmap,
                                   self._description)
            if member == "Get":
                sig_val = props.get(msg.body[1])
                if sig_val is None:
                    return db.new_error(msg, "org.freedesktop.DBus.Error.InvalidArgs")
                return db.new_method_return(msg, "v", (sig_val,))
            if member == "GetAll":
                return db.new_method_return(msg, "a{sv}", (dict(props),))
        if member in ("Activate", "SecondaryActivate"):  # 左/右键单击：开仪表盘
            self._menu.activate(MENU_ID_OPEN)
            return db.new_method_return(msg)
        if member == "Scroll":
            return db.new_method_return(msg)
        return db.new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod")

    def _dispatch_menu(self, msg, iface: str, member: str):
        """com.canonical.dbusmenu 对象：GetLayout/Event 等（UKUI 面板菜单渲染协议）。"""
        db = self._dbus
        if iface == PROPS_IFACE:
            if member == "Get" and msg.body[1] == "Version":
                return db.new_method_return(msg, "v", (("u", 3),))
            if member == "GetAll":
                return db.new_method_return(msg, "a{sv}", ({"Version": ("u", 3)},))
        if iface != MENU_IFACE:
            return db.new_error(msg, "org.freedesktop.DBus.Error.UnknownInterface")
        if member == "GetLayout":
            parent = msg.body[0]
            tree = self._menu.layout_tree() if parent == MENU_ID_ROOT \
                else (parent, {}, [])
            return db.new_method_return(msg, "u(ia{sv}av)",
                                        (self._revision, self._menu_node_value(tree)))
        if member == "GetGroupProperties":
            body = [(i, self._wrap_props(self._menu.item(i)))
                    for i in msg.body[0] if self._menu.item(i) is not None]
            return db.new_method_return(msg, "a(ia{sv})", (body,))
        if member == "GetProperty":
            props = self._menu.item(msg.body[0])
            if props is None or msg.body[1] not in props:
                return db.new_error(msg, "org.freedesktop.DBus.Error.InvalidArgs")
            sig = _MENU_PROP_SIG[msg.body[1]]
            return db.new_method_return(msg, "v", ((sig, props[msg.body[1]]),))
        if member == "Event":
            item_id, event_id = msg.body[0], msg.body[1]
            if event_id == "clicked":
                self._menu.activate(item_id)
            return db.new_method_return(msg)
        if member == "EventGroup":
            for item_id, event_id, _data, _ts in msg.body[0]:
                if event_id == "clicked":
                    self._menu.activate(item_id)
            return db.new_method_return(msg, "ai", ([],))
        if member == "AboutToShow":
            return db.new_method_return(msg, "b", (False,))
        if member == "AboutToShowGroup":
            return db.new_method_return(msg, "aiai", ([], []))
        return db.new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod")

    # ---------- dbusmenu 装配 ----------
    def _wrap_props(self, props: dict[str, Any]) -> dict:
        return {k: (_MENU_PROP_SIG.get(k, "s"), v) for k, v in props.items()}

    def _menu_node_value(self, node) -> tuple:
        """布局树节点 → (id, a{sv}, av)；子节点以 (签名, 值) 变体包成 (ia{sv}av)。"""
        node_id, props, children = node
        wrapped_children = [("(ia{sv}av)", self._menu_node_value(c)) for c in children]
        return (node_id, self._wrap_props(props), wrapped_children)
