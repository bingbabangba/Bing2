"""Monte Carlo simulator for a fresh Honkai: Star Rail account's warps.

Rates follow the community-measured model (consolidated 1.6% for characters):
  character / standard / departure: 0.6% base, +6%p per pull from pull 74, hard pity 90
  light cone: 0.8% base, +7%p per pull from pull 66, hard pity 80
  departure warp: a 5-star is guaranteed within its 50 pulls
  limited character banner: 50/50 (configurable), limited light cone: 75/25
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources

STANDARD_5STARS = ["Himeko", "Welt", "Bronya", "Gepard", "Clara", "Yanqing", "Bailu"]


def five_star_rate(pity: int, base: float, soft_start: int, step: float, hard: int) -> float:
    """Probability that pull number `pity` (1-based since last 5-star) is a 5-star."""
    if pity >= hard:
        return 1.0
    if pity < soft_start:
        return base
    return min(1.0, base + step * (pity - soft_start + 1))


CHAR_RATE = dict(base=0.006, soft_start=74, step=0.06, hard=90)
CONE_RATE = dict(base=0.008, soft_start=66, step=0.07, hard=80)


@dataclass
class Plan:
    """How a new account spends its pulls."""

    departure: int = 50          # pulls on Departure Warp (max 50, costs 8 passes per 10)
    limited: int = 0             # pulls on the limited character banner
    standard: int = 0            # pulls on Stellar Warp
    limited_name: str = "Limited"
    win_rate: float = 0.5        # chance a limited 5-star is the rate-up

    def __post_init__(self):
        self.departure = max(0, min(50, self.departure))


@dataclass
class Outcome:
    five_stars: list[str] = field(default_factory=list)
    four_stars: int = 0

    @property
    def limited_count(self) -> int:
        return sum(1 for n in self.five_stars if n.startswith("*"))


def _roll_banner(rng: random.Random, pulls: int, rate: dict, hard_cap: int | None = None,
                 stop_first: bool = False):
    """Yields pull indices (1-based within banner) that hit 5-star and the 4-star count."""
    pity5 = pity4 = 0
    hits, fours = [], 0
    for i in range(1, pulls + 1):
        pity5 += 1
        pity4 += 1
        p = five_star_rate(pity5, **rate)
        if hard_cap is not None and pity5 >= hard_cap:
            p = 1.0
        if rng.random() < p:
            hits.append(i)
            pity5 = 0
            if stop_first:
                break
            continue
        if pity4 >= 10 or rng.random() < 0.051:
            fours += 1
            pity4 = 0
    return hits, fours


def simulate_account(plan: Plan, rng: random.Random) -> Outcome:
    out = Outcome()
    if plan.departure:
        # Departure Warp ends after its first 5-star, guaranteed by pull 50.
        hits, fours = _roll_banner(rng, plan.departure, CHAR_RATE, hard_cap=50, stop_first=True)
        out.four_stars += fours
        if hits:
            out.five_stars.append(rng.choice(STANDARD_5STARS))
    hits, fours = _roll_banner(rng, plan.limited, CHAR_RATE)
    out.four_stars += fours
    guaranteed = False
    for _ in hits:
        if guaranteed or rng.random() < plan.win_rate:
            out.five_stars.append("*" + plan.limited_name)
            guaranteed = False
        else:
            out.five_stars.append(rng.choice(STANDARD_5STARS))
            guaranteed = True
    hits, fours = _roll_banner(rng, plan.standard, CHAR_RATE)
    out.four_stars += fours
    for _ in hits:
        # Stellar Warp 5-stars are split between characters and light cones.
        out.five_stars.append(rng.choice(STANDARD_5STARS) if rng.random() < 0.5 else "LightCone")
    return out


def load_scores() -> dict[str, float]:
    data = json.loads(resources.files("hsr_reroll.data").joinpath("characters.json").read_text("utf-8"))
    return {c["name"]: c["score"] for c in data["characters"]}


def score_outcome(out: Outcome, scores: dict[str, float], limited_score: float) -> float:
    total = 0.0
    for name in out.five_stars:
        if name.startswith("*"):
            total += limited_score
        else:
            total += scores.get(name, 1.0)
    return total


@dataclass
class Summary:
    trials: int
    p_limited: float
    p_two_plus_5star: float
    mean_5star: float
    mean_score: float
    p_goal: float
    expected_accounts_for_goal: float
    count_hist: dict[int, float]
    standard_hist: dict[str, float]


def run(plan: Plan, trials: int = 100_000, seed: int | None = None,
        goal_score: float | None = None, limited_score: float = 10.0) -> Summary:
    rng = random.Random(seed)
    scores = load_scores()
    n_limited = n_two = n_goal = 0
    total5 = 0
    total_score = 0.0
    hist: Counter[int] = Counter()
    std: Counter[str] = Counter()
    for _ in range(trials):
        out = simulate_account(plan, rng)
        k = len(out.five_stars)
        hist[k] += 1
        total5 += k
        n_limited += out.limited_count > 0
        n_two += k >= 2
        s = score_outcome(out, scores, limited_score)
        total_score += s
        if goal_score is not None and s >= goal_score:
            n_goal += 1
        for name in out.five_stars:
            if not name.startswith("*"):
                std[name] += 1
    p_goal = n_goal / trials if goal_score is not None else 0.0
    return Summary(
        trials=trials,
        p_limited=n_limited / trials,
        p_two_plus_5star=n_two / trials,
        mean_5star=total5 / trials,
        mean_score=total_score / trials,
        p_goal=p_goal,
        expected_accounts_for_goal=(1 / p_goal) if p_goal else float("inf"),
        count_hist={k: v / trials for k, v in sorted(hist.items())},
        standard_hist={k: v / trials for k, v in std.most_common()},
    )
