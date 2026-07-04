#!/usr/bin/env python3
"""
Auto-label harvested captchas with a Claude vision model (Haiku 4.5 by default),
concurrently. Structural validation keeps only clean 6-char alphanumeric reads.

Credentials: reads ANTHROPIC_API_KEY from env or a .env file (python-dotenv).

Usage:
  # calibrate: label a gold set and report the labeler's own accuracy vs truth
  label_captchas.py --calibrate --dir captchas50 --truth truth50.json
  # full run: label every image in a dataset dir -> labels.json
  label_captchas.py --dir dataset --out dataset/labels.json [--model claude-haiku-4-5] [--workers 12]
"""
import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import anthropic

PROMPT = (
    "This image is a 6-character alphanumeric CAPTCHA (letters a-z, A-Z and digits 0-9). "
    "Read it exactly, preserving UPPER/lowercase. Reply with ONLY the 6 characters, nothing else."
)
ALNUM = re.compile(r"[^A-Za-z0-9]")


def read_one(client, model, path):
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": PROMPT},
            ]}],
        )
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        return ALNUM.sub("", txt)
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--truth", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("No ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in env or .env — cannot call the API.")

    client = anthropic.Anthropic()

    if args.calibrate:
        truth = {int(k): v for k, v in json.load(open(args.truth)).items()}
        items = [(i, os.path.join(args.dir, f"cap_{i:02d}.png")) for i in sorted(truth)]
        if args.limit:
            items = items[: args.limit]
        preds = {}
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(read_one, client, args.model, p): i for i, p in items}
            for fut in cf.as_completed(futs):
                preds[futs[fut]] = fut.result()
        exact = ci = valid = 0
        for i, _ in items:
            p, t = preds[i], truth[i]
            valid += bool(re.fullmatch(r"[A-Za-z0-9]{6}", p))
            exact += p == t
            ci += p.lower() == t.lower()
            if p != t:
                print(f"  [{i:02d}] {p:<8} != {t}")
        n = len(items)
        print(f"\n{args.model} on {n}: exact {exact}/{n} ({100*exact/n:.0f}%)  "
              f"case-insens {ci}/{n} ({100*ci/n:.0f}%)  well-formed {valid}/{n}")
        return

    manifest = json.load(open(os.path.join(args.dir, "manifest.json")))
    files = [e["file"] for e in manifest]
    if args.limit:
        files = files[: args.limit]
    labels, dropped = {}, 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(read_one, client, args.model, os.path.join(args.dir, f)): f for f in files}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            f = futs[fut]
            p = fut.result()
            if re.fullmatch(r"[A-Za-z0-9]{6}", p):
                labels[f] = p
            else:
                dropped += 1
            if n % 100 == 0:
                print(f"labeled {n}/{len(files)}  (kept {len(labels)}, dropped {dropped})")
    out = args.out or os.path.join(args.dir, "labels.json")
    json.dump(labels, open(out, "w"), indent=2)
    print(f"\nWrote {out}: {len(labels)} clean labels, {dropped} dropped (malformed).")


if __name__ == "__main__":
    main()
