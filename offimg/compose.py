"""텍스트 -> 이미지: 프롬프트 해석 결과로 그림을 합성한다.

여섯 가지 구도(landscape / nebula / abstract / mosaic / mandala / waves)를
제공하고, 각 구도는 노이즈 필드와 팔레트만으로 그려진다.
"""

from __future__ import annotations

import math

from .noise import Rng, fbm, resize_field, warp, normalize
from .palette import Palette, hsv_to_rgb
from .raster import Canvas

__all__ = ["render"]

_MAX_NOISE_DIM = 192  # 노이즈는 이 해상도 이하에서 만들고 확대한다.


def _field(w, h, seed, octaves=5, cells=3, stretch=1.0, maxdim=_MAX_NOISE_DIM):
    """저해상도에서 fbm 을 만들고 목표 크기로 확대."""
    longest = max(w, h)
    if longest > maxdim:
        scale = longest / maxdim
        lw = max(8, int(w / scale))
        lh = max(8, int(h / scale))
    else:
        lw, lh = w, h
    f = fbm(lw, lh, seed, octaves=octaves, cells=cells, stretch=stretch)
    return resize_field(f, lw, lh, w, h)


def _ridge(width, seed, octaves=5, cells=3):
    """가로 방향 1차원 능선 프로파일 (0~1)."""
    line = fbm(max(8, width), 1, seed, octaves=octaves, cells=cells)
    if len(line) != width:
        line = resize_field(line, len(line), 1, width, 1)
    return normalize(line)


def _stars(canvas: Canvas, rng: Rng, density=1.0, top_ratio=1.0):
    """별을 흩뿌린다. 밝은 별에는 십자 플레어를 넣는다."""
    w, h = canvas.width, canvas.height
    limit = max(1, int(h * top_ratio))
    count = int(w * limit / 1400.0 * density)
    for _ in range(count):
        x = rng.randint(0, w - 1)
        y = rng.randint(0, limit - 1)
        b = rng.random() ** 2.2
        v = int(120 + 135 * b)
        tint = rng.uniform(0.85, 1.0)
        r, g, bl = canvas.get(x, y)
        canvas.put(x, y, max(r, int(v * tint)), max(g, int(v * tint)), max(bl, v))
        if b > 0.86:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                px, py = x + dx, y + dy
                if 0 <= px < w and 0 <= py < h:
                    r, g, bl = canvas.get(px, py)
                    hv = int(v * 0.45)
                    canvas.put(px, py, max(r, hv), max(g, hv), max(bl, hv))


