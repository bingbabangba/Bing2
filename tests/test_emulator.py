"""Engine tests against a fake adb binary and a fake game screen (no emulator needed)."""

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from hsr_reroll import adbio, gachalog, runner, vision
from hsr_reroll.backends import AdbBackend
from hsr_reroll.ledger import Ledger


def screen_with(marker: bool, seed=0):
    rng = np.random.default_rng(seed)
    img = (rng.random((540, 960, 3)) * 60).astype(np.uint8)
    if marker:
        cv2.putText(img, "WARP", (400, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 5)
    return img


class FakeAdb:
    """Stands in for subprocess.run(['adb', ...])."""

    def __init__(self, screens, urls=b""):
        self.screens = screens  # callable(serial) -> image
        self.urls = urls
        self.calls = []

    def __call__(self, argv, capture_output=True, timeout=None):
        args = argv[1:]
        self.calls.append(args)
        out = b""
        if args[0] == "devices":
            out = b"List of devices attached\n127.0.0.1:16384\tdevice\n127.0.0.1:16416\tdevice\n"
        elif args[0] == "connect":
            out = b"connected to " + args[1].encode() if args[1].endswith(("16384", "16416")) else b"cannot connect"
        elif "screencap" in args:
            out = cv2.imencode(".png", self.screens(args[1]))[1].tobytes()
        elif args[2] == "shell" and "grep" in args[3]:
            out = self.urls if args[3].startswith("su") else b""
        return subprocess.CompletedProcess(argv, 0, out, b"")


def make_adb(fake):
    a = adbio.Adb.__new__(adbio.Adb)
    a.path, a._run = "adb", fake
    return a


def test_device_commands():
    fake = FakeAdb(lambda s: screen_with(False))
    adb = make_adb(fake)
    assert adb.devices() == ["127.0.0.1:16384", "127.0.0.1:16416"]
    assert adb.connect_ports([16384, 16416, 16448]) == ["127.0.0.1:16384", "127.0.0.1:16416"]
    d = adbio.Device(adb, "127.0.0.1:16384")
    d.tap(10, 20)
    d.swipe(1, 2, 3, 4, 500)
    d.key("esc")
    d.launch()
    shells = [c[3] for c in fake.calls if len(c) > 3 and c[2] == "shell"]
    assert shells == ["input tap 10 20", "input swipe 1 2 3 4 500", "input keyevent 4",
                      "monkey -p com.HoYoverse.hkrpgoversea -c android.intent.category.LAUNCHER 1"]
    assert d.screencap().shape == (540, 960, 3)
    with pytest.raises(adbio.AdbError):
        d.key("w")


def test_authkeys_from_emulator_cache():
    url = (b"https://public-operation-hkrpg-sg.hoyoverse.com/common/gacha_record/api/getGachaLog?"
           b"authkey_ver=1&authkey=EMUKEY&lang=en\n")
    adb = make_adb(FakeAdb(lambda s: screen_with(False), urls=url))
    be = AdbBackend(adbio.Device(adb, "127.0.0.1:16384"))
    assert be.read_authkeys() == ["EMUKEY"]


def test_multi_emulator_cycle_end_to_end(tmp_path, monkeypatch):
    # Each device shows the WARP marker from its 3rd screenshot on.
    grabs = {}

    def screens(serial):
        grabs[serial] = grabs.get(serial, 0) + 1
        return screen_with(grabs[serial] >= 3)

    keys = {"127.0.0.1:16384": b"K1", "127.0.0.1:16416": b"K2"}

    class PerDeviceAdb(FakeAdb):
        def __call__(self, argv, capture_output=True, timeout=None):
            if len(argv) > 4 and argv[3] == "shell" and "grep" in argv[4]:
                self.urls = (b"https://x.hoyoverse.com/common/gacha_record/api/getGachaLog?authkey="
                             + keys[argv[2]] + b"&a=1\n")
            return super().__call__(argv, capture_output, timeout)

    fake = PerDeviceAdb(screens)
    adb = make_adb(fake)

    templ_dir = tmp_path / "templates"
    vision.Templates(templ_dir).save("menu_warp", vision.crop_to_template(screen_with(True), (380, 240, 260, 80)))

    results = {"K1": ("800000001", ["Mortenax Blade", "Bronya"]), "K2": ("800000002", ["Yanqing"])}

    def fake_fetch_all(key, delay=0.3, getter=None):
        uid, names = results[key]
        return [gachalog.Warp(str(i), uid, "Character Event", n, "Character", 5, "t") for i, n in enumerate(names)]

    monkeypatch.setattr(gachalog, "fetch_all", fake_fetch_all)

    scenario = {"cycle": [
        {"type": "launch", "wait": 0},
        {"type": "wait", "template": "menu_warp", "timeout": 10},
        {"type": "click", "template": "menu_warp"},
        {"type": "swipe", "x1": 0.15, "y1": 0.75, "x2": 0.15, "y2": 0.6, "ms": 100},
        {"type": "screenshot", "name": "warp"},
        {"type": "fetch_results"},
    ]}
    sc = tmp_path / "s.json"
    sc.write_text(json.dumps(scenario))
    ledger = Ledger(tmp_path / "acc.json")
    devices = [adbio.Device(adb, s) for s in adb.devices()]
    monkeypatch.chdir(tmp_path)
    errors = runner.run_multi(sc, templ_dir, ledger, devices, accounts=1)
    assert errors == {}

    by_label = {a["label"]: a for a in Ledger(tmp_path / "acc.json").accounts}
    assert by_label["e1-001"]["five_star_names"] == ["Mortenax Blade", "Bronya"]
    assert by_label["e1-001"]["score"] == 15
    assert by_label["e2-001"]["uid"] == "800000002"
    taps = [c[3] for c in fake.calls if len(c) > 3 and c[3].startswith("input tap")]
    assert len(taps) == 2  # one template click per device, at the marker position
    x, y = map(int, taps[0].split()[2:4])
    assert 380 < x < 640 and 240 < y < 320
    assert (tmp_path / "reroll_data" / "shots" / "e1-001").is_dir()
