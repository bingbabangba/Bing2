"""이미지 입출력 도우미.

기본은 내장 순수 파이썬 PNG 코덱(의존성 0). Pillow 가 설치돼 있으면
JPEG/BMP/WEBP 같은 다른 형식도 자동으로 읽고 쓴다.
"""

from __future__ import annotations

import os

from .raster import Canvas

__all__ = ["load", "save", "have_pillow"]


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def load(path: str) -> Canvas:
    """이미지 파일 -> Canvas."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".png":
        try:
            return Canvas.load(path)
        except Exception:
            if not have_pillow():
                raise
    if have_pillow():
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            return Canvas(im.width, im.height, buf=bytearray(im.tobytes()))
    raise ValueError(
        f"'{ext or path}' 형식은 기본 코덱으로 읽을 수 없습니다. "
        "PNG 로 변환하거나 Pillow 를 설치하세요 (오프라인이면 PNG 권장)."
    )


def save(canvas: Canvas, path: str) -> str:
    """Canvas -> 이미지 파일. PNG 는 항상 가능, 그 외는 Pillow 필요."""
    ext = os.path.splitext(path)[1].lower()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if ext in ("", ".png"):
        if ext == "":
            path += ".png"
        return canvas.save(path)
    if have_pillow():
        from PIL import Image
        im = Image.frombytes("RGB", (canvas.width, canvas.height), bytes(canvas.buf))
        im.save(path)
        return path
    raise ValueError(f"'{ext}' 저장은 Pillow 가 필요합니다. .png 로 저장하세요.")
