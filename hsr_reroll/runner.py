"""JSON scenario engine that drives the game through template matching and replayed macros.

Works with the PC client (backends.WindowsBackend) or an emulator over adb
(backends.AdbBackend); `run_multi` runs one Runner per emulator in parallel.

Scenario file:
{
  "threshold": 0.85,
  "label_prefix": "acc",
  "setup":  [steps...],          # run once
  "cycle":  [steps...],          # run once per account (--accounts N)
  "subroutines": {"name": [steps...]}
}

Step types (all keys besides "type" are parameters):
  sleep        {"sec": 1.5}
  key          {"key": "esc", "times": 1, "interval": 0.3, "hold": 0.08}   (adb: esc/back, enter, home)
  click        {"template": "name"} or {"x": 0.5, "y": 0.9} (fractions of the screen)
               optional "timeout" (seconds to wait for the template, default 10), "optional": true
  swipe        {"x1": 0.15, "y1": 0.75, "x2": 0.15, "y2": 0.6, "ms": 3000}  (adb; hold the joystick to walk)
  wait         {"template": "name", "timeout": 60}   waits until visible
  wait_gone    {"template": "name", "timeout": 60}
  wait_any     {"templates": ["a","b"], "timeout": 60}  -> remembers which one matched
  until        {"template": "name", "do": [steps], "timeout": 600}  repeats `do` until visible
  if           {"template": "name" | "matched": "a", "then": [...], "else": [...]}
  repeat       {"times": 10, "do": [...]}
  call         {"name": "subroutine"}
  replay       {"file": "macros/tutorial_walk.json", "speed": 1.0}
  launch / stop_app {}               start / force-stop the game (adb)
  prompt       {"message": "text"}   waits for Enter (manual step: sign-up, captcha, email code)
  screenshot   {"name": "result"}
  fetch_results {}                   reads the warp history of the logged-in account into the ledger
  log          {"message": "text"}
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import gachalog
from .ledger import Ledger

PROMPT_LOCK = threading.Lock()


class StepTimeout(RuntimeError):
    pass


class Runner:
    def __init__(self, scenario_path: Path, templates_dir: Path, ledger: Ledger, backend,
                 shots_dir: Path = Path("reroll_data/shots"), dry_run: bool = False,
                 label_prefix: str | None = None):
        from .vision import Templates
        self.scenario_path = Path(scenario_path)
        self.scenario = json.loads(self.scenario_path.read_text("utf-8"))
        self.templates = Templates(templates_dir)
        self.threshold = self.scenario.get("threshold", 0.85)
        self.ledger = ledger
        self.backend = backend
        self.shots_dir = Path(shots_dir)
        self.dry_run = dry_run
        self.prefix = label_prefix or self.scenario.get("label_prefix", "acc")
        self.label = "setup"
        self.matched: str | None = None

    # ---------- primitives ----------
    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] [{self.label}] {msg}", flush=True)

    def look(self, name: str):
        from .vision import find
        self.backend.check_abort()
        return find(self.backend.grab(), self.templates.get(name), self.threshold)

    def wait_for(self, names: list[str], timeout: float, gone: bool = False):
        end = time.time() + timeout
        while time.time() < end:
            for n in names:
                m = self.look(n)
                if (m is None) == gone:
                    return n, m
            time.sleep(0.4)
        raise StepTimeout(f"{'still visible' if gone else 'not seen'}: {names} after {timeout}s")

    def click_at(self, cx: int, cy: int):
        if self.dry_run:
            self.log(f"(dry) click {cx},{cy}")
            return
        self.backend.tap(cx, cy)

    def frac(self, fx: float, fy: float) -> tuple[int, int]:
        w, h = self.backend.size()
        return int(fx * w), int(fy * h)

    # ---------- steps ----------
    def run_steps(self, steps: list[dict]):
        for step in steps:
            self.backend.check_abort()
            getattr(self, "s_" + step["type"])(step)

    def s_sleep(self, st):
        end = time.time() + st["sec"]
        while time.time() < end:
            self.backend.check_abort()
            time.sleep(min(0.2, max(0, end - time.time())))

    def s_log(self, st):
        self.log(st["message"].format(label=self.label))

    def s_key(self, st):
        for _ in range(st.get("times", 1)):
            if self.dry_run:
                self.log(f"(dry) key {st['key']}")
            else:
                self.backend.key(st["key"], st.get("hold", 0.08))
            time.sleep(st.get("interval", 0.3))

    def s_click(self, st):
        if "template" in st:
            try:
                _, m = self.wait_for([st["template"]], st.get("timeout", 10))
            except StepTimeout:
                if st.get("optional"):
                    return
                raise
            self.click_at(m.x + st.get("dx", 0), m.y + st.get("dy", 0))
        else:
            self.click_at(*self.frac(st["x"], st["y"]))
        time.sleep(st.get("after", 0.5))

    def s_swipe(self, st):
        x1, y1 = self.frac(st["x1"], st["y1"])
        x2, y2 = self.frac(st["x2"], st["y2"])
        if self.dry_run:
            self.log(f"(dry) swipe {x1},{y1} -> {x2},{y2} {st.get('ms', 300)}ms")
            return
        self.backend.swipe(x1, y1, x2, y2, st.get("ms", 300))
        time.sleep(st.get("after", 0.3))

    def s_launch(self, st):
        if not self.dry_run:
            self.backend.launch()
        self.s_sleep({"sec": st.get("wait", 5)})

    def s_stop_app(self, st):
        if not self.dry_run:
            self.backend.stop()
        time.sleep(1)

    def s_wait(self, st):
        self.wait_for([st["template"]], st.get("timeout", 60))

    def s_wait_gone(self, st):
        self.wait_for([st["template"]], st.get("timeout", 60), gone=True)

    def s_wait_any(self, st):
        self.matched, _ = self.wait_for(st["templates"], st.get("timeout", 60))
        self.log(f"matched {self.matched}")

    def s_until(self, st):
        end = time.time() + st.get("timeout", 600)
        while self.look(st["template"]) is None:
            if time.time() > end:
                raise StepTimeout(f"until {st['template']} timed out")
            self.run_steps(st["do"])

    def s_if(self, st):
        if "matched" in st:
            cond = self.matched == st["matched"]
        else:
            cond = self.look(st["template"]) is not None
        self.run_steps(st.get("then", []) if cond else st.get("else", []))

    def s_repeat(self, st):
        for _ in range(st["times"]):
            self.run_steps(st["do"])

    def s_call(self, st):
        self.run_steps(self.scenario["subroutines"][st["name"]])

    def s_replay(self, st):
        """Plays a macro file. Events: down/up (PC keys), click (fractions), swipe (fractions + ms)."""
        events = json.loads((self.scenario_path.parent / st["file"]).read_text("utf-8"))["events"]
        speed = st.get("speed", 1.0)
        start = time.perf_counter()
        held: set[str] = set()
        try:
            for ev in events:
                while time.perf_counter() - start < ev["t"] / speed:
                    self.backend.check_abort()
                    time.sleep(0.005)
                if self.dry_run:
                    continue
                kind = ev["kind"]
                if kind == "down":
                    self.backend.key_down(ev["key"])
                    held.add(ev["key"])
                elif kind == "up":
                    self.backend.key_up(ev["key"])
                    held.discard(ev["key"])
                elif kind == "click":
                    self.backend.tap(*self.frac(ev["x"], ev["y"]))
                elif kind == "swipe":
                    self.backend.swipe(*self.frac(ev["x1"], ev["y1"]), *self.frac(ev["x2"], ev["y2"]), ev.get("ms", 300))
        finally:
            for k in held:
                self.backend.key_up(k)

    def s_prompt(self, st):
        with PROMPT_LOCK:  # several emulators may ask at once; answer them one by one
            print("\a")
            input(f"\n>>> [{self.label} @ {self.backend.name}] {st['message'].format(label=self.label)}\n    (끝나면 Enter) ")
        self.backend.prepare()

    def s_screenshot(self, st):
        import cv2
        d = self.shots_dir / self.label
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%H%M%S')}_{st.get('name', 'shot')}.png"
        cv2.imencode(".png", self.backend.grab())[1].tofile(str(path))
        self.log(f"saved {path}")

    def s_fetch_results(self, st):
        try:
            keys = self.backend.read_authkeys()
        except Exception as e:  # missing cache / no root: keep rolling, screenshots still exist
            self.log(f"authkey 읽기 실패: {e}")
            keys = []
        acc = fetch_new_results(self.ledger, self.label, keys, log=self.log)
        if acc is None and st.get("required", False):
            raise RuntimeError("no warp history found for this account")

    # ---------- top level ----------
    def next_index(self) -> int:
        with self.ledger.lock:
            used = [a["label"][len(self.prefix):] for a in self.ledger.accounts
                    if a.get("label", "").startswith(self.prefix)]
        nums = [int(u) for u in used if u.isdigit()]
        return max(nums, default=0) + 1

    def run(self, accounts: int, start_index: int | None = None):
        warn = self.backend.startup_warning()
        if warn:
            print(warn)
        self.run_steps(self.scenario.get("setup", []))
        n0 = start_index if start_index is not None else self.next_index()
        for i in range(accounts):
            self.label = f"{self.prefix}{n0 + i:03d}"
            with self.ledger.lock:
                if not self.ledger.by_label(self.label):
                    self.ledger.add(self.label, note=f"device {self.backend.name}")
                    self.ledger.save()
            t0 = time.time()
            self.log("cycle start")
            try:
                self.run_steps(self.scenario["cycle"])
            except StepTimeout as e:
                self.log(f"타임아웃: {e} -> 스크린샷 저장 후 수동 개입 대기")
                self.s_screenshot({"name": "timeout"})
                self.s_prompt({"message": "화면을 다음 계정 시작 지점으로 맞춰주세요"})
            self.log(f"cycle done in {(time.time() - t0) / 60:.1f} min")


def run_multi(scenario: Path, templates: Path, ledger: Ledger, devices, accounts: int,
              prefix: str = "e", dry_run: bool = False):
    """One thread per emulator; labels look like e1-001, e2-001 ..."""
    from .backends import AdbBackend
    threads = []
    errors: dict[str, BaseException] = {}

    def work(i, dev):
        r = Runner(scenario, templates, ledger, AdbBackend(dev), dry_run=dry_run, label_prefix=f"{prefix}{i}-")
        try:
            r.run(accounts)
        except BaseException as e:  # report per device, keep the others running
            errors[dev.serial] = e
            r.log(f"중단: {e!r}")

    for i, dev in enumerate(devices, 1):
        t = threading.Thread(target=work, args=(i, dev), name=dev.serial, daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1)
    for t in threads:
        while t.is_alive():
            t.join(0.5)
    return errors


def fetch_new_results(ledger: Ledger, label: str | None, keys: list[str], log=print):
    """Uses the newest authkey not already stored for another account."""
    with ledger.lock:
        return _fetch_new_results(ledger, label, keys, log)


def _fetch_new_results(ledger: Ledger, label: str | None, keys: list[str], log=print):
    seen = ledger.seen_keys()
    fresh = [k for k in reversed(keys) if gachalog_hash(k) not in seen]
    if not fresh:
        log("새 authkey 없음: 게임에서 워프 > 상세 > 기록 화면을 한 번 열어주세요")
        return None
    for key in fresh:
        try:
            warps = gachalog.fetch_all(key)
        except gachalog.AuthKeyError as e:
            log(f"authkey 사용 불가 ({e}), 다음 후보 시도")
            continue
        summary = gachalog.summarize(warps)
        if not summary["uid"]:
            # Records lag behind pulls by up to ~1 hour; keep the key (valid ~24h) for `refresh`.
            acc = (ledger.by_label(label) if label else None) or ledger.add(label or f"pending-{len(ledger.accounts) + 1}")
            acc["pending_key"] = key
            acc.setdefault("key_hashes", []).append(gachalog_hash(key))
            acc["status"] = "pending"
            ledger.save()
            log("워프 기록이 아직 비어 있음 (반영 지연 최대 ~1시간). 나중에 `python -m hsr_reroll refresh` 로 갱신")
            return None
        existing = ledger.by_uid(summary["uid"])
        if existing and label and existing.get("label") != label:
            log(f"이 authkey 는 이미 기록된 {existing['label']} 의 것입니다. 갱신만 합니다.")
            label = existing["label"]
        acc = ledger.record(summary, key, label)
        acc.pop("pending_key", None)
        ledger.save()
        ledger.export_html(ledger.path.with_name("report.html"))
        log(f"UID {acc['uid']} | {acc['pulls']}뽑 | 5성: {', '.join(acc['five_stars']) or '없음'} | 점수 {acc['score']}")
        return acc
    return None


def gachalog_hash(k: str) -> str:
    from .ledger import key_hash
    return key_hash(k)


def refresh_pending(ledger: Ledger, log=print) -> int:
    """Re-fetches accounts whose records were empty when first checked."""
    done = 0
    for acc in [a for a in ledger.accounts if a.get("pending_key")]:
        key = acc["pending_key"]
        try:
            summary = gachalog.summarize(gachalog.fetch_all(key))
        except gachalog.AuthKeyError as e:
            log(f"{acc['label']}: {e}")
            if "expired" in str(e):
                acc.pop("pending_key")
                acc["status"] = "expired"
            continue
        if not summary["uid"]:
            log(f"{acc['label']}: 아직 비어 있음")
            continue
        ledger.record(summary, key, acc["label"])
        acc.pop("pending_key", None)
        acc["status"] = "rolled"
        done += 1
        log(f"{acc['label']}: UID {acc['uid']} 5성 {', '.join(acc['five_stars']) or '없음'} 점수 {acc['score']}")
    ledger.save()
    ledger.export_html(ledger.path.with_name("report.html"))
    return done
