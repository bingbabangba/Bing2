"""순수 파이썬 PNG 인코더 / 디코더.

표준 라이브러리(zlib, struct)만 사용한다. Pillow 같은 외부 의존성이 없어도
이미지 파일을 읽고 쓸 수 있게 하기 위한 모듈이다.

내부 픽셀 표현은 항상 8bit RGB 로 정규화한 ``bytearray`` (길이 = w * h * 3).
"""

from __future__ import annotations

import struct
import zlib

__all__ = ["encode", "write", "decode", "read", "PngError"]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    """PNG 파일이 손상되었거나 지원하지 않는 형식일 때."""


# --------------------------------------------------------------------------
# 인코딩
# --------------------------------------------------------------------------
def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """8bit RGB 바이트열을 PNG 바이트열로 인코딩한다."""
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be positive")
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"rgb buffer size mismatch: {len(rgb)} != {expected}")

    stride = width * 3
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = bytes(rgb[y * stride:(y + 1) * stride])
        # Up 필터: 위쪽 줄과의 차분. 그라데이션이 많은 생성 이미지에서 잘 줄어든다.
        filtered = bytes((line[i] - prev[i]) & 0xFF for i in range(stride))
        raw.append(2)  # filter type 2 = Up
        raw += filtered
        prev = line

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + _chunk(b"IEND", b"")
    )


def write(path: str, width: int, height: int, rgb: bytes, level: int = 6) -> str:
    """PNG 파일로 저장하고 경로를 돌려준다."""
    with open(path, "wb") as fp:
        fp.write(encode(width, height, rgb, level))
    return path


# --------------------------------------------------------------------------
# 디코딩
# --------------------------------------------------------------------------
def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data: bytes, height: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        else:
            raise PngError(f"unknown filter type {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def decode(blob: bytes):
    """PNG 바이트열 -> (width, height, bytearray RGB).

    지원: bit depth 8/16, color type 0/2/3/4/6, 비인터레이스.
    알파 채널은 흰 배경에 합성해서 제거한다.
    """
    if not blob.startswith(_SIGNATURE):
        raise PngError("not a PNG file")

    pos = len(_SIGNATURE)
    width = height = depth = ctype = 0
    palette = b""
    idat = bytearray()
    seen_ihdr = False

    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        payload = blob[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + tag + payload + crc

        if tag == b"IHDR":
            width, height, depth, ctype, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if interlace:
                raise PngError("interlaced PNG is not supported")
            if depth not in (8, 16):
                raise PngError(f"unsupported bit depth {depth}")
            if ctype not in (0, 2, 3, 4, 6):
                raise PngError(f"unsupported color type {ctype}")
            if ctype == 3 and depth != 8:
                raise PngError("palette PNG must be 8bit")
            seen_ihdr = True
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break

    if not seen_ihdr:
        raise PngError("missing IHDR")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    sample = depth // 8
    bpp = channels * sample
    stride = width * bpp
    raw = _unfilter(zlib.decompress(bytes(idat)), height, stride, bpp)

    rgb = bytearray(width * height * 3)
    step = sample  # 16bit 는 상위 바이트만 사용
    for y in range(height):
        src = y * stride
        dst = y * width * 3
        for x in range(width):
            base = src + x * bpp
            if ctype == 0:
                v = raw[base]
                r = g = b = v
            elif ctype == 2:
                r = raw[base]
                g = raw[base + step]
                b = raw[base + 2 * step]
            elif ctype == 3:
                idx = raw[base] * 3
                if idx + 2 >= len(palette):
                    r = g = b = 0
                else:
                    r, g, b = palette[idx], palette[idx + 1], palette[idx + 2]
            elif ctype == 4:
                v = raw[base]
                a = raw[base + step]
                r = g = b = (v * a + 255 * (255 - a)) // 255
            else:  # ctype == 6
                a = raw[base + 3 * step]
                r = (raw[base] * a + 255 * (255 - a)) // 255
                g = (raw[base + step] * a + 255 * (255 - a)) // 255
                b = (raw[base + 2 * step] * a + 255 * (255 - a)) // 255
            o = dst + x * 3
            rgb[o] = r
            rgb[o + 1] = g
            rgb[o + 2] = b
    return width, height, rgb


def read(path: str):
    """PNG 파일 -> (width, height, bytearray RGB)."""
    with open(path, "rb") as fp:
        return decode(fp.read())
