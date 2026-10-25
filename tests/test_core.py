import json
import random
from pathlib import Path

import numpy as np
import pytest

from hsr_reroll import gachalog, simulator, vision
from hsr_reroll.ledger import Ledger
from hsr_reroll.runner import Runner

ROOT = Path(__file__).resolve().parent.parent


def test_rate_curve():
    r = simulator.CHAR_RATE
    assert simulator.five_star_rate(1, **r) == 0.006
    assert simulator.five_star_rate(73, **r) == 0.006
    assert simulator.five_star_rate(74, **r) == pytest.approx(0.066)
    assert simulator.five_star_rate(90, **r) == 1.0


def test_departure_always_gives_one_five_star():
    rng = random.Random(1)
    for _ in range(2000):
        out = simulator.simulate_account(simulator.Plan(departure=50), rng)
        assert len(out.five_stars) == 1
        assert out.five_stars[0] in simulator.STANDARD_5STARS


def test_ninety_limited_pulls_guarantee_a_five_star():
    rng = random.Random(2)
    for _ in range(2000):
        out = simulator.simulate_account(simulator.Plan(departure=0, limited=90), rng)
        assert len(out.five_stars) >= 1


def test_consolidated_rate_close_to_official():
    rng = random.Random(3)
    hits = 0
    pulls = 400_000
    h, _ = simulator._roll_banner(rng, pulls, simulator.CHAR_RATE)
    assert len(h) / pulls == pytest.approx(0.016, abs=0.001)


def test_run_summary():
    s = simulator.run(simulator.Plan(departure=50, limited=40), trials=5000, seed=4, goal_score=10)
    assert 0 < s.p_limited < 1
    assert s.mean_5star >= 1
    assert abs(sum(s.count_hist.values()) - 1) < 1e-9


URL = ("https://public-operation-hkrpg-sg.hoyoverse.com/common/gacha_record/api/getGachaLog?"
       "authkey_ver=1&authkey={}&lang=en&game_biz=hkrpg_global")


def test_extract_authkeys_orders_newest_last():
    blob = b"\0junk" + URL.format("AAA").encode() + b"\0" + URL.format("BBB").encode() + b"\0" + URL.format("AAA").encode()
    assert gachalog.extract_authkeys(blob) == ["BBB", "AAA"]


def fake_getter(pages):
    def get(url):
        gt = url.split("gacha_type=")[1].split("&")[0]
        end = url.split("end_id=")[1].split("&")[0]
        return pages.get((gt, end), {"retcode": 0, "data": {"list": []}})
    return get


def make_warp(i, gt, name, rank):
    return {"id": str(i), "uid": "800123456", "gacha_type": gt, "name": name, "item_type": "Character",
            "rank_type": str(rank), "time": "2026-10-25 12:00:00"}


def test_fetch_paginates_and_summarizes():
    first = [make_warp(100 - i, "11", "Pela", 4) for i in range(20)]
    second = [make_warp(50, "11", "Mortenax Blade", 5)]
    pages = {("11", "0"): {"retcode": 0, "data": {"list": first}},
             ("11", "81"): {"retcode": 0, "data": {"list": second}},
             ("2", "0"): {"retcode": 0, "data": {"list": [make_warp(10, "2", "Bronya", 5)]}}}
    warps = gachalog.fetch_all("k", delay=0, getter=fake_getter(pages))
    s = gachalog.summarize(warps)
    assert s["pulls"] == 22
    assert s["uid"] == "800123456"
    assert s["five_star_names"] == ["Bronya", "Mortenax Blade"]


def test_expired_key_raises():
    with pytest.raises(gachalog.AuthKeyError):
        gachalog.fetch_banner("k", "1", 0, lambda u: {"retcode": -101, "message": "authkey timeout"})


def test_ledger_scores_and_roundtrip(tmp_path):
    lg = Ledger(tmp_path / "a.json")
    lg.add("acc001")
    acc = lg.record({"uid": "1", "pulls": 60, "five_star_names": ["Bronya", "Mortenax Blade"],
                     "five_stars": [], "four_star_names": []}, "key1", "acc001")
    assert acc["score"] == 6 + 9
    assert acc["status"] == "rolled"
    lg.save()
    lg2 = Ledger(tmp_path / "a.json")
    assert lg2.by_uid("1")["label"] == "acc001"
    assert len(lg2.seen_keys()) == 1
    lg2.export_html(tmp_path / "r.html")
    lg2.export_csv(tmp_path / "r.csv")
    assert "acc001" in (tmp_path / "r.html").read_text("utf-8")


def test_template_match_across_resolutions():
    rng = np.random.default_rng(0)
    big = (rng.random((1080, 1920, 3)) * 255).astype(np.uint8)
    templ = vision.crop_to_template(big, (800, 500, 120, 60))
    small = __import__("cv2").resize(big, (1280, 720))
    m = vision.find(small, vision.crop_to_template(big, (800, 500, 120, 60)), threshold=0.5)
    assert m is not None
    assert abs(m.x - (860 * 1280 / 1920)) < 4 and abs(m.y - (530 * 720 / 1080)) < 4
    assert vision.find(big, templ).score > 0.99


def collect_types(steps, out):
    for st in steps:
        out.add(st["type"])
        for k in ("do", "then", "else"):
            collect_types(st.get(k, []), out)


@pytest.mark.parametrize("name", ["reroll_cycle.json", "fetch_only.json"])
def test_scenarios_use_known_steps(name):
    sc = json.loads((ROOT / "scenarios" / name).read_text("utf-8"))
    types = set()
    collect_types(sc.get("setup", []) + sc["cycle"], types)
    for sub in sc.get("subroutines", {}).values():
        collect_types(sub, types)
    for t in types:
        assert hasattr(Runner, "s_" + t), t


def test_budget_mode_spends_departure_first():
    rng = random.Random(5)
    for _ in range(2000):
        out = simulator.simulate_account(simulator.Plan(budget=40), rng)
        assert len(out.five_stars) >= 1  # 40 passes buy all 50 Departure pulls
    assert len(simulator.simulate_account(simulator.Plan(budget=0), rng).five_stars) == 0
