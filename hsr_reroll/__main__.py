"""CLI: python -m hsr_reroll <command> ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ledger import DEFAULT_PATH, Ledger

ROOT = Path(__file__).resolve().parent.parent


def cmd_simulate(a):
    from . import simulator
    plan = simulator.Plan(departure=a.departure, limited=a.limited, standard=a.standard,
                          limited_name=a.name, win_rate=a.win_rate, budget=a.budget)
    s = simulator.run(plan, trials=a.trials, seed=a.seed, goal_score=a.goal, limited_score=a.limited_score)
    if plan.budget is not None:
        print(f"시뮬레이션 {s.trials:,}회 | 패스 {plan.budget}장 (출발 워프 먼저, 나머지 한정)")
    else:
        print(f"시뮬레이션 {s.trials:,}회 | 출발 {plan.departure} + 한정 {plan.limited} + 상시 {plan.standard}뽑")
    print(f"  평균 5성 수      : {s.mean_5star:.2f}")
    print(f"  한정 5성 1+ 확률 : {s.p_limited:.1%}")
    print(f"  5성 2+ 확률      : {s.p_two_plus_5star:.1%}")
    print(f"  평균 점수        : {s.mean_score:.2f}")
    if a.goal is not None:
        print(f"  목표 점수 {a.goal}+ 확률: {s.p_goal:.2%}  -> 평균 {s.expected_accounts_for_goal:.1f} 계정 필요")
    print("  5성 개수 분포    : " + "  ".join(f"{k}개 {v:.1%}" for k, v in s.count_hist.items()))
    print("  상시 5성 등장률  : " + "  ".join(f"{k} {v:.1%}" for k, v in s.standard_hist.items()))


def cmd_simreport(a):
    from . import simreport
    md = simreport.run([int(x) for x in a.budgets.split(",")], a.trials, a.name, a.minutes)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, "utf-8")
    print(md.split("<details>")[0])
    print(f"저장: {out}")


def cmd_capture(a):
    import cv2
    from . import vision, winio
    hwnd = winio.find_window()
    winio.focus(hwnd)
    img = winio.Screen(hwnd).grab()
    print("마우스로 영역을 드래그하고 Enter/Space, 취소는 c")
    roi = cv2.selectROI(f"capture: {a.name}", img, showCrosshair=True)
    cv2.destroyAllWindows()
    if roi[2] == 0 or roi[3] == 0:
        print("취소됨")
        return
    vision.Templates(a.templates).save(a.name, vision.crop_to_template(img, roi))
    print(f"저장: {Path(a.templates) / (a.name + '.png')}")


def cmd_check(a):
    from . import vision, winio
    hwnd = winio.find_window()
    img = winio.Screen(hwnd).grab()
    t = vision.Templates(a.templates)
    for p in sorted(Path(a.templates).glob("*.png")):
        m = vision.find(img, t.get(p.stem), threshold=0.0)
        flag = "O" if m and m.score >= a.threshold else "."
        print(f"  {flag} {p.stem:<28} {m.score if m else 0:.3f}")


def cmd_run(a):
    from .runner import Runner
    r = Runner(Path(a.scenario), Path(a.templates), Ledger(a.ledger), game_dir=a.game_dir, dry_run=a.dry_run)
    r.run(a.accounts, a.start)


def cmd_fetch(a):
    from .runner import fetch_new_results
    fetch_new_results(Ledger(a.ledger), a.label, a.game_dir)


def cmd_refresh(a):
    from .runner import refresh_pending
    print(f"{refresh_pending(Ledger(a.ledger))} 계정 갱신")


def cmd_record(a):
    from .recorder import record
    record(Path(a.out))


def cmd_add(a):
    lg = Ledger(a.ledger)
    lg.add(a.label, a.email, a.note)
    lg.save()


def cmd_mark(a):
    lg = Ledger(a.ledger)
    acc = lg.by_label(a.label)
    if not acc:
        sys.exit(f"no account {a.label}")
    acc["status"] = a.status
    if a.note:
        acc["note"] = a.note
    lg.save()


def cmd_report(a):
    lg = Ledger(a.ledger)
    for i, acc in enumerate(lg.ranked()[: a.top], 1):
        print(f"{i:>3}. {acc.get('label', ''):<10} 점수 {acc.get('score', '-'):<5} "
              f"{acc.get('status', ''):<8} 5성: {', '.join(acc.get('five_stars', [])) or '-'}")
    lg.export_csv(lg.path.with_name("report.csv"))
    lg.export_html(lg.path.with_name("report.html"))
    print(f"\n{lg.path.with_name('report.html')} / report.csv 저장")


def main(argv=None):
    p = argparse.ArgumentParser(prog="hsr_reroll", description="붕괴: 스타레일 PC 리세마라 도우미")
    p.add_argument("--ledger", default=str(DEFAULT_PATH))
    p.add_argument("--templates", default=str(ROOT / "templates"))
    p.add_argument("--game-dir", default=None, help="StarRail_Data 경로 (자동 탐지 실패 시)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("simulate", help="새 계정 뽑기 결과 몬테카를로 시뮬레이션")
    s.add_argument("--departure", type=int, default=50)
    s.add_argument("--limited", type=int, default=0)
    s.add_argument("--standard", type=int, default=0)
    s.add_argument("--name", default="Limited")
    s.add_argument("--win-rate", type=float, default=0.5)
    s.add_argument("--trials", type=int, default=100_000)
    s.add_argument("--seed", type=int)
    s.add_argument("--goal", type=float, help="이 점수 이상이면 '성공 계정'")
    s.add_argument("--limited-score", type=float, default=9)
    s.add_argument("--budget", type=int, help="총 패스 수 (출발 워프 먼저, 나머지 한정 배너)")
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("simreport", help="예산별 목표 달성 확률 표(markdown) 생성")
    s.add_argument("--budgets", default="20,30,40,60,80,100,130")
    s.add_argument("--trials", type=int, default=200_000)
    s.add_argument("--name", default="Blade")
    s.add_argument("--minutes", type=float, default=25)
    s.add_argument("--out", default="reroll_data/simulation.md")
    s.set_defaults(fn=cmd_simreport)

    s = sub.add_parser("capture", help="게임 화면에서 템플릿 이미지 잘라 저장")
    s.add_argument("name")
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("check", help="현재 화면에서 모든 템플릿 일치도 표시")
    s.add_argument("--threshold", type=float, default=0.85)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("record", help="입력 매크로 녹화 (F12 종료)")
    s.add_argument("out")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("run", help="시나리오 자동 실행")
    s.add_argument("scenario")
    s.add_argument("--accounts", type=int, default=1)
    s.add_argument("--start", type=int, help="계정 번호 시작값")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("fetch", help="로그인 중인 계정의 워프 기록을 장부에 기록")
    s.add_argument("--label")
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("refresh", help="기록 반영 대기(pending) 계정 재조회")
    s.set_defaults(fn=cmd_refresh)

    s = sub.add_parser("add", help="장부에 계정 추가")
    s.add_argument("label")
    s.add_argument("--email", default="")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("mark", help="계정 상태 변경 (keep/discard 등)")
    s.add_argument("label")
    s.add_argument("status")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("report", help="점수순 결과 + report.html/csv")
    s.add_argument("--top", type=int, default=30)
    s.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
