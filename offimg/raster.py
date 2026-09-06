"""픽셀 버퍼(Canvas)와 기본 래스터 연산."""

from __future__ import annotations

import math

from . import png
from .noise import gauss_kernel

__all__ = ["Canvas"]


def _clamp8(v: float) -> int:
    i = int(v)
    if i < 0:
        return 0
    if i > 255:
        return 255
    return i


class Canvas:
    """8bit RGB 픽셀 버퍼. 내부 저장은 ``bytearray`` (w*h*3)."""

    __slots__ = ("width", "height", "buf")

    def __init__(self, width: int, height: int, color=(0, 0, 0), buf=None):
        self.width = int(width)
        self.height = int(height)
        if buf is not None:
            if len(buf) != self.width * self.height * 3:
                raise ValueError("buffer size mismatch")
            self.buf = bytearray(buf)
        else:
            self.buf = bytearray(bytes(color) * (self.width * self.height))

    # ---------------------------------------------------------------- 입출력
    @classmethod
    def load(cls, path: str) -> "Canvas":
        w, h, rgb = png.read(path)
        return cls(w, h, buf=rgb)

    def save(self, path: str) -> str:
        return png.write(path, self.width, self.height, bytes(self.buf))

    def copy(self) -> "Canvas":
        return Canvas(self.width, self.height, buf=self.buf)

    # ---------------------------------------------------------------- 픽셀
    def get(self, x: int, y: int):
        i = (y * self.width + x) * 3
        return self.buf[i], self.buf[i + 1], self.buf[i + 2]

    def put(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.buf[i] = _clamp8(r)
            self.buf[i + 1] = _clamp8(g)
            self.buf[i + 2] = _clamp8(b)

    def fill(self, color) -> None:
        self.buf = bytearray(bytes(color) * (self.width * self.height))

    # ---------------------------------------------------------------- 합성
    def gradient_v(self, palette, curve: float = 1.0) -> None:
        """세로 그라데이션으로 채운다. ``palette`` 는 sample(t)->rgb 인 객체."""
        h = max(1, self.height - 1)
        row_len = self.width * 3
        for y in range(self.height):
            t = (y / h) ** curve
            color = bytes(palette.sample(t))
            self.buf[y * row_len:(y + 1) * row_len] = color * self.width

    def paint_field(self, field, palette, opacity: float = 1.0) -> None:
        """필드 값(0~1)을 팔레트 색으로 칠한다."""
        lut = [palette.sample(i / 255.0) for i in range(256)]
        buf = self.buf
        if opacity >= 0.999:
            for i, v in enumerate(field):
                idx = int(v * 255.0)
                r, g, b = lut[0 if idx < 0 else 255 if idx > 255 else idx]
                o = i * 3
                buf[o] = r
                buf[o + 1] = g
                buf[o + 2] = b
        else:
            k = opacity
            for i, v in enumerate(field):
                idx = int(v * 255.0)
                r, g, b = lut[0 if idx < 0 else 255 if idx > 255 else idx]
                o = i * 3
                buf[o] = _clamp8(buf[o] + (r - buf[o]) * k)
                buf[o + 1] = _clamp8(buf[o + 1] + (g - buf[o + 1]) * k)
                buf[o + 2] = _clamp8(buf[o + 2] + (b - buf[o + 2]) * k)

    def blend_color(self, color, mask, opacity: float = 1.0) -> None:
        """마스크(0~1 필드) 강도로 단색을 올린다."""
        cr, cg, cb = color
        buf = self.buf
        for i, m in enumerate(mask):
            k = m * opacity
            if k <= 0.002:
                continue
            if k > 1.0:
                k = 1.0
            o = i * 3
            buf[o] = _clamp8(buf[o] + (cr - buf[o]) * k)
            buf[o + 1] = _clamp8(buf[o + 1] + (cg - buf[o + 1]) * k)
            buf[o + 2] = _clamp8(buf[o + 2] + (cb - buf[o + 2]) * k)

    def blend_canvas(self, other: "Canvas", amount=0.5) -> None:
        """다른 캔버스를 섞는다. amount 는 실수 또는 픽셀별 필드."""
        if other.width != self.width or other.height != self.height:
            other = other.resized(self.width, self.height)
        a = self.buf
        b = other.buf
        n = self.width * self.height
        if isinstance(amount, (int, float)):
            k = float(amount)
            if k <= 0:
                return
            for o in range(0, n * 3):
                a[o] = _clamp8(a[o] + (b[o] - a[o]) * k)
        else:
            for i in range(n):
                k = amount[i]
                if k <= 0.002:
                    continue
                if k > 1.0:
                    k = 1.0
                o = i * 3
                a[o] = _clamp8(a[o] + (b[o] - a[o]) * k)
                a[o + 1] = _clamp8(a[o + 1] + (b[o + 1] - a[o + 1]) * k)
                a[o + 2] = _clamp8(a[o + 2] + (b[o + 2] - a[o + 2]) * k)

    # ---------------------------------------------------------------- 도형
    def disc(self, cx: float, cy: float, radius: float, color,
             softness: float = 0.15, glow: float = 0.0) -> None:
        """부드러운 원(해/달) + 선택적 글로우."""
        cr, cg, cb = color
        reach = radius * (1.0 + max(glow, 0.0) * 6.0) + 2
        x0 = max(0, int(cx - reach))
        x1 = min(self.width, int(cx + reach) + 1)
        y0 = max(0, int(cy - reach))
        y1 = min(self.height, int(cy + reach) + 1)
        edge = max(1e-6, radius * softness)
        inner = radius - edge
        outer = radius + edge
        glow = max(0.0, glow)
        buf = self.buf
        for y in range(y0, y1):
            dy = y - cy
            dy2 = dy * dy
            base = y * self.width
            for x in range(x0, x1):
                dx = x - cx
                d = math.sqrt(dx * dx + dy2)
                if d <= inner:
                    k = 1.0
                elif d <= outer:
                    # 원 가장자리는 1.0 -> glow 로 이어져야 테두리에 검은 띠가 안 생긴다.
                    f = (outer - d) / (2 * edge)
                    k = glow + (1.0 - glow) * f
                elif glow > 0.0 and reach > outer:
                    t = (d - outer) / (reach - outer)
                    k = glow * max(0.0, 1.0 - t) ** 3
                else:
                    continue
                if k <= 0.002:
                    continue
                o = (base + x) * 3
                buf[o] = _clamp8(buf[o] + (cr - buf[o]) * k)
                buf[o + 1] = _clamp8(buf[o + 1] + (cg - buf[o + 1]) * k)
                buf[o + 2] = _clamp8(buf[o + 2] + (cb - buf[o + 2]) * k)

    def column_from(self, x: int, y_start: int, color) -> None:
        """(x, y_start) 부터 아래쪽 끝까지 세로선을 칠한다(산 능선 채우기)."""
        if not 0 <= x < self.width:
            return
        cr, cg, cb = color
        buf = self.buf
        y0 = max(0, y_start)
        for y in range(y0, self.height):
            o = (y * self.width + x) * 3
            buf[o] = cr
            buf[o + 1] = cg
            buf[o + 2] = cb

    # ---------------------------------------------------------------- 필터
    def luminance(self):
        """0~1 밝기 필드."""
        buf = self.buf
        n = self.width * self.height
        out = [0.0] * n
        for i in range(n):
            o = i * 3
            out[i] = (buf[o] * 0.299 + buf[o + 1] * 0.587 + buf[o + 2] * 0.114) / 255.0
        return out

    def blur(self, radius: int) -> "Canvas":
        """가우시안(분리형) 블러. 새 캔버스를 돌려준다."""
        if radius < 1:
            return self.copy()
        k = gauss_kernel(radius)
        w, h = self.width, self.height
        src = self.buf
        tmp = bytearray(len(src))
        for y in range(h):
            row = y * w
            for x in range(w):
                r = g = b = 0.0
                for j, kv in enumerate(k):
                    sx = x + j - radius
                    if sx < 0:
                        sx = 0
                    elif sx >= w:
                        sx = w - 1
                    o = (row + sx) * 3
                    r += src[o] * kv
                    g += src[o + 1] * kv
                    b += src[o + 2] * kv
                o = (row + x) * 3
                tmp[o] = _clamp8(r)
                tmp[o + 1] = _clamp8(g)
                tmp[o + 2] = _clamp8(b)
        out = bytearray(len(src))
        for y in range(h):
            for x in range(w):
                r = g = b = 0.0
                for j, kv in enumerate(k):
                    sy = y + j - radius
                    if sy < 0:
                        sy = 0
                    elif sy >= h:
                        sy = h - 1
                    o = (sy * w + x) * 3
                    r += tmp[o] * kv
                    g += tmp[o + 1] * kv
                    b += tmp[o + 2] * kv
                o = (y * w + x) * 3
                out[o] = _clamp8(r)
                out[o + 1] = _clamp8(g)
                out[o + 2] = _clamp8(b)
        return Canvas(w, h, buf=out)

    def bloom(self, radius: int = 6, threshold: float = 0.72,
              strength: float = 0.55) -> None:
        """밝은 부분만 뽑아 블러해서 더한다."""
        w, h = self.width, self.height
        bright = bytearray(len(self.buf))
        buf = self.buf
        t = threshold * 255.0
        for i in range(w * h):
            o = i * 3
            lum = buf[o] * 0.299 + buf[o + 1] * 0.587 + buf[o + 2] * 0.114
            if lum > t:
                k = (lum - t) / (255.0 - t + 1e-6)
                bright[o] = _clamp8(buf[o] * k)
                bright[o + 1] = _clamp8(buf[o + 1] * k)
                bright[o + 2] = _clamp8(buf[o + 2] * k)
        glow = Canvas(w, h, buf=bright).blur(radius).buf
        for o in range(len(buf)):
            buf[o] = _clamp8(buf[o] + glow[o] * strength)

    def resized(self, dw: int, dh: int) -> "Canvas":
        """bilinear 리사이즈."""
        sw, sh = self.width, self.height
        if (dw, dh) == (sw, sh):
            return self.copy()
        out = bytearray(dw * dh * 3)
        src = self.buf
        fx_scale = (sw - 1) / (dw - 1) if dw > 1 else 0.0
        fy_scale = (sh - 1) / (dh - 1) if dh > 1 else 0.0
        xs = []
        for x in range(dw):
            fx = x * fx_scale
            x0 = min(int(fx), max(0, sw - 2))
            xs.append((x0, fx - x0))
        for y in range(dh):
            fy = y * fy_scale
            y0 = min(int(fy), max(0, sh - 2))
            ty = fy - y0
            r0 = y0 * sw
            r1 = r0 + (sw if sh > 1 else 0)
            base = y * dw
            for i in range(dw):
                x0, tx = xs[i]
                x1 = x0 + 1 if sw > 1 else x0
                oa = (r0 + x0) * 3
                ob = (r0 + x1) * 3
                oc = (r1 + x0) * 3
                od = (r1 + x1) * 3
                o = (base + i) * 3
                for c in range(3):
                    top = src[oa + c] + (src[ob + c] - src[oa + c]) * tx
                    bot = src[oc + c] + (src[od + c] - src[oc + c]) * tx
                    out[o + c] = _clamp8(top + (bot - top) * ty)
        return Canvas(dw, dh, buf=out)

    def adjust(self, brightness: float = 0.0, contrast: float = 1.0,
               saturation: float = 1.0) -> None:
        """밝기/대비/채도 보정 (LUT 없이 픽셀 단위)."""
        buf = self.buf
        add = brightness * 255.0
        for i in range(self.width * self.height):
            o = i * 3
            r = buf[o] + add
            g = buf[o + 1] + add
            b = buf[o + 2] + add
            r = (r - 128.0) * contrast + 128.0
            g = (g - 128.0) * contrast + 128.0
            b = (b - 128.0) * contrast + 128.0
            if saturation != 1.0:
                lum = r * 0.299 + g * 0.587 + b * 0.114
                r = lum + (r - lum) * saturation
                g = lum + (g - lum) * saturation
                b = lum + (b - lum) * saturation
            buf[o] = _clamp8(r)
            buf[o + 1] = _clamp8(g)
            buf[o + 2] = _clamp8(b)

    def vignette(self, strength: float = 0.35) -> None:
        if strength <= 0:
            return
        w, h = self.width, self.height
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        maxd = math.sqrt(cx * cx + cy * cy)
        buf = self.buf
        for y in range(h):
            dy = (y - cy) / maxd
            base = y * w
            for x in range(w):
                dx = (x - cx) / maxd
                d = math.sqrt(dx * dx + dy * dy)
                k = 1.0 - strength * (d ** 2.2)
                o = (base + x) * 3
                buf[o] = _clamp8(buf[o] * k)
                buf[o + 1] = _clamp8(buf[o + 1] * k)
                buf[o + 2] = _clamp8(buf[o + 2] * k)

    def grain(self, rng, amount: float = 0.03) -> None:
        """필름 그레인. 큰 이미지에서도 부담 없게 정수 연산만 쓴다."""
        if amount <= 0:
            return
        buf = self.buf
        span = int(amount * 255)
        if span <= 0:
            return
        for i in range(self.width * self.height):
            n = rng.randint(-span, span)
            o = i * 3
            buf[o] = _clamp8(buf[o] + n)
            buf[o + 1] = _clamp8(buf[o + 1] + n)
            buf[o + 2] = _clamp8(buf[o + 2] + n)
