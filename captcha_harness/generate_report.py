#!/usr/bin/env python3
"""
Build the captcha verification report (HTML).

Reads:
  - captchas/manifest.json  (image list, from the scraper)
  - captchas/results.json   (OCR output, from run_ocr.py) -- optional
  - MY_READING below        (Claude's visual ground-truth reading of each image)

Writes: captcha_report.html
"""
import base64
import json
import os
import sys

CAP_DIR = sys.argv[1] if len(sys.argv) > 1 else "captchas"
OUT = sys.argv[2] if len(sys.argv) > 2 else "captcha_report.html"

# Claude's own visual reading of each scraped captcha (the ground truth column).
MY_READING = {
    1: "nzKlt0", 2: "nHL1ab", 3: "t14Ylw", 4: "GuaAyG", 5: "obeUXv",
    6: "aS6X94", 7: "SzagyT", 8: "FMb8wE", 9: "bmzQVn", 10: "IgM778",
    11: "vaZpiS", 12: "E3cGuC", 13: "aEAoTm", 14: "GfZ5j5", 15: "Q9bmvP",
    16: "oGEiO6", 17: "IbnneK", 18: "L3XbQ0", 19: "PqcUtO", 20: "9VVZSr",
}


def load(path, default):
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return default


def data_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def char_diff(ocr, truth):
    """HTML for the OCR string, per-char colored vs the truth reading."""
    if ocr is None:
        return '<span class="err">— none —</span>'
    out = []
    n = max(len(ocr), len(truth))
    for i in range(len(ocr)):
        t = truth[i] if i < len(truth) else ""
        cls = "ok" if (i < len(truth) and ocr[i] == t) else "bad"
        out.append(f'<span class="{cls}">{ocr[i]}</span>')
    if len(ocr) < len(truth):
        out.append(f'<span class="miss">{"·" * (len(truth) - len(ocr))}</span>')
    return "".join(out)


