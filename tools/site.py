#!/usr/bin/env python3
"""Render data/census.json into one self-contained page. No runtime fetches.

    python tools/site.py [--in data/census.json] [--out site]
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from census import human

ROOT = Path(__file__).resolve().parent.parent

REPO = "https://github.com/Booyaka101/npm-install-census"
LENS = "https://github.com/Booyaka101/npm-script-lens"

# The three dashboards share a palette; changing it here only changes this one.
CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#262d36;--fg:#e6edf3;--muted:#8b949e;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 20px 80px}
h1{font-size:30px;margin:0 0 8px;letter-spacing:-.02em}
h2{font-size:19px;margin:38px 0 6px;letter-spacing:-.01em}
.lede{color:var(--muted);max-width:70ch;margin:0 0 6px}
.meta{color:var(--muted);font-size:13px;margin:18px 0 28px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
h1 code{font-size:.82em}
a{color:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card b{display:block;font-size:28px;line-height:1.2}
.card span{color:var(--muted);font-size:12.5px}
.card.high b{color:var(--bad)}
.note{color:var(--muted);font-size:13.5px;max-width:74ch;margin:8px 0 0}
figure{margin:14px 0 0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 18px 12px}
figcaption{color:var(--muted);font-size:12.5px;margin-top:10px}
.bar{fill:var(--accent)}
.bar:hover{fill:#79bbff}
.axis{stroke:var(--line);stroke-width:1}
.tick{fill:var(--muted);font-size:11px}
.val{fill:var(--fg);font-size:12px;font-weight:600}
.sub{fill:var(--muted);font-size:10.5px}
details{margin-top:12px}
summary{cursor:pointer;color:var(--muted);font-size:12.5px}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0;align-items:center}
input[type=search]{background:var(--panel);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 12px;min-width:300px;flex:1;max-width:420px;font-size:14px}
input[type=search]:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button{background:var(--panel);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 13px;cursor:pointer;font-size:13.5px}
button[aria-pressed=true]{border-color:var(--accent);color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:11px 14px;border-bottom:1px solid var(--line);font-weight:600}
td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:hover{background:#1c2230}
.pkg{font-weight:600;font-size:14px}
.dim{color:var(--muted);font-size:11.5px}
.num{text-align:right;white-space:nowrap}
.pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11.5px;font-weight:600;border:1px solid}
.pill.HIGH{color:var(--bad);border-color:#8b2d28;background:#2b1210}
.pill.MEDIUM{color:var(--warn);border-color:#8a6415;background:#2a2009}
.pill.LOW{color:var(--warn);border-color:#8a6415;background:#2a2009}
.pill.SAFE{color:var(--ok);border-color:#23823a;background:#0f2a17}
.empty{padding:26px;text-align:center;color:var(--muted)}
footer{color:var(--muted);font-size:13px;margin-top:38px}
@media(max-width:720px){.d{display:none}}
"""

JS = """
const q = document.getElementById('q');
const rows = [...document.querySelectorAll('#queue tbody tr')];
const buttons = [...document.querySelectorAll('button[data-risk]')];
let risk = 'all';
function apply() {
  const needle = q.value.trim().toLowerCase();
  let shown = 0;
  for (const tr of rows) {
    const hit = (!needle || tr.dataset.search.includes(needle)) &&
                (risk === 'all' || tr.dataset.risk === risk);
    tr.hidden = !hit;
    if (hit) shown++;
  }
  document.getElementById('none').hidden = shown > 0;
}
q.addEventListener('input', apply);
for (const b of buttons) {
  b.addEventListener('click', () => {
    risk = b.dataset.risk;
    for (const o of buttons) o.setAttribute('aria-pressed', String(o === b));
    apply();
  });
}
"""


