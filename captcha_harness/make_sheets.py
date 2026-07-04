#!/usr/bin/env python3
"""
Tile dataset captchas into indexed contact sheets so Claude can transcribe many per read.
Each cell: a left index strip ("001") + the captcha upscaled for legibility.

Usage: make_sheets.py [start] [count] [per_sheet] [scale]
Writes sheets/sheet_XX.png and sheets/sheet_map.json ({seq -> filename}).
"""
import json
import os
import sys

from PIL import Image, ImageDraw

HARNESS = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HARNESS, "dataset")
OUT = os.path.join(HARNESS, "sheets")

start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
count = int(sys.argv[2]) if len(sys.argv) > 2 else 240
per = int(sys.argv[3]) if len(sys.argv) > 3 else 24
scale = int(sys.argv[4]) if len(sys.argv) > 4 else 2
COLS = 4

manifest = json.load(open(os.path.join(DS, "manifest.json")))
files = [e["file"] for e in manifest][start:start + count]

os.makedirs(OUT, exist_ok=True)
IDXW = 46                      # index strip width
GAPY = 8
seq_map = {}
seq = start

def build_cell(seq, fname):
    im = Image.open(os.path.join(DS, fname)).convert("L")
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    cell = Image.new("RGB", (IDXW + im.width, im.height), "white")
    cell.paste(im.convert("RGB"), (IDXW, 0))
    d = ImageDraw.Draw(cell)
    d.rectangle([0, 0, IDXW - 1, im.height - 1], fill=(230, 235, 245))
    d.text((5, im.height // 2 - 6), f"{seq:03d}", fill=(20, 40, 120))
    d.line([IDXW - 1, 0, IDXW - 1, im.height - 1], fill=(150, 160, 180))
    return cell

sheets = 0
for s in range(0, len(files), per):
    chunk = files[s:s + per]
    cells = []
    for j, fn in enumerate(chunk):
        seq_map[seq] = fn
        cells.append(build_cell(seq, fn))
        seq += 1
    cw, ch = cells[0].width, cells[0].height
    rows = (len(cells) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * (cw + 10) + 10, rows * (ch + GAPY) + 10), (245, 245, 248))
    for k, c in enumerate(cells):
        r, col = divmod(k, COLS)
        sheet.paste(c, (10 + col * (cw + 10), 10 + r * (ch + GAPY)))
    path = os.path.join(OUT, f"sheet_{sheets:02d}.png")
    sheet.save(path)
    sheets += 1

json.dump(seq_map, open(os.path.join(OUT, "sheet_map.json"), "w"), indent=0)
print(f"wrote {sheets} sheets ({len(files)} captchas, seq {start}..{seq-1}) to {OUT}")
