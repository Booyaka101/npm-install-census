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
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "npm-script-census (github.com/Booyaka101)"}
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "resolved.json"

# Resolving every package on every run means thousands of unauthenticated
# requests from one runner IP, which the registry throttles hard enough to drop
# coverage below the floor and abort the whole census. Entries are reused for
# TTL_DAYS and only a bounded slice is refreshed per run, so a daily run costs a
# few hundred requests instead of the full corpus.
TTL_DAYS = 7
MAX_REFRESH = int(os.environ.get("CENSUS_MAX_REFRESH", "700"))
# A cache that can never be refreshed would keep the census green while the
# sample quietly rotted, so entries past this age stop counting as resolved.
MAX_AGE_DAYS = 30


def _headers() -> dict[str, str]:
    """Registry headers, authenticated when a token is available.

    Authenticated requests get a far higher rate limit, which is the difference
    between finishing the corpus and being throttled halfway through.
    """
    token = os.environ.get("NPM_TOKEN") or os.environ.get("NODE_AUTH_TOKEN")
    return {**UA, "Authorization": f"Bearer {token}"} if token else dict(UA)


def latest(name: str) -> dict | None:
    """Resolve a package's current `latest`, retrying through rate limits.

    A silent None here drops the package from the lockfile and shrinks the
    reported sample without saying so, which is why the caller also enforces a
    coverage floor.
    """
    delay = 1.0
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                f"https://registry.npmjs.org/{name}/latest", headers=_headers()
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # unpublished; genuinely not there
            if attempt == 4:
                return None
            time.sleep(delay)
            delay *= 2
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == 4:
                return None
            time.sleep(delay)
            delay *= 2
    return None


def _now() -> float:
    return time.time()


def _load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("entries", {})
    except (json.JSONDecodeError, OSError):
        return {}  # a corrupt cache is a slow run, not a wrong answer


def _save_cache(entries: dict[str, dict]) -> None:
    CACHE.write_text(
        json.dumps(
            {
                "ttl_days": TTL_DAYS,
                "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "entries": dict(sorted(entries.items())),
            },
            indent=0,
        ),
        encoding="utf-8",
    )


def _stale_at(name: str) -> float:
    """Per-package TTL, jittered so the whole corpus never expires on one day.

    crc32 rather than hash(): PYTHONHASHSEED randomises str hashing per process,
    which would move a package's TTL every run.

    The spread is wide on purpose. A seeded cache has every entry stamped at the
    same moment, so a narrow window would expire the whole corpus over a day or
    two and blow straight through MAX_REFRESH.
    """
    spread = ((zlib.crc32(name.encode()) % 601) / 601 * 2 - 1) * 3  # +/- 3 days
    return (TTL_DAYS + spread) * 86400


def resolve_corpus(packages: list[dict]) -> tuple[dict[str, dict], dict[str, int]]:
    """Return usable resolutions, topping the cache up within a request budget.

    Freshly resolved entries win; a stale entry is kept when the registry
    refuses to answer, because a slightly old version is a truer sample than a
    hole. Entries older than MAX_AGE_DAYS are dropped so the floor still bites.
    """
    cache = _load_cache()
    now = _now()
    names = [p["name"] for p in packages]

    missing = [n for n in names if n not in cache]
    stale = [n for n in names if n in cache and now - cache[n].get("at", 0) > _stale_at(n)]
    stale.sort(key=lambda n: cache[n].get("at", 0))  # oldest first

    # Missing entries are not optional: without them the sample really is short.
    budget = max(0, MAX_REFRESH - len(missing))
    todo = missing + stale[:budget]
    print(
        f"cache: {len(cache)} entries, {len(missing)} missing, {len(stale)} stale; "
        f"refreshing {len(todo)}"
    )

    refreshed = 0
    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, d in pool.map(lambda n: (n, latest(n)), todo):
                if d and "dist" in d:
                    cache[name] = {
                        "version": d["version"],
                        "tarball": d["dist"]["tarball"],
                        "integrity": d["dist"].get("integrity"),
                        "at": now,
                    }
                    refreshed += 1
        _save_cache(cache)

    usable, expired = {}, 0
    for n in names:
        e = cache.get(n)
        if not e:
            continue
        if now - e.get("at", 0) > MAX_AGE_DAYS * 86400:
            expired += 1
            continue
        usable[n] = e
    return usable, {
        "refreshed": refreshed,
        "from_cache": len(usable) - refreshed,
        "expired": expired,
    }