# --------------------------------------------------------------------------
# 구도별 렌더러
# --------------------------------------------------------------------------
def _render_landscape(spec, w, h, rng: Rng) -> Canvas:
    pal: Palette = spec.palette
    tags = spec.tags
    night = "night" in tags
    canvas = Canvas(w, h)

    horizon = int(h * rng.uniform(0.52, 0.72))

    # 하늘: 위쪽이 짙고 지평선이 밝은 그라데이션
    sky = pal if night else pal
    for y in range(horizon):
        t = 1.0 - (y / max(1, horizon - 1))
        color = bytes(sky.sample(0.15 + 0.85 * t ** 1.4))
        canvas.buf[y * w * 3:(y + 1) * w * 3] = color * w

    if night or "star" in tags:
        _stars(canvas, rng, density=1.2 if "star" in tags else 0.7,
               top_ratio=horizon / h)

    # 해 / 달
    if tags & {"sun", "moon"} or rng.chance(0.5):
        sx = rng.uniform(w * 0.15, w * 0.85)
        sy = rng.uniform(h * 0.12, horizon * 0.72)
        radius = w * rng.uniform(0.035, 0.085)
        if "moon" in tags or night:
            color = (238, 240, 228)
            glow = 0.45
        else:
            color = pal.sample(rng.uniform(0.75, 1.0))
            color = tuple(min(255, int(c * 1.25) + 40) for c in color)
            glow = 0.6
        canvas.disc(sx, sy, radius, color, softness=0.08, glow=glow)

    # 구름 / 안개
    if "cloud" in tags or rng.chance(0.55):
        cf = _field(w, horizon, rng.next_u64(), octaves=5, cells=3, stretch=3.2)
        cloud_color = pal.sample(0.95) if not night else pal.sample(0.55)
        mask = [0.0] * (w * horizon)
        thr = rng.uniform(0.5, 0.62)
        for y in range(horizon):
            fade = min(1.0, (y / max(1, horizon)) * 2.4)  # 위쪽은 옅게
            base = y * w
            for x in range(w):
                v = cf[base + x]
                if v > thr:
                    mask[base + x] = min(1.0, (v - thr) / (1.0 - thr)) * fade
        sub = Canvas(w, horizon, buf=canvas.buf[:w * horizon * 3])
        sub.blend_color(cloud_color, mask, opacity=rng.uniform(0.5, 0.8))
        canvas.buf[:w * horizon * 3] = sub.buf

    # 산 능선 (뒤 -> 앞)
    layers = rng.randint(3, 4)
    for i in range(layers):
        depth = i / max(1, layers - 1)          # 0 = 가장 먼 산
        prof = _ridge(w, rng.next_u64(), octaves=4 + i, cells=2 + i)
        top = horizon - int(h * (0.26 - 0.055 * i) * rng.uniform(0.8, 1.2))
        amp = h * (0.10 + 0.045 * i)
        base_color = pal.sample(0.42 - 0.11 * depth)
        fog = pal.sample(0.72)
        k = 0.55 * (1.0 - depth)                 # 먼 산일수록 하늘색에 섞임
        color = tuple(int(c + (f - c) * k) for c, f in zip(base_color, fog))
        snow = "snow" in tags
        snow_color = (236, 240, 248)
        for x in range(w):
            y0 = int(top + (1.0 - prof[x]) * amp)
            canvas.column_from(x, y0, color)
            if snow and depth < 0.7:
                cap = int(amp * 0.28 * prof[x])
                for yy in range(max(0, y0), min(h, y0 + cap)):
                    r, g, b = canvas.get(x, yy)
                    m = 1.0 - (yy - y0) / max(1, cap)
                    canvas.put(x, yy,
                               r + (snow_color[0] - r) * m,
                               g + (snow_color[1] - g) * m,
                               b + (snow_color[2] - b) * m)

    # 지면 / 수면: 아래로 갈수록 어두워지는 그라데이션
    depth = max(1, h - horizon)
    for y in range(horizon, h):
        t = (y - horizon) / depth
        color = bytes(pal.sample(max(0.0, 0.2 - 0.17 * t)))
        canvas.buf[y * w * 3:(y + 1) * w * 3] = color * w

    # 지평선 부근 옅은 안개 (하늘과 땅이 만나는 선을 부드럽게)
    haze = pal.sample(0.66)
    band = max(2, int(h * 0.06))
    for y in range(max(0, horizon - band), min(h, horizon + band)):
        k = 0.45 * (1.0 - abs(y - horizon) / band)
        if k <= 0.01:
            continue
        row = y * w
        for x in range(w):
            o = (row + x) * 3
            for c in range(3):
                canvas.buf[o + c] = int(canvas.buf[o + c]
                                        + (haze[c] - canvas.buf[o + c]) * k)

    if "water" not in tags and h > horizon:
        depth_h = h - horizon
        tex = normalize(_field(w, depth_h, rng.next_u64(), octaves=4, cells=2,
                               stretch=4.0))
        sub = Canvas(w, depth_h, buf=canvas.buf[horizon * w * 3:])
        sub.blend_color(pal.sample(0.32), [max(0.0, (v - 0.45) * 1.4) for v in tex],
                        opacity=0.5)
        canvas.buf[horizon * w * 3:] = sub.buf

    if "water" in tags:
        # 하늘을 아래로 뒤집어 반사시키고 가로 결을 넣는다.
        depth_h = h - horizon
        shimmer = _field(w, max(1, depth_h), rng.next_u64(), octaves=4, cells=2,
                         stretch=6.0)
        for y in range(horizon, h):
            t = (y - horizon) / max(1, depth_h)
            src_y = int(horizon - 1 - t * (horizon - 1) * 0.85)
            fade = 0.75 * (1.0 - t * 0.75)
            base = (y - horizon) * w
            for x in range(w):
                off = int((shimmer[base + x] - 0.5) * 10 * (0.4 + t))
                sx = min(w - 1, max(0, x + off))
                r, g, b = canvas.get(sx, max(0, src_y))
                dr, dg, db = canvas.get(x, y)
                canvas.put(x, y,
                           dr + (r - dr) * fade,
                           dg + (g - dg) * fade,
                           db + (b - db) * fade)
    return canvas


