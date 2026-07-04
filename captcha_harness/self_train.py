#!/usr/bin/env python3
"""
Self-training round: use the seed per-char CNN to pseudo-label the remaining harvested
captchas (those segmenting into 6 glyphs) at high per-character confidence, then retrain
on hand-labels + high-confidence pseudo-labels. Expands ~240 hand-labels into ~1000s.
"""
import argparse
import json
import os
import sys

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
import train_char_cnn as T  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--seed-model", required=True)
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--gold-truth", required=True)
    ap.add_argument("--conf", type=float, default=0.99)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--out", default="char_cnn")
    args = ap.parse_args()

    hand = json.load(open(args.labels))
    manifest = [e["file"] for e in json.load(open(os.path.join(args.dir, "manifest.json")))]
    unlabeled = [f for f in manifest if f not in hand]

    seed = T.CharCNN().to(T.DEV)
    seed.load_state_dict(torch.load(args.seed_model, map_location=T.DEV, weights_only=True))
    seed.eval()

    # pseudo-label high-confidence, cleanly-segmented captchas
    pseudo = {}
    with torch.no_grad():
        for f in unlabeled:
            crops = T.crops_of(open(os.path.join(args.dir, f), "rb").read())
            if crops is None or len(crops) != 6:
                continue
            x = torch.from_numpy(np.stack(crops)[:, None]).to(T.DEV)
            prob = torch.softmax(seed(x), -1)
            conf, idx = prob.max(-1)
            if float(conf.min()) >= args.conf:
                pseudo[f] = "".join(T.CHARS[j] for j in idx.cpu().numpy())
    print(f"pseudo-labeled {len(pseudo)}/{len(unlabeled)} unlabeled captchas at conf>={args.conf}")

    combined = {**hand, **pseudo}
    caps = [(f, l) for f, l in combined.items() if len(l) == 6]
    rng = np.random.default_rng(0); rng.shuffle(caps)
    nval = max(40, len(caps) // 10)
    val_caps, train_caps = dict(caps[:nval]), dict(caps[nval:])
    Xtr, Ytr, used = T.build_char_set(args.dir, train_caps)
    print(f"combined train: {used} captchas -> {len(Xtr)} char samples; val {len(val_caps)}")

    truth = {int(k): v for k, v in json.load(open(args.gold_truth)).items()}
    model = T.CharCNN().to(T.DEV)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.CrossEntropyLoss()
    dl = torch.utils.data.DataLoader(T.CharDS(Xtr, Ytr, aug=True), batch_size=128, shuffle=True)

    best = -1.0
    for ep in range(1, args.epochs + 1):
        model.train(); tot = 0.0
        for x, y in dl:
            x, y = x.to(T.DEV), y.to(T.DEV)
            opt.zero_grad(); loss = lossf(model(x), y); loss.backward(); opt.step()
            tot += loss.item() * len(x)
        sched.step()
        model.eval()
        vex = sum(T.read_string(model, open(os.path.join(args.dir, f), "rb").read()) == l
                  for f, l in val_caps.items()) / len(val_caps)
        if vex >= best:
            best = vex; torch.save(model.state_dict(), args.out + ".pt")
        if ep % 5 == 0 or ep == 1:
            print(f"ep {ep:3d}  loss {tot/max(1,len(Xtr)):.3f}  val_exact {vex:.3f}  best {best:.3f}")

    model.load_state_dict(torch.load(args.out + ".pt", map_location=T.DEV, weights_only=True))
    mo, ens = T.gold_eval(model, args.gold_dir, truth)
    print(f"\nGOLD ({len(truth)}): model-only {100*mo:.0f}%  |  model+ddddocr-fallback {100*ens:.0f}%")
    print(f"  5-retry: model-only {100*(1-(1-mo)**5):.1f}%  |  ensemble {100*(1-(1-ens)**5):.1f}%")
    model.eval()
    torch.onnx.export(model, torch.zeros(1, 1, T.S, T.S, device=T.DEV), args.out + ".onnx",
                      input_names=["c"], output_names=["logits"],
                      dynamic_axes={"c": {0: "b"}, "logits": {0: "b"}}, opset_version=17, dynamo=False)
    print(f"exported {args.out}.pt / {args.out}.onnx")


if __name__ == "__main__":
    main()
