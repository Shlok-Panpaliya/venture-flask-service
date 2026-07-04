"""
Bhulekh captcha OCR: prefers a Selenium *element screenshot* (rendered pixels) over decoding
the data: base64 src, which can differ from what the browser composites.

Strategy (why this is more accurate than "best single guess"):
    A single Tesseract pass on a single preprocessing is noisy. Instead we run MANY
    (preprocessing variant x PSM) passes across two pipelines and treat every reading as a
    *vote*, weighted by Tesseract's own per-word confidence. We then:
      1. pick the most likely captcha length by weighted vote, and
      2. do per-character majority voting among candidates of that length.
    The true characters recur across variants; OCR errors are random, so consensus wins.

Pipeline A: gentle PIL preprocessing + multi-config Tesseract (image_to_data for confidences).
Pipeline B: OpenCV binarization variants + image_to_data.

Requires: pip install pytesseract Pillow opencv-python-headless
System: Tesseract OCR.

Use ?captcha_manual= when OCR is still unreliable.
"""
import base64
import io
import os
import re
import tempfile
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from PIL import Image, ImageEnhance, ImageFilter

_WHITELIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# Bhulekh captchas are short alphanumeric strings. Readings outside this range are almost
# always OCR noise (stray marks split into extra chars, or letters merged/dropped).
_MIN_LEN = 4
_MAX_LEN = 6

# Letters whose upper/lowercase forms differ only in glyph SIZE (same shape, scaled).
# The ddddocr case-correction pass flips these by measured glyph height. Deliberately
# excludes k (lowercase k is a tall ascender), p (descender), the l/I/1 family, and every
# shape-distinct pair (a/A, b/B, e/E, n/N, ...) where the recognizer already gets case right.
_SIZE_AMBIGUOUS = frozenset("cosuvwxz")

# A single (candidate, weight) vote.
Vote = Tuple[str, float]

_TESSERACT_STRING_CONFIGS = [
    rf"--oem 3 --psm 8 -c tessedit_char_whitelist={_WHITELIST}",
    rf"--oem 3 --psm 7 -c tessedit_char_whitelist={_WHITELIST}",
    r"--oem 3 --psm 8",
    r"--oem 3 --psm 7",
    rf"--oem 3 --psm 6 -c tessedit_char_whitelist={_WHITELIST}",
    r"--oem 3 --psm 6",
]


def captcha_png_bytes_from_data_src(src: str) -> bytes:
    if not src or not str(src).startswith("data:"):
        raise ValueError("Captcha img src is not a data URL")
    _, b64 = str(src).split(",", 1)
    return base64.b64decode(b64.strip())


def screenshot_captcha_element_png(element) -> bytes:
    """
    PNG bytes from a Selenium WebElement (what is painted — same as your element.screenshot flow).
    """
    if hasattr(element, "screenshot_as_png"):
        return element.screenshot_as_png
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        element.screenshot(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        if path and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _clean_captcha(s: str, max_len: int = _MAX_LEN) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(s))[:max_len]


# ---------------------------------------------------------------------------
# Consensus voting
# ---------------------------------------------------------------------------

def _vote_captcha(votes: List[Vote]) -> Optional[str]:
    """
    Combine many weighted candidate readings into one consensus string.

    1. Keep only plausible-length (4-6) alnum candidates.
    2. Choose the target length by summed weight (ties -> longer, captchas skew to 5-6).
    3. Per position, pick the highest-weighted character across same-length candidates.
    4. Fall back to the single highest-weighted candidate if voting yields nothing.
    """
    cleaned: List[Vote] = []
    for text, w in votes:
        c = _clean_captcha(text, _MAX_LEN)
        if _MIN_LEN <= len(c) <= _MAX_LEN and w > 0:
            cleaned.append((c, float(w)))

    if not cleaned:
        # Loosen the length gate: accept >=3 as a last resort so we still return something.
        salvage = [
            (_clean_captcha(t, _MAX_LEN), float(w))
            for t, w in votes
            if len(_clean_captcha(t, _MAX_LEN)) >= 3 and w > 0
        ]
        if not salvage:
            return None
        salvage.sort(key=lambda cw: cw[1], reverse=True)
        return salvage[0][0]

    # 2. Weighted vote for the most likely length.
    length_weight: Dict[int, float] = defaultdict(float)
    for c, w in cleaned:
        length_weight[len(c)] += w
    target_len = max(length_weight, key=lambda L: (length_weight[L], L))

    same_len = [(c, w) for c, w in cleaned if len(c) == target_len]

    # 3. Per-position weighted character voting.
    result_chars: List[str] = []
    for i in range(target_len):
        char_weight: Dict[str, float] = defaultdict(float)
        for c, w in same_len:
            char_weight[c[i]] += w
        result_chars.append(max(char_weight, key=lambda ch: char_weight[ch]))

    consensus = "".join(result_chars)
    return consensus or None


