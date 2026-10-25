"""Reads the logged-in account's warp history straight from HoYoverse.

The game writes the warp-records URL (with a short-lived authkey) into its web
cache whenever the in-game Warp -> "View Details" -> "Records" page is opened.
This module finds that URL, fetches every banner's history and returns the
results, the same way open-source trackers (e.g. StarRail-Warp-Tracker) do.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

GACHA_LOG_RE = re.compile(
    rb"https://[^\s\0\"']+?\.(?:mihoyo|hoyoverse)\.com/common/(?:hkrpg_)?gacha_record/api/"
    rb"(?:getGachaLog|getLdGachaLog)[^\s\0\"']*"
)
PLAYER_LOG_RE = re.compile(r"Loading player data from (?P<dir>.+)/data\.unity3d")

BANNERS = {
    "1": "Stellar Warp",
    "2": "Departure Warp",
    "11": "Character Event",
    "12": "Light Cone Event",
    "21": "Collab Character",
    "22": "Collab Light Cone",
}
API = "https://public-operation-hkrpg-sg.hoyoverse.com/common/gacha_record/api/"


class AuthKeyError(RuntimeError):
    pass


@dataclass
class Warp:
    id: str
    uid: str
    banner: str
    name: str
    item_type: str
    rarity: int
    time: str


def player_log_candidates() -> list[Path]:
    base = Path(os.environ.get("USERPROFILE", Path.home())) / "AppData" / "LocalLow"
    out = []
    for d in ("Cognosphere/Star Rail", "miHoYo/崩坏：星穹铁道"):
        for f in ("Player.log", "Player-prev.log"):
            out.append(base / d / f)
    return out


def find_data_dir(explicit: str | None = None) -> Path:
    """Returns the game's StarRail_Data directory."""
    if explicit:
        p = Path(explicit)
        return p if p.name == "StarRail_Data" else p / "StarRail_Data"
    for log in player_log_candidates():
        if log.is_file() and log.stat().st_size:
            with open(log, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    m = PLAYER_LOG_RE.search(line.strip())
                    if m:
                        return Path(m.group("dir"))
    default = Path("C:/Program Files/Star Rail/Games/StarRail_Data")
    if default.is_dir():
        return default
    raise FileNotFoundError("StarRail_Data not found. Run the game once or pass --game-dir.")


def cache_file(data_dir: Path) -> Path:
    caches = [p for p in (data_dir / "webCaches").glob("*") if p.is_dir()]
    if not caches:
        raise FileNotFoundError(f"No webCaches under {data_dir}")
    latest = max(caches, key=lambda p: p.stat().st_mtime)
    return latest / "Cache" / "Cache_Data" / "data_2"


def extract_authkeys(blob: bytes) -> list[str]:
    """All distinct authkeys in cache order, newest (last written) last."""
    keys: list[str] = []
    for m in GACHA_LOG_RE.finditer(blob):
        url = m.group(0).decode("utf-8", errors="ignore")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        key = q.get("authkey", [None])[0]
        if key:
            if key in keys:
                keys.remove(key)
            keys.append(key)
    return keys


def read_authkeys(game_dir: str | None = None) -> list[str]:
    path = cache_file(find_data_dir(game_dir))
    # The game keeps the file open; read a copy of the bytes.
    with open(path, "rb") as fh:
        return extract_authkeys(fh.read())


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "hsr-reroll/0.1"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_banner(authkey: str, gacha_type: str, delay: float = 0.3, getter=_get) -> list[Warp]:
    endpoint = "getLdGachaLog" if gacha_type in ("21", "22") else "getGachaLog"
    warps: list[Warp] = []
    end_id = "0"
    while True:
        params = {
            "authkey_ver": "1", "authkey": authkey, "lang": "en", "game_biz": "hkrpg_global",
            "gacha_type": gacha_type, "size": "20", "end_id": end_id,
        }
        data = getter(API + endpoint + "?" + urllib.parse.urlencode(params))
        if data.get("retcode") == -101:
            raise AuthKeyError("authkey expired - reopen Warp > Records in game")
        if data.get("retcode") != 0:
            raise AuthKeyError(f"API error {data.get('retcode')}: {data.get('message')}")
        page = data["data"]["list"]
        for w in page:
            warps.append(Warp(w["id"], w["uid"], BANNERS.get(w["gacha_type"], w["gacha_type"]),
                              w["name"], w.get("item_type", ""), int(w["rank_type"]), w["time"]))
        if len(page) < 20:
            return warps
        end_id = page[-1]["id"]
        time.sleep(delay)


def fetch_all(authkey: str, delay: float = 0.3, getter=_get) -> list[Warp]:
    out: list[Warp] = []
    for gt in BANNERS:
        try:
            out.extend(fetch_banner(authkey, gt, delay, getter))
        except AuthKeyError:
            if gt in ("21", "22"):  # collab endpoints may be closed between collabs
                continue
            raise
        time.sleep(delay)
    return out


def summarize(warps: list[Warp]) -> dict:
    uid = warps[0].uid if warps else ""
    five = [w for w in warps if w.rarity == 5]
    four = [w for w in warps if w.rarity == 4]
    return {
        "uid": uid,
        "pulls": len(warps),
        "five_stars": [f"{w.name} ({w.banner})" for w in sorted(five, key=lambda w: w.id)],
        "five_star_names": [w.name for w in sorted(five, key=lambda w: w.id)],
        "four_star_names": sorted({w.name for w in four}),
        "raw": [asdict(w) for w in warps],
    }
