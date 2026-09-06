"""결정적 난수와 노이즈 필드.

* 같은 시드(=같은 프롬프트)면 언제나 같은 결과가 나오도록 표준 random 대신
  자체 PRNG(splitmix64)를 쓴다.
* 노이즈는 저해상도 격자에서 만들고 확대(bilinear)해서 순수 파이썬으로도
  큰 이미지를 빠르게 채운다.
"""

from __future__ import annotations

import hashlib
import math

__all__ = ["Rng", "seed_from_text", "fbm", "resize_field", "warp", "normalize"]

_MASK = (1 << 64) - 1


class Rng:
    """splitmix64 기반 결정적 난수 생성기."""

    __slots__ = ("state",)

    def __init__(self, seed: int):
        self.state = seed & _MASK

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def random(self) -> float:
        """[0, 1) 실수."""
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.random()

    def randint(self, lo: int, hi: int) -> int:
        """[lo, hi] 정수."""
        return lo + int(self.random() * (hi - lo + 1)) % (hi - lo + 1)

    def choice(self, seq):
        return seq[int(self.random() * len(seq)) % len(seq)]

    def chance(self, p: float) -> bool:
        return self.random() < p


def seed_from_text(text: str, salt: int = 0) -> int:
    """문자열 -> 64bit 시드 (플랫폼/실행마다 동일)."""
    digest = hashlib.sha256(f"{salt}\x00{text}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _octave(width: int, height: int, cells_x: int, cells_y: int, rng: Rng):
    """격자 값 노이즈 한 옥타브. 길이 width*height 의 float 리스트."""
    cells_x = max(1, cells_x)
    cells_y = max(1, cells_y)
    lw = cells_x + 1
    lattice = [rng.random() for _ in range(lw * (cells_y + 1))]

    # x 방향 보간 계수는 모든 행에서 동일하므로 미리 계산한다.
    xs = []
    sx = cells_x / width
    for x in range(width):
        fx = x * sx
        gx = int(fx)
        if gx >= cells_x:
            gx = cells_x - 1
        xs.append((gx, _smoothstep(fx - gx)))

    out = [0.0] * (width * height)
    sy = cells_y / height
    for y in range(height):
        fy = y * sy
        gy = int(fy)
        if gy >= cells_y:
            gy = cells_y - 1
        ty = _smoothstep(fy - gy)
        row0 = gy * lw
        row1 = row0 + lw
        base = y * width
        for i in range(width):
            gx, tx = xs[i]
            a = lattice[row0 + gx]
            b = lattice[row0 + gx + 1]
            c = lattice[row1 + gx]
            d = lattice[row1 + gx + 1]
            top = a + (b - a) * tx
            bot = c + (d - c) * tx
            out[base + i] = top + (bot - top) * ty
    return out


def fbm(width: int, height: int, seed: int, octaves: int = 5,
        cells: int = 4, gain: float = 0.5, ratio: float = 2.0,
        stretch: float = 1.0):
    """fractal Brownian motion 노이즈 필드. 값 범위 [0, 1].

    ``stretch`` > 1 이면 가로로 늘어난 결(수평 구름/물결)이 된다.
    """
    rng = Rng(seed)
    field = [0.0] * (width * height)
    amp = 1.0
    total = 0.0
    cx = cells
    cy = max(1, int(round(cells / stretch)))
    for _ in range(max(1, octaves)):
        layer = _octave(width, height, int(cx), int(cy), rng)
        for i in range(len(field)):
            field[i] += layer[i] * amp
        total += amp
        amp *= gain
        cx *= ratio
        cy *= ratio
        if cx > width and cy > height:
            break
    inv = 1.0 / total if total else 1.0
    return [v * inv for v in field]


def normalize(field):
    """필드를 [0, 1] 로 재정규화."""
    lo = min(field)
    hi = max(field)
    if hi - lo < 1e-9:
        return [0.5] * len(field)
    inv = 1.0 / (hi - lo)
    return [(v - lo) * inv for v in field]


def resize_field(field, sw: int, sh: int, dw: int, dh: int):
    """float 필드 bilinear 확대/축소."""
    if sw == dw and sh == dh:
        return list(field)
    out = [0.0] * (dw * dh)
    fx_scale = (sw - 1) / (dw - 1) if dw > 1 else 0.0
    fy_scale = (sh - 1) / (dh - 1) if dh > 1 else 0.0
    xs = []
    for x in range(dw):
        fx = x * fx_scale
        x0 = int(fx)
        if x0 >= sw - 1:
            x0 = max(0, sw - 2)
        xs.append((x0, fx - x0))
    for y in range(dh):
        fy = y * fy_scale
        y0 = int(fy)
        if y0 >= sh - 1:
            y0 = max(0, sh - 2)
        ty = fy - y0
        r0 = y0 * sw
        r1 = r0 + sw if sh > 1 else r0
        base = y * dw
        for i in range(dw):
            x0, tx = xs[i]
            a = field[r0 + x0]
            b = field[r0 + x0 + 1] if sw > 1 else a
            c = field[r1 + x0]
            d = field[r1 + x0 + 1] if sw > 1 else c
            top = a + (b - a) * tx
            bot = c + (d - c) * tx
            out[base + i] = top + (bot - top) * ty
    return out


def warp(field, width: int, height: int, dx_field, dy_field, amount: float):
    """노이즈 필드로 다른 필드를 휘게 만든다(domain warping)."""
    out = [0.0] * (width * height)
    for y in range(height):
        base = y * width
        for x in range(width):
            i = base + x
            nx = x + (dx_field[i] - 0.5) * amount
            ny = y + (dy_field[i] - 0.5) * amount
            xi = int(nx)
            yi = int(ny)
            if xi < 0:
                xi = 0
            elif xi > width - 1:
                xi = width - 1
            if yi < 0:
                yi = 0
            elif yi > height - 1:
                yi = height - 1
            out[i] = field[yi * width + xi]
    return out


def gauss_kernel(radius: int):
    """1차원 정규화 가우시안 커널."""
    if radius <= 0:
        return [1.0]
    sigma = radius / 2.0
    k = [math.exp(-(i * i) / (2 * sigma * sigma)) for i in range(-radius, radius + 1)]
    s = sum(k)
    return [v / s for v in k]
