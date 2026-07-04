# Captcha OCR verification harness

Measures how well `helpers/bhulekh_captcha.ocr_bhulekh_captcha_png` reads **live**
Bhulekh captchas. It harvests real captcha images from the site by clicking the
refresh button in a headless browser, runs them through the project OCR pipeline,
and produces an HTML table comparing OCR output against a human reading.

## OCR engine

`ocr_bhulekh_captcha_png` uses **ddddocr** (a captcha-specialized CRNN, CPU/ONNX,
~6 ms/image) as the primary engine, then a **glyph-height case-correction** pass
recovers letter case for the case-sensitive form field (ddddocr tends to lowercase;
for size-only-ambiguous letters `c/C o/O s/S u/U v/V w/W x/X z/Z` we compare each
glyph's height to the tallest glyph). The old multi-variant **Tesseract voting**
pipeline remains as a fallback if ddddocr isn't installed.

Measured accuracy (case-sensitive exact match, Claude-vision ground truth):

| Batch | Exact per-captcha | 5-retry eventual |
|-------|-------------------|------------------|
| 20 captchas | 70% | 99.8% |
| 50 captchas | 52% (95% CI 39–65%) | 97.5% (CI 91–99.5%) |

The app retries up to 5 times with a fresh captcha each attempt, so the per-captcha
rate compounds well past the 90% end-to-end target. Residual misses are the
unresolvable `l/I`, `O/0` glyph ambiguities and ascender-letter case (`f/F`, `k/K`)
that glyph height can't distinguish — a font-trained CNN would be the lever to push
per-captcha higher, but end-to-end already clears 90%.

## Pipeline

1. **`scrape_captchas.mjs`** — Puppeteer loads
   `https://bhulekh.mahabhumi.gov.in/NewBhulekh.aspx`, reads the
   `#ContentPlaceHolder1_captchaImage` data URL, saves a PNG, clicks
   `#ContentPlaceHolder1_btnreferesh`, waits for a new image, repeats.
   → `captchas/cap_NN.png` + `captchas/manifest.json`
2. **`run_ocr.py`** — feeds each PNG through `ocr_bhulekh_captcha_png`.
   → `captchas/results.json`
3. **`generate_report.py`** — joins images + OCR + the `MY_READING` ground-truth
   dict into `captcha_report.html` (accuracy tiles, per-char diff, manual-pass column).

## Requirements

- **Node** ≥ 18 and `puppeteer` (`npm i puppeteer` — downloads its own Chrome).
- **Tesseract** system binary (`brew install tesseract`).
- **Python** deps: `numpy Pillow pytesseract opencv-python-headless`
  (opencv is optional; the pipeline falls back to PIL without it).

## Run

```bash
# 1. harvest N live captchas (headless browser)
node scrape_captchas.mjs 20 ./captchas

# 2. OCR them with the project pipeline (PYTHONPATH must reach the repo root)
PYTHONPATH=.. python run_ocr.py ./captchas

# 3. fill in MY_READING in generate_report.py by eyeballing each cap_NN.png,
#    then build the report
python generate_report.py ./captchas ./captcha_report.html
open captcha_report.html
```

## Notes

- `MY_READING` in `generate_report.py` is the human ground truth. Update it for a
  fresh batch (captchas are random each run). Without it, the "match" column is
  meaningless.
- These captchas use wavy strike-through distortion lines, which Tesseract handles
  poorly — expect low exact-match accuracy. Use the report to decide whether the
  OCR path is viable or whether `?captcha_manual=` / a dedicated captcha model /
  a solving service is needed.
