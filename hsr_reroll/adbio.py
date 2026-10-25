"""ADB control of Android emulators (MuMu, LDPlayer, BlueStacks, Nox ...).

Every emulator instance exposes an adb port; one Device per instance lets the
same scenario run on several instances in parallel.
"""

from __future__ import annotations

import re
import shutil
import subprocess

PACKAGES = {
    "global": "com.HoYoverse.hkrpgoversea",
    "cn": "com.miHoYo.hkrpg",
}

# Default adb ports per emulator family (instance index i = 0, 1, 2 ...).
EMULATOR_PORTS = {
    "mumu12": [16384 + 32 * i for i in range(16)],
    "ldplayer": [5555 + 2 * i for i in range(16)],
    "bluestacks": [5555 + 10 * i for i in range(8)],
    "nox": [62001] + [62025 + i for i in range(15)],
    "memu": [21503 + 10 * i for i in range(8)],
}

KEYCODES = {"esc": 4, "back": 4, "enter": 66, "home": 3, "space": 62, "tab": 61}

GACHA_GREP = r'https://[^" ]*gacha_record/api/get(Ld)?GachaLog[^" ]*'


class AdbError(RuntimeError):
    pass


def find_adb(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    raise AdbError("adb not found. Install platform-tools or pass --adb <path to emulator's adb.exe>.")


class Adb:
    def __init__(self, adb_path: str | None = None, runner=subprocess.run):
        self.path = find_adb(adb_path)
        self._run = runner

    def call(self, *args: str, timeout: float = 30, check: bool = True) -> bytes:
        res = self._run([self.path, *args], capture_output=True, timeout=timeout)
        if check and res.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} failed: {res.stderr.decode(errors='ignore').strip()}")
        return res.stdout

    def devices(self) -> list[str]:
        out = self.call("devices").decode(errors="ignore")
        return [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]

    def connect_ports(self, ports: list[int], host: str = "127.0.0.1") -> list[str]:
        ok = []
        for p in ports:
            out = self.call("connect", f"{host}:{p}", timeout=5, check=False).decode(errors="ignore")
            if "connected" in out and "cannot" not in out:
                ok.append(f"{host}:{p}")
        return ok


class Device:
    def __init__(self, adb: Adb, serial: str, package: str = PACKAGES["global"]):
        self.adb = adb
        self.serial = serial
        self.package = package

    def sh(self, cmd: str, timeout: float = 30, check: bool = True) -> bytes:
        return self.adb.call("-s", self.serial, "shell", cmd, timeout=timeout, check=check)

    def screencap(self):
        import cv2
        import numpy as np
        png = self.adb.call("-s", self.serial, "exec-out", "screencap", "-p", timeout=15)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise AdbError(f"{self.serial}: screencap returned no image")
        return img

    def tap(self, x: int, y: int):
        self.sh(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        self.sh(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}", timeout=ms / 1000 + 15)

    def key(self, name: str):
        code = KEYCODES.get(name)
        if code is None:
            raise AdbError(f"key {name!r} has no Android keycode; use tap/swipe instead")
        self.sh(f"input keyevent {code}")

    def text(self, s: str):
        self.sh("input text " + re.sub(r"([\\\"'`$&|;<>() ])", r"\\\1", s).replace(" ", "%s"))

    def launch(self):
        self.sh(f"monkey -p {self.package} -c android.intent.category.LAUNCHER 1")

    def stop(self):
        self.sh(f"am force-stop {self.package}")

    def is_running(self) -> bool:
        return bool(self.sh(f"pidof {self.package}", check=False).strip())

    def read_gacha_urls(self) -> bytes:
        """Greps the game's WebView cache for warp-record URLs. Needs root (emulator root toggle)."""
        cmd = f"grep -rhaoE '{GACHA_GREP}' /data/data/{self.package} 2>/dev/null"
        out = self.sh(f"su -c \"{cmd}\"", check=False)
        if not out.strip():
            out = self.sh(cmd, check=False)  # adbd already running as root
        return out
