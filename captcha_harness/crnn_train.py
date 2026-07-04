#!/usr/bin/env python3
"""
CRNN + CTC reader for the Bhulekh captcha — the path to 90% single-read.

Reads the WHOLE 140x40 strip (raw, with noise + strike line): CNN feature extractor ->
BiLSTM -> CTC over 63 classes (62 chars + blank). No segmentation, so the ~20% "doesn't
split into 6 glyphs" ceiling of the per-char CNN disappears.

Trained on Arial synthetic (unlimited, perfectly labeled, balanced) MIXED with the 1500
real hand-labels (oversampled) so it fits the true on-site distribution, not just my
render. Evaluates full-string exact on the real gold-50 every few epochs. Exports ONNX
for CPU inference (greedy CTC decode in numpy at serve time).
"""
import argparse
import io
import json
import os
import string
import sys

import numpy as np
import torch
import torch.nn as nn
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import synth_captcha as G  # noqa: E402

CHARS = string.digits + string.ascii_uppercase + string.ascii_lowercase
C2I = {c: i for i, c in enumerate(CHARS)}
NCLASS = len(CHARS)          # 62
BLANK = NCLASS               # 62 -> CTC blank index; logits dim = 63
IW, IH = 140, 40          # native resolution: keep 40px height so g/q & v/y descenders survive
DEV = "cpu"
torch.set_num_threads(max(1, os.cpu_count() - 1))


def prep(png_or_arr):
    if isinstance(png_or_arr, (bytes, bytearray)):
        im = Image.open(io.BytesIO(png_or_arr)).convert("L")
    else:
        im = Image.fromarray(png_or_arr, "L")
    im = im.resize((IW, IH), Image.BILINEAR)
    a = np.asarray(im, np.float32) / 255.0
    return 1.0 - a               # ink -> ~1, background -> ~0


class CRNN(nn.Module):
    def __init__(self, nclass=NCLASS + 1, nh=128):
        super().__init__()

        def cbr(i, o, k=3, s=1, p=1, bn=True):
            layers = [nn.Conv2d(i, o, k, s, p)]
            if bn:
                layers.append(nn.BatchNorm2d(o))
            layers.append(nn.ReLU(inplace=True))
            return layers

        self.cnn = nn.Sequential(
            *cbr(1, 64, bn=False), nn.MaxPool2d(2, 2),          # 32x128 -> 16x64
            *cbr(64, 128, bn=False), nn.MaxPool2d(2, 2),        # -> 8x32
            *cbr(128, 256), *cbr(256, 256), nn.MaxPool2d((2, 1), (2, 1)),  # -> 4x32
            *cbr(256, 256), nn.MaxPool2d((2, 1), (2, 1)),       # -> 2x32
            *cbr(256, 256, k=2, p=0),                           # -> 1x31
        )
        self.rnn = nn.LSTM(256, nh, num_layers=2, bidirectional=True, batch_first=True)
        self.fc = nn.Linear(nh * 2, nclass)

    def forward(self, x):                    # x: (N,1,32,128)
        f = self.cnn(x)                      # (N,256,1,W')
        f = f.squeeze(2).permute(0, 2, 1)    # (N, W', 256)
        r, _ = self.rnn(f)
        return self.fc(r)                    # (N, T, nclass) logits


def greedy_decode(logits):                   # logits: (T, nclass) numpy
    idx = logits.argmax(-1)
    out, prev = [], -1
    for i in idx:
        if i != prev and i != BLANK:
            out.append(CHARS[i])
        prev = i
    return "".join(out)


def read_string(model, x1):                  # x1: (1,32,128) tensor
    with torch.no_grad():
        lg = model(x1.unsqueeze(0).to(DEV))[0].cpu().numpy()
    return greedy_decode(lg)


def build_real(ds_dir):
    labels = json.load(open(os.path.join(ds_dir, "labels.json")))
    X, Y = [], []
    for f, lab in labels.items():
        if len(lab) != 6 or any(c not in C2I for c in lab):
            continue
        a = prep(open(os.path.join(ds_dir, f), "rb").read())
        X.append(a); Y.append([C2I[c] for c in lab])
    return X, Y


# glyph pairs the epoch-10 model confused; oversample so it gets more signal on the
# features that separate them (g's left hook vs q's straight descender, v vs y, l vs I...)
HARD = "gqvyilIjO0"

def gen_text(rng, dup_frac=0.4, hard_frac=0.55):
    """Random 6-char string, biased for two known failure modes:
    - with prob dup_frac inject an adjacent-duplicate pair (CTC blank between repeats),
    - with prob hard_frac replace 1-2 positions with confusable glyphs (g/q, v/y, ...)."""
    t = list(G.ALPHABET[i] for i in rng.integers(0, len(G.ALPHABET), 6))
    if rng.random() < hard_frac:
        for _ in range(1 + int(rng.random() < 0.4)):  # 1, sometimes 2 hard chars
            t[int(rng.integers(0, 6))] = HARD[int(rng.integers(0, len(HARD)))]
    if rng.random() < dup_frac:
        npairs = 1 + int(rng.random() < 0.3)          # usually 1, sometimes 2 dup pairs
        for _ in range(npairs):
            p = int(rng.integers(0, 5))               # duplicate char at p into p+1
            t[p + 1] = t[p]
    return "".join(t)


