# Captcha OCR — harness, training, and verification

Tools for reading **live** Bhulekh captchas in `helpers/bhulekh_captcha.ocr_bhulekh_captcha_png`.
Covers harvesting, labeling, training the custom CNN, and measuring accuracy against a
human-read gold set.

## Production OCR engine (4-tier, best-first)

`ocr_bhulekh_captcha_png` tries, in order:

1. **CRNN + CTC** (`helpers/models/captcha_crnn.onnx`, ONNX/CPU, ~6ms) — trained on this exact
   captcha font (Arial). Reads the whole 140×40 strip at native resolution and decodes with
   greedy CTC. **No segmentation**, so background noise and the strike line don't break it.
   Primary engine.
2. **Per-character CNN** (`captcha_char_cnn.onnx`) — segment-then-classify; lighter fallback.
3. **ddddocr + glyph-height case-correction** — generic captcha CRNN.
4. **Tesseract multi-variant voting** — final fallback.

Case matters (the Bhulekh field is case-sensitive); every tier preserves letter case.

### Measured accuracy (case-sensitive exact match, human-labeled gold-50)

| Engine | Gold-50 exact | 5-retry end-to-end |
|--------|---------------|--------------------|
| Tesseract only (original) | 0% | ~0% |
| ddddocr + case-correction | 52% | 97.5% |
| Per-char CNN (1500 labels) + ddddocr | 68% | 99.7% |
| CRNN (30k synthetic + 1500 real) @ 128×32 | 86% | 100% |
| CRNN + confusable-pair augmentation | 88% | 100% |
| **CRNN @ native 140×40 (shipped)** | **90%** | **100%** |

Why the CRNN wins: it removes the two ceilings of the per-char CNN — the ~18% of frames that
don't segment into 6 glyphs, and the label noise from hand-labeling. It trains on **Arial
synthetic** captchas (the font was identified by direct glyph comparison; see
`synth_captcha.py`) mixed with the 1500 real labels. Two targeted augmentations closed most of
the gap: biasing synthetic strings toward adjacent-duplicate pairs (so CTC learns to separate
`mm`→`m`) and toward confusable glyphs (`g/q`, `v/y`), plus keeping the native 40px height so
glyph descenders survive. The 5 residual gold misses are `g/q`/`v/y` look-alikes and one
likely mislabel in the gold set (true accuracy is probably ~92%).

## Pipeline scripts

| Script | Purpose |
|--------|---------|
| `scrape_parallel.mjs` | Harvest captchas fast — N isolated Puppeteer browser contexts (independent ASP.NET sessions), content-dedup. `node scrape_parallel.mjs 1500 ./dataset 6` |
| `scrape_captchas.mjs` | Single-session serial scraper (kept for small/simple runs). |
| `make_sheets.py` | Tile dataset captchas into indexed contact sheets for fast human labeling. |
| `label_captchas.py` | Alternative: auto-label with a Claude vision model (needs `ANTHROPIC_API_KEY`). |
| `synth_captcha.py` | **Arial synthetic captcha generator** — reproduces the live render (gray-128 strike line + 9.5% salt noise) with jitter, plus adjacent-duplicate and confusable-glyph biasing. Zero-label, unlimited, balanced data. |
| `crnn_train.py` | **Train the shipped CRNN + CTC** on synthetic + real; reports gold every 5 epochs; exports ONNX. |
| `synth_train.py` | Spike: train the per-char CNN on synthetic only (validated font transfer). |
| `train_char_cnn.py` | Train the per-character CNN (fallback engine); reports gold accuracy; exports ONNX. |
| `self_train.py` | Optional: expand labels via high-confidence pseudo-labels (plateaued here — genuine labels help more). |
| `run_ocr.py` / `generate_report.py` / `validate.py` | Run the pipeline over images and score / build an HTML report. |
| `labels.json` | The 1500 human labels used (with synthetic) to train the shipped model. Raw dataset images are re-harvestable and kept out of the repo. |

## Retrain the shipped CRNN from scratch

```bash
# 1. harvest real captchas (parallel, fast) — used to close the synthetic->real domain gap
node scrape_parallel.mjs 1500 ./dataset 6
# 2. label them (contact sheets read by a human, or label_captchas.py) -> dataset/labels.json
# 3. train the CRNN on 30k Arial synthetic + the 1500 real labels (oversampled), export ONNX
PYTHONPATH=.. python crnn_train.py --ds-dir dataset --gold-dir captchas50 \
    --gold-truth truth50.json --n-synth 30000 --real-repeat 8 --epochs 15 --out crnn
# 4. drop crnn.onnx into helpers/models/captcha_crnn.onnx
```

The CRNN converges by ~epoch 5–10. `synth_captcha.py` hard-codes the macOS Arial path; point
it at any Arial/Liberation-Sans `.ttf` elsewhere.

## Requirements

- **Runtime (service):** `onnxruntime` (CRNN + CNN), `ddddocr` (fallback),
  `opencv-python-headless`, `Pillow`, `numpy`; `pytesseract` + Tesseract for the last-resort
  fallback. All in `requirements.txt`.
- **Training only:** `torch`, and Node ≥18 + `puppeteer` for harvesting.