# ---------------------------------------------------------------------------
# Pipeline A: PIL preprocessing + multi-config Tesseract
# ---------------------------------------------------------------------------

def _pil_gentle_preprocess(image: Image.Image) -> Image.Image:
    """Resize small images, grayscale, mild contrast + unsharp (matches common scraper recipe)."""
    if image.mode != "L":
        image = image.convert("L")
    width, height = image.size
    if width < 150 or height < 50:
        scale_factor = max(150 / width, 50 / height, 2.0)
        image = image.resize(
            (int(width * scale_factor), int(height * scale_factor)),
            Image.Resampling.LANCZOS,
        )
    image = ImageEnhance.Contrast(image).enhance(1.2)
    image = image.filter(
        ImageFilter.UnsharpMask(radius=0.5, percent=100, threshold=2)
    )
    return image


def _votes_from_data(d: dict) -> List[Vote]:
    """
    Turn one image_to_data result into weighted votes.

    Emits per-word votes AND a merged full-line vote (words concatenated), each weighted by the
    mean Tesseract confidence so that clean, high-confidence reads dominate the tally.
    """
    votes: List[Vote] = []
    chunks: List[str] = []
    confs: List[float] = []
    n = len(d.get("text", []))

    for i in range(n):
        raw = (d["text"][i] or "").strip()
        if not raw:
            continue
        try:
            cf = float(d["conf"][i])
        except (ValueError, TypeError):
            cf = -1.0
        if cf < 12:
            continue
        cleaned = _clean_captcha(raw, _MAX_LEN)
        if not cleaned:
            continue
        # Confidence (0-100) is the vote weight; scale to a small, comparable range.
        votes.append((cleaned, cf / 100.0))
        chunks.append(cleaned)
        confs.append(cf)

    if chunks:
        merged = _clean_captcha("".join(chunks), _MAX_LEN)
        if len(merged) >= _MIN_LEN:
            avg = sum(confs) / len(confs) if confs else 0.0
            # Slight boost: a merged multi-word line is often the whole captcha.
            votes.append((merged, avg / 100.0 + 0.05))

    return votes


def _votes_pil_multiconfig(image: Image.Image) -> List[Vote]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return []

    votes: List[Vote] = []
    for cfg in _TESSERACT_STRING_CONFIGS:
        try:
            d = pytesseract.image_to_data(image, config=cfg, output_type=Output.DICT)
        except Exception:
            continue
        votes.extend(_votes_from_data(d))
    return votes


def _votes_pil_pipeline(png_bytes: bytes) -> List[Vote]:
    try:
        img = Image.open(io.BytesIO(png_bytes))
        proc = _pil_gentle_preprocess(img)
        return _votes_pil_multiconfig(proc)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Front-end cleaning: kill background noise + the horizontal strike line
# ---------------------------------------------------------------------------

def _deline_denoise_png(png_bytes: bytes) -> Optional[bytes]:
    """
    Bhulekh captchas are crisp glyphs defaced by (a) salt-and-pepper background dots and
    (b) a single thin straight horizontal strike line. Both wreck Tesseract segmentation.

    We median-blur out the dots, drop tiny speck components, detect the strike line with a
    long-thin horizontal open, then *inpaint* it (not subtract — subtraction gouges the
    glyph strokes it crosses). Returns cleaned PNG bytes, or None if OpenCV is unavailable
    or cleaning fails (caller then falls back to the raw image).

    Measured on 20 live captchas: Tesseract char accuracy 12% (raw) -> 67% (cleaned).
    """
    if cv2 is None:
        return None
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        den = cv2.medianBlur(gray, 3)
        _, bw = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Drop residual specks (tiny connected components).
        n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] <= 4:
                bw[lab == i] = 0

        # Strike line = a long thin horizontal run. Isolate then widen it a hair.
        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
        line = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
        line = cv2.dilate(line, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))

        # Inpaint the line region so crossing glyph strokes are reconstructed, not erased.
        inpainted = cv2.inpaint(den, line, 3, cv2.INPAINT_TELEA)
        _, clean = cv2.threshold(inpainted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        clean = cv2.resize(clean, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        clean = cv2.copyMakeBorder(clean, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)

        ok, buf = cv2.imencode(".png", clean)
        return buf.tobytes() if ok else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pipeline B: OpenCV binarization variants + Tesseract
# ---------------------------------------------------------------------------

def _gray_from_png(png_bytes: bytes) -> np.ndarray:
    if cv2 is not None:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("Could not decode PNG bytes")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    pil = Image.open(io.BytesIO(png_bytes)).convert("L")
    return np.array(pil, dtype=np.uint8)


def _preprocess_variants_opencv(gray: np.ndarray) -> List[np.ndarray]:
    h, w = int(gray.shape[0]), int(gray.shape[1])
    scale = max(340.0 / float(max(h, w)), 2.6)
    scale = min(scale, 6.0)
    large = cv2.resize(
        gray,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_CUBIC,
    )
    blur = cv2.GaussianBlur(large, (3, 3), 0)
    out: List[np.ndarray] = []

    for block, c in ((21, 3), (25, 5), (31, 7), (35, 9), (41, 11)):
        at = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c
        )
        out.append(at)
        k = np.ones((2, 2), np.uint8)
        out.append(cv2.morphologyEx(at, cv2.MORPH_CLOSE, k))

    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(otsu)
    out.append(cv2.bitwise_not(otsu))

    if float(np.mean(blur)) < 130:
        inv_at = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 29, 5
        )
        out.append(cv2.bitwise_not(inv_at))

    return out


