"""원본 이미지 -> 새 이미지 (image-to-image).

프롬프트에서 뽑아낸 팔레트/태그와 강도(strength)로 원본을 다시 그린다.
모든 연산은 순수 파이썬이며 네트워크나 모델 가중치가 필요 없다.
"""

from __future__ import annotations

import math

from .noise import Rng, normalize
from .raster import Canvas
from .compose import _field, render

__all__ = ["EFFECTS", "apply_effect", "pick_effect"]

EFFECTS = ("paint", "dream", "duotone", "mosaic", "halftone", "sketch", "pixel",
           "blend")


def _clamp8(v):
    i = int(v)
    return 0 if i < 0 else 255 if i > 255 else i


# --------------------------------------------------------------------------
# 공통 연산
# --------------------------------------------------------------------------
def _warp_canvas(src: Canvas, amount: float, rng: Rng) -> Canvas:
    """노이즈 필드로 픽셀을 밀어 붓터치 느낌을 만든다."""
    w, h = src.width, src.height
    if amount < 0.5:
        return src.copy()
    dx = _field(w, h, rng.next_u64(), octaves=4, cells=4)
    dy = _field(w, h, rng.next_u64(), octaves=4, cells=4)
    out = Canvas(w, h)
    sbuf, obuf = src.buf, out.buf
    for y in range(h):
        base = y * w
        for x in range(w):
            i = base + x
            sx = int(x + (dx[i] - 0.5) * amount)
            sy = int(y + (dy[i] - 0.5) * amount)
            if sx < 0:
                sx = 0
            elif sx >= w:
                sx = w - 1
            if sy < 0:
                sy = 0
            elif sy >= h:
                sy = h - 1
            o = i * 3
            s = (sy * w + sx) * 3
            obuf[o] = sbuf[s]
            obuf[o + 1] = sbuf[s + 1]
            obuf[o + 2] = sbuf[s + 2]
    return out


def _posterize(canvas: Canvas, levels: int) -> None:
    levels = max(2, levels)
    step = 255.0 / (levels - 1)
    lut = [_clamp8(round(i / step) * step) for i in range(256)]
    buf = canvas.buf
    for i in range(len(buf)):
        buf[i] = lut[buf[i]]


def _palette_map(canvas: Canvas, palette, amount: float) -> None:
    """밝기를 팔레트 색으로 치환하고 원본과 섞는다."""
    if amount <= 0:
        return
    lut = [palette.sample(i / 255.0) for i in range(256)]
    buf = canvas.buf
    for i in range(canvas.width * canvas.height):
        o = i * 3
        lum = int(buf[o] * 0.299 + buf[o + 1] * 0.587 + buf[o + 2] * 0.114)
        r, g, b = lut[lum]
        buf[o] = _clamp8(buf[o] + (r - buf[o]) * amount)
        buf[o + 1] = _clamp8(buf[o + 1] + (g - buf[o + 1]) * amount)
        buf[o + 2] = _clamp8(buf[o + 2] + (b - buf[o + 2]) * amount)


def _sobel(canvas: Canvas):
    """0~1 윤곽 강도 필드."""
    w, h = canvas.width, canvas.height
    lum = canvas.luminance()
    out = [0.0] * (w * h)
    for y in range(1, h - 1):
        row = y * w
        up = row - w
        dn = row + w
        for x in range(1, w - 1):
            gx = (lum[up + x + 1] + 2 * lum[row + x + 1] + lum[dn + x + 1]
                  - lum[up + x - 1] - 2 * lum[row + x - 1] - lum[dn + x - 1])
            gy = (lum[dn + x - 1] + 2 * lum[dn + x] + lum[dn + x + 1]
                  - lum[up + x - 1] - 2 * lum[up + x] - lum[up + x + 1])
            v = math.sqrt(gx * gx + gy * gy)
            out[row + x] = v if v < 1.0 else 1.0
    return out


