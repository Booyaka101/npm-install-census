#!/usr/bin/env python3
"""Nominate candidate packages by sweeping npm's own search.

Search ranks popularity *within a query*, so a sweep alone would put react at
the top of everything. This step only produces the candidate set; ranking is
rank.py's job and uses real download counts.
"""
from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KEYWORDS = [
    "react", "cli", "test", "build", "http", "server", "typescript", "webpack",
    "babel", "lint", "css", "node", "stream", "parser", "logger", "database",
    "async", "json", "util", "framework", "vue", "angular", "bundler",
    "compiler", "crypto", "date", "validation", "api", "graphql", "orm",
    "config", "promise", "template", "markdown", "image", "auth", "cache",
    "queue", "websocket", "yaml", "csv", "polyfill", "runtime", "loader",
    "plugin", "types", "swc", "esbuild", "rollup", "vite",
]
UA = {"User-Agent": "npm-install-census (github.com/Booyaka101)"}
DATA = Path(__file__).resolve().parent.parent / "data"


def search(keyword: str) -> list[str]:
    url = (
        f"https://registry.npmjs.org/-/v1/search?text={keyword}"
        "&popularity=1.0&quality=0.0&maintenance=0.0&size=100"
    )
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return [o["package"]["name"] for o in json.load(r).get("objects", [])]
        except Exception:
            time.sleep(2 * (attempt + 1))
    print(f"  keyword {keyword!r} failed, skipping")
    return []


def main() -> int:
    DATA.mkdir(exist_ok=True)
    pool: set[str] = set()
    existing = DATA / "pool.json"
    if existing.exists():
        pool |= set(json.loads(existing.read_text(encoding="utf-8")))

    with ThreadPoolExecutor(max_workers=4) as ex:
        for names in ex.map(search, KEYWORDS):
            pool |= set(names)

    existing.write_text(json.dumps(sorted(pool), indent=0), encoding="utf-8")
    scoped = sum(1 for n in pool if n.startswith("@"))
    print(f"pool: {len(pool)} candidates ({scoped} scoped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
