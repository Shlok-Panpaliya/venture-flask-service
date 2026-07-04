#!/usr/bin/env python3
"""Score the shipped production OCR on the 200 fresh held-out captchas (labeled by eye)."""
import json
import os
import sys
import time

sys.path.insert(0, "/Users/utkarshchaudhary/Desktop/Personal/venture-flask-service")
from helpers.bhulekh_captcha import ocr_bhulekh_captcha_png  # noqa: E402

HARN = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HARN, "holdout200")

# Human ground-truth, in sheet sequence order 0..199.
LABELS = [
    "bfqzr0","6AoV4W","dmKluU","7h0v2M","WZS8Q3","zu9tPH","yyCu7m","7cyqxj","eJHTNK","wQO4FP",
    "hOD0r9","5cVBr3","45Av1f","9Ks9nk","rrbCxH","iqs6b0","1wGamQ","oqnSKX","Nmh3uy","0M7fCq",
    "uPOsRr","QLoJhJ","o6ciCb","4pL592","n58LAq","Vq8kTw","vG2qNB","EFA3xm","CKHleL","h7fOTY",
    "0o1hAO","AiFgkd","z2XdZK","IG98xn","s35Ev4","8EBTpW","of2scM","PBIYVV","flgbQ5","uTSTvz",
    "y1XCcm","ihavvu","VzeWCd","yS3woe","rm1olQ","cPkkOc","hiE9FF","QC68Ad","EQVt5w","0x6Tiv",
    "vZEefr","KagWTV","VfJY7P","xS3UAQ","n7C1yP","ReVek5","PNTMqx","7TubYz","sep43f","PMGot6",
    "btSOG5","qzRRpO","n8Ppwg","1VBdih","txqons","yci2Kx","7QeY9u","ucuHXp","Jn6qBT","WXJqBZ",
    "VctLiJ","OMGNFI","npDJ4F","GQfUOa","0Am68B","dq1lZ7","5Qeml5","iMZTx3","LtSKIL","yViJdk",
    "8Dovrv","Kr3gsk","Zlrl3i","jOPnqu","tXqLw2","eBhs8N","c67DnB","U5OSwG","nrsdX6","7tuNQZ",
    "h40B4T","mKSfqX","px2nwn","drmLxh","OpWCle","vvUkdF","6tuc2B","F6g8ry","AtNiUX","PKbJvU",
    "on7GUR","28RFPe","ul0DB9","JCo5c7","uNV2Ku","MAkU9X","W040wq","OAh2To","zLN0rM","AAPuTb",
    "d4CAdN","yRYAmH","FKGX84","uCWwcT","NqmlhT","yfAmJu","LlyVPm","o0nvBn","TofiYa","7AwZOe",
    "LPdcJA","QGrSwE","PLBsrz","PwYp7Q","VwTbGN","WEjQdW","hd4BUM","4k0dmK","xsqQrt","Py1eZv",
    "2CcNUe","FU0nGf","aiSb32","3a5wKe","vxoqoA","lWSDaH","wekoJ2","NPXzh4","5Nmswc","98Ytrw",
    "Bccmqk","yaq8UD","TNQwYm","yEnmUD","CN3Yre","FMa0dr","lJvoLR","zOh3w9","xmgBCB","tQp6PR",
    "fjl1Ve","yt2Wmt","oSw88A","6j1krc","iUEbri","JKvccS","nxh1YT","dWLdK0","Vngq3C","VSdD3C",
    "hzp3hB","DjlQne","PUlGmk","bBw6Aj","UxYwTW","hB0fU0","5mqx6N","cwSMqg","N7SWAL","XloJOE",
    "WNzjJz","T5LTh3","nsDHEQ","7Ap9j1","SVRcFx","zftwei","YTJ9mP","Rjl5VG","fXhJ3c","jY5tPW",
    "HCl6Xt","LD9QKd","99uEGL","NAe4EL","QB2Nrv","5HQQZP","gMfxZz","bPDvhC","K6sLvH","TL5VBf",
    "ironAr","E8zNOp","0nQOT3","ctvqT9","ljiedG","h69gqK","hEeRip","b8giJs","ig3X2g","AWqDtv",
]

# l (lowercase L) and I (capital i) render as an identical bar in this Arial captcha — the
# distinction is unrecoverable from pixels. Fold them so a mismatch there isn't a "real" error.
def fold(s):
    return s.replace("I", "l")

def main():
    smap = json.load(open(os.path.join(DS if os.path.exists(os.path.join(DS, "sheet_map.json"))
                                        else os.path.join(HARN, "sheets200"), "sheet_map.json")))
    assert len(LABELS) == 200, len(LABELS)
    strict = lenient = 0
    misses = []
    t0 = time.time()
    for seq in range(200):
        fn = smap[str(seq)]
        truth = LABELS[seq]
        png = open(os.path.join(DS, fn), "rb").read()
        pred = ocr_bhulekh_captcha_png(png)
        s_ok = pred == truth
        l_ok = fold(pred) == fold(truth)
        strict += s_ok
        lenient += l_ok
        if not s_ok:
            misses.append((seq, truth, pred, "l/I only" if l_ok else "real"))
    dt = (time.time() - t0) / 200 * 1000
    print(f"HELD-OUT 200 (fresh, never trained on):")
    print(f"  strict exact         {strict}/200 = {100*strict/200:.1f}%")
    print(f"  lenient (l/I folded) {lenient}/200 = {100*lenient/200:.1f}%")
    print(f"  5-retry end-to-end (strict)  {100*(1-(1-strict/200)**5):.2f}%")
    print(f"  ~{dt:.1f} ms/image")
    print(f"\nmisses ({len(misses)}):")
    for seq, t, p, kind in misses:
        d = [f"{a}->{b}" for a, b in zip(t, p) if a != b] if len(t) == len(p) else [f"len{len(t)}->{len(p)}"]
        print(f"  [{seq:3d}] {t} -> {p:8s} ({', '.join(d)})  [{kind}]")

if __name__ == "__main__":
    main()
