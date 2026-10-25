"""Records keyboard/mouse-click input with timing and replays it into the game.

Use it for the parts template matching cannot handle, like walking through the
tutorial map. Clicks are stored as fractions of the game window so the macro
survives window moves. Recording stops with F12.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import winio


def _key_name(key) -> str | None:
    from pynput import keyboard
    special = {keyboard.Key.esc: "esc", keyboard.Key.space: "space", keyboard.Key.enter: "enter",
               keyboard.Key.tab: "tab", keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift",
               keyboard.Key.alt_l: "alt", keyboard.Key.f1: "f1", keyboard.Key.f2: "f2",
               keyboard.Key.f3: "f3", keyboard.Key.f4: "f4"}
    if key in special:
        return special[key]
    ch = getattr(key, "char", None)
    if ch and ch.lower() in winio.SCAN:
        return ch.lower()
    return None


def record(out: Path):
    winio.require_windows()
    from pynput import keyboard, mouse
    hwnd = winio.find_window()
    events: list[dict] = []
    t0 = time.perf_counter()
    held: set[str] = set()
    stop = {"flag": False}

    def now():
        return round(time.perf_counter() - t0, 3)

    def on_press(key):
        if key == keyboard.Key.f12:
            stop["flag"] = True
            return False
        name = _key_name(key)
        if name and name not in held:
            held.add(name)
            events.append({"t": now(), "kind": "down", "key": name})

    def on_release(key):
        name = _key_name(key)
        if name and name in held:
            held.discard(name)
            events.append({"t": now(), "kind": "up", "key": name})

    def on_click(x, y, button, pressed):
        if pressed and button == mouse.Button.left:
            cx, cy, w, h = winio.client_rect(hwnd)
            if 0 <= x - cx < w and 0 <= y - cy < h:
                events.append({"t": now(), "kind": "click", "x": round((x - cx) / w, 4), "y": round((y - cy) / h, 4)})

    print(f"녹화 시작: 게임 창에서 조작하세요. F12 = 종료 -> {out}")
    winio.focus(hwnd)
    t0 = time.perf_counter()
    with keyboard.Listener(on_press=on_press, on_release=on_release) as kl, mouse.Listener(on_click=on_click) as ml:
        while not stop["flag"]:
            time.sleep(0.05)
        ml.stop()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"events": events}, indent=1), "utf-8")
    print(f"{len(events)} events, {events[-1]['t'] if events else 0:.1f}s 저장됨")


def replay(path: Path, hwnd: int, speed: float = 1.0, dry_run: bool = False):
    events = json.loads(Path(path).read_text("utf-8"))["events"]
    if not dry_run:
        winio.focus(hwnd)
    start = time.perf_counter()
    held: set[str] = set()
    try:
        for ev in events:
            while time.perf_counter() - start < ev["t"] / speed:
                winio.check_abort()
                time.sleep(0.005)
            if dry_run:
                continue
            if ev["kind"] == "down":
                winio.key_down(ev["key"])
                held.add(ev["key"])
            elif ev["kind"] == "up":
                winio.key_up(ev["key"])
                held.discard(ev["key"])
            elif ev["kind"] == "click":
                x, y, w, h = winio.client_rect(hwnd)
                winio.click(x + ev["x"] * w, y + ev["y"] * h)
    finally:
        for k in held:
            winio.key_up(k)
