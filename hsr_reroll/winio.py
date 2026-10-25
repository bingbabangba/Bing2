"""Windows-only window lookup, screen capture and input (ctypes, no pywin32 needed).

Star Rail runs elevated, so the script must also run as Administrator or the
game silently ignores synthetic input.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

WINDOW_TITLES = ["崩坏：星穹铁道", "Honkai: Star Rail", "붕괴: 스타레일"]

# DirectInput-style scan codes; the game reads scan codes, not virtual keys.
SCAN = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "enter": 0x1C, "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22,
    "shift": 0x2A, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "m": 0x32,
    "alt": 0x38, "space": 0x39, "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E,
    "j": 0x24, "k": 0x25, "l": 0x26, "h": 0x23, "i": 0x17, "o": 0x18, "p": 0x19, "u": 0x16,
}
VK_ABORT = 0x7B  # F12

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()

    class _KBD(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSE(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", _KBD), ("mi", _MOUSE), ("pad", ctypes.c_byte * 32)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class Aborted(Exception):
    pass


def require_windows():
    if not IS_WINDOWS:
        raise SystemExit("This command needs Windows with the Star Rail PC client.")


def is_admin() -> bool:
    return IS_WINDOWS and bool(ctypes.windll.shell32.IsUserAnAdmin())


def check_abort():
    if IS_WINDOWS and user32.GetAsyncKeyState(VK_ABORT) & 0x8000:
        raise Aborted("F12 pressed")


def find_window(titles: list[str] | None = None) -> int:
    require_windows()
    for t in titles or WINDOW_TITLES:
        hwnd = user32.FindWindowW(None, t)
        if hwnd:
            return hwnd
    raise RuntimeError("Star Rail window not found. Start the game first (windowed 1920x1080 recommended).")


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the client area in screen pixels."""
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def focus(hwnd: int):
    if user32.GetForegroundWindow() != hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)


def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def key_down(name: str):
    inp = _INPUT(type=1)
    inp.u.ki = _KBD(0, SCAN[name], 0x0008, 0, 0)  # KEYEVENTF_SCANCODE
    _send(inp)


def key_up(name: str):
    inp = _INPUT(type=1)
    inp.u.ki = _KBD(0, SCAN[name], 0x0008 | 0x0002, 0, 0)
    _send(inp)


def press(name: str, hold: float = 0.08):
    key_down(name)
    time.sleep(hold)
    key_up(name)


def click(x: int, y: int, hold: float = 0.06):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for flag in (0x0002, 0x0004):  # LEFTDOWN, LEFTUP
        inp = _INPUT(type=0)
        inp.u.mi = _MOUSE(0, 0, 0, flag, 0, 0)
        _send(inp)
        time.sleep(hold)


def scroll(amount: int):
    inp = _INPUT(type=0)
    inp.u.mi = _MOUSE(0, 0, ctypes.c_ulong(amount * 120).value, 0x0800, 0, 0)
    _send(inp)


class Screen:
    """Grabs the game's client area as a BGR numpy array."""

    def __init__(self, hwnd: int):
        import mss
        self.hwnd = hwnd
        self._mss = mss.mss()

    def rect(self):
        return client_rect(self.hwnd)

    def grab(self):
        import numpy as np
        x, y, w, h = self.rect()
        shot = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3].copy()