# --------------------------------------------------------------------------
# 효과
# --------------------------------------------------------------------------
def _fx_paint(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """유화/일러스트 풍: 붓터치 왜곡 + 색 단순화 + 윤곽 강조."""
    w = src.width
    # 원본의 미세한 잡티는 먼저 눌러야 색 단순화 후 얼룩이 생기지 않는다.
    smooth = src.blur(max(1, int(w / 220) + 1))
    out = _warp_canvas(smooth, amount=w * 0.012 * strength + 1.0, rng=rng)
    out = out.blur(max(1, int(w / 200 * (0.4 + strength))))
    _posterize(out, int(12 - 7 * strength))
    _palette_map(out, spec.palette, 0.35 * strength)
    edges = _sobel(smooth)
    ink = spec.palette.sample(0.0)
    out.blend_color(ink, [min(1.0, e * 1.6) for e in edges], opacity=0.55 * strength)
    out.adjust(contrast=1.0 + 0.12 * strength, saturation=1.0 + 0.25 * strength)
    return out


def _fx_dream(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """몽환적인 글로우 + 색 이동."""
    w = src.width
    out = _warp_canvas(src, amount=w * 0.02 * strength + 1.0, rng=rng)
    soft = out.blur(max(2, int(w / 90)))
    out.blend_canvas(soft, 0.45 + 0.35 * strength)
    _palette_map(out, spec.palette, 0.45 * strength)
    out.bloom(radius=max(2, w // 110), threshold=0.62, strength=0.35 + 0.4 * strength)
    out.adjust(saturation=1.0 + 0.35 * strength)
    return out


def _fx_duotone(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """밝기를 팔레트 두세 색으로 재해석."""
    out = src.copy()
    out.adjust(contrast=1.0 + 0.3 * strength)
    _palette_map(out, spec.palette, min(1.0, 0.55 + 0.45 * strength))
    return out


def _fx_mosaic(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """사각 타일 모자이크(셀 평균색)."""
    w, h = src.width, src.height
    cell = max(3, int(w / max(8, 90 - 70 * strength)))
    out = src.copy()
    buf, sbuf = out.buf, src.buf
    for cy in range(0, h, cell):
        y1 = min(h, cy + cell)
        for cx in range(0, w, cell):
            x1 = min(w, cx + cell)
            r = g = b = n = 0
            for y in range(cy, y1):
                row = y * w
                for x in range(cx, x1):
                    o = (row + x) * 3
                    r += sbuf[o]
                    g += sbuf[o + 1]
                    b += sbuf[o + 2]
                    n += 1
            r //= n
            g //= n
            b //= n
            for y in range(cy, y1):
                row = y * w
                for x in range(cx, x1):
                    o = (row + x) * 3
                    buf[o] = r
                    buf[o + 1] = g
                    buf[o + 2] = b
    _palette_map(out, spec.palette, 0.25 * strength)
    return out


def _fx_halftone(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """인쇄 망점 스타일."""
    w, h = src.width, src.height
    step = max(3, int(w / max(10, 110 - 60 * strength)))
    paper = spec.palette.sample(1.0)
    ink = spec.palette.sample(0.0)
    out = Canvas(w, h, paper)
    lum = src.luminance()
    radius_max = step * 0.72
    for cy in range(0, h, step):
        for cx in range(0, w, step):
            n = 0
            acc = 0.0
            for y in range(cy, min(h, cy + step)):
                row = y * w
                for x in range(cx, min(w, cx + step)):
                    acc += lum[row + x]
                    n += 1
            v = acc / max(1, n)
            radius = radius_max * (1.0 - v)
            if radius < 0.4:
                continue
            ccx = cx + step / 2.0
            ccy = cy + step / 2.0
            out.disc(ccx, ccy, radius, ink, softness=0.45)
    if strength < 0.99:
        out.blend_canvas(src, 1.0 - strength)
    return out


def _fx_sketch(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """연필 스케치."""
    w, h = src.width, src.height
    edges = _sobel(src)
    paper = spec.palette.sample(1.0)
    paper = tuple(min(255, int(c * 0.25 + 200)) for c in paper)
    out = Canvas(w, h, paper)
    lum = src.luminance()
    buf = out.buf
    gain = 1.0 + 2.5 * strength
    for i in range(w * h):
        e = min(1.0, edges[i] * gain)
        shade = (1.0 - lum[i]) * 0.35 * strength
        k = min(1.0, e * 0.9 + shade)
        o = i * 3
        buf[o] = _clamp8(buf[o] * (1 - k) + 30 * k)
        buf[o + 1] = _clamp8(buf[o + 1] * (1 - k) + 30 * k)
        buf[o + 2] = _clamp8(buf[o + 2] * (1 - k) + 35 * k)
    out.grain(rng, 0.02)
    return out


def _fx_pixel(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """레트로 도트 그래픽: 축소 -> 팔레트 양자화 -> 확대."""
    w, h = src.width, src.height
    grid = max(16, int(w / (4 + 20 * strength)))
    small = src.resized(grid, max(1, int(grid * h / w)))
    colors = [spec.palette.sample(i / 7.0) for i in range(8)]
    buf = small.buf
    for i in range(small.width * small.height):
        o = i * 3
        r, g, b = buf[o], buf[o + 1], buf[o + 2]
        best = None
        bestd = 1 << 30
        for c in colors:
            d = (r - c[0]) ** 2 + (g - c[1]) ** 2 + (b - c[2]) ** 2
            if d < bestd:
                bestd = d
                best = c
        buf[o], buf[o + 1], buf[o + 2] = best
    # 확대는 최근접 이웃으로(도트가 뭉개지지 않게)
    out = Canvas(w, h)
    sw, sh = small.width, small.height
    obuf = out.buf
    for y in range(h):
        sy = min(sh - 1, y * sh // h)
        for x in range(w):
            sx = min(sw - 1, x * sw // w)
            o = (y * w + x) * 3
            s = (sy * sw + sx) * 3
            obuf[o] = buf[s]
            obuf[o + 1] = buf[s + 1]
            obuf[o + 2] = buf[s + 2]
    return out


def _fx_blend(src: Canvas, spec, strength: float, rng: Rng) -> Canvas:
    """프롬프트로 생성한 그림과 원본의 구조를 합성한다."""
    generated = render(spec, src.width, src.height)
    lum = src.luminance()
    out = generated
    # 원본의 명암을 생성 이미지에 입힌다.
    buf = out.buf
    for i in range(src.width * src.height):
        k = (lum[i] - 0.5) * 1.6 * strength
        o = i * 3
        f = 1.0 + k
        buf[o] = _clamp8(buf[o] * f)
        buf[o + 1] = _clamp8(buf[o + 1] * f)
        buf[o + 2] = _clamp8(buf[o + 2] * f)
    out.blend_canvas(src, max(0.0, 0.45 - 0.4 * strength))
    edges = _sobel(src)
    out.blend_color(spec.palette.sample(0.0),
                    [min(1.0, e * 1.4) for e in edges], opacity=0.4 * strength)
    return out


_FX = {
    "paint": _fx_paint,
    "dream": _fx_dream,
    "duotone": _fx_duotone,
    "mosaic": _fx_mosaic,
    "halftone": _fx_halftone,
    "sketch": _fx_sketch,
    "pixel": _fx_pixel,
    "blend": _fx_blend,
}


def pick_effect(spec) -> str:
    """프롬프트 태그로 효과를 자동 선택."""
    tags = spec.tags
    if "mono" in tags:
        return "sketch"
    if "geometric" in tags:
        return "mosaic"
    if tags & {"neon", "glow", "night", "star"}:
        return "dream"
    if "grain" in tags:
        return "halftone"
    if "pastel" in tags:
        return "duotone"
    return "paint"


def apply_effect(src: Canvas, spec, effect: str = "auto",
                 strength: float = 0.7) -> Canvas:
    """원본 캔버스에 효과를 적용한 새 캔버스를 돌려준다."""
    if effect in (None, "auto"):
        effect = pick_effect(spec)
    if effect not in _FX:
        raise ValueError(f"unknown effect: {effect} (choose from {', '.join(EFFECTS)})")
    strength = max(0.0, min(1.0, strength))
    rng = Rng(spec.seed ^ 0x5EED1234)
    out = _FX[effect](src, spec, strength, rng)
    if "mono" in spec.tags:
        out.adjust(saturation=0.0)
    if "grain" in spec.tags:
        out.grain(rng, 0.04)
    return out
