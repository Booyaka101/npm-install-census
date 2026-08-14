#!/usr/bin/env python3
"""Run npm-script-lens over the ranked corpus and summarise the result.

The tool is used exactly as shipped: we synthesise a lockfile naming each
corpus package at its current `latest`, then call `audit --json`. No forking,
no reaching into its internals, so the census measures the same thing a user
gets when they run the CLI on their own project.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "npm-script-census (github.com/Booyaka101)"}
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def latest(name: str) -> dict | None:
    try:
        req = urllib.request.Request(
            f"https://registry.npmjs.org/{name}/latest", headers=UA
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def build_lockfile(packages: list[dict], workdir: Path) -> int:
    root = {"name": "census", "version": "1.0.0", "dependencies": {}}
    out = {"": root}
    resolved = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        metas = list(pool.map(lambda p: (p["name"], latest(p["name"])), packages))
    for name, d in metas:
        if not d or "dist" not in d:
            continue
        root["dependencies"][name] = "^" + d["version"]
        out[f"node_modules/{name}"] = {
            "version": d["version"],
            "resolved": d["dist"]["tarball"],
            "integrity": d["dist"].get("integrity"),
        }
        resolved += 1
    (workdir / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "census",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "requires": True,
                "packages": out,
            }
        ),
        encoding="utf-8",
    )
    return resolved


def band_stats(results: list[dict], order: list[str]) -> list[dict]:
    """Split the corpus into download-rank bands and count scripted packages."""
    pos = {n: i for i, n in enumerate(order)}
    results = sorted(results, key=lambda r: pos.get(r["name"], 10**9))
    out = []
    size = max(1, len(results) // 4)
    for start in range(0, len(results), size):
        chunk = results[start : start + size]
        if not chunk:
            continue
        scripted = [r for r in chunk if r.get("rows")]
        risky = [r for r in chunk if r.get("risk") in ("MEDIUM", "HIGH")]
        out.append({
            "band": f"{start + 1}-{start + len(chunk)}",
            "packages": len(chunk),
            "scripted": len(scripted),
            "risky": len(risky),
            "pct_scripted": round(100 * len(scripted) / len(chunk), 1),
        })
    return out


def summarise(results: list[dict], downloads: dict[str, int]) -> dict:
    order = ["SAFE", "LOW", "MEDIUM", "HIGH"]
    bands = band_stats(results, [n for n, _ in sorted(downloads.items(), key=lambda kv: -kv[1])])
    risk_counts = {k: 0 for k in order}
    signals: dict[str, int] = {}
    scripted = []
    for r in results:
        risk = r.get("risk", "SAFE")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        rows = r.get("rows") or []
        if rows:
            scripted.append(r)
        for row in rows:
            for s in row.get("signals", []):
                # Signals carry the concrete binary/path; keep the class only.
                key = s.split(":", 1)[0].strip()
                signals[key] = signals.get(key, 0) + 1

    total = len(results)
    risky = [r for r in results if r.get("risk") in ("MEDIUM", "HIGH")]
    risky.sort(key=lambda r: -downloads.get(r["name"], 0))
    return {
        "total": total,
        "bands": bands,
        "with_install_scripts": len(scripted),
        "risk": risk_counts,
        "signal_classes": dict(sorted(signals.items(), key=lambda kv: -kv[1])[:12]),
        "top_risky": [
            {
                "name": r["name"],
                "version": r.get("version"),
                "risk": r.get("risk"),
                "downloads": downloads.get(r["name"], 0),
                "scripts": [row.get("script") for row in (r.get("rows") or [])],
            }
            for r in risky[:20]
        ],
    }


def write_headline(summary: dict, downloads: dict[str, int]) -> None:
    """Refresh the marker block in the README so the top line never goes stale."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8")
    high = summary["risk"].get("HIGH", 0)
    biggest = summary["top_risky"][0] if summary["top_risky"] else None
    line = (
        f"> **{summary['with_install_scripts']} of {summary['total']:,}** packages "
        f"in the current sample run an install script. **{high}** score HIGH."
    )
    if biggest:
        line += (
            f" The most-installed one is `{biggest['name']}` at "
            f"{biggest['downloads'] / 1e6:.1f}M downloads a week."
        )
    line += f"\n>\n> <sub>Rebuilt {summary['generated_utc'][:10]}.</sub>"
    block = re.compile(
        r"<!-- auto:headline -->.*?<!-- /auto:headline -->", re.DOTALL
    )
    if block.search(text):
        readme.write_text(
            block.sub(f"<!-- auto:headline -->\n{line}\n<!-- /auto:headline -->", text),
            encoding="utf-8",
        )


def main() -> int:
    corpus = json.loads((DATA / "corpus.json").read_text(encoding="utf-8"))
    packages = corpus["packages"]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(packages)
    packages = packages[:limit]
    downloads = {p["name"]: p["downloads"] for p in packages}

    work = ROOT / ".run"
    work.mkdir(exist_ok=True)
    print(f"resolving {len(packages)} packages to their current latest...")
    resolved = build_lockfile(packages, work)
    print(f"lockfile: {resolved} packages")

    # NPM_SCRIPT_LENS_CLI lets CI point at a checkout; otherwise npx fetches
    # the published package, which is the same thing a user would run.
    cli = os.environ.get("NPM_SCRIPT_LENS_CLI")
    cmd = (["node", cli] if cli else ["npx", "--yes", "npm-script-lens"]) + [
        "audit", "--path", str(work), "--json", "--no-trust"
    ]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    elapsed = time.time() - started
    if proc.returncode not in (0, 1):  # 1 is "findings present", not a failure
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1

    body = proc.stdout[proc.stdout.index("{") :]
    data = json.loads(body)
    summary = summarise(data.get("results", []), downloads)
    summary["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["corpus_candidates"] = corpus.get("candidates")
    summary["audit_seconds"] = round(elapsed, 1)

    (DATA / "census.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    write_headline(summary, downloads)
    print(json.dumps({k: v for k, v in summary.items() if k != "top_risky"}, indent=1))
    print(f"audit took {elapsed:.0f}s for {summary['total']} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
