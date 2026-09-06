"""offimg 명령줄 인터페이스."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

from . import __version__
from .imageio import load, save, have_pillow
from .palette import analyze, STYLES, LEXICON
from .compose import render
from .stylize import apply_effect, EFFECTS, pick_effect

__all__ = ["main"]

_SIZE_RE = re.compile(r"^(\d+)(?:[xX*](\d+))?$")


def _parse_size(text: str):
    m = _SIZE_RE.match(text.strip())
    if not m:
        raise argparse.ArgumentTypeError("크기는 768 또는 1024x640 형식으로 주세요")
    w = int(m.group(1))
    h = int(m.group(2)) if m.group(2) else w
    if not (16 <= w <= 4096 and 16 <= h <= 4096):
        raise argparse.ArgumentTypeError("크기는 16~4096 사이여야 합니다")
    return w, h


def _slug(text: str, limit: int = 24) -> str:
    text = re.sub(r"\s+", "-", (text or "image").strip())
    text = re.sub(r"[^0-9A-Za-z가-힣_\-]", "", text)
    return (text[:limit] or "image")


def _out_path(base: str, prompt: str, seed: int, index: int, total: int) -> str:
    if base:
        root, ext = os.path.splitext(base)
        ext = ext or ".png"
        return f"{root}{ext}" if total == 1 else f"{root}-{index + 1}{ext}"
    name = f"{_slug(prompt)}-{seed & 0xFFFFFF:06x}"
    if total > 1:
        name += f"-{index + 1}"
    return os.path.join("output", name + ".png")


def _seed_for(args, index: int):
    if args.seed is None:
        return None
    return args.seed + index * 7919


# --------------------------------------------------------------------------
# 서브커맨드
# --------------------------------------------------------------------------
def _cmd_text(args) -> int:
    width, height = args.size
    total = args.count
    for i in range(total):
        started = time.time()
        seed = _seed_for(args, i)
        if args.backend == "diffusion":
            from .backends import diffusion
            canvas = diffusion.text_to_image(
                args.prompt, args.model, width, height, steps=args.steps,
                guidance=args.guidance, seed=seed, negative=args.negative)
            spec_note = "diffusion"
        else:
            spec = analyze(args.prompt, seed=seed, style=args.style)
            canvas = render(spec, width, height)
            seed = spec.seed
            spec_note = f"style={spec.style} seed={spec.seed & 0xFFFFFF:06x}"
            if spec.matched and not args.quiet:
                print(f"  인식한 키워드: {', '.join(spec.matched)}")
        path = save(canvas, _out_path(args.output, args.prompt,
                                      seed if seed is not None else 0, i, total))
        if not args.quiet:
            print(f"저장: {path}  ({width}x{height}, {spec_note}, "
                  f"{time.time() - started:.1f}s)")
    return 0


def _cmd_image(args) -> int:
    src = load(args.source)
    if args.size:
        w, h = args.size
        src = src.resized(w, h)
    elif args.max_side and max(src.width, src.height) > args.max_side:
        k = args.max_side / max(src.width, src.height)
        src = src.resized(max(1, int(src.width * k)), max(1, int(src.height * k)))

    total = args.count
    for i in range(total):
        started = time.time()
        seed = _seed_for(args, i)
        if args.backend == "diffusion":
            from .backends import diffusion
            canvas = diffusion.image_to_image(
                src, args.prompt, args.model, strength=args.strength,
                steps=args.steps, guidance=args.guidance, seed=seed,
                negative=args.negative)
            note = "diffusion"
        else:
            spec = analyze(args.prompt, seed=seed)
            effect = args.effect if args.effect != "auto" else pick_effect(spec)
            canvas = apply_effect(src, spec, effect, args.strength)
            seed = spec.seed
            note = f"effect={effect} strength={args.strength:g}"
        path = save(canvas, _out_path(args.output, args.prompt or
                                      os.path.basename(args.source),
                                      seed if seed is not None else 0, i, total))
        if not args.quiet:
            print(f"저장: {path}  ({canvas.width}x{canvas.height}, {note}, "
                  f"{time.time() - started:.1f}s)")
    return 0


def _cmd_info(args) -> int:
    from .backends import diffusion
    print(f"offimg {__version__} - 오프라인 이미지 생성기")
    print(f"  파이썬        : {sys.version.split()[0]}")
    print(f"  외부 의존성    : 없음 (표준 라이브러리만)")
    print(f"  Pillow        : {'있음 (JPEG 등 지원)' if have_pillow() else '없음 (PNG 만)'}")
    print(f"  diffusion 백엔드: {diffusion.available()[1]}")
    print()
    print("구도(style):", ", ".join(STYLES))
    print("효과(effect):", ", ".join(EFFECTS))
    print()
    print("인식하는 키워드:")
    for words, attrs in LEXICON:
        tags = ",".join(sorted(attrs.get("tags", ()))) or "-"
        print(f"  {' / '.join(words[:6]):<52} -> {attrs.get('style') or 'auto':<10} [{tags}]")
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="offimg",
        description="인터넷 연결 없이 텍스트/원본 이미지로 그림을 만드는 프로그램",
        epilog="예) python3 -m offimg text \"밤바다 위의 보름달\" --size 768",
    )
    parser.add_argument("--version", action="version", version=f"offimg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("-o", "--output", default="", help="저장 경로 (기본: output/…)")
        sp.add_argument("--seed", type=int, default=None,
                        help="시드 고정 (같은 시드 = 같은 결과)")
        sp.add_argument("-n", "--count", type=int, default=1, help="생성 장수")
        sp.add_argument("-q", "--quiet", action="store_true", help="로그 숨김")
        sp.add_argument("--backend", choices=("procedural", "diffusion"),
                        default="procedural",
                        help="procedural(기본, 의존성 0) 또는 diffusion(로컬 모델 필요)")
        sp.add_argument("--model", default=os.environ.get("OFFIMG_MODEL", ""),
                        help="diffusion 백엔드용 로컬 모델 폴더")
        sp.add_argument("--steps", type=int, default=25, help="diffusion 스텝 수")
        sp.add_argument("--guidance", type=float, default=7.5, help="diffusion CFG")
        sp.add_argument("--negative", default=None, help="diffusion 네거티브 프롬프트")

    p_text = sub.add_parser("text", help="텍스트 -> 이미지")
    p_text.add_argument("prompt", help="프롬프트 (한국어/영어)")
    p_text.add_argument("--size", type=_parse_size, default=(768, 768),
                        help="출력 크기: 768 또는 1024x640")
    p_text.add_argument("--style", choices=("auto",) + STYLES, default="auto",
                        help="구도 (기본: 프롬프트에서 자동 선택)")
    add_common(p_text)
    p_text.set_defaults(func=_cmd_text)

    p_img = sub.add_parser("image", help="원본 이미지 -> 이미지")
    p_img.add_argument("source", help="원본 이미지 경로 (PNG, Pillow 있으면 JPEG 등)")
    p_img.add_argument("-p", "--prompt", default="", help="색감/분위기 프롬프트")
    p_img.add_argument("-e", "--effect", choices=("auto",) + EFFECTS, default="auto",
                       help="효과 (기본: 프롬프트에서 자동 선택)")
    p_img.add_argument("-s", "--strength", type=float, default=0.7,
                       help="변형 강도 0~1 (기본 0.7)")
    p_img.add_argument("--size", type=_parse_size, default=None, help="출력 크기 강제")
    p_img.add_argument("--max-side", type=int, default=1024,
                       help="긴 변이 이 값을 넘으면 축소 (기본 1024, 0이면 원본 유지)")
    add_common(p_img)
    p_img.set_defaults(func=_cmd_image)

    p_info = sub.add_parser("info", help="사용 가능한 구도/효과/키워드 보기")
    p_info.set_defaults(func=_cmd_info)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "count", 1) < 1:
        parser.error("--count 는 1 이상이어야 합니다")
    if getattr(args, "strength", 0.5) < 0 or getattr(args, "strength", 0.5) > 1:
        parser.error("--strength 는 0~1 사이여야 합니다")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n중단했습니다.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `offimg info | head` 처럼 파이프가 먼저 닫히는 경우
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