def main():
    manifest = load(os.path.join(CAP_DIR, "manifest.json"), [])
    results = {r["index"]: r for r in load(os.path.join(CAP_DIR, "results.json"), [])}
    have_ocr = bool(results)

    rows = []
    exact = ci = 0
    scored = 0
    for e in manifest:
        idx = e["index"]
        img = data_uri(os.path.join(CAP_DIR, e["file"]))
        truth = MY_READING.get(idx, "")
        rec = results.get(idx, {})
        ocr = rec.get("inferred")
        err = rec.get("error")
        ms = rec.get("ms")

        status = "pending"
        if have_ocr:
            scored += 1
            if ocr is not None and truth:
                if ocr == truth:
                    exact += 1
                    ci += 1
                    status = "exact"
                elif ocr.lower() == truth.lower():
                    ci += 1
                    status = "case"
                else:
                    status = "wrong"
            else:
                status = "wrong"

        badge = {
            "exact": '<span class="pill pill-ok">exact</span>',
            "case": '<span class="pill pill-warn">case-only</span>',
            "wrong": '<span class="pill pill-bad">mismatch</span>',
            "pending": '<span class="pill pill-pend">pending OCR</span>',
        }[status]

        ocr_cell = char_diff(ocr, truth) if have_ocr else '<span class="pend">⏳</span>'
        meta = f'<span class="ms">{ms} ms</span>' if ms else ""
        if err:
            ocr_cell = f'<span class="err" title="{err}">{err[:48]}…</span>'

        rows.append(f"""
        <tr>
          <td class="idx">{idx:02d}</td>
          <td class="imgcell"><img src="{img}" alt="captcha {idx}"></td>
          <td class="mono ocr">{ocr_cell} {meta}</td>
          <td class="mono truth">{truth}</td>
          <td class="statuscell">{badge}</td>
          <td class="manual"></td>
        </tr>""")

    exact_pct = f"{100*exact/scored:.0f}%" if scored else "—"
    ci_pct = f"{100*ci/scored:.0f}%" if scored else "—"

    tiles = f"""
      <div class="tile"><div class="tnum">{len(manifest)}</div><div class="tlbl">captchas harvested</div></div>
      <div class="tile"><div class="tnum">{exact if have_ocr else '—'}<span class="tden">/{scored or len(manifest)}</span></div><div class="tlbl">exact match</div></div>
      <div class="tile"><div class="tnum accent">{exact_pct}</div><div class="tlbl">exact accuracy</div></div>
      <div class="tile"><div class="tnum accent">{ci_pct}</div><div class="tlbl">case-insensitive accuracy</div></div>
    """

    note = "" if have_ocr else """
      <div class="banner">⏳ <strong>OCR column pending</strong> — the tesseract binary is not yet installed.
      Run <code>brew install tesseract</code>, then <code>run_ocr.py</code> + this script to fill the inferred column and accuracy tiles.</div>"""

    html = f"""<title>Bhulekh Captcha OCR — Verification Harness</title>
<style>
  :root {{
    --bg:#f7f8fa; --panel:#ffffff; --ink:#1a1d24; --muted:#5b6470; --line:#e4e7ec;
    --accent:#4f46e5; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --pend:#6b7280;
    --okbg:#e9f6ee; --badbg:#fdeaea; --warnbg:#fdf3e5;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0e1116; --panel:#161a22; --ink:#e8ebf0; --muted:#9aa3b2; --line:#252b36;
      --accent:#8b85f5; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --pend:#8b93a1;
      --okbg:#132a1c; --badbg:#2c1618; --warnbg:#2c2210; }}
  }}
  :root[data-theme="dark"] {{ --bg:#0e1116; --panel:#161a22; --ink:#e8ebf0; --muted:#9aa3b2; --line:#252b36;
    --accent:#8b85f5; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --pend:#8b93a1;
    --okbg:#132a1c; --badbg:#2c1618; --warnbg:#2c2210; }}
  :root[data-theme="light"] {{ --bg:#f7f8fa; --panel:#ffffff; --ink:#1a1d24; --muted:#5b6470; --line:#e4e7ec;
    --accent:#4f46e5; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --pend:#6b7280;
    --okbg:#e9f6ee; --badbg:#fdeaea; --warnbg:#fdf3e5; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.5; -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:940px; margin:0 auto; padding:40px 24px 80px; }}
  header h1 {{ font-size:1.5rem; font-weight:650; letter-spacing:-0.02em; margin:0 0 4px; text-wrap:balance; }}
  header p {{ color:var(--muted); margin:0 0 24px; font-size:.92rem; }}
  header .src {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.78rem; }}
  .tiles {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:0 0 20px; }}
  .tile {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .tnum {{ font-size:1.7rem; font-weight:680; letter-spacing:-0.02em; font-variant-numeric:tabular-nums; }}
  .tnum.accent {{ color:var(--accent); }}
  .tden {{ font-size:1rem; color:var(--muted); font-weight:500; }}
  .tlbl {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-top:2px; }}
  .banner {{ background:var(--warnbg); border:1px solid var(--warn); border-radius:10px;
    padding:12px 16px; font-size:.88rem; margin:0 0 20px; }}
  .banner code {{ background:rgba(128,128,128,.15); padding:1px 6px; border-radius:5px; font-size:.85em; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--panel); }}
  table {{ border-collapse:collapse; width:100%; min-width:720px; }}
  thead th {{ position:sticky; top:0; background:var(--panel); text-align:left;
    font-size:.7rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted);
    padding:12px 14px; border-bottom:1px solid var(--line); }}
  tbody td {{ padding:10px 14px; border-bottom:1px solid var(--line); vertical-align:middle; }}
  tbody tr:last-child td {{ border-bottom:none; }}
  tbody tr:nth-child(even) {{ background:color-mix(in srgb, var(--line) 25%, transparent); }}
  .idx {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.85rem; }}
  .imgcell img {{ display:block; height:36px; width:auto; border-radius:4px;
    background:#fff; border:1px solid var(--line); image-rendering:auto; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:1rem; letter-spacing:.04em; }}
  .truth {{ font-weight:600; }}
  .ocr .ok {{ color:var(--ok); }}
  .ocr .bad {{ color:var(--bad); font-weight:700; text-decoration:underline wavy var(--bad); text-underline-offset:3px; }}
  .ocr .miss {{ color:var(--bad); opacity:.6; }}
  .ms {{ font-size:.7rem; color:var(--muted); margin-left:6px; }}
  .pend {{ font-size:1.1rem; }}
  .err {{ color:var(--bad); font-size:.8rem; font-family:ui-monospace,monospace; }}
  .pill {{ display:inline-block; font-size:.72rem; font-weight:600; padding:3px 9px; border-radius:999px; white-space:nowrap; }}
  .pill-ok {{ background:var(--okbg); color:var(--ok); }}
  .pill-warn {{ background:var(--warnbg); color:var(--warn); }}
  .pill-bad {{ background:var(--badbg); color:var(--bad); }}
  .pill-pend {{ background:color-mix(in srgb,var(--pend) 15%,transparent); color:var(--pend); }}
  .manual {{ min-width:120px; }}
  .manual::after {{ content:""; display:block; height:20px; border-bottom:1px dashed var(--line); }}
  .legend {{ margin-top:16px; font-size:.8rem; color:var(--muted); display:flex; gap:18px; flex-wrap:wrap; }}
  .legend b {{ font-family:ui-monospace,monospace; font-weight:600; }}
  .legend .ok {{ color:var(--ok); }} .legend .bad {{ color:var(--bad); }}
</style>
<div class="wrap">
  <header>
    <h1>Bhulekh Captcha OCR — Verification Harness</h1>
    <p>Live captchas harvested via Puppeteer (refresh-button loop) from
      <span class="src">bhulekh.mahabhumi.gov.in/NewBhulekh.aspx</span>,
      run through <span class="src">helpers/bhulekh_captcha.ocr_bhulekh_captcha_png</span>.
      "My reading" is Claude's visual transcription of each image (ground truth for scoring).</p>
  </header>
  {note}
  <div class="tiles">{tiles}</div>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>#</th><th>Captcha image</th><th>OCR inferred</th>
        <th>My reading</th><th>Match</th><th>Your manual pass</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <div class="legend">
    <span>In the OCR column: <b class="ok">green</b> = char matches my reading,
      <b class="bad">red underline</b> = differs. Case-only differences count separately —
      the site's captcha field may or may not be case-sensitive.</span>
  </div>
</div>"""

    with open(OUT, "w") as f:
        f.write(html)
    print(f"Wrote {OUT}  (OCR={'yes' if have_ocr else 'PENDING'}, rows={len(manifest)})")


if __name__ == "__main__":
    main()
