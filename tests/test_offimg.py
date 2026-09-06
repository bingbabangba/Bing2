"""offimg 단위 테스트 (표준 라이브러리만 사용).

실행: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import offimg
from offimg import png, cli
from offimg.compose import render
from offimg.noise import Rng, fbm, seed_from_text
from offimg.palette import analyze, hsv_to_rgb, rgb_to_hsv, Palette, STYLES
from offimg.raster import Canvas
from offimg.stylize import EFFECTS, apply_effect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_png(width, height, color_type, raw_rows, palette=b""):
    """테스트용 PNG 를 필터 0 으로 직접 만든다."""
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(row) for row in raw_rows)
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", palette)
    out += chunk(b"IDAT", zlib.compress(raw))
    out += chunk(b"IEND", b"")
    return out


class TestPng(unittest.TestCase):
    def test_rgb_roundtrip(self):
        w, h = 7, 5
        rgb = bytes((x * 3 + y) % 256 for y in range(h) for x in range(w * 3))
        w2, h2, back = png.decode(png.encode(w, h, rgb))
        self.assertEqual((w, h), (w2, h2))
        self.assertEqual(bytes(back), rgb)

    def test_decode_grayscale(self):
        blob = _make_png(2, 2, 0, [[10, 20], [30, 40]])
        w, h, rgb = png.decode(blob)
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(tuple(rgb[:3]), (10, 10, 10))
        self.assertEqual(tuple(rgb[9:12]), (40, 40, 40))

    def test_decode_palette(self):
        blob = _make_png(2, 1, 3, [[0, 1]], palette=bytes([255, 0, 0, 0, 0, 255]))
        _, _, rgb = png.decode(blob)
        self.assertEqual(tuple(rgb), (255, 0, 0, 0, 0, 255))

    def test_decode_rgba_composites_on_white(self):
        blob = _make_png(1, 1, 6, [[0, 0, 0, 0]])  # 완전 투명 -> 흰색
        _, _, rgb = png.decode(blob)
        self.assertEqual(tuple(rgb), (255, 255, 255))

    def test_rejects_garbage(self):
        with self.assertRaises(png.PngError):
            png.decode(b"not a png at all")

    def test_size_mismatch(self):
        with self.assertRaises(ValueError):
            png.encode(2, 2, b"\x00")


class TestNoise(unittest.TestCase):
    def test_rng_is_deterministic(self):
        a = [Rng(1234).random() for _ in range(3)]
        b = [Rng(1234).random() for _ in range(3)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, [Rng(9999).random() for _ in range(3)])

    def test_rng_ranges(self):
        rng = Rng(7)
        for _ in range(500):
            self.assertTrue(0.0 <= rng.random() < 1.0)
            self.assertIn(rng.randint(0, 3), (0, 1, 2, 3))

    def test_seed_from_text_stable(self):
        self.assertEqual(seed_from_text("바다"), seed_from_text("바다"))
        self.assertNotEqual(seed_from_text("바다"), seed_from_text("산"))

    def test_fbm_range_and_size(self):
        f = fbm(16, 12, 42, octaves=4, cells=2)
        self.assertEqual(len(f), 16 * 12)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in f))


class TestColor(unittest.TestCase):
    def test_hsv_roundtrip(self):
        for h, s, v in ((0, 1, 1), (120, 0.5, 0.8), (240, 0.25, 0.4), (300, 1, 0.6)):
            r, g, b = hsv_to_rgb(h, s, v)
            h2, s2, v2 = rgb_to_hsv(r, g, b)
            self.assertAlmostEqual(h, h2, delta=2.0)
            self.assertAlmostEqual(s, s2, delta=0.03)
            self.assertAlmostEqual(v, v2, delta=0.03)

    def test_palette_sampling(self):
        pal = Palette([(0, 0, 0), (255, 255, 255)])
        self.assertEqual(pal.sample(0.0), (0, 0, 0))
        self.assertEqual(pal.sample(1.0), (255, 255, 255))
        self.assertEqual(pal.sample(0.5), (127, 127, 127))
        self.assertEqual(pal.sample(-5), (0, 0, 0))
        self.assertEqual(pal.sample(5), (255, 255, 255))


class TestPromptAnalysis(unittest.TestCase):
    def test_keyword_style(self):
        self.assertEqual(analyze("깊은 우주 성운").style, "nebula")
        self.assertEqual(analyze("겨울 설산").style, "landscape")
        self.assertEqual(analyze("만다라 문양").style, "mandala")

    def test_tags(self):
        spec = analyze("밤바다 위의 보름달")
        self.assertIn("night", spec.tags)
        self.assertIn("water", spec.tags)

    def test_english_keywords(self):
        self.assertIn("snow", analyze("winter snow mountain").tags)

    def test_unknown_prompt_still_works(self):
        spec = analyze("zzz 알 수 없는 말")
        self.assertIn(spec.style, STYLES)
        self.assertGreaterEqual(len(spec.palette), 2)

    def test_explicit_style_overrides(self):
        self.assertEqual(analyze("바다", style="mosaic").style, "mosaic")

    def test_bad_style_raises(self):
        with self.assertRaises(ValueError):
            analyze("바다", style="nope")


class TestRaster(unittest.TestCase):
    def test_resize_keeps_solid_color(self):
        c = Canvas(8, 8, (10, 20, 30))
        r = c.resized(16, 4)
        self.assertEqual((r.width, r.height), (16, 4))
        self.assertEqual(r.get(0, 0), (10, 20, 30))
        self.assertEqual(r.get(15, 3), (10, 20, 30))

    def test_blur_keeps_solid_color(self):
        c = Canvas(12, 12, (200, 100, 50))
        self.assertEqual(c.blur(3).get(6, 6), (200, 100, 50))

    def test_put_is_clipped(self):
        c = Canvas(4, 4)
        c.put(-1, -1, 255, 255, 255)
        c.put(99, 99, 255, 255, 255)
        self.assertEqual(sum(c.buf), 0)

    def test_luminance_range(self):
        c = Canvas(4, 4, (255, 255, 255))
        self.assertAlmostEqual(max(c.luminance()), 1.0, places=2)

    def test_save_load_roundtrip(self):
        c = offimg.generate("노을", 32)
        with tempfile.TemporaryDirectory() as tmp:
            path = c.save(os.path.join(tmp, "a.png"))
            back = Canvas.load(path)
        self.assertEqual(bytes(back.buf), bytes(c.buf))


class TestGenerate(unittest.TestCase):
    def test_all_styles_render(self):
        for style in STYLES:
            with self.subTest(style=style):
                c = offimg.generate("테스트", 64, style=style, seed=5)
                self.assertEqual((c.width, c.height), (64, 64))
                self.assertGreater(len(set(bytes(c.buf))), 3)  # 단색이 아님

    def test_non_square(self):
        c = offimg.generate("바다", 96, 48, seed=1)
        self.assertEqual((c.width, c.height), (96, 48))

    def test_same_seed_same_image(self):
        a = offimg.generate("밤하늘", 64, seed=99)
        b = offimg.generate("밤하늘", 64, seed=99)
        self.assertEqual(bytes(a.buf), bytes(b.buf))

    def test_different_seed_different_image(self):
        a = offimg.generate("밤하늘", 64, seed=1)
        b = offimg.generate("밤하늘", 64, seed=2)
        self.assertNotEqual(bytes(a.buf), bytes(b.buf))

    def test_prompt_changes_image(self):
        a = offimg.generate("불타는 사막", 64)
        b = offimg.generate("겨울 눈 덮인 산", 64)
        self.assertNotEqual(bytes(a.buf), bytes(b.buf))


class TestTransform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = offimg.generate("겨울 산", 64, seed=3)

    def test_all_effects(self):
        for fx in EFFECTS:
            with self.subTest(effect=fx):
                out = apply_effect(self.src, analyze("네온", seed=4), fx, 0.6)
                self.assertEqual((out.width, out.height), (64, 64))
                self.assertGreater(len(set(bytes(out.buf))), 3)

    def test_auto_effect(self):
        out = apply_effect(self.src, analyze("흑백 사진", seed=4), "auto", 0.6)
        self.assertEqual((out.width, out.height), (64, 64))

    def test_mono_prompt_is_grayscale(self):
        out = apply_effect(self.src, analyze("흑백 모노", seed=4), "duotone", 1.0)
        for i in range(0, len(out.buf), 3):
            self.assertEqual(out.buf[i], out.buf[i + 1])
            self.assertEqual(out.buf[i + 1], out.buf[i + 2])

    def test_zero_strength_is_close_to_source(self):
        out = apply_effect(self.src, analyze("파랑", seed=4), "duotone", 0.0)
        diff = sum(abs(a - b) for a, b in zip(out.buf, self.src.buf)) / len(out.buf)
        self.assertLess(diff, 90)

    def test_unknown_effect_raises(self):
        with self.assertRaises(ValueError):
            apply_effect(self.src, analyze("x"), "nope", 0.5)

    def test_transform_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.src.save(os.path.join(tmp, "src.png"))
            out = offimg.transform(path, "네온", effect="dream", strength=0.5)
        self.assertEqual((out.width, out.height), (64, 64))


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "offimg", *args],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_size_parsing(self):
        self.assertEqual(cli._parse_size("512"), (512, 512))
        self.assertEqual(cli._parse_size("640x480"), (640, 480))
        with self.assertRaises(Exception):
            cli._parse_size("abc")
        with self.assertRaises(Exception):
            cli._parse_size("8")

    def test_text_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.png")
            r = self._run("text", "밤바다", "--size", "64", "-o", out, "--seed", "1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(out))
            self.assertEqual(png.read(out)[0], 64)

    def test_count_creates_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.png")
            r = self._run("text", "숲", "--size", "48", "-o", out, "-n", "3", "-q")
            self.assertEqual(r.returncode, 0, r.stderr)
            for i in (1, 2, 3):
                self.assertTrue(os.path.exists(os.path.join(tmp, f"a-{i}.png")))

    def test_image_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = offimg.generate("숲", 64, seed=2).save(os.path.join(tmp, "s.png"))
            out = os.path.join(tmp, "o.png")
            r = self._run("image", src, "-p", "빈티지", "-e", "sketch",
                          "-s", "0.5", "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(out))

    def test_info_command(self):
        r = self._run("info")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("offimg", r.stdout)

    def test_missing_source_reports_error(self):
        r = self._run("image", "/definitely/missing.png")
        self.assertEqual(r.returncode, 1)
        self.assertIn("오류", r.stderr)

    def test_bad_strength_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = offimg.generate("숲", 32, seed=2).save(os.path.join(tmp, "s.png"))
            r = self._run("image", src, "-s", "5")
            self.assertNotEqual(r.returncode, 0)

    def test_diffusion_backend_without_torch_is_graceful(self):
        r = self._run("text", "x", "--size", "64", "--backend", "diffusion")
        self.assertEqual(r.returncode, 1)
        self.assertIn("torch", r.stderr)


if __name__ == "__main__":
    unittest.main()
