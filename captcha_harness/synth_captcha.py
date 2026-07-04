#!/usr/bin/env python3
"""
Synthetic Bhulekh captcha generator.

The live captcha is a GDI+ (System.Drawing) render: Arial glyphs, antialiased black
on white, one 2px gray-128 horizontal strike line at y~29-30, and ~9.5% salt noise
also at gray 128. Font identified as Arial by direct glyph comparison (see fontspike/).

Renders 140x40 PNGs with per-sample jitter around the measured parameters so a model
trained on these is robust to the real distribution. Labels are known exactly -> zero
label noise, unlimited data, perfectly balanced classes.
"""
import io
import string

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 140, 40
ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase
_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FONT_CACHE = {}


def _font(size):
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = ImageFont.truetype(_FONT_PATH, size)
    return _FONT_CACHE[size]


def render(text, rng):
    """Render one captcha string to a uint8 grayscale array (H, W)."""
    size = int(rng.integers(26, 29))          # 26..28
    track = float(rng.uniform(2.0, 4.5))       # inter-glyph tracking px
    x = float(rng.uniform(11, 16))             # left margin
    baseline = float(rng.uniform(33, 35))
    font = _font(size)

    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    for ch in text:
        dy = float(rng.uniform(-1.0, 1.0))
        d.text((x, baseline + dy), ch, font=font, fill=0, anchor="ls")
        bb = font.getbbox(ch)
        adv = (bb[2] - bb[0]) if (bb[2] - bb[0]) > 0 else font.getlength(ch)
        x += adv + track

    a = np.asarray(img).astype(np.uint8).copy()

    # strike line: gray 128, 2px, full width, y jittered; do not darken black glyphs
    ly = int(rng.integers(28, 32))
    for yy in (ly, ly + 1):
        if 0 <= yy < H:
            row = a[yy]
            row[row > 200] = 128           # only overwrite background
            a[yy] = row

    # salt noise at gray 128 on background; a fraction as 2px clusters (matches real)
    rate = float(rng.uniform(0.08, 0.11))
    mask = (rng.random((H, W)) < rate) & (a > 200)
    a[mask] = 128
    # occasional 2px vertical noise clusters for texture realism
    ys, xs = np.where((rng.random((H, W)) < 0.012) & (a > 200))
    for yy, xx in zip(ys, xs):
        if yy + 1 < H and a[yy + 1, xx] > 200:
            a[yy, xx] = 128
            a[yy + 1, xx] = 128
    return a


def render_png(text, rng):
    a = render(text, rng)
    buf = io.BytesIO()
    Image.fromarray(a, "L").save(buf, format="PNG")
    return buf.getvalue()


def random_text(rng, n=6):
    return "".join(ALPHABET[i] for i in rng.integers(0, len(ALPHABET), n))
