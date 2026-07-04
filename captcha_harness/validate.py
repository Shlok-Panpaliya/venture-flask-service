#!/usr/bin/env python3
"""Validate the integrated production OCR (ddddocr + case-correction) against a
ground-truth JSON on the 50-captcha batch. Truth is Claude's by-eye reading."""
import json
import os
import sys

sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
from helpers.bhulekh_captcha import ocr_bhulekh_captcha_png  # noqa: E402

CAP = sys.argv[1] if len(sys.argv) > 1 else "captchas50"
TRUTH_FILE = sys.argv[2] if len(sys.argv) > 2 else "truth50.json"

with open(os.path.join(CAP, "manifest.json")) as f:
    manifest = json.load(f)
with open(TRUTH_FILE) as f:
    truth = {int(k): v for k, v in json.load(f).items()}

exact = ch = tot = 0
misses = []
for e in manifest:
    idx = e["index"]
    t = truth.get(idx)
    if not t:
        continue
    pred = ocr_bhulekh_captcha_png(open(os.path.join(CAP, e["file"]), "rb").read())
    ok = pred == t
    exact += ok
    ch += sum(a == b for a, b in zip(pred, t))
    tot += len(t)
    if not ok:
        misses.append((idx, pred, t))

n = len([1 for e in manifest if truth.get(e["index"])])
p = exact / n if n else 0
print(f"N={n}  EXACT {exact}/{n} ({100*p:.1f}%)  char {ch}/{tot} ({100*ch/tot:.1f}%)")
print(f"5-retry eventual = {100*(1-(1-p)**5):.1f}%")
# Wilson 95% CI for exact per-captcha p
if n:
    import math
    z = 1.96
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    lo, hi = max(0, centre-half), min(1, centre+half)
    print(f"per-captcha 95% CI: {100*lo:.0f}%-{100*hi:.0f}%  -> 5-retry CI: "
          f"{100*(1-(1-lo)**5):.1f}%-{100*(1-(1-hi)**5):.1f}%")
print("\nmisses (idx, pred, truth):")
for idx, pred, t in misses:
    print(f"  [{idx:02d}] {pred:<8} != {t}")
