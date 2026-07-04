#!/usr/bin/env python3
"""
SPIKE: train the per-character CNN on PURE SYNTHETIC captchas (Arial generator),
evaluate on the REAL human-labeled gold-50 set. If synthetic transfers, this
validates the font->synthetic->train pipeline as the path to 90% single-read.

Two feeding modes for training crops:
  --mode pipeline : render full synthetic PNG -> real _clean_binary/_char_boxes
                    segmentation -> crops (mirrors deployment exactly)
  --mode direct   : segment by known glyph x-order (keeps every sample, balanced)
"""
import argparse
import json
import os
import sys

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train_char_cnn as T          # noqa: E402  (CharCNN, crops_of, gold_eval, CHARS, C2I)
import synth_captcha as G           # noqa: E402


def gen_pipeline_samples(n, seed):
    """Render n synthetic captchas, segment with the real pipeline, keep 6-box ones."""
    rng = np.random.default_rng(seed)
    X, Y, kept = [], [], 0
    for _ in range(n):
        text = G.random_text(rng)
        png = G.render_png(text, rng)
        crops = T.crops_of(png)
        if crops is None or len(crops) != 6:
            continue
        kept += 1
        for cr, ch in zip(crops, text):
            X.append(cr); Y.append(T.C2I[ch])
    return X, torch.tensor(Y), kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000, help="synthetic captchas to render")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--gold-truth", required=True)
    ap.add_argument("--out", default="char_cnn_synth")
    args = ap.parse_args()

    print(f"rendering + segmenting {args.n} synthetic captchas ...")
    Xtr, Ytr, kept = gen_pipeline_samples(args.n, seed=1)
    print(f"  segmented cleanly: {kept}/{args.n} ({100*kept/args.n:.1f}%) -> {len(Xtr)} char samples")

    # small synthetic val set (full-string exact) rendered independently
    rng = np.random.default_rng(999)
    val = [(G.random_text(rng)) for _ in range(300)]
    val_png = [(t, G.render_png(t, rng)) for t in val]

    truth = {int(k): v for k, v in json.load(open(args.gold_truth)).items()}
    dev = T.DEV
    model = T.CharCNN().to(dev)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.CrossEntropyLoss()
    dl = torch.utils.data.DataLoader(T.CharDS(Xtr, Ytr, aug=True), batch_size=256, shuffle=True)

    best = -1.0
    for ep in range(1, args.epochs + 1):
        model.train(); tot = 0.0
        for x, y in dl:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad(); loss = lossf(model(x), y); loss.backward(); opt.step()
            tot += loss.item() * len(x)
        sched.step()
        model.eval()
        vex = sum(T.read_string(model, p) == t for t, p in val_png) / len(val_png)
        if vex >= best:
            best = vex; torch.save(model.state_dict(), args.out + ".pt")
        if ep % 5 == 0 or ep == 1:
            print(f"ep {ep:3d}  loss {tot/max(1,len(Xtr)):.3f}  synth_val_exact {vex:.3f}  best {best:.3f}")

    model.load_state_dict(torch.load(args.out + ".pt", map_location=dev, weights_only=True))
    mo, ens = T.gold_eval(model, args.gold_dir, truth)
    n = len(truth)
    print(f"\n=== REAL GOLD ({n}) — trained on synthetic only ===")
    print(f"  model-only exact         {100*mo:.0f}%")
    print(f"  model + ddddocr fallback {100*ens:.0f}%")
    print(f"  5-retry: model-only {100*(1-(1-mo)**5):.1f}%  |  ensemble {100*(1-(1-ens)**5):.1f}%")

    model.eval()
    torch.onnx.export(model, torch.zeros(1, 1, T.S, T.S, device=dev), args.out + ".onnx",
                      input_names=["c"], output_names=["logits"],
                      dynamic_axes={"c": {0: "b"}, "logits": {0: "b"}},
                      opset_version=17, dynamo=False)
    print(f"exported {args.out}.pt / {args.out}.onnx")


if __name__ == "__main__":
    main()
