"""角落清单：屏幕角落的置顶小窗口，条目随时可改。收起时窗口完全隐藏，不占屏幕。

展开/收起：全局快捷键（默认 Ctrl+Alt+L），或把鼠标移到屏幕角落停一下；窗口里点收起或按 Esc 也能收起。
再次运行 checklist.bat 时不会开第二个，而是把已有的展开。
"""

import ctypes
import ctypes.wintypes as wt
import socket
import threading
import time
import tomllib
from pathlib import Path

import webview

from workbench.config import ROOT

from . import store

TITLE = "AnotherMe 清单"
PORT = 8767           # 只用来保证单实例
WEB = Path(__file__).resolve().parent / "web"
user32 = ctypes.windll.user32
# 句柄是指针宽度，不声明参数类型时 -1（HWND_TOPMOST）在 64 位下会被截断，调用失败
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.FindWindowW.restype = wt.HWND


def settings() -> dict:
    s = {"corner": "top-right", "hotkey": "ctrl+alt+l", "width": 320}
    cfg = ROOT / "config.toml"
    if cfg.exists():
        s.update(tomllib.loads(cfg.read_text(encoding="utf-8")).get("checklist", {}))
    return s


S = settings()
win: webview.Window | None = None
shown = False
height = 200


# ---------- 位置 ----------

def work_area() -> tuple[int, int, int, int]:
    """主屏除去任务栏的区域，物理像素。"""
    r = wt.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def scale() -> float:
    try:
        return ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
    except Exception:
        return 1.0


def hwnd() -> int:
    return user32.FindWindowW(None, TITLE)


def place():
    """把窗口贴到配置的角落，留 12 像素边距。直接用 Win32 设置物理像素，避开缩放换算的差异。"""
    h = hwnd()
    if not h:
        return
    k = scale()
    w, hh = int(S["width"] * k), int(height * k)
    l, t, r, b = work_area()
    m = int(12 * k)
    x = r - w - m if "right" in S["corner"] else l + m
    y = b - hh - m if "bottom" in S["corner"] else t + m
    HWND_TOPMOST, SWP_NOACTIVATE = wt.HWND(-1), 0x10
    user32.SetWindowPos(h, HWND_TOPMOST, x, y, w, hh, SWP_NOACTIVATE)


def show(focus: bool):
    global shown
    if not win:
        return
    shown = True
    win.show()
    place()
    if focus:
        user32.SetForegroundWindow(hwnd())
        win.evaluate_js("focusLast()")


def hide():
    global shown
    if win:
        shown = False
        win.hide()


def toggle(focus: bool = True):
    hide() if shown else show(focus)


# ---------- 唤出方式 ----------

VK = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}, "space": 0x20, "`": 0xC0}
MOD = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}


def hotkey_loop():
    parts = S["hotkey"].lower().replace(" ", "").split("+")
    mods = sum(MOD[p] for p in parts[:-1]) | 0x4000  # MOD_NOREPEAT
    if not user32.RegisterHotKey(None, 1, mods, VK[parts[-1]]):
        return  # 快捷键被别的程序占了，就只用角落唤出
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            toggle(focus=True)


def corner_loop():
    """鼠标在屏幕角落停 0.4 秒就展开。要求停留，是为了不在点最大化窗口的关闭按钮时误触。"""
    since = None
    while True:
        time.sleep(0.1)
        if shown:
            since = None
            continue
        p = wt.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        x_ok = p.x >= w - 3 if "right" in S["corner"] else p.x <= 2
        y_ok = p.y >= h - 3 if "bottom" in S["corner"] else p.y <= 2
        if x_ok and y_ok:
            since = since or time.time()
            if time.time() - since > 0.4:
                show(focus=False)
                since = None
        else:
            since = None


def instance_loop(sock: socket.socket):
    while True:
        conn, _ = sock.accept()
        conn.close()
        show(focus=True)


# ---------- 给页面调用的接口 ----------

class Api:
    def load(self):
        return {"items": store.items(), "hotkey": S["hotkey"]}

    def add(self, text, pos):
        return store.add(text, pos)

    def edit(self, item_id, text):
        store.edit(item_id, text)

    def delete(self, item_id):
        store.delete(item_id)

    def move(self, item_id, pos):
        store.move(item_id, pos)

    def fit(self, h):
        global height
        _, t, _, b = work_area()
        height = int(max(96, min(h, (b - t) / scale() * 0.75)))
        if shown:
            place()

    def collapse(self):
        hide()


def main():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", PORT))
    except OSError:  # 已经开着一个：让它展开
        socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        return
    sock.listen()
    global win
    win = webview.create_window(TITLE, str(WEB / "index.html"), js_api=Api(), width=S["width"], height=height,
                                frameless=True, on_top=True, resizable=False, hidden=True,
                                background_color="#22252d")

    def started():
        threading.Thread(target=hotkey_loop, daemon=True).start()
        threading.Thread(target=corner_loop, daemon=True).start()
        threading.Thread(target=instance_loop, args=(sock,), daemon=True).start()
        time.sleep(0.6)  # 等页面算好高度
        show(focus=True)

    webview.start(started)


if __name__ == "__main__":
    main()
