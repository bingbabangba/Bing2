"""Input/screen backends the scenario runner drives: the Windows PC client or an ADB emulator."""

from __future__ import annotations

from . import gachalog, winio


class Unsupported(RuntimeError):
    pass


class WindowsBackend:
    """Star Rail PC client window."""

    def __init__(self, titles: list[str] | None = None, game_dir: str | None = None):
        self.hwnd = winio.find_window(titles)
        self.screen = winio.Screen(self.hwnd)
        self.game_dir = game_dir
        self.name = "pc"

    def size(self):
        _, _, w, h = self.screen.rect()
        return w, h

    def grab(self):
        return self.screen.grab()

    def check_abort(self):
        winio.check_abort()

    def prepare(self):
        winio.focus(self.hwnd)

    def tap(self, x, y):
        winio.focus(self.hwnd)
        ox, oy, _, _ = self.screen.rect()
        winio.click(ox + x, oy + y)

    def key(self, name, hold=0.08):
        winio.focus(self.hwnd)
        winio.press(name, hold)

    def key_down(self, name):
        winio.key_down(name)

    def key_up(self, name):
        winio.key_up(name)

    def swipe(self, x1, y1, x2, y2, ms):
        raise Unsupported("swipe is for emulators; on PC record a macro and use replay")

    def launch(self):
        raise Unsupported("start the PC client yourself")

    def stop(self):
        raise Unsupported("close the PC client yourself")

    def read_authkeys(self) -> list[str]:
        return gachalog.read_authkeys(self.game_dir)

    def startup_warning(self) -> str | None:
        if not winio.is_admin():
            return "경고: 관리자 권한이 아닙니다. 게임이 입력을 무시할 수 있습니다 (run_admin.bat 사용)."
        return None


class AdbBackend:
    """One Android emulator instance reached over adb."""

    def __init__(self, device):
        self.dev = device
        self.name = device.serial.replace(":", "_").replace(".", "_")
        self._size = None

    def size(self):
        if self._size is None:
            h, w = self.dev.screencap().shape[:2]
            self._size = (w, h)
        return self._size

    def grab(self):
        img = self.dev.screencap()
        self._size = (img.shape[1], img.shape[0])
        return img

    def check_abort(self):
        winio.check_abort()  # F12 still works when the host is Windows

    def prepare(self):
        pass

    def tap(self, x, y):
        self.dev.tap(x, y)

    def key(self, name, hold=0.08):
        self.dev.key(name)

    def key_down(self, name):
        raise Unsupported("held keys are PC-only; use swipe with a duration on the joystick")

    key_up = key_down

    def swipe(self, x1, y1, x2, y2, ms):
        self.dev.swipe(x1, y1, x2, y2, ms)

    def launch(self):
        self.dev.launch()

    def stop(self):
        self.dev.stop()

    def read_authkeys(self) -> list[str]:
        return gachalog.extract_authkeys(self.dev.read_gacha_urls())

    def startup_warning(self) -> str | None:
        return None
