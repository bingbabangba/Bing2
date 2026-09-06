"""offimg - 인터넷 없이 동작하는 이미지 생성기.

* 텍스트 -> 이미지 : ``offimg.generate("밤바다 위의 보름달")``
* 이미지 -> 이미지 : ``offimg.transform("photo.png", "네온 느낌", effect="dream")``

표준 라이브러리만으로 동작하며, 모델 가중치도 네트워크도 필요 없다.
"""

from __future__ import annotations

from .palette import analyze, PromptSpec, STYLES
from .raster import Canvas
from .compose import render
from .stylize import apply_effect, EFFECTS, pick_effect
from .imageio import load, save

__version__ = "1.0.0"
__all__ = [
    "generate", "transform", "analyze", "render", "apply_effect",
    "Canvas", "PromptSpec", "STYLES", "EFFECTS", "pick_effect", "load", "save",
    "__version__",
]


def generate(prompt: str, width: int = 768, height: int = None,
             style: str = "auto", seed: int = None) -> Canvas:
    """텍스트 프롬프트로 이미지를 만든다."""
    height = width if height is None else height
    spec = analyze(prompt, seed=seed, style=style)
    return render(spec, width, height)


def transform(source, prompt: str = "", effect: str = "auto",
              strength: float = 0.7, seed: int = None,
              width: int = None, height: int = None) -> Canvas:
    """원본 이미지(경로 또는 Canvas)를 프롬프트 방향으로 다시 그린다."""
    canvas = source if isinstance(source, Canvas) else load(source)
    if width or height:
        w = width or canvas.width
        h = height or int(canvas.height * w / canvas.width)
        canvas = canvas.resized(w, h)
    spec = analyze(prompt, seed=seed)
    return apply_effect(canvas, spec, effect, strength)