def _render_nebula(spec, w, h, rng: Rng) -> Canvas:
    pal = spec.palette
    canvas = Canvas(w, h, pal.sample(0.0))

    base = _field(w, h, rng.next_u64(), octaves=6, cells=2)
    dx = _field(w, h, rng.next_u64(), octaves=4, cells=2)
    dy = _field(w, h, rng.next_u64(), octaves=4, cells=2)
    swirled = warp(base, w, h, dx, dy, amount=w * rng.uniform(0.05, 0.14))
    swirled = normalize(swirled)

    # 대비를 세워 성운의 결을 만든다.
    shaped = [max(0.0, min(1.0, (v - 0.42) * 1.9 + 0.42)) ** 1.6 for v in swirled]
    canvas.paint_field(shaped, pal)

    # 두 번째 층: 다른 색상의 가스
    second = normalize(_field(w, h, rng.next_u64(), octaves=5, cells=3))
    accent = pal.sample(rng.uniform(0.7, 1.0))
    mask = [max(0.0, (v - 0.62) * 2.6) for v in second]
    canvas.blend_color(accent, mask, opacity=0.55)

    _stars(canvas, rng, density=2.0)
    canvas.bloom(radius=max(2, w // 130), threshold=0.55, strength=0.6)
    return canvas


def _render_abstract(spec, w, h, rng: Rng) -> Canvas:
    pal = spec.palette
    canvas = Canvas(w, h)

    base = _field(w, h, rng.next_u64(), octaves=5, cells=2)
    dx = _field(w, h, rng.next_u64(), octaves=3, cells=2)
    dy = _field(w, h, rng.next_u64(), octaves=3, cells=2)
    field = normalize(warp(base, w, h, dx, dy, amount=w * rng.uniform(0.08, 0.2)))

    bands = rng.randint(2, 5)
    smooth = rng.uniform(0.25, 0.6)
    shaped = []
    for v in field:
        b = (v * bands) % 1.0
        # 톱니를 부드럽게 만들어 유체/마블 느낌으로
        shaped.append(b * (1 - smooth) + v * smooth)
    canvas.paint_field(shaped, pal)

    overlay = normalize(_field(w, h, rng.next_u64(), octaves=4, cells=4))
    canvas.blend_color(pal.sample(1.0), [max(0.0, (v - 0.7) * 3.0) for v in overlay],
                       opacity=0.45)
    canvas.blend_color(pal.sample(0.0), [max(0.0, (0.3 - v) * 3.0) for v in overlay],
                       opacity=0.35)
    return canvas


def _render_mosaic(spec, w, h, rng: Rng) -> Canvas:
    pal = spec.palette
    canvas = Canvas(w, h, pal.sample(0.0))

    cell = max(6, int(w / rng.randint(10, 22)))
    field = normalize(_field(w, h, rng.next_u64(), octaves=4, cells=2))
    triangles = rng.chance(0.45)
    accent = pal.sample(1.0)

    for cy in range(0, h, cell):
        for cx in range(0, w, cell):
            sx = min(w - 1, cx + cell // 2)
            sy = min(h - 1, cy + cell // 2)
            t = field[sy * w + sx]
            t = (t - 0.5) * 1.5 + 0.5                       # 셀 사이 대비를 키운다
            t = max(0.0, min(1.0, t + rng.uniform(-0.14, 0.14)))
            c1 = accent if rng.chance(0.1) else pal.sample(t)
            c2 = pal.sample(max(0.0, min(1.0, t + rng.uniform(0.15, 0.45))))
            x1 = min(w, cx + cell)
            y1 = min(h, cy + cell)
            for y in range(cy, y1):
                row = y * w
                for x in range(cx, x1):
                    if triangles and (x - cx) + (y - cy) > cell:
                        r, g, b = c2
                    else:
                        r, g, b = c1
                    o = (row + x) * 3
                    canvas.buf[o] = r
                    canvas.buf[o + 1] = g
                    canvas.buf[o + 2] = b
    # 셀 경계에 얇은 어두운 선 (타일 느낌)
    if rng.chance(0.6):
        line = pal.sample(0.0)
        for cy in range(0, h, cell):
            for x in range(w):
                canvas.put(x, cy, *line)
        for cx in range(0, w, cell):
            for y in range(h):
                canvas.put(cx, y, *line)
    return canvas


def _render_mandala(spec, w, h, rng: Rng) -> Canvas:
    pal = spec.palette
    canvas = Canvas(w, h, pal.sample(0.0))

    sym = rng.choice((6, 8, 10, 12, 16))
    rings = rng.randint(5, 11)
    petal = rng.randint(3, 7)
    twist = rng.uniform(-2.5, 2.5)
    levels = rng.randint(4, 9)          # 색 계단 수 (또렷한 문양)
    soft = rng.uniform(0.15, 0.5)       # 계단 사이 부드러움
    edge = rng.uniform(0.55, 0.8)       # 문양이 끝나는 반지름
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    maxr = math.sqrt(cx * cx + cy * cy)
    sector = 2 * math.pi / sym
    buf = canvas.buf
    lut = [pal.sample(i / 255.0) for i in range(256)]

    for y in range(h):
        dy = y - cy
        row = y * w
        for x in range(w):
            dx = x - cx
            r = math.sqrt(dx * dx + dy * dy) / maxr
            a = math.atan2(dy, dx) + twist * r
            a = a % sector
            if a > sector / 2:
                a = sector - a            # 거울 대칭
            v = (math.sin(r * rings * math.pi) * 0.5 + 0.5) * 0.6 \
                + (math.sin(a * petal * sym) * 0.5 + 0.5) * 0.4
            v = v ** 1.7                                    # 어두운 골을 깊게
            v = (int(v * levels) + (v * levels % 1.0) * soft) / levels
            if r > edge:                                    # 바깥은 배경으로
                v *= max(0.0, 1.0 - (r - edge) / (1.0 - edge) * 2.2)
            idx = int(v * 255.0)
            rr, gg, bb = lut[0 if idx < 0 else 255 if idx > 255 else idx]
            o = (row + x) * 3
            buf[o] = rr
            buf[o + 1] = gg
            buf[o + 2] = bb
    canvas.bloom(radius=max(2, w // 200), threshold=0.75, strength=0.35)
    return canvas


def _render_waves(spec, w, h, rng: Rng) -> Canvas:
    pal = spec.palette
    canvas = Canvas(w, h)
    canvas.gradient_v(pal.reversed(), curve=0.8)

    if "sun" in spec.tags or "moon" in spec.tags or rng.chance(0.6):
        color = (245, 238, 220) if "night" in spec.tags else pal.sample(0.95)
        canvas.disc(rng.uniform(w * 0.2, w * 0.8), h * rng.uniform(0.12, 0.28),
                    w * rng.uniform(0.05, 0.1), color, softness=0.1, glow=0.5)

    if spec.tags & {"night", "star"}:
        _stars(canvas, rng, density=1.0, top_ratio=0.45)

    layers = rng.randint(5, 9)
    start = h * rng.uniform(0.32, 0.45)
    for i in range(layers):
        t = i / max(1, layers - 1)
        prof = _ridge(w, rng.next_u64(), octaves=3, cells=2)
        base_y = start + (h - start) * (t ** 1.25) * rng.uniform(0.9, 1.05)
        amp = h * 0.055 * (1.0 - 0.5 * t)
        phase = rng.uniform(0, 6.28)
        freq = rng.uniform(1.5, 4.0)
        color = pal.sample(max(0.0, 0.62 - 0.6 * t))
        for x in range(w):
            wave = math.sin(x / w * freq * 6.2832 + phase) * amp * 0.6
            y0 = int(base_y + wave + (prof[x] - 0.5) * amp * 2.0)
            canvas.column_from(x, y0, color)
    return canvas


_RENDERERS = {
    "landscape": _render_landscape,
    "nebula": _render_nebula,
    "abstract": _render_abstract,
    "mosaic": _render_mosaic,
    "mandala": _render_mandala,
    "waves": _render_waves,
}


def render(spec, width: int, height: int) -> Canvas:
    """PromptSpec -> Canvas. 같은 spec/크기면 항상 같은 결과."""
    rng = Rng(spec.seed ^ 0xA5A5A5A5)
    canvas = _RENDERERS[spec.style](spec, width, height, rng)

    # 공통 마무리
    tags = spec.tags
    if tags & {"glow", "neon"}:
        canvas.bloom(radius=max(2, width // 120), threshold=0.6, strength=0.5)
    canvas.adjust(brightness=0.0,
                  contrast=1.06 if "mono" not in tags else 1.15,
                  saturation=0.0 if "mono" in tags else 1.06)
    canvas.vignette(0.3 if "night" in tags else 0.2)
    canvas.grain(rng, 0.05 if "grain" in tags else 0.015)
    return canvas
