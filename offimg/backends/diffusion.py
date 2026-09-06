"""선택 백엔드: 로컬에 이미 받아둔 Stable Diffusion 가중치로 생성.

이 파일은 **완전 오프라인**을 전제로 한다. 허깅페이스 허브 접속을 강제로 끄고
(`local_files_only=True`), 인자로 받은 로컬 디렉터리에서만 모델을 읽는다.
torch/diffusers 가 없거나 모델 폴더가 없으면 친절한 오류만 내고 끝난다.

기본 백엔드(procedural)는 이 파일 없이도 동작하므로, 여기서 import 실패가
프로그램 전체를 막지 않도록 모든 무거운 import 는 함수 안에서 한다.
"""

from __future__ import annotations

import os

from ..raster import Canvas

__all__ = ["available", "text_to_image", "image_to_image"]


def _force_offline() -> None:
    """네트워크 접속을 시도하지 않도록 환경 변수를 고정한다."""
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "DIFFUSERS_OFFLINE"):
        os.environ[key] = "1"


def available():
    """(사용 가능 여부, 설명) 을 돌려준다."""
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except Exception as exc:
        return False, (
            "diffusion 백엔드에는 torch 와 diffusers 가 필요합니다 "
            f"({exc.__class__.__name__}). 미리 설치된 PC 에서만 쓸 수 있습니다."
        )
    return True, "diffusion 백엔드 사용 가능"


def _check_model(model_dir: str) -> str:
    if not model_dir:
        raise ValueError(
            "--model 로 로컬 모델 폴더를 지정하세요 "
            "(예: --model ~/models/stable-diffusion-v1-5). "
            "오프라인이므로 모델은 미리 내려받아 둬야 합니다."
        )
    model_dir = os.path.expanduser(model_dir)
    if not os.path.isdir(model_dir):
        raise FileNotFoundError(f"모델 폴더를 찾을 수 없습니다: {model_dir}")
    return model_dir


def _device_and_dtype():
    import torch
    if torch.cuda.is_available():
        return "cuda", torch.float16
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


def _to_canvas(image) -> Canvas:
    image = image.convert("RGB")
    return Canvas(image.width, image.height, buf=bytearray(image.tobytes()))


def _to_pil(canvas: Canvas):
    from PIL import Image
    return Image.frombytes("RGB", (canvas.width, canvas.height), bytes(canvas.buf))


def text_to_image(prompt: str, model_dir: str, width: int = 512, height: int = 512,
                  steps: int = 25, guidance: float = 7.5, seed: int = None,
                  negative: str = None) -> Canvas:
    """로컬 SD 모델로 텍스트 -> 이미지."""
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    _force_offline()
    model_dir = _check_model(model_dir)

    import torch
    from diffusers import StableDiffusionPipeline

    device, dtype = _device_and_dtype()
    pipe = StableDiffusionPipeline.from_pretrained(
        model_dir, torch_dtype=dtype, local_files_only=True, safety_checker=None
    ).to(device)
    generator = None
    if seed is not None:
        generator = torch.Generator(device=device).manual_seed(int(seed) & 0x7FFFFFFF)
    image = pipe(prompt, negative_prompt=negative, width=width, height=height,
                 num_inference_steps=steps, guidance_scale=guidance,
                 generator=generator).images[0]
    return _to_canvas(image)


def image_to_image(source: Canvas, prompt: str, model_dir: str,
                   strength: float = 0.6, steps: int = 25, guidance: float = 7.5,
                   seed: int = None, negative: str = None) -> Canvas:
    """로컬 SD 모델로 이미지 -> 이미지."""
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    _force_offline()
    model_dir = _check_model(model_dir)

    import torch
    from diffusers import StableDiffusionImg2ImgPipeline

    device, dtype = _device_and_dtype()
    pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
        model_dir, torch_dtype=dtype, local_files_only=True, safety_checker=None
    ).to(device)
    generator = None
    if seed is not None:
        generator = torch.Generator(device=device).manual_seed(int(seed) & 0x7FFFFFFF)
    image = pipe(prompt=prompt, image=_to_pil(source), strength=strength,
                 negative_prompt=negative, num_inference_steps=steps,
                 guidance_scale=guidance, generator=generator).images[0]
    return _to_canvas(image)