def _preprocess_variants_pil(png_bytes: bytes) -> List[np.ndarray]:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    w, h = img.size
    img = img.resize((w * 4, h * 4), Image.Resampling.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(2.8)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    arr = np.array(img, dtype=np.uint8)
    variants = [arr]
    for thr in (115, 130, 145, 160):
        variants.append(np.where(arr > thr, 255, 0).astype(np.uint8))
    return variants


def _votes_all_variants(variants: List[np.ndarray]) -> List[Vote]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as e:
        raise RuntimeError(
            "pytesseract is required. pip install pytesseract Pillow opencv-python-headless"
        ) from e

    modes = [
        (7, True),
        (8, True),
        (13, True),
        (6, True),
        (7, False),
        (8, False),
        (13, False),
    ]

    votes: List[Vote] = []
    for var in variants:
        var = np.asarray(var, dtype=np.uint8)
        for psm, use_wl in modes:
            cfg = f"--oem 3 --psm {psm}"
            if use_wl:
                cfg += f" -c tessedit_char_whitelist={_WHITELIST}"
            try:
                d = pytesseract.image_to_data(var, config=cfg, output_type=Output.DICT)
            except Exception:
                continue
            votes.extend(_votes_from_data(d))
    return votes


def _votes_opencv_pipeline(png_bytes: bytes) -> List[Vote]:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return []
    try:
        gray = _gray_from_png(png_bytes)
        if cv2 is not None:
            variants = _preprocess_variants_opencv(gray)
        else:
            variants = _preprocess_variants_pil(png_bytes)
        return _votes_all_variants(variants)
    except RuntimeError:
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _correct_common_ocr_errors(text: str) -> str:
    """Light-touch fixes; avoid aggressive 5<->S that breaks real letters."""
    if not text:
        return text
    chars = list(text)
    for i, char in enumerate(chars):
        if char in ("i", "I", "l") and 0 < i < len(chars) - 1:
            if chars[i - 1].isdigit() or chars[i + 1].isdigit():
                chars[i] = "1"
        elif char in ("O", "o") and 0 < i < len(chars) - 1:
            if chars[i - 1].isdigit() or chars[i + 1].isdigit():
                chars[i] = "0"
    return "".join(chars)[:_MAX_LEN]


# ---------------------------------------------------------------------------
# Primary engine: ddddocr (captcha-specialized CRNN) + glyph-height case fix
# ---------------------------------------------------------------------------

_DDDDOCR = None
_DDDDOCR_UNAVAILABLE = False


def _get_ddddocr():
    """Lazily construct the ddddocr model once (it loads an ONNX model); None if not installed."""
    global _DDDDOCR, _DDDDOCR_UNAVAILABLE
    if _DDDDOCR is not None or _DDDDOCR_UNAVAILABLE:
        return _DDDDOCR
    try:
        import ddddocr
        _DDDDOCR = ddddocr.DdddOcr(show_ad=False)
    except Exception:
        _DDDDOCR_UNAVAILABLE = True
    return _DDDDOCR


def _ddddocr_read(png_bytes: bytes) -> Optional[str]:
    """Run ddddocr on the raw image; return the alphanumeric reading, or None if unavailable."""
    model = _get_ddddocr()
    if model is None:
        return None
    try:
        raw = model.classification(png_bytes)
    except Exception:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw or "")
    return cleaned or None