def build_lockfile(packages: list[dict], workdir: Path) -> tuple[int, dict[str, int]]:
    root = {"name": "census", "version": "1.0.0", "dependencies": {}}
    out = {"": root}
    resolved = 0
    usable, stats = resolve_corpus(packages)
    for p in packages:
        e = usable.get(p["name"])
        if not e:
            continue
        root["dependencies"][p["name"]] = "^" + e["version"]
        out[f"node_modules/{p['name']}"] = {
            "version": e["version"],
            "resolved": e["tarball"],
            "integrity": e["integrity"],
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
    return resolved, stats


def _counts(chunk: list[dict]) -> tuple[int, int]:
    scripted = sum(1 for r in chunk if r.get("rows"))
    risky = sum(1 for r in chunk if r.get("risk") in ("MEDIUM", "HIGH"))
    return scripted, risky


def by_downloads(results: list[dict], order: list[str]) -> list[dict]:
    pos = {n: i for i, n in enumerate(order)}
    return sorted(results, key=lambda r: pos.get(r["name"], 10**9))


def band_stats(ranked: list[dict]) -> list[dict]:
    """Split the corpus into equal download-rank bands and count scripted packages."""
    out = []
    size = max(1, len(ranked) // 4)
    for start in range(0, len(ranked), size):
        chunk = ranked[start : start + size]
        if not chunk:
            continue
        scripted, risky = _counts(chunk)
        out.append({
            "band": f"{start + 1}-{start + len(chunk)}",
            "packages": len(chunk),
            "scripted": scripted,
            "risky": risky,
            "pct_scripted": round(100 * scripted / len(chunk), 1),
        })
    return out


def cut_stats(ranked: list[dict], downloads: dict[str, int]) -> list[dict]:
    """The same counts over cumulative top-N cuts.

    Bands answer "is the middle of the registry worse than the top". Cuts answer
    "does sticking to popular packages help me", which is the question people
    actually ask, and answering it is the point of the census.
    """
    out = []
    for n in (100, 500, 1000, 2000, len(ranked)):
        chunk = ranked[: min(n, len(ranked))]
        if not chunk or (out and out[-1]["packages"] == len(chunk)):
            continue
        scripted, risky = _counts(chunk)
        out.append({
            "cut": f"Top {n:,}" if n < len(ranked) else "Full sample",
            "packages": len(chunk),
            "floor": min(downloads.get(r["name"], 0) for r in chunk),
            "scripted": scripted,
            "risky": risky,
            "pct_scripted": round(100 * scripted / len(chunk), 2),
        })
    return out


def summarise(results: list[dict], downloads: dict[str, int]) -> dict:
    order = ["SAFE", "LOW", "MEDIUM", "HIGH"]
    ranked = by_downloads(results, [n for n, _ in sorted(downloads.items(), key=lambda kv: -kv[1])])
    risk_counts = {k: 0 for k in order}
    signals: dict[str, int] = {}
    scripted = []
    for r in ranked:
        risk = r.get("risk", "SAFE")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        rows = r.get("rows") or []
        if not rows:
            continue
        scripted.append({
            "name": r["name"],
            "version": r.get("version"),
            "risk": risk,
            "downloads": downloads.get(r["name"], 0),
            "rows": [
                {
                    "script": row.get("script"),
                    "command": row.get("command"),
                    "risk": row.get("risk"),
                    "signals": sorted({s.split(":", 1)[0].strip() for s in row.get("signals", [])}),
                }
                for row in rows
            ],
        })
        for row in rows:
            for s in row.get("signals", []):
                # Signals carry the concrete binary/path; keep the class only.
                key = s.split(":", 1)[0].strip()
                signals[key] = signals.get(key, 0) + 1

    return {
        "total": len(results),
        "scoped": sum(1 for r in results if r["name"].startswith("@")),
        "bands": band_stats(ranked),
        "cuts": cut_stats(ranked, downloads),
        "with_install_scripts": len(scripted),
        "risk": risk_counts,
        "signal_classes": dict(sorted(signals.items(), key=lambda kv: -kv[1])[:12]),
        # Every package that runs something, not a top-N slice. It is 28 rows out
        # of thousands, and a truncated approval queue is a misleading one.
        "scripted": scripted,
    }


def human(n: float) -> str:
    """Download counts the way the README and the site both say them."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(int(n))


def _block(text: str, name: str, body: str) -> str:
    pattern = re.compile(rf"<!-- auto:{name} -->.*?<!-- /auto:{name} -->", re.DOTALL)
    if not pattern.search(text):
        return text
    return pattern.sub(f"<!-- auto:{name} -->\n{body}\n<!-- /auto:{name} -->", text)


def _cuts_table(summary: dict) -> str:
    lines = [
        "| Sample | Download floor | Run an install script | HIGH risk |",
        "|---|---|---|---|",
    ]
    for c in summary.get("cuts", []):
        label = c["cut"] if c["cut"].startswith("Top") else f"{c['cut']} ({c['packages']:,})"
        lines.append(
            f"| {label} | {human(c['floor'])}/week "
            f"| {c['scripted']} ({c['pct_scripted']:.2f}%) | {c['risky']} |"
        )
    return "\n".join(lines)


def _queue_table(summary: dict) -> str:
    lines = [
        "| Package | Downloads/week | Script | Command | Risk | Signals |",
        "|---|---|---|---|---|---|",
    ]
    for pkg in summary.get("scripted", []):
        for row in pkg["rows"]:
            command = (row.get("command") or "").replace("|", "\\|")
            if len(command) > 60:
                command = command[:59] + "\u2026"
            signals = ", ".join(row["signals"]) or "none"
            lines.append(
                f"| `{pkg['name']}` | {human(pkg['downloads'])} | {row['script']} "
                f"| `{command}` | {row['risk']} | {signals} |"
            )
    return "\n".join(lines)


def _mix_line(summary: dict) -> str:
    mix: dict[str, int] = {}
    for pkg in summary.get("scripted", []):
        mix[pkg["risk"]] = mix.get(pkg["risk"], 0) + 1
    parts = [f"{mix[k]} {k}" for k in ("HIGH", "MEDIUM", "LOW", "SAFE") if mix.get(k)]
    classes = ", ".join(f"`{k}` {v}" for k, v in summary.get("signal_classes", {}).items())
    return (
        f"Scripted does not mean risky. Of the {summary['with_install_scripts']} "
        f"scripted packages, {', '.join(parts)}. Across the whole sample the "
        f"signal classes break down as: {classes}."
    )


def _sample_line(summary: dict) -> str:
    floor = summary["cuts"][-1]["floor"] if summary.get("cuts") else 0
    return (
        f'- **Not "the top {summary["total"]:,} packages on npm."** npm has no '
        "top-N endpoint. This is a keyword-nominated sample ranked by real "
        f"downloads, and the tail runs down to {human(floor)} downloads a week. "
        "Slices are reported with their download floor so you can see what each "
        "one covers."
    )


def _scoped_line(summary: dict) -> str:
    return (
        f"- **Not exhaustive on scoped packages.** {summary['scoped']:,} of "
        f"{summary['total']:,} are scoped. The nomination sweep under-samples "
        "them relative to their real share of the registry."
    )


def write_readme(summary: dict) -> None:
    """Refresh every marker block, so no figure in the README is hand-maintained.

    These tables were typed once and were several runs out of date before anyone
    noticed, which is the failure the markers exist to prevent.
    """
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    high = summary["risk"].get("HIGH", 0)
    biggest = next(
        (p for p in summary.get("scripted", []) if p["risk"] in ("MEDIUM", "HIGH")), None
    )
    headline = (
        f"> **{summary['with_install_scripts']} of {summary['total']:,}** packages "
        f"in the current sample run an install script. **{high}** score HIGH."
    )
    if biggest:
        headline += (
            f" The most-installed one is `{biggest['name']}` at "
            f"{human(biggest['downloads'])} downloads a week."
        )
    headline += f"\n>\n> <sub>Rebuilt {summary['generated_utc'][:10]}.</sub>"

    text = readme.read_text(encoding="utf-8")
    for name, body in (
        ("headline", headline),
        ("cuts", _cuts_table(summary)),
        ("queue", _queue_table(summary)),
        ("mix", _mix_line(summary)),
        ("sample", _sample_line(summary)),
        ("scoped", _scoped_line(summary)),
    ):
        text = _block(text, name, body)
    readme.write_text(text, encoding="utf-8")


def main() -> int:
    corpus = json.loads((DATA / "corpus.json").read_text(encoding="utf-8"))
    packages = corpus["packages"]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(packages)
    packages = packages[:limit]
    downloads = {p["name"]: p["downloads"] for p in packages}

    work = ROOT / ".run"
    work.mkdir(exist_ok=True)
    print(f"resolving {len(packages)} packages to their current latest...")
    resolved, resolve_stats = build_lockfile(packages, work)
    coverage = resolved / len(packages) if packages else 0
    print(
        f"lockfile: {resolved}/{len(packages)} packages ({coverage:.1%}) "
        f"[{resolve_stats['refreshed']} refreshed, "
        f"{resolve_stats['from_cache']} cached, "
        f"{resolve_stats['expired']} expired]"
    )
    if coverage < 0.95:
        print(
            f"ABORT: only resolved {coverage:.1%} of the corpus. Publishing this "
            "would report a silently truncated sample as if it were the whole "
            "thing. Refusing.",
            file=sys.stderr,
        )
        return 1

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
    summary["requested"] = len(packages)
    summary["resolved"] = resolved
    summary["coverage"] = round(coverage, 4)
    # How much of the sample is live versus reused, so a reader can tell.
    summary["resolution"] = resolve_stats
    summary["audit_seconds"] = round(elapsed, 1)

    (DATA / "census.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    write_readme(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "scripted"}, indent=1))
    print(f"audit took {elapsed:.0f}s for {summary['total']} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
