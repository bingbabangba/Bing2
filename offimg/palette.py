"""색 공간 변환, 팔레트 생성, 그리고 프롬프트 해석용 키워드 사전.

프롬프트에 들어 있는 단어(한국어/영어)를 보고 색감·분위기·구도를 정한다.
인터넷도 모델 가중치도 필요 없는 순수 규칙 기반이다.
"""

from __future__ import annotations

import math

from .noise import Rng, seed_from_text

__all__ = ["Palette", "hsv_to_rgb", "rgb_to_hsv", "analyze", "PromptSpec",
           "LEXICON", "STYLES"]

STYLES = ("landscape", "nebula", "abstract", "mosaic", "mandala", "waves")


# --------------------------------------------------------------------------
# 색 변환
# --------------------------------------------------------------------------
def hsv_to_rgb(h: float, s: float, v: float):
    """h: 0~360, s/v: 0~1 -> (r, g, b) 0~255 정수."""
    h = h % 360.0
    s = 0.0 if s < 0 else 1.0 if s > 1 else s
    v = 0.0 if v < 0 else 1.0 if v > 1 else v
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0.0
    elif h < 120:
        r, g, b = x, c, 0.0
    elif h < 180:
        r, g, b = 0.0, c, x
    elif h < 240:
        r, g, b = 0.0, x, c
    elif h < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (int((r + m) * 255 + 0.5), int((g + m) * 255 + 0.5), int((b + m) * 255 + 0.5))


def rgb_to_hsv(r: int, g: int, b: int):
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx = max(rf, gf, bf)
    mn = min(rf, gf, bf)
    d = mx - mn
    if d < 1e-9:
        h = 0.0
    elif mx == rf:
        h = (60 * ((gf - bf) / d)) % 360
    elif mx == gf:
        h = 60 * ((bf - rf) / d) + 120
    else:
        h = 60 * ((rf - gf) / d) + 240
    s = 0.0 if mx < 1e-9 else d / mx
    return h % 360.0, s, mx


# --------------------------------------------------------------------------
# 팔레트
# --------------------------------------------------------------------------
class Palette:
    """등간격 색 스톱을 선형 보간하는 팔레트."""

    __slots__ = ("colors",)

    def __init__(self, colors):
        self.colors = [tuple(int(c) for c in col) for col in colors]
        if len(self.colors) < 2:
            self.colors = self.colors * 2

    def sample(self, t: float):
        if t <= 0.0:
            return self.colors[0]
        if t >= 1.0:
            return self.colors[-1]
        n = len(self.colors) - 1
        f = t * n
        i = int(f)
        k = f - i
        a = self.colors[i]
        b = self.colors[i + 1]
        return (
            int(a[0] + (b[0] - a[0]) * k),
            int(a[1] + (b[1] - a[1]) * k),
            int(a[2] + (b[2] - a[2]) * k),
        )

    def reversed(self) -> "Palette":
        return Palette(list(reversed(self.colors)))

    def darkened(self, k: float) -> "Palette":
        return Palette([(int(r * k), int(g * k), int(b * k)) for r, g, b in self.colors])

    def mixed(self, color, k: float) -> "Palette":
        cr, cg, cb = color
        return Palette([
            (int(r + (cr - r) * k), int(g + (cg - g) * k), int(b + (cb - b) * k))
            for r, g, b in self.colors
        ])

    def __len__(self):
        return len(self.colors)

    def __repr__(self):
        return "Palette(%s)" % ", ".join("#%02x%02x%02x" % c for c in self.colors)


