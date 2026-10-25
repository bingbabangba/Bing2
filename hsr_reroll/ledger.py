"""Account ledger: one entry per rerolled account, scored by data/characters.json."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import threading
import time
from importlib import resources
from pathlib import Path

DEFAULT_PATH = Path("reroll_data/accounts.json")


def load_character_table() -> dict:
    return json.loads(resources.files("hsr_reroll.data").joinpath("characters.json").read_text("utf-8"))


def key_hash(authkey: str) -> str:
    return hashlib.sha256(authkey.encode()).hexdigest()[:16]


def score_names(names: list[str], table: dict | None = None) -> float:
    table = table or load_character_table()
    by_name = {c["name"].lower(): c for c in table["characters"]}
    default = table.get("limited_default_score", 8)
    total = 0.0
    for n in names:
        c = by_name.get(n.lower())
        total += c["score"] if c else default
    return total


class Ledger:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()  # shared by parallel emulator runners
        self.accounts: list[dict] = []
        if self.path.exists():
            self.accounts = json.loads(self.path.read_text("utf-8"))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.accounts, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    def seen_keys(self) -> set[str]:
        return {h for a in self.accounts for h in a.get("key_hashes", [])}

    def by_uid(self, uid: str) -> dict | None:
        return next((a for a in self.accounts if a.get("uid") == uid), None)

    def by_label(self, label: str) -> dict | None:
        return next((a for a in self.accounts if a.get("label") == label), None)

    def add(self, label: str, email: str = "", note: str = "") -> dict:
        if self.by_label(label):
            raise ValueError(f"label {label!r} already exists")
        acc = {"label": label, "email": email, "note": note, "status": "new",
               "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.accounts.append(acc)
        return acc

    def record(self, summary: dict, authkey: str, label: str | None = None) -> dict:
        """Stores a gachalog.summarize() result. Matches by uid, then label, else creates."""
        acc = self.by_uid(summary["uid"]) or (self.by_label(label) if label else None)
        if acc is None:
            acc = self.add(label or f"uid-{summary['uid']}")
        acc.update({
            "uid": summary["uid"],
            "pulls": summary["pulls"],
            "five_star_names": summary["five_star_names"],
            "five_stars": summary["five_stars"],
            "four_star_names": summary["four_star_names"],
            "score": score_names(summary["five_star_names"]),
            "fetched": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": acc.get("status") if acc.get("status") not in (None, "new", "pending", "expired") else "rolled",
        })
        acc.setdefault("key_hashes", [])
        if key_hash(authkey) not in acc["key_hashes"]:
            acc["key_hashes"].append(key_hash(authkey))
        return acc

    def ranked(self) -> list[dict]:
        return sorted(self.accounts, key=lambda a: a.get("score", -1), reverse=True)

    def export_csv(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        cols = ["label", "email", "uid", "status", "score", "pulls", "five_stars", "four_star_names", "fetched", "note"]
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for a in self.ranked():
                w.writerow([" / ".join(a.get(c, [])) if isinstance(a.get(c), list) else a.get(c, "") for c in cols])

    def export_html(self, path: Path):
        rows = []
        for i, a in enumerate(self.ranked(), 1):
            five = html.escape(", ".join(a.get("five_stars", [])) or "-")
            four = html.escape(", ".join(a.get("four_star_names", [])) or "-")
            rows.append(
                f"<tr><td>{i}</td><td>{html.escape(a.get('label', ''))}</td><td>{html.escape(a.get('uid', ''))}</td>"
                f"<td>{a.get('score', '')}</td><td>{a.get('pulls', '')}</td><td>{five}</td><td>{four}</td>"
                f"<td>{html.escape(a.get('status', ''))}</td><td>{html.escape(a.get('email', ''))}</td></tr>"
            )
        page = f"""<!doctype html><meta charset="utf-8"><title>Reroll Results</title>
<style>body{{font-family:sans-serif;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:4px 8px;font-size:14px}}th{{background:#eee}}tr:nth-child(2){{background:#fff6d6}}</style>
<h1>리세마라 결과 ({len(self.accounts)} 계정)</h1>
<table><tr><th>#</th><th>라벨</th><th>UID</th><th>점수</th><th>뽑기</th><th>5성</th><th>4성</th><th>상태</th><th>이메일</th></tr>
{''.join(rows)}</table>"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(page, "utf-8")
