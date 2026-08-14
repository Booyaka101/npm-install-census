#!/usr/bin/env python3
"""Rank the candidate pool by real weekly downloads, resumably.

npm's bulk downloads endpoint takes 128 unscoped names per call but rejects
scoped ones, so ~2k scoped packages need individual requests against a
rate-limited API. That is slow, but it is also the only part that is slow, and
it only needs refreshing weekly. Progress is checkpointed so a killed run
resumes instead of starting over.
"""
from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "npm-script-census (github.com/Booyaka101)"}
DATA = Path(__file__).resolve().parent.parent / "data"
CKPT = DATA / "downloads.json"


def get(url: str, tries: int = 3):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception:
            if a == tries - 1:
                return None
            time.sleep(1.5 * (a + 1))
    return None


def main() -> int:
    pool = json.loads((DATA / "pool.json").read_text(encoding="utf-8"))
    counts = json.loads(CKPT.read_text(encoding="utf-8")) if CKPT.exists() else {}
    todo = [n for n in pool if n not in counts]
    plain = [n for n in todo if not n.startswith("@")]
    scoped = [n for n in todo if n.startswith("@")]
    print(f"pool={len(pool)} done={len(counts)} todo={len(todo)}", flush=True)

    batches = [plain[i : i + 100] for i in range(0, len(plain), 100)]

    def bulk(b):
        return get("https://api.npmjs.org/downloads/point/last-week/" + ",".join(b))

    with ThreadPoolExecutor(max_workers=4) as ex:
        for d in ex.map(bulk, batches):
            for k, v in (d or {}).items():
                counts[k] = (v or {}).get("downloads") or 0
    CKPT.write_text(json.dumps(counts), encoding="utf-8")
    print(f"unscoped done, {len(counts)} known", flush=True)

    def one(n):
        d = get(f"https://api.npmjs.org/downloads/point/last-week/{n}")
        return n, (d or {}).get("downloads") or 0

    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, (n, dl) in enumerate(ex.map(one, scoped)):
            counts[n] = dl
            if (i + 1) % 50 == 0:
                CKPT.write_text(json.dumps(counts), encoding="utf-8")
                print(f"  scoped {i + 1}/{len(scoped)}", flush=True)
    CKPT.write_text(json.dumps(counts), encoding="utf-8")

    ranked = sorted(
        ((n, c) for n, c in counts.items() if c), key=lambda kv: -kv[1]
    )
    json.dump(
        {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "candidates": len(pool),
            "method": "npm search keyword sweep nominates; npm downloads API ranks by real weekly downloads",
            "packages": [{"name": n, "downloads": c} for n, c in ranked],
        },
        open(DATA / "corpus.json", "w"),
        indent=1,
    )
    scoped_n = sum(1 for n, _ in ranked if n.startswith("@"))
    print(f"ranked={len(ranked)} scoped={scoped_n}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