def esc(v: object) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def columns(cuts: list[dict]) -> str:
    """One column per cumulative cut. Single measure, so no legend: the title names it."""
    if not cuts:
        return ""
    w, h = 760, 210
    pad_l, pad_b, pad_t = 44, 46, 18
    top = max(1.0, max(c["pct_scripted"] for c in cuts) * 1.35)
    slot = (w - pad_l - 10) / len(cuts)
    # A 2px surface gap between neighbours, per the shared mark spec.
    bar_w = min(54, slot - 14)
    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" width="100%" '
        f'aria-label="Share of packages that run an install script, by cumulative download rank">'
    ]
    for i in range(3):
        v = top * i / 2
        y = pad_t + (h - pad_t - pad_b) * (1 - v / top)
        parts.append(f'<line class="axis" x1="{pad_l}" x2="{w - 6}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">{v:.1f}%</text>')
    base = h - pad_b
    for i, c in enumerate(cuts):
        x = pad_l + slot * i + (slot - bar_w) / 2
        bh = max(2.0, (h - pad_t - pad_b) * c["pct_scripted"] / top)
        mid = x + bar_w / 2
        parts.append(
            f'<rect class="bar" x="{x:.1f}" y="{base - bh:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" rx="4"><title>{esc(c["cut"])}: {c["scripted"]} of '
            f'{c["packages"]:,} ({c["pct_scripted"]:.2f}%), floor {esc(human(c["floor"]))}/week'
            f'</title></rect>'
        )
        parts.append(
            f'<text class="val" x="{mid:.1f}" y="{base - bh - 7:.1f}" text-anchor="middle">'
            f'{c["pct_scripted"]:.2f}%</text>'
        )
        parts.append(
            f'<text class="tick" x="{mid:.1f}" y="{base + 17:.1f}" text-anchor="middle">'
            f'{esc(c["cut"])}</text>'
        )
        parts.append(
            f'<text class="sub" x="{mid:.1f}" y="{base + 32:.1f}" text-anchor="middle">'
            f'{c["scripted"]} of {c["packages"]:,} &#183; &#8805;{esc(human(c["floor"]))}/wk</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def signal_bars(classes: dict[str, int]) -> str:
    """Magnitude by category: one hue, sorted, labelled at the end of each bar."""
    if not classes:
        return ""
    items = list(classes.items())
    top = max(v for _, v in items)
    row_h, w, label_w = 26, 620, 52
    h = row_h * len(items) + 6
    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" width="100%" '
        f'aria-label="How often each capability class appears across the scripted packages">'
    ]
    for i, (name, count) in enumerate(items):
        y = i * row_h + 4
        bw = max(2.0, (w - label_w - 46) * count / top)
        parts.append(
            f'<text class="tick" x="{label_w - 10}" y="{y + 14}" text-anchor="end">{esc(name)}</text>'
        )
        parts.append(
            f'<rect class="bar" x="{label_w}" y="{y + 3}" width="{bw:.1f}" height="14" rx="4">'
            f'<title>{esc(name)}: {count}</title></rect>'
        )
        parts.append(f'<text class="val" x="{label_w + bw + 8:.1f}" y="{y + 15}">{count}</text>')
    parts.append("</svg>")
    return "".join(parts)


def queue_rows(scripted: list[dict]) -> str:
    out = []
    for pkg in scripted:
        for row in pkg["rows"]:
            signals = ", ".join(row["signals"]) or "none"
            command = row.get("command") or ""
            search = f"{pkg['name']} {row['script']} {command} {signals}".lower()
            out.append(
                f'<tr data-risk="{esc(row["risk"])}" data-search="{esc(search)}">'
                f'<td><span class="pkg">{esc(pkg["name"])}</span>'
                f'<span class="dim"> {esc(pkg["version"])}</span></td>'
                f'<td class="num">{esc(human(pkg["downloads"]))}</td>'
                f'<td><code>{esc(row["script"])}</code></td>'
                f'<td class="d"><code>{esc(command)}</code></td>'
                f'<td><span class="pill {esc(row["risk"])}">{esc(row["risk"])}</span></td>'
                f'<td class="d dim">{esc(signals)}</td></tr>'
            )
    return "".join(out)


def cuts_table(cuts: list[dict]) -> str:
    rows = "".join(
        f'<tr><td>{esc(c["cut"])}</td><td class="num">{esc(human(c["floor"]))}/week</td>'
        f'<td class="num">{c["scripted"]} of {c["packages"]:,}</td>'
        f'<td class="num">{c["pct_scripted"]:.2f}%</td>'
        f'<td class="num">{c["risky"]}</td></tr>'
        for c in cuts
    )
    return (
        "<table><thead><tr><th>Sample</th><th class=num>Download floor</th>"
        "<th class=num>Run an install script</th><th class=num>Share</th>"
        "<th class=num>HIGH or MEDIUM</th></tr></thead><tbody>" + rows + "</tbody></table>"
    )


