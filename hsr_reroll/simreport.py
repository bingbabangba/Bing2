"""Large Monte Carlo report: probability of each reroll goal per pass budget."""

from __future__ import annotations

import random
import time

from .simulator import Plan, simulate_account

GOALS = [
    ("한정 1장+", lambda f: f.count("*") >= 1),
    ("한정 + 브로냐", lambda f: f.count("*") >= 1 and "Bronya" in f),
    ("한정 + 브로냐/히메코/웰트", lambda f: f.count("*") >= 1 and any(n in f for n in ("Bronya", "Himeko", "Welt"))),
    ("한정 2장 (1돌)", lambda f: f.count("*") >= 2),
    ("5성 3장+", lambda f: len(f) >= 3),
]


def run(budgets, trials: int, name: str, minutes_per_account: float, seed: int = 0, samples: int = 20):
    rng = random.Random(seed)
    lines = [f"# 리세마라 경우의 수 시뮬레이션 ({name}, 예산별 {trials:,}개 가상 계정)", "",
             f"생성: {time.strftime('%Y-%m-%d')} · 모델: 5성 0.6%, 74뽑부터 +6%p, 90 천장, 출발 워프 50 확정(10연 8장), 한정 반반 50%",
             f"계정 1개당 {minutes_per_account:.0f}분 가정. 재화(성옥/패스)는 서로 교환 가능하다고 가정.", "",
             "| 패스 예산 | " + " | ".join(g for g, _ in GOALS) + " |",
             "|---|" + "---|" * len(GOALS)]
    examples = {}
    for b in budgets:
        plan = Plan(budget=b, limited_name=name)
        hits = [0] * len(GOALS)
        ex = []
        for i in range(trials):
            out = simulate_account(plan, rng)
            names = [("*" if n.startswith("*") else n) for n in out.five_stars]
            for gi, (_, fn) in enumerate(GOALS):
                hits[gi] += fn(names)
            if i < samples:
                ex.append(", ".join(n.lstrip("*") if not n.startswith("*") else name for n in out.five_stars) or "-")
        examples[b] = ex
        cells = []
        for h in hits:
            p = h / trials
            if p == 0:
                cells.append("0%")
            else:
                n = 1 / p
                cells.append(f"{p:.1%} · {n:.0f}계정 · {n * minutes_per_account / 60:.0f}h")
        lines.append(f"| {b} | " + " | ".join(cells) + " |")
    lines += ["", "각 칸: 확률 · 평균 필요 계정 수 · 평균 소요 시간", ""]
    for b, ex in examples.items():
        lines.append(f"<details><summary>예산 {b}: 가상 계정 {len(ex)}개 결과 예시</summary>\n")
        lines += [f"{i + 1}. {e}" for i, e in enumerate(ex)]
        lines.append("\n</details>\n")
    return "\n".join(lines)
