#!/usr/bin/env python3
"""
Per-character CNN for the Bhulekh captcha font (data-efficient).

Cleans each captcha (denoise + inpaint strike line), segments into glyph boxes, and trains
ONE 62-way classifier on individual character crops — so N captchas yield ~6N training
samples. Inference: clean -> segment -> if exactly 6 boxes, classify each; else the caller
falls back to ddddocr. Reports full-string exact on a held-out gold set, model-only and
with the ddddocr fallback (the deployed ensemble). Exports ONNX for CPU inference.
"""
import argparse
import json
import os
import string
import sys

import numpy as np
import torch
import torch.nn as nn
from PIL import Image

sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
from helpers.bhulekh_captcha import _clean_binary, _char_boxes, _ddddocr_read, _correct_case  # noqa: E402

CHARS = string.digits + string.ascii_uppercase + string.ascii_lowercase
C2I = {c: i for i, c in enumerate(CHARS)}
NCLASS = len(CHARS)
S = 32
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def norm_crop(bw, box):
    x, y, w, h = box
    crop = bw[y:y + h, x:x + w]
    scale = 24.0 / max(w, h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    im = Image.fromarray(crop).resize((nw, nh), Image.LANCZOS)
    canvas = np.zeros((S, S), np.float32)
    ox, oy = (S - nw) // 2, (S - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = np.asarray(im, np.float32) / 255.0
    return canvas


def crops_of(png_bytes):
    bw = _clean_binary(png_bytes)
    if bw is None:
        return None
    boxes = _char_boxes(bw)
    return [norm_crop(bw, b) for b in boxes]


class CharDS(torch.utils.data.Dataset):
    def __init__(self, X, Y, aug=False):
        self.X, self.Y, self.aug = X, Y, aug

    def __len__(self):
        return len(self.X)

    def __getitem__(self, i):
        a = self.X[i].copy()
        if self.aug:
            dx, dy = np.random.randint(-2, 3), np.random.randint(-2, 3)
            a = np.roll(a, (dy, dx), axis=(0, 1))
            a = np.clip(a + np.random.normal(0, 0.05, a.shape).astype(np.float32), 0, 1)
        return torch.from_numpy(a[None]), self.Y[i]


class CharCNN(nn.Module):
    def __init__(self):
        super().__init__()
        def blk(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o),
                                 nn.ReLU(inplace=True), nn.MaxPool2d(2))
        self.feat = nn.Sequential(blk(1, 32), blk(32, 64), blk(64, 128))  # 32->4
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(128 * 4 * 4, 256),
                                  nn.ReLU(inplace=True), nn.Dropout(0.3), nn.Linear(256, NCLASS))

    def forward(self, x):
        return self.head(self.feat(x))


def build_char_set(cap_dir, labels):
    """From captchas that segment into exactly 6 boxes, collect (crop, class) pairs."""
    X, Y, used = [], [], 0
    for f, lab in labels.items():
        if len(lab) != 6 or any(c not in C2I for c in lab):
            continue
        crops = crops_of(open(os.path.join(cap_dir, f), "rb").read())
        if crops is None or len(crops) != 6:
            continue
        used += 1
        for cr, ch in zip(crops, lab):
            X.append(cr); Y.append(C2I[ch])
    return X, torch.tensor(Y), used


@torch.no_grad()
def read_string(model, png_bytes):
    crops = crops_of(png_bytes)
    if crops is None or len(crops) != 6:
        return None
    x = torch.from_numpy(np.stack(crops)[:, None]).to(DEV)
    idx = model(x).argmax(-1).cpu().numpy()
    return "".join(CHARS[j] for j in idx)


@torch.no_grad()
def gold_eval(model, gold_dir, truth):
    model.eval()
    model_only = ens = 0
    for i in sorted(truth):
        t = truth[i]
        b = open(os.path.join(gold_dir, f"cap_{i:02d}.png"), "rb").read()
        pred = read_string(model, b)
        model_only += pred == t
        if pred is None:                       # deployed fallback: ddddocr + case-correction
            g = _ddddocr_read(b)
            pred = _correct_case(g, b) if g else ""
        ens += pred == t
    n = len(truth)
    return model_only / n, ens / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--gold-truth", required=True)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--out", default="char_cnn")
    args = ap.parse_args()

    labels = json.load(open(args.labels))
    caps = [(f, l) for f, l in labels.items() if len(l) == 6]
    rng = np.random.default_rng(0)
    rng.shuffle(caps)
    nval = max(24, len(caps) // 8)
    val_caps, train_caps = dict(caps[:nval]), dict(caps[nval:])

    Xtr, Ytr, used_tr = build_char_set(args.dir, train_caps)
    print(f"train captchas used {used_tr}/{len(train_caps)} -> {len(Xtr)} char samples; "
          f"val captchas {len(val_caps)}")

    truth = {int(k): v for k, v in json.load(open(args.gold_truth)).items()}
    model = CharCNN().to(DEV)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.CrossEntropyLoss()
    dl = torch.utils.data.DataLoader(CharDS(Xtr, Ytr, aug=True), batch_size=128, shuffle=True)

    best = -1.0
    for ep in range(1, args.epochs + 1):
        model.train()
        tot = 0.0
        for x, y in dl:
            x, y = x.to(DEV), y.to(DEV)
            opt.zero_grad(); out = model(x); loss = lossf(out, y); loss.backward(); opt.step()
            tot += loss.item() * len(x)
        sched.step()
        # val full-string exact
        model.eval()
        vex = sum(read_string(model, open(os.path.join(args.dir, f), "rb").read()) == l
                  for f, l in val_caps.items()) / len(val_caps)
        if vex >= best:
            best = vex; torch.save(model.state_dict(), args.out + ".pt")
        if ep % 5 == 0 or ep == 1:
            print(f"ep {ep:3d}  loss {tot/max(1,len(Xtr)):.3f}  val_exact {vex:.3f}  best {best:.3f}")

    model.load_state_dict(torch.load(args.out + ".pt", map_location=DEV, weights_only=True))
    mo, ens = gold_eval(model, args.gold_dir, truth)
    print(f"\nGOLD ({len(truth)}): model-only exact {100*mo:.0f}%  |  model+ddddocr-fallback {100*ens:.0f}%")
    print(f"  5-retry: model-only {100*(1-(1-mo)**5):.1f}%  |  ensemble {100*(1-(1-ens)**5):.1f}%")

    model.eval()
    torch.onnx.export(model, torch.zeros(1, 1, S, S, device=DEV), args.out + ".onnx",
                      input_names=["c"], output_names=["logits"],
                      dynamic_axes={"c": {0: "b"}, "logits": {0: "b"}}, opset_version=17, dynamo=False)
    print(f"exported {args.out}.pt / {args.out}.onnx")


if __name__ == "__main__":
    main()