# --------------------------------------------------------------------------
# 키워드 사전
# --------------------------------------------------------------------------
# 각 항목: (키워드들, {hue, sat, val, style, tags})
LEXICON = [
    (("바다", "해변", "파도", "ocean", "sea", "beach", "wave", "물결"),
     {"hue": 200, "sat": 0.62, "val": 0.85, "style": "waves", "tags": {"water", "horizon"}}),
    (("호수", "강", "lake", "river", "물"),
     {"hue": 190, "sat": 0.5, "val": 0.8, "style": "landscape", "tags": {"water", "horizon"}}),
    (("하늘", "구름", "sky", "cloud"),
     {"hue": 205, "sat": 0.45, "val": 0.95, "style": "landscape", "tags": {"cloud", "horizon"}}),
    (("노을", "석양", "일몰", "sunset", "dusk", "황혼"),
     {"hue": 20, "sat": 0.8, "val": 0.95, "style": "landscape", "tags": {"sun", "cloud", "horizon", "warm"}}),
    (("일출", "새벽", "아침", "sunrise", "dawn", "morning"),
     {"hue": 35, "sat": 0.6, "val": 0.95, "style": "landscape", "tags": {"sun", "horizon", "warm"}}),
    (("밤", "야경", "달", "night", "moon", "midnight"),
     {"hue": 230, "sat": 0.6, "val": 0.35, "style": "landscape", "tags": {"night", "star", "moon", "horizon"}}),
    (("별", "은하", "우주", "성운", "star", "galaxy", "space", "nebula", "cosmos"),
     {"hue": 265, "sat": 0.7, "val": 0.5, "style": "nebula", "tags": {"night", "star"}}),
    (("산", "봉우리", "능선", "mountain", "peak", "alps", "설산"),
     {"hue": 210, "sat": 0.35, "val": 0.7, "style": "landscape", "tags": {"mountain", "horizon"}}),
    (("숲", "나무", "정글", "forest", "tree", "jungle", "woods", "초록"),
     {"hue": 130, "sat": 0.55, "val": 0.6, "style": "landscape", "tags": {"mountain", "horizon"}}),
    (("들판", "초원", "언덕", "field", "meadow", "hill", "grass"),
     {"hue": 100, "sat": 0.5, "val": 0.75, "style": "landscape", "tags": {"horizon", "sun"}}),
    (("사막", "모래", "desert", "sand", "dune"),
     {"hue": 35, "sat": 0.55, "val": 0.85, "style": "landscape", "tags": {"sun", "horizon", "warm"}}),
    (("겨울", "눈", "얼음", "winter", "snow", "ice", "frost"),
     {"hue": 200, "sat": 0.22, "val": 0.95, "style": "landscape", "tags": {"snow", "horizon", "cold"}}),
    (("봄", "벚꽃", "꽃", "spring", "blossom", "flower", "sakura"),
     {"hue": 335, "sat": 0.42, "val": 0.95, "style": "abstract", "tags": {"pastel"}}),
    (("가을", "단풍", "autumn", "fall", "maple"),
     {"hue": 25, "sat": 0.7, "val": 0.8, "style": "landscape", "tags": {"warm", "horizon"}}),
    (("불", "화염", "용암", "fire", "flame", "lava", "ember"),
     {"hue": 12, "sat": 0.9, "val": 0.95, "style": "abstract", "tags": {"warm", "glow"}}),
    (("네온", "사이버", "미래", "neon", "cyber", "synthwave", "retrowave"),
     {"hue": 300, "sat": 0.95, "val": 0.95, "style": None, "tags": {"night", "neon", "glow"}}),
    (("파스텔", "부드러운", "pastel", "soft", "dreamy", "몽환"),
     {"hue": 320, "sat": 0.3, "val": 0.97, "style": None, "tags": {"pastel"}}),
    (("흑백", "모노", "무채색", "mono", "monochrome", "grayscale", "noir"),
     {"hue": 220, "sat": 0.0, "val": 0.85, "style": None, "tags": {"mono"}}),
    (("기하", "패턴", "타일", "모자이크", "geometric", "pattern", "tile", "mosaic"),
     {"hue": 210, "sat": 0.7, "val": 0.9, "style": "mosaic", "tags": {"geometric"}}),
    (("만다라", "대칭", "문양", "mandala", "symmetry", "kaleidoscope", "만화경"),
     {"hue": 280, "sat": 0.75, "val": 0.9, "style": "mandala", "tags": {"geometric"}}),
    (("추상", "abstract", "flow", "유체", "마블", "marble"),
     {"hue": 250, "sat": 0.6, "val": 0.85, "style": "abstract", "tags": set()}),
    (("빈티지", "레트로", "필름", "vintage", "retro", "film", "sepia"),
     {"hue": 30, "sat": 0.35, "val": 0.8, "style": None, "tags": {"grain", "warm"}}),
    (("비", "폭풍", "안개", "rain", "storm", "fog", "mist", "흐린"),
     {"hue": 215, "sat": 0.2, "val": 0.6, "style": "landscape", "tags": {"cloud", "horizon", "cold"}}),
    (("도시", "빌딩", "야경", "city", "urban", "skyline", "building"),
     {"hue": 220, "sat": 0.55, "val": 0.6, "style": "landscape", "tags": {"night", "mountain", "horizon", "glow"}}),
]

# 태그 -> 팔레트 후처리 힌트
_DARK_TAGS = {"night"}