def render(d: dict) -> str:
    total, scripted = d["total"], d["with_install_scripts"]
    high = d["risk"].get("HIGH", 0)
    share = 100 * scripted / total if total else 0
    biggest = next((p for p in d["scripted"] if p["risk"] in ("MEDIUM", "HIGH")), None)
    day = d["generated_utc"][:10]
    lede = (
        "npm v12 turned install scripts off by default, so you now approve them one "
        "at a time. This is what that approval queue actually looks like across the "
        "registry, measured every day."
    )
    cards = [
        (f"{scripted}", f"run an install script, of {total:,}", ""),
        (f"{share:.2f}%", "of the sample", ""),
        (f"{high}", "score HIGH", "high"),
    ]
    if biggest:
        cards.append((human(biggest["downloads"]), f"a week &#183; {esc(biggest['name'])}", ""))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What actually runs at npm install time</title>
<meta name="description" content="{esc(lede)}">
<meta property="og:title" content="What actually runs at npm install time">
<meta property="og:description" content="{esc(lede)}">
<style>{CSS}</style>
</head>
<body>
<main>
<h1>What actually runs at <code>npm install</code> time</h1>
<p class="lede">{lede}</p>
<p class="meta">Sample of {total:,} packages, {d['scoped']:,} of them scoped, resolved at
{d['coverage'] * 100:.0f}% coverage. Rebuilt {esc(day)} by
<a href="{LENS}">npm-script-lens</a>, run unmodified. Raw output:
<a href="{REPO}/blob/main/data/census.json">census.json</a>.</p>

<div class="cards">
{"".join(f'<div class="card {c}"><b>{v}</b><span>{l}</span></div>' for v, l, c in cards)}
</div>

<h2>Sticking to popular packages does not help</h2>
<figure>
{columns(d.get("cuts", []))}
<figcaption>Share of each cumulative top-N cut that runs an install script. If install
scripts clustered in the unmaintained tail, the left-hand columns would be shorter.
They are not.</figcaption>
<details><summary>Show the numbers</summary>{cuts_table(d.get("cuts", []))}</details>
</figure>

<h2>The approval queue</h2>
<p class="note">Every package in the sample that runs something, ordered by weekly
downloads. None of this is an accusation: these are well-known packages fetching a
prebuilt binary or building a native addon. They are simply the ones you are now
asked to approve, and "what does it actually do" is a per-package question.</p>
<div class="controls">
<input type="search" id="q" placeholder="Filter by package, command or signal" aria-label="Filter the approval queue">
<button data-risk="all" aria-pressed="true">All</button>
<button data-risk="HIGH" aria-pressed="false">HIGH</button>
<button data-risk="LOW" aria-pressed="false">LOW</button>
<button data-risk="SAFE" aria-pressed="false">SAFE</button>
</div>
<table id="queue">
<thead><tr><th>Package</th><th class="num">Downloads/wk</th><th>Script</th>
<th class="d">Command</th><th>Risk</th><th class="d">Signals</th></tr></thead>
<tbody>{queue_rows(d["scripted"])}</tbody>
</table>
<p class="empty" id="none" hidden>Nothing matches that filter.</p>

<h2>What they reach for</h2>
<figure>
{signal_bars(d.get("signal_classes", {}))}
<figcaption>Capability classes seen across the scripted packages. Capability, not
intent: <code>exec</code> means the script spawns a process, which is exactly what a
native build does.</figcaption>
</figure>

<footer>
A keyword-nominated sample ranked by real download counts, not a top-N list, since npm
publishes no such endpoint. The run aborts rather than publish if it resolves less than
95% of the corpus. Method, caveats and the <code>prepare</code> trap are written up in
the <a href="{REPO}">repo</a>.
</footer>
</main>
<script>{JS}</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=str(ROOT / "data" / "census.json"))
    ap.add_argument("--out", dest="out", default=str(ROOT / "site"))
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(render(data), encoding="utf-8")
    # Pages would otherwise run the output through Jekyll and drop nothing useful,
    # but it also slows every deploy down for no reason.
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"{page} ({page.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
