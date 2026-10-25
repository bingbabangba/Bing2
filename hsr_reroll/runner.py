"""JSON scenario engine that drives the game through template matching and replayed macros.

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
  key          {"key": "esc", "times": 1, "interval": 0.3, "hold": 0.08}
  click        {"template": "name"} or {"x": 0.5, "y": 0.9} (fractions of the window)
               optional "timeout" (seconds to wait for the template, default 10), "optional": true
  wait         {"template": "name", "timeout": 60}   waits until visible
  wait_gone    {"template": "name", "timeout": 60}
  wait_any     {"templates": ["a","b"], "timeout": 60}  -> remembers which one matched
  until        {"template": "name", "do": [steps], "timeout": 600}  repeats `do` until visible
  if           {"template": "name" | "matched": "a", "then": [...], "else": [...]}
  repeat       {"times": 10, "do": [...]}
  call         {"name": "subroutine"}
  replay       {"file": "macros/tutorial_walk.json", "speed": 1.0}
  prompt       {"message": "text"}   waits for Enter (manual step: sign-up, captcha, email code)
  screenshot   {"name": "result"}
  fetch_results {}                   reads the warp history of the logged-in account into the ledger
  log          {"message": "text"}
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import gachalog, winio
from .ledger import Ledger


class StepTimeout(RuntimeError):
    pass


class Runner:
    def __init__(self, scenario_path: Path, templates_dir: Path, ledger: Ledger,
                 game_dir: str | None = None, shots_dir: Path = Path("reroll_data/shots"),
                 dry_run: bool = False):
        from .vision import Templates
        self.scenario_path = Path(scenario_path)
        self.scenario = json.loads(self.scenario_path.read_text("utf-8"))
        self.templates = Templates(templates_dir)
        self.threshold = self.scenario.get("threshold", 0.85)
        self.ledger = ledger
        self.game_dir = game_dir
        self.shots_dir = Path(shots_dir)
        self.dry_run = dry_run
        self.label = "setup"
        self.matched: str | None = None
        self.hwnd = winio.find_window(self.scenario.get("window_titles"))
        self.screen = winio.Screen(self.hwnd)

    # ---------- primitives ----------
    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] [{self.label}] {msg}", flush=True)

    def look(self, name: str):
        from .vision import find
        winio.check_abort()
        return find(self.screen.grab(), self.templates.get(name), self.threshold)

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
        x, y, _, _ = self.screen.rect()
        if self.dry_run:
            self.log(f"(dry) click {cx},{cy}")
            return
        winio.focus(self.hwnd)
        winio.click(x + cx, y + cy)

    # ---------- steps ----------
    def run_steps(self, steps: list[dict]):
        for step in steps:
            winio.check_abort()
            getattr(self, "s_" + step["type"])(step)

    def s_sleep(self, st):
        end = time.time() + st["sec"]
        while time.time() < end:
            winio.check_abort()
            time.sleep(min(0.2, max(0, end - time.time())))

    def s_log(self, st):
        self.log(st["message"].format(label=self.label))

    def s_key(self, st):
        for _ in range(st.get("times", 1)):
            if self.dry_run:
                self.log(f"(dry) key {st['key']}")
            else:
                winio.focus(self.hwnd)
                winio.press(st["key"], st.get("hold", 0.08))
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
            _, _, w, h = self.screen.rect()
            self.click_at(int(st["x"] * w), int(st["y"] * h))
        time.sleep(st.get("after", 0.5))

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
        from .recorder import replay
        path = self.scenario_path.parent / st["file"]
        replay(path, self.hwnd, speed=st.get("speed", 1.0), dry_run=self.dry_run)

    def s_prompt(self, st):
        print("\a")
        input(f"\n>>> [{self.label}] {st['message'].format(label=self.label)}\n    (끝나면 Enter) ")
        winio.focus(self.hwnd)

    def s_screenshot(self, st):
        import cv2
        d = self.shots_dir / self.label
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%H%M%S')}_{st.get('name', 'shot')}.png"
        cv2.imencode(".png", self.screen.grab())[1].tofile(str(path))
        self.log(f"saved {path}")

    def s_fetch_results(self, st):
        acc = fetch_new_results(self.ledger, self.label, self.game_dir, log=self.log)
        if acc is None and st.get("required", False):
            raise RuntimeError("no warp history found for this account")

    # ---------- top level ----------
    def run(self, accounts: int, start_index: int | None = None):
        if not winio.is_admin():
            print("경고: 관리자 권한이 아닙니다. 게임이 입력을 무시할 수 있습니다 (run_admin.bat 사용).")
        print("F12 를 누르면 언제든 중단됩니다.")
        self.run_steps(self.scenario.get("setup", []))
        prefix = self.scenario.get("label_prefix", "acc")
        n0 = start_index if start_index is not None else len(self.ledger.accounts) + 1
        for i in range(accounts):
            self.label = f"{prefix}{n0 + i:03d}"
            if not self.ledger.by_label(self.label):
                self.ledger.add(self.label)
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


def fetch_new_results(ledger: Ledger, label: str | None, game_dir: str | None = None, log=print):
    """Uses the newest authkey not already stored for another account."""
    keys = gachalog.read_authkeys(game_dir)
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