def _clean_binary(png_bytes: bytes) -> Optional[np.ndarray]:
    """Denoise + inpaint the strike line; return a native-scale binary (text=255) for segmentation."""
    if cv2 is None:
        return None
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        den = cv2.medianBlur(gray, 3)
        _, bw = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] <= 4:
                bw[lab == i] = 0
        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
        line = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
        line = cv2.dilate(line, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
        inpainted = cv2.inpaint(den, line, 3, cv2.INPAINT_TELEA)
        _, clean = cv2.threshold(inpainted, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return clean
    except Exception:
        return None


def _char_boxes(bw: np.ndarray) -> List[List[int]]:
    """Left-to-right glyph bounding boxes; merges x-overlapping parts (e.g. an i/j dot)."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 10 or h < 5:
            continue
        boxes.append([int(x), int(y), int(w), int(h)])
    boxes.sort(key=lambda b: b[0])
    merged: List[List[int]] = []
    for b in boxes:
        if merged and b[0] <= merged[-1][0] + merged[-1][2] * 0.55:
            px, py, pw, ph = merged[-1]
            nx, ny = min(px, b[0]), min(py, b[1])
            nx2, ny2 = max(px + pw, b[0] + b[2]), max(py + ph, b[1] + b[3])
            merged[-1] = [nx, ny, nx2 - nx, ny2 - ny]
        else:
            merged.append(list(b))
    return merged


def _correct_case(pred: str, png_bytes: bytes, thresh: float = 0.78) -> str:
    """
    Recover letter case for the case-sensitive Bhulekh field. ddddocr identifies characters
    well but tends to lowercase; for letters whose case is size-only (c/C o/O s/S u/U v/V w/W
    x/X z/Z) we compare each glyph's height to the tallest glyph — cap-height -> uppercase,
    x-height -> lowercase. No-op when segmentation can't be aligned 1:1 with the reading.
    """
    bw = _clean_binary(png_bytes)
    if bw is None:
        return pred
    boxes = _char_boxes(bw)
    if len(boxes) != len(pred) or not boxes:
        return pred
    cap_h = max(b[3] for b in boxes)
    out = []
    for ch, box in zip(pred, boxes):
        if ch.lower() in _SIZE_AMBIGUOUS:
            out.append(ch.upper() if box[3] >= thresh * cap_h else ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _ocr_tesseract_vote(png_bytes: bytes) -> str:
    """Fallback engine: clean the raster, then vote across PIL + OpenCV Tesseract passes."""
    try:
        import pytesseract  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "No OCR engine available. Install ddddocr (pip install ddddocr) "
            "or Tesseract + pytesseract."
        ) from e

    # Clean the raster first (denoise + inpaint the strike line). The cleaned image is far
    # more legible to Tesseract; fall back to the raw bytes if OpenCV is missing or cleaning
    # fails for this frame.
    source = _deline_denoise_png(png_bytes) or png_bytes

    try:
        votes: List[Vote] = []
        votes.extend(_votes_pil_pipeline(source))
        votes.extend(_votes_opencv_pipeline(source))

        if not votes:
            raise RuntimeError(
                "Tesseract returned no captcha text (PIL + OpenCV pipelines). "
                "Try captcha_manual=… or check Tesseract install."
            )

        consensus = _vote_captcha(votes)
        if not consensus:
            raise RuntimeError("No OCR candidate produced usable captcha text")

        return _correct_common_ocr_errors(consensus)
    except RuntimeError:
        raise
    except Exception as ex:
        if "tesseract" in str(ex).lower() or "TesseractNotFoundError" in type(ex).__name__:
            raise RuntimeError(
                "Tesseract OCR binary not found. "
                "https://github.com/tesseract-ocr/tesseract (macOS: brew install tesseract)"
            ) from ex
        raise


def ocr_bhulekh_captcha_png(png_bytes: bytes) -> str:
    """
    Read a Bhulekh captcha.

    Primary path: ddddocr (a captcha-specialized CRNN — CPU/ONNX, ~ms per image) reads the raw
    image, then a glyph-height pass recovers letter case for the case-sensitive form field.
    Falls back to the multi-variant Tesseract voting pipeline if ddddocr isn't installed or
    returns nothing usable.
    """
    guess = _ddddocr_read(png_bytes)
    if guess and len(guess) >= 3:
        return _correct_case(guess, png_bytes)

    return _ocr_tesseract_vote(png_bytes)


def ocr_bhulekh_captcha_for_driver(element) -> str:
    """Screenshot the captcha <img> then OCR (preferred for Bhulekh)."""
    png = screenshot_captcha_element_png(element)
    return ocr_bhulekh_captcha_png(png)
