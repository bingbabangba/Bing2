"""Template matching at a fixed reference resolution so templates work at any window size."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

REF_W = 1920


def normalize(img: np.ndarray) -> tuple[np.ndarray, float]:
    """Resizes to REF_W wide grayscale; returns (image, scale from original to ref)."""
    h, w = img.shape[:2]
    s = REF_W / w
    if abs(s - 1) > 1e-3:
        img = cv2.resize(img, (REF_W, round(h * s)), interpolation=cv2.INTER_AREA)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img, s


@dataclass
class Match:
    x: int          # center in original screenshot pixels
    y: int
    score: float


class Templates:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._cache: dict[str, np.ndarray] = {}

    def get(self, name: str) -> np.ndarray:
        if name not in self._cache:
            path = self.folder / f"{name}.png"
            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"template {path} missing - capture it with: python -m hsr_reroll capture {name}")
            self._cache[name] = img
        return self._cache[name]

    def save(self, name: str, img: np.ndarray):
        self.folder.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", img)
        buf.tofile(str(self.folder / f"{name}.png"))
        self._cache.pop(name, None)


def find(screen_bgr: np.ndarray, templ: np.ndarray, threshold: float = 0.85) -> Match | None:
    gray, s = normalize(screen_bgr)
    if templ.shape[0] > gray.shape[0] or templ.shape[1] > gray.shape[1]:
        return None
    res = cv2.matchTemplate(gray, templ, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(res)
    if best < threshold:
        return None
    cx = (loc[0] + templ.shape[1] / 2) / s
    cy = (loc[1] + templ.shape[0] / 2) / s
    return Match(int(cx), int(cy), float(best))


def crop_to_template(screen_bgr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """roi in original pixels -> grayscale template in reference scale."""
    gray, s = normalize(screen_bgr)
    x, y, w, h = (round(v * s) for v in roi)
    return gray[y:y + h, x:x + w].copy()
