"""README 용 예시 이미지를 다시 만든다.

실행: python3 examples/make_examples.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import offimg

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 320

TEXT_JOBS = [
    ("landscape.png", "겨울 설산 위로 떠오르는 아침 해", None, 11),
    ("nebula.png", "깊은 우주의 보라색 성운과 별", None, 3),
    ("waves.png", "밤바다 위에 뜬 보름달", None, 21),
    ("mandala.png", "파스텔 만다라 문양", None, 5),
]


def main() -> int:
    for name, prompt, style, seed in TEXT_JOBS:
        canvas = offimg.generate(prompt, SIZE, style=style or "auto", seed=seed)
        canvas.save(os.path.join(HERE, name))
        print(f"{name:16s} <- {prompt}")

    # 원본 이미지 -> 이미지 예시 (위에서 만든 그림을 원본으로 사용)
    source = offimg.load(os.path.join(HERE, "landscape.png"))
    for name, prompt, effect in [
        ("i2i-paint.png", "가을 단풍", "paint"),
        ("i2i-sketch.png", "흑백 스케치", "sketch"),
        ("i2i-halftone.png", "빈티지 인쇄", "halftone"),
    ]:
        out = offimg.apply_effect(source, offimg.analyze(prompt, seed=7), effect, 0.75)
        out.save(os.path.join(HERE, name))
        print(f"{name:16s} <- landscape.png + '{prompt}' ({effect})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
