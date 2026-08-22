# npm install-script census

**How many npm packages actually run code when you install them?** About one in a hundred, and it barely changes with popularity.

npm v12 flipped install scripts off by default. You now approve them one by one via `allowScripts`. Nobody had published what that approval queue actually looks like across the ecosystem, so this measures it, daily.

<!-- auto:headline -->
> **23 of 2,941** packages in the current sample run an install script. **16** score HIGH. The most-installed one is `esbuild` at 255.5M downloads a week.
>
> <sub>Rebuilt 2026-08-22.</sub>
<!-- /auto:headline -->

Every number here is produced by [npm-script-lens](https://github.com/Booyaka101/npm-script-lens) run unmodified against packages pulled from the public registry. Raw output is in [`data/census.json`](data/census.json).

## What it found

Install scripts are rare, and they are roughly as rare at the top of the ecosystem as in the middle:

| Sample | Download floor | Run an install script | HIGH risk |
|---|---|---|---|
| Top 100 | 104.7M/week | 1 (1.00%) | 1 |
| Top 500 | 11.1M/week | 5 (1.00%) | 3 |
| Top 1,000 | 1.8M/week | 8 (0.80%) | 4 |
| Top 2,000 | 131.9K/week | 20 (1.00%) | 13 |
| Full sample (3,077) | 14/week | 25 (0.81%) | 18 |

That flatness is the result. There is no long tail where install scripts suddenly proliferate, and no clean gradient by popularity. It sits near 1% wherever you cut it.

The packages that do run scripts are overwhelmingly native or binary-distribution tooling: prebuilt binary fetchers, node-gyp builds, and tree-sitter grammars.

### The approval queue, by reach

These are the packages npm v12 will actually ask you about, ordered by weekly downloads.

| Package | Downloads/week | Script | Command | Risk | Signals |
|---|---|---|---|---|---|
| `esbuild` | 255.5M | postinstall | `node install.js` | HIGH | env, exec, fs, net |
| `unrs-resolver` | 51.3M | postinstall | `node postinstall.js` | SAFE | none |
| `@swc/core` | 40.7M | postinstall | `node postinstall.js` | HIGH | env, exec, fs |
| `workerd` | 19.0M | postinstall | `node install.js` | HIGH | env, exec, fs, net |
| `prisma` | 16.0M | preinstall | `node scripts/preinstall-entry.js` | LOW | env |
| `vue-demi` | 8.0M | postinstall | `node -e "try{require('./scripts/postinstall.js')}…"` | LOW | fs |
| `bufferutil` | 6.8M | install | `node-gyp-build` | HIGH | exec |
| `mongodb-memory-server` | 2.1M | postinstall | `node ./postinstall.js` | SAFE | none |
| `node` | 1.4M | preinstall | `node installArchSpecificPackage` | SAFE | none |
| `@sentry-internal/node-cpu-profiler` | 1.1M | install | `node scripts/check-build.js` | HIGH | env, exec |
| `tree-sitter-json` | 1.1M | install | `node-gyp-build` | HIGH | bin, env, exec |
| `@tree-sitter-grammars/tree-sitter-yaml` | 1.0M | install | `node-gyp-build` | HIGH | bin, env, exec |

**None of this is an accusation.** Every one of these is a well-known package doing something legitimate: fetching a prebuilt binary, or building a native addon. The point is that these are the ones you are now being asked to approve, and "what does it actually do" is a question you have to answer per package. That is what the capability signals are for.

Note that scripted does not mean risky. `unrs-resolver` is the second-most-installed package with a postinstall and it comes back clean. Of the 25 scripted packages, 18 score HIGH, 3 LOW, and 4 SAFE.

Three worth singling out, further down the list:

- **`oracledb`** (806K/week) base64-decodes during its install script.
- **`nodent-runtime`** (113K/week) uses the `new Function()` constructor at install time.
- **`@salesforce/cli`** (410K/week) uses `preinstall`, which runs before the rest of the tree is in place.

Across the whole sample the signal classes break down as: `exec` 40, `env` 12, `fs` 7, `net` 7, `bin` 4, `gyp` 4, `obf` 2.

## The `prepare` trap

A naive scan of this question overcounts by an order of magnitude.

In the top 100 packages, 13 have something that looks like an install-time script. Twelve of them are `prepare`, and npm does **not** run `prepare` when you install a package from the registry. It runs on a git dependency, or in the package's own repo during development. npm's own `hasInstallScript` flag is `false` on all twelve.

The real count is one. If a census tells you 13% of top packages run install scripts, it is reading `package.json` and not thinking about it.

## Method

1. **Nominate.** Sweep npm's own search across ~50 ecosystem keywords. Search ranks popularity *within a query*, so this only nominates candidates, it never ranks them.
2. **Rank.** Fetch real weekly download counts from npm's downloads API. Unscoped names go through the bulk endpoint 100 at a time; scoped names are rejected by the bulk form and are fetched individually, which is the slow part.
3. **Resolve.** Look up each package's current `latest` and synthesise a lockfile pointing at the real registry tarballs.
4. **Audit.** Run `npm-script-lens audit --json` against that lockfile.

Step 4 is the tool exactly as published. The census measures the same thing you get running the CLI on your own project.

### What this is not

- **Not "the top 3,077 packages on npm."** npm has no top-N endpoint. This is a keyword-nominated sample ranked by real downloads, and the tail runs down to 14 downloads a week. Slices are reported with their download floor so you can see what each one covers.
- **Not a malware scan.** It reports capability, not intent. `--no-trust` is set, so no OSV lookups.
- **Never silently truncated.** The first CI run resolved only 705 of 3,077 packages because the registry rate-limited the runner and the failures were swallowed, publishing a 705-package sample as if it were the whole thing. Resolution now retries with backoff, and the run aborts rather than publishing if it covers less than 95% of the corpus. `data/census.json` records `requested`, `resolved` and `coverage` on every run.
- **Not exhaustive on scoped packages.** 707 of 3,077 are scoped. The nomination sweep under-samples them relative to their real share of the registry.

The first version of this census excluded scoped packages entirely, and reported that the biggest scripted package was `bufferutil` at 6.8M downloads. Adding scoped packages surfaced `esbuild` at 255M and `@swc/core` at 40M. A corpus that structurally omits `@`-scoped packages is not measuring npm.

## Running it

```sh
python tools/rank.py      # rebuild the corpus (slow, rate-limited, resumable)
python tools/census.py    # audit it and write data/census.json
```

`rank.py` checkpoints to `data/downloads.json` so a killed run resumes rather than restarting.

## Schedule

The census runs daily. The corpus refreshes weekly, because ranking thousands of scoped packages against a rate-limited API takes far longer than the audit, which finishes in about a minute.

## License

MIT.