class PromptSpec:
    """프롬프트 해석 결과."""

    __slots__ = ("prompt", "seed", "style", "tags", "palette", "hue", "sat", "val",
                 "matched")

    def __init__(self, prompt, seed, style, tags, palette, hue, sat, val, matched):
        self.prompt = prompt
        self.seed = seed
        self.style = style
        self.tags = tags
        self.palette = palette
        self.hue = hue
        self.sat = sat
        self.val = val
        self.matched = matched

    def __repr__(self):
        return (f"PromptSpec(style={self.style!r}, hue={self.hue:.0f}, "
                f"tags={sorted(self.tags)}, matched={self.matched}, "
                f"palette={self.palette})")


def _build_palette(rng: Rng, hue: float, sat: float, val: float, tags) -> Palette:
    """기준 색상에서 5스톱 팔레트를 만든다(어두운 -> 밝은)."""
    if "mono" in tags:
        stops = []
        for i in range(5):
            v = 0.08 + 0.88 * (i / 4.0) ** 0.9
            stops.append(hsv_to_rgb(hue, 0.02, v))
        return Palette(stops)

    scheme = rng.choice(("analogous", "analogous", "complementary", "triad", "split"))
    if scheme == "analogous":
        spread = rng.uniform(18, 55)
        hues = [hue - spread, hue - spread * 0.4, hue, hue + spread * 0.6, hue + spread]
    elif scheme == "complementary":
        hues = [hue, hue + 12, hue + 30, hue + 150, hue + 180]
    elif scheme == "triad":
        hues = [hue, hue + 40, hue + 120, hue + 200, hue + 240]
    else:  # split complementary
        hues = [hue, hue - 25, hue + 150, hue + 175, hue + 200]

    pastel = "pastel" in tags
    night = "night" in tags
    neon = "neon" in tags

    stops = []
    n = len(hues) - 1
    for i, h in enumerate(hues):
        t = i / n
        if pastel:
            s = sat * (0.5 + 0.35 * t)
            v = 0.72 + 0.26 * t
        elif night:
            s = min(1.0, sat * (1.15 - 0.45 * t))
            v = 0.06 + 0.80 * t ** 1.5
        elif neon:
            s = min(1.0, sat * (0.85 + 0.25 * t))
            v = 0.15 + 0.85 * t ** 1.2
        else:
            s = min(1.0, sat * (1.1 - 0.35 * t))
            v = (0.18 + 0.80 * t) * val + 0.02
        s = max(0.0, min(1.0, s + rng.uniform(-0.05, 0.05)))
        v = max(0.0, min(1.0, v + rng.uniform(-0.03, 0.03)))
        stops.append(hsv_to_rgb(h + rng.uniform(-6, 6), s, v))
    return Palette(stops)


def _infer_style(styles, tags, rng: Rng) -> str:
    """키워드가 지목한 구도 -> 태그 기반 추론 -> 무작위 순으로 스타일을 고른다."""
    if styles:
        return styles[0]
    if "geometric" in tags:
        return "mandala" if rng.chance(0.4) else "mosaic"
    if "star" in tags or "night" in tags:
        return "nebula"
    if "water" in tags:
        return "waves"
    if "horizon" in tags or "mountain" in tags:
        return "landscape"
    if "pastel" in tags or "mono" in tags:
        return "abstract"
    return rng.choice(STYLES)


def analyze(prompt: str, seed=None, style=None) -> PromptSpec:
    """프롬프트 문자열을 해석해 시드/스타일/태그/팔레트를 정한다."""
    text = (prompt or "").lower()
    if seed is None:
        seed = seed_from_text(prompt or "offimg")
    rng = Rng(seed)

    hues, sats, vals, styles = [], [], [], []
    tags = set()
    matched = []
    for words, attrs in LEXICON:
        for w in words:
            if w in text:
                hues.append(attrs["hue"])
                sats.append(attrs["sat"])
                vals.append(attrs["val"])
                if attrs.get("style"):
                    styles.append(attrs["style"])
                tags |= set(attrs.get("tags", ()))
                matched.append(w)
                break

    if hues:
        # 여러 키워드가 걸리면 원형 평균으로 색상을 합친다.
        sx = sum(math.cos(math.radians(h)) for h in hues)
        sy = sum(math.sin(math.radians(h)) for h in hues)
        hue = math.degrees(math.atan2(sy, sx)) % 360.0
        sat = sum(sats) / len(sats)
        val = sum(vals) / len(vals)
    else:
        hue = rng.uniform(0, 360)
        sat = rng.uniform(0.45, 0.8)
        val = rng.uniform(0.7, 0.95)

    if style in (None, "auto"):
        style = _infer_style(styles, tags, rng)
    if style not in STYLES:
        raise ValueError(f"unknown style: {style} (choose from {', '.join(STYLES)})")

    palette = _build_palette(rng, hue, sat, val, tags)
    return PromptSpec(prompt or "", seed, style, tags, palette, hue, sat, val, matched)