class DS(torch.utils.data.Dataset):
    """Mix: on-the-fly synthetic (majority) + oversampled real, with light aug."""
    def __init__(self, realX, realY, n_synth, real_repeat, seed):
        self.realX, self.realY = realX, realY
        self.n_real = len(realX) * real_repeat
        self.n_synth = n_synth
        self.rng = np.random.default_rng(seed)
        # pre-render synthetic once (fast: raw render, no opencv)
        self.synX, self.synY = [], []
        for _ in range(n_synth):
            t = gen_text(self.rng)
            a = prep(G.render(t, self.rng))
            self.synX.append(a); self.synY.append([C2I[c] for c in t])

    def __len__(self):
        return self.n_synth + self.n_real

    def __getitem__(self, i):
        if i < self.n_synth:
            a, y = self.synX[i], self.synY[i]
        else:
            j = (i - self.n_synth) % len(self.realX)
            a, y = self.realX[j], self.realY[j]
        a = a.copy()
        # light aug: small shift + gaussian noise
        dx = np.random.randint(-2, 3)
        a = np.roll(a, dx, axis=1)
        a = np.clip(a + np.random.normal(0, 0.04, a.shape).astype(np.float32), 0, 1)
        return torch.from_numpy(a[None]), torch.tensor(y, dtype=torch.long)


def collate(batch):
    xs = torch.stack([b[0] for b in batch])
    ys = torch.cat([b[1] for b in batch])
    yl = torch.tensor([len(b[1]) for b in batch], dtype=torch.long)
    return xs, ys, yl


def gold_eval(model, gold_dir, truth, ddddocr_read=None):
    model.eval()
    mo = ens = 0
    for i in sorted(truth):
        t = truth[i]
        b = open(os.path.join(gold_dir, f"cap_{i:02d}.png"), "rb").read()
        pred = read_string(model, torch.from_numpy(prep(b)[None]))
        mo += pred == t
        p2 = pred
        if len(pred) != 6 and ddddocr_read is not None:
            g = ddddocr_read(b)
            if g:
                p2 = g
        ens += p2 == t
    n = len(truth)
    return mo / n, ens / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds-dir", required=True)          # 1500 real + labels.json
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--gold-truth", required=True)
    ap.add_argument("--n-synth", type=int, default=30000)
    ap.add_argument("--real-repeat", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--bs", type=int, default=128)
    ap.add_argument("--out", default="crnn")
    args = ap.parse_args()

    print(f"threads={torch.get_num_threads()}  building data ...", flush=True)
    realX, realY = build_real(args.ds_dir)
    ds = DS(realX, realY, args.n_synth, args.real_repeat, seed=1)
    print(f"real={len(realX)} (x{args.real_repeat}) + synth={args.n_synth} -> {len(ds)} samples/epoch",
          flush=True)
    truth = {int(k): v for k, v in json.load(open(args.gold_truth)).items()}

    # optional ddddocr fallback for gold ensemble number
    try:
        sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
        from helpers.bhulekh_captcha import _ddddocr_read, _correct_case
        ddread = lambda b: (_correct_case(_ddddocr_read(b), b) if _ddddocr_read(b) else None)  # noqa: E731
    except Exception as e:
        print("ddddocr fallback unavailable:", e, flush=True)
        ddread = None

    dl = torch.utils.data.DataLoader(ds, batch_size=args.bs, shuffle=True,
                                     collate_fn=collate, num_workers=0)
    model = CRNN().to(DEV)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    ctc = nn.CTCLoss(blank=BLANK, zero_infinity=True)

    best = -1.0
    for ep in range(1, args.epochs + 1):
        model.train(); tot = 0.0; nb = 0
        for xs, ys, yl in dl:
            xs = xs.to(DEV)
            logits = model(xs)                       # (N,T,C)
            logp = logits.log_softmax(2).permute(1, 0, 2)   # (T,N,C)
            T = logp.size(0)
            inl = torch.full((xs.size(0),), T, dtype=torch.long)
            loss = ctc(logp, ys, inl, yl)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item(); nb += 1
        sched.step()
        msg = f"ep {ep:3d}  ctc_loss {tot/max(1,nb):.3f}"
        if ep % 5 == 0 or ep == 1 or ep == args.epochs:
            mo, ens = gold_eval(model, args.gold_dir, truth, ddread)
            msg += (f"  | REAL gold: model-only {100*mo:.0f}%  ensemble {100*ens:.0f}%"
                    f"  (5-retry mo {100*(1-(1-mo)**5):.1f}%)")
            if mo >= best:
                best = mo; torch.save(model.state_dict(), args.out + ".pt")
                msg += "  *saved"
        print(msg, flush=True)

    model.load_state_dict(torch.load(args.out + ".pt", map_location=DEV, weights_only=True))
    mo, ens = gold_eval(model, args.gold_dir, truth, ddread)
    print(f"\n=== BEST CRNN on REAL gold ({len(truth)}) ===", flush=True)
    print(f"  model-only exact {100*mo:.0f}%  |  + ddddocr fallback {100*ens:.0f}%", flush=True)
    print(f"  5-retry: model-only {100*(1-(1-mo)**5):.1f}%  |  ensemble {100*(1-(1-ens)**5):.1f}%",
          flush=True)
    model.eval()
    torch.onnx.export(model, torch.zeros(1, 1, IH, IW), args.out + ".onnx",
                      input_names=["img"], output_names=["logits"],
                      dynamic_axes={"img": {0: "b"}, "logits": {0: "b"}},
                      opset_version=17, dynamo=False)
    print(f"exported {args.out}.pt / {args.out}.onnx", flush=True)


if __name__ == "__main__":
    main()
