#!/usr/bin/env python3
"""촬영 세션 점검: RAW16 저장 상태, 장면별 프레임 수, RGB 짝 맞춤, FFC 정지 구간을 요약.

사용: python3 check_session.py captures/20261125_101500_공학관3층 [--preview]
--preview 를 주면 장면별 첫 짝(열화상 AGC | RGB)을 preview/ 폴더에 저장합니다.
"""
import argparse, csv, json, os, sys
from collections import Counter

import numpy as np
import cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    s = a.session
    meta = json.load(open(os.path.join(s, "meta.json"), encoding="utf-8"))
    th = list(csv.DictReader(open(os.path.join(s, "thermal_frames.csv"), newline="")))
    print(f"세션: {s}\n장소: {meta.get('location')}  메모: {meta.get('note')}")
    print(f"열화상 {meta.get('thermal_saved')}장 (측정 {meta.get('thermal_fps_measured')} fps), "
          f"RGB {meta.get('rgb_saved')}장 (측정 {meta.get('rgb_fps_measured')} fps), 저장 누락 {meta.get('writer_dropped')}")

    # RAW16 확인
    bad = 0
    for x in th[:: max(1, len(th) // 20)]:
        img = cv2.imread(os.path.join(s, x["file"]), cv2.IMREAD_UNCHANGED)
        if img is None or img.dtype != np.uint16 or img.shape != (512, 640):
            bad += 1
    print("RAW16 형식 점검(표본):", "정상 (uint16, 640x512)" if bad == 0 else f"이상 {bad}장")
    if th:
        mn = min(int(x["min"]) for x in th); mx = max(int(x["max"]) for x in th)
        print(f"count 범위: {mn} ~ {mx}  (TLinear 고이득이면 {mn/100-273.15:.1f} ~ {mx/100-273.15:.1f} °C)")
        fr = sum(int(x["frozen"]) for x in th)
        print(f"정지 프레임(FFC 추정): {fr}장")

    print("장면별 열화상 프레임 수:")
    for k, v in sorted(Counter(x["scene"] for x in th).items()):
        print(f"  {k:<18} {v}")

    pp = os.path.join(s, "pairs.csv")
    if os.path.exists(pp):
        pr = list(csv.DictReader(open(pp, newline="")))
        dts = np.array([abs(float(x["dt_ms"])) for x in pr])
        ok = sum(int(x["ok"]) for x in pr)
        print(f"RGB 짝: {ok}/{len(pr)} (50 ms 이내), 시간차 중앙값 {np.median(dts):.1f} ms, 최대 {dts.max():.1f} ms")
        if a.preview:
            os.makedirs(os.path.join(s, "preview"), exist_ok=True)
            done = set()
            for x in pr:
                key = (x["scene"], x["segment"])
                if key in done:
                    continue
                done.add(key)
                raw = cv2.imread(os.path.join(s, x["thermal"]), cv2.IMREAD_UNCHANGED)
                lo, hi = np.percentile(raw, (1, 99))
                v = np.clip((raw.astype(np.float32) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
                v = cv2.cvtColor(v, cv2.COLOR_GRAY2BGR)
                rgb = cv2.imread(os.path.join(s, x["rgb"]))
                rgb = cv2.resize(rgb, (int(512 * rgb.shape[1] / rgb.shape[0]), 512))
                cv2.imwrite(os.path.join(s, "preview", f"seg{x['segment']}_{x['scene']}.jpg"), np.hstack([v, rgb]))
            print(f"미리보기 {len(done)}장 저장: {os.path.join(s, 'preview')}")


if __name__ == "__main__":
    sys.exit(main())
