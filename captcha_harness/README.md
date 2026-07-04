# Captcha OCR — harness, training, and verification

Tools for reading **live** Bhulekh captchas in `helpers/bhulekh_captcha.ocr_bhulekh_captcha_png`.
Covers harvesting, labeling, training the custom CNN, and measuring accuracy against a
human-read gold set.

## Production OCR engine (3-tier, best-first)

`ocr_bhulekh_captcha_png` tries, in order:

1. **Per-character CNN** (`helpers/models/captcha_char_cnn.onnx`, ONNX/CPU, ~ms) — trained on
   this exact captcha font. Cleans the image (denoise + inpaint the strike line), segments it
   into 6 glyphs, and classifies each. Used when the image segments into exactly 6 glyphs.
2. **ddddocr + glyph-height case-correction** — a captcha-specialized CRNN; covers frames the
   CNN can't segment, and works even if the CNN model / onnxruntime isn't installed.
3. **Tesseract multi-variant voting** — final fallback.

Case matters (the Bhulekh field is case-sensitive); every tier preserves letter case.

### Measured accuracy (case-sensitive exact match, Claude-vision gold truth)

| Engine | Gold-50 exact | 5-retry end-to-end |
|--------|---------------|--------------------|
| Tesseract only (original) | 0% | ~0% |
| ddddocr + case-correction | 52% | 97.5% |
| CNN (240 labels) + ddddocr fallback | 64% | 99.4% |
| **CNN (1500 labels) + ddddocr fallback** | **68%** | **99.7%** |

The app retries up to 5× with a fresh captcha each attempt, so per-captcha accuracy compounds
well past the 90% end-to-end target. Remaining misses are the unresolvable `l/I` and `O/0`
glyph ambiguities (present even in human labels) and the ~18% of captchas that don't segment
cleanly into 6 glyphs (handled by the ddddocr fallback).

## Pipeline scripts

| Script | Purpose |
|--------|---------|
| `scrape_parallel.mjs` | Harvest captchas fast — N isolated Puppeteer browser contexts (independent ASP.NET sessions), content-dedup. `node scrape_parallel.mjs 1500 ./dataset 6` |
| `scrape_captchas.mjs` | Single-session serial scraper (kept for small/simple runs). |
| `make_sheets.py` | Tile dataset captchas into indexed contact sheets for fast human labeling. |
| `label_captchas.py` | Alternative: auto-label with a Claude vision model (needs `ANTHROPIC_API_KEY`). |
| `train_char_cnn.py` | Train the per-character CNN; reports gold accuracy; exports ONNX. |
| `self_train.py` | Optional: expand labels via high-confidence pseudo-labels (plateaued here — genuine labels help more). |
| `run_ocr.py` / `generate_report.py` / `validate.py` | Run the pipeline over images and score / build an HTML report. |
| `labels.json` | The 1500 human (Claude-vision) labels used to train the shipped model. Raw dataset images are re-harvestable and kept out of the repo. |

## Retrain from scratch

```bash
# 1. harvest (parallel, fast)
node scrape_parallel.mjs 1500 ./dataset 6
# 2. label — either contact sheets (python make_sheets.py) read by a human,
#    or auto-label:  PYTHONPATH=.. python label_captchas.py --dir dataset --out dataset/labels.json
# 3. train the per-char CNN + export ONNX
PYTHONPATH=.. python train_char_cnn.py --dir dataset --labels dataset/labels.json \
    --gold-dir captchas50 --gold-truth truth50.json --epochs 50 --out char_cnn
# 4. drop char_cnn.onnx into helpers/models/captcha_char_cnn.onnx
```

## Requirements

- **Runtime (service):** `onnxruntime` (CNN), `ddddocr` (fallback), `opencv-python-headless`,
  `Pillow`, `numpy`; `pytesseract` + Tesseract for the last-resort fallback. All in
  `requirements.txt`.
- **Training only:** `torch`, and Node ≥18 + `puppeteer` for harvesting.
