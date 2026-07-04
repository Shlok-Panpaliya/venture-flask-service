#!/usr/bin/env python3
"""
Feed scraped captcha PNGs through the project's real OCR logic
(helpers.bhulekh_captcha.ocr_bhulekh_captcha_png) and emit results.json.

Usage: run_ocr.py <captchas_dir>
Requires PYTHONPATH to include the venture-flask-service project root.
"""
import json
import os
import sys
import time

from helpers.bhulekh_captcha import ocr_bhulekh_captcha_png


def main():
    cap_dir = sys.argv[1]
    manifest_path = os.path.join(cap_dir, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    results = []
    for entry in manifest:
        fpath = os.path.join(cap_dir, entry["file"])
        rec = {"index": entry["index"], "file": entry["file"]}
        try:
            with open(fpath, "rb") as fh:
                png = fh.read()
            t0 = time.time()
            inferred = ocr_bhulekh_captcha_png(png)
            rec["inferred"] = inferred
            rec["ms"] = round((time.time() - t0) * 1000)
        except Exception as e:
            rec["inferred"] = None
            rec["error"] = f"{type(e).__name__}: {e}"
        results.append(rec)
        print(f"[{rec['index']:>2}] {entry['file']}: "
              f"{rec.get('inferred') or rec.get('error')}")

    out = os.path.join(cap_dir, "results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {out} ({len(results)} rows)")


if __name__ == "__main__":
    main()
