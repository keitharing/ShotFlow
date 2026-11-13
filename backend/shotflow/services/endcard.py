"""End card still: product title over a dark tone sampled from the film's last shot."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import Settings, load_settings, writable_path

TITLE_FONTS = [r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf"]
SUB_FONTS = [r"C:\Windows\Fonts\malgun.ttf"]


def _font(candidates: list[str], size: int) -> ImageFont.ImageFont:
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_end_card(
    name: str,
    title: str,
    subtitle: str,
    tone_bgr: tuple[float, float, float],
    size: tuple[int, int] = (1920, 1080),
    settings: Settings | None = None,
) -> Path:
    settings = settings or load_settings()
    w, h = size
    tone = np.array(tone_bgr[::-1], dtype=np.float32)  # to RGB
    base = np.clip(tone * 0.35 + 8, 0, 60)
    # Soft radial glow keeps the card from reading as a flat, frozen frame.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    glow = np.exp(-(((xx - w / 2) / (w * 0.42)) ** 2 + ((yy - h * 0.46) / (h * 0.45)) ** 2))
    img = base[None, None, :] + glow[..., None] * (np.clip(tone * 0.55, 0, 90)[None, None, :])
    card = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))

    draw = ImageDraw.Draw(card)
    title_font = _font(TITLE_FONTS, int(h * 0.085))
    sub_font = _font(SUB_FONTS, int(h * 0.026))
    spaced = " ".join(title.upper())
    tw = draw.textlength(spaced, font=title_font)
    draw.text(((w - tw) / 2, h * 0.40), spaced, font=title_font, fill=(240, 236, 228))
    sw = draw.textlength(subtitle, font=sub_font)
    draw.text(((w - sw) / 2, h * 0.55), subtitle, font=sub_font, fill=(190, 186, 178))
    line_w = w * 0.06
    draw.line([(w / 2 - line_w / 2, h * 0.525), (w / 2 + line_w / 2, h * 0.525)], fill=(170, 160, 140), width=2)

    out = writable_path(settings.cache_dir / "endcards" / f"{name}.png", settings)
    card.save(out)
    return out
