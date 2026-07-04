"""
Bhulekh captcha OCR: prefers a Selenium *element screenshot* (rendered pixels) over decoding
the data: base64 src, which can differ from what the browser composites.

Pipeline A: gentle PIL preprocessing + multi-config Tesseract (image_to_string), same idea as
common scraper projects.

Pipeline B: OpenCV binarization variants + image_to_data / string fallback.

Requires: pip install pytesseract Pillow opencv-python-headless
System: Tesseract OCR.

Use ?captcha_manual= when OCR is still unreliable.
"""
import base64
import io
import os
import re
import tempfile
from typing import List, Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from PIL import Image, ImageEnhance, ImageFilter

_WHITELIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

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


def _clean_captcha(s: str, max_len: int = 6) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(s))[:max_len]


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


def _score_candidate_string(ocr_text: str, cleaned: str) -> float:
    if not cleaned or len(cleaned) < 3:
        return -1.0
    return len(cleaned) * (len(cleaned) / max(len(ocr_text), 1))


def _ocr_pil_multiconfig(image: Image.Image) -> Optional[str]:
    try:
        import pytesseract
    except ImportError:
        return None

    best_result: Optional[str] = None
    best_confidence = 0.0

    for cfg in _TESSERACT_STRING_CONFIGS:
        try:
            ocr_text = (pytesseract.image_to_string(image, config=cfg) or "").strip()
        except Exception:
            continue
        cleaned = "".join(c for c in ocr_text if c.isalnum())
        if len(cleaned) < 3:
            continue
        conf = _score_candidate_string(ocr_text, cleaned)
        if conf > best_confidence:
            best_confidence = conf
            best_result = cleaned

    return best_result[:6] if best_result else None


def _correct_common_ocr_errors(text: str) -> str:
    """Light-touch fixes; avoid aggressive 5↔S that breaks real letters."""
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
    return "".join(chars)[:6]


def _ocr_pil_pipeline(png_bytes: bytes) -> Optional[str]:
    try:
        img = Image.open(io.BytesIO(png_bytes))
        proc = _pil_gentle_preprocess(img)
        out = _ocr_pil_multiconfig(proc)
        if out:
            out = _correct_common_ocr_errors(out)
        return out[:6] if out else None
    except Exception:
        return None


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


def _candidates_from_data(d: dict) -> List[Tuple[str, float]]:
    cands: List[Tuple[str, float]] = []
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
        cleaned = _clean_captcha(raw, 12)
        if len(cleaned) < 1:
            continue
        cands.append((cleaned[:6], cf))
        chunks.append(cleaned)
        confs.append(cf)

    if chunks:
        merged = _clean_captcha("".join(chunks), 8)[:6]
        if len(merged) >= 3:
            avg = sum(confs) / len(confs) if confs else 0.0
            cands.append((merged, avg + 2.0))

    return cands


def _ocr_all_variants(variants: List[np.ndarray]) -> str:
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

    best_text = ""
    best_score = -1.0

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
            for txt, sc in _candidates_from_data(d):
                if len(txt) < 3:
                    continue
                if sc > best_score:
                    best_score = sc
                    best_text = txt

    if best_text:
        return best_text[:6]

    for var in variants:
        var = np.asarray(var, dtype=np.uint8)
        for psm in (8, 7, 13):
            cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist={_WHITELIST}"
            try:
                line = (pytesseract.image_to_string(var, config=cfg) or "").strip()
            except Exception:
                continue
            alt = _clean_captcha(line, 8)
            if len(alt) >= 4:
                return alt[:6]

    raise RuntimeError(
        "Tesseract returned no captcha text after OpenCV preprocess passes"
    )


def _ocr_opencv_pipeline(png_bytes: bytes) -> Optional[str]:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return None
    try:
        gray = _gray_from_png(png_bytes)
        if cv2 is not None:
            variants = _preprocess_variants_opencv(gray)
        else:
            variants = _preprocess_variants_pil(png_bytes)
        return _ocr_all_variants(variants)
    except RuntimeError:
        return None


def _pick_best_captcha_string(candidates: List[str]) -> str:
    """Prefer length 4–6, then longest plausible."""
    filtered = [c for c in candidates if c and len(c) >= 3]
    if not filtered:
        raise RuntimeError("No OCR candidate produced captcha text")
    filtered.sort(
        key=lambda s: (
            1 if 4 <= len(s) <= 6 else 0,
            min(len(s), 6),
            len(s),
        ),
        reverse=True,
    )
    return filtered[0][:6]


def ocr_bhulekh_captcha_png(png_bytes: bytes) -> str:
    """
    Run PIL-style OCR on raster (e.g. element screenshot), then OpenCV pipeline; best of both.
    """
    try:
        import pytesseract  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pytesseract is required for captcha OCR.") from e

    pooled: List[str] = []

    pil_guess = _ocr_pil_pipeline(png_bytes)
    if pil_guess:
        pooled.append(pil_guess)

    cv_guess = _ocr_opencv_pipeline(png_bytes)
    if cv_guess:
        pooled.append(cv_guess)

    if not pooled:
        raise RuntimeError(
            "Tesseract returned no captcha text (PIL + OpenCV pipelines). "
            "Try captcha_manual=… or check Tesseract install."
        )

    try:
        return _pick_best_captcha_string(pooled)
    except RuntimeError:
        raise
    except Exception as ex:
        if "tesseract" in str(ex).lower() or "TesseractNotFoundError" in type(ex).__name__:
            raise RuntimeError(
                "Tesseract OCR binary not found. "
                "https://github.com/tesseract-ocr/tesseract (macOS: brew install tesseract)"
            ) from ex
        raise


def ocr_bhulekh_captcha_for_driver(element) -> str:
    """Screenshot the captcha <img> then OCR (preferred for Bhulekh)."""
    png = screenshot_captcha_element_png(element)
    return ocr_bhulekh_captcha_png(png)
