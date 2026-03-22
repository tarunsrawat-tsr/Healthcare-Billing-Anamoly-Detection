# claimsight

**Billing-anomaly detection over 197,283 rows of real US Medicare inpatient claims — built as a tested, containerised data service.**

`claimsight` ingests the CMS Medicare Provider Charge File (FY2016), builds
statistically valid peer groups for every claim, scores hospitals whose charges
are outliers relative to those peers, and serves the results behind a versioned
HTTP API.

> **Not a diagnostic or legal tool.** This is a decision-support *research*
> project over public, de-identified, aggregated government data. Flagging a
> hospital here means "this billing pattern is statistically unusual relative to
> peers and worth a human looking at it" — never "this hospital committed
> fraud". See [Limitations](#limitations-and-honest-caveats).

---

## Read this first, depending on who you are

<details>
<summary><b>🔧 Engineering reviewer — what the system does</b></summary>

- **Explicit-schema ingestion.** No CSV type inference anywhere in the pipeline.
  Every column is declared and parsed through tested converters. See
  [Why the schema is explicit](#why-the-schema-is-explicit-the-part-worth-reading).
- **DuckDB as the execution engine.** All heavy work (197k-row ingest, peer-group
  window functions, aggregation, joins) runs inside DuckDB, set-based. Full
  pipeline: **~9 seconds on 2 CPU cores**.
- **Hierarchical peer groups with recorded fallback.** 43% of the natural
  `DRG × HRR` grouping is singletons; the pipeline falls back through
  `DRG × State` → `DRG × national` and **records which level it used**, so
  uncertainty is part of the output rather than hidden inside it.
- **Two independent detectors, disagreement reported not smoothed over.**
  A robust peer z-score and an Isolation Forest. Their Jaccard overlap is
  **0.047** — they mostly disagree, and [that turned out to be the most
  interesting result in the project](#the-result-i-did-not-expect).
- **Tested.** 94 tests, including hand-built fixtures that encode every dirty-data
  trap in the source file, and a test asserting every branch of the flagging
  `CASE` chain is reachable (after one was found to be dead code).
- **Reproducible data.** The full 34 MB file is fetched and *structurally
  validated* by `make data`; a seeded, stratified 24,370-row sample is committed
  so CI and the demo never depend on a network download.

</details>

<details>
<summary><b>📊 ML reviewer — how the model works</b></summary>

- **No labels exist**, so there is no accuracy to report. Validity is argued
  structurally instead: interpretability, two-method agreement, and explicit
  guards against the failure modes that produce confident nonsense.
- **Robust statistics, not means.** Peer groups are summarised by **median and
  MAD**, because markup ratios run 0.26 → 55.7 with a median of 4.75. Mean/std
  would let one extreme hospital define "normal" for its whole peer group and
  mask the outliers being searched for.
- **Confidence as a first-class output.** Every score carries a confidence in
  `(0, 1]` from a geometric mean of peer-support and case-volume saturation
  terms. Flagging requires it to clear a floor — low-volume hospitals are
  down-weighted, **not deleted**, because deleting them would systematically
  remove small and rural providers from the analysis.
- **Statistical *and* practical significance.** A 3.5σ deviation that amounts to
  billing 4% above peers is not worth a human's time. Flags must clear both a
  z-threshold and a `1.5×` peer-median effect-size floor.
- **Degenerate cases are reported, not imputed.** 48 rows sit in peer groups with
  zero dispersion; they are marked `degenerate_peers` and excluded from flagging
  rather than being assigned a fabricated score.

</details>

---

## Quickstart

```bash
make install-dev        # runtime + dev deps
make test               # 94 tests, hermetic (uses the committed sample)
make report             # full pipeline -> anomaly report on stdout + JSON
make fairness           # flag-rate audit across hospital size bands and states
make serve              # FastAPI service on :8000  (/docs for OpenAPI)
```

`make report` works immediately against the committed 24k-row sample. For the
full dataset:

```bash
make data               # fetches + validates the 34 MB CMS file (~1 min)
make report             # now runs on all 197,283 rows
```

---

## The data

| | |
|---|---|
| Source | CMS Medicare Provider Charge Data — Inpatient, FY2016 (US Government work) |
| Rows | 197,283 |
| Providers | 3,208 hospitals |
| DRGs | 573 procedure groups |
| HRRs | 306 hospital referral regions |
| States | 51 |
| Size | 34 MB (fetched, not committed) |

Each row is one hospital's **average** billing for one procedure group:
covered charges, total payments, and the Medicare portion, plus discharge volume.

A **DRG** (Diagnosis-Related Group) is the procedure/case category Medicare pays
by — "470 - Major joint replacement", "001 - Heart transplant". Comparing
hospitals only makes sense *within* a DRG, since a heart transplant legitimately
costs more than a knee replacement.

### Why the schema is explicit (the part worth reading)

Automatic CSV type inference fails on this file in **three independent ways**,
each discovered by profiling the real data:

**1. Money columns are formatted strings.** `"$1,016,806.46"` cannot be typed as
a number, so inference falls back to `VARCHAR`. Arithmetic then fails — and worse,
`ORDER BY` silently becomes *lexicographic*. Because `$` is `0x24` and `9` is
`0x39`, every `$`-prefixed value sorts below a bare digit:

```
string sort "top" charge:  98136.7      ← wrong
true maximum charge:       $3,104,374   ← never surfaces, off by 31x
```

**2. Sampling-based inference breaks mid-file.** `Total Discharges` is inferred as
`BIGINT` from the first 20,480 sampled rows, which all happen to be under 1,000
and therefore comma-free. **Row 111,180 contains `"1,134"`** and the read
hard-fails — or, with error tolerance enabled, silently drops rows. A pipeline
that passes on a sample and dies in production.

**3. Zero-padded identifiers get eaten.** `Provider Id` (`010033`) and
`Provider Zip Code` (`02115`) are identifiers that look numeric. Cast to integers
they lose leading zeros, and joins against any other CMS vintage break *quietly*.

The fix is one rule, enforced by `src/claimsight/schema.py`: **declare every
column, read everything as `VARCHAR`, parse through tested converters.** DuckDB's
`auto_detect=false` plus an explicit `columns={...}` map removes inference from
the pipeline entirely.

Two parsers exist — pure Python (API layer, unit tests) and DuckDB SQL (the
197k-row batch path) — and `tests/test_schema.py` runs the *same*
`Column.expression()` object through both to prove they cannot drift apart.

---

## How scoring works

### Step 1 — peer groups, with a hierarchy

The obvious grouping, `DRG × HRR`, is **too granular to be usable**:

```
DRG × HRR groups ......... 58,441
  singletons (n = 1) ..... 24,976   (43%)
  groups with n < 5 ...... 46,847   (80%)
  median group size ...... 2
```

A robust z-score over a group of one is undefined; over a group of two it is
noise. Scoring naively therefore returns confident-looking garbage for **43% of
rows** — the worst class of bug, because nothing errors.

`claimsight` walks a hierarchy, finest first, using the first level with at least
`min_support` (default 8) members:

```
drg_hrr  →  drg_state  →  drg_national
```

On the real file, **59% of rows could not be scored at the finest level**:

| Level used | Rows | % | Median group size |
|---|---:|---:|---:|
| `drg_state` | 90,808 | 46.0% | 31 |
| `drg_hrr` | 81,121 | 41.1% | 15 |
| `drg_national` | 25,354 | 12.9% | 236 |

The level actually used is written to every output row (`peer_level`, `peer_n`).
A score from 8 peers is visibly less trustworthy than one from 2,837.

### Step 2 — robust z-score

```
z = (markup_ratio − peer_median) / (1.4826 × MAD)
```

with `markup_ratio = avg_covered_charges / avg_medicare_payments`. The 1.4826
factor makes MAD comparable to a standard deviation under normality.

**Dispersion fallback chain:** `MAD → IQR/1.349 → NULL`. If both are zero the row
is marked `dispersion_degenerate` and excluded rather than assigned a fabricated
score.

### Step 3 — confidence

```
support = peer_n        / (peer_n        + 20)
volume  = discharges    / (discharges    + 50)
confidence = sqrt(support × volume)
```

Each term saturates quickly then flattens, which is the right shape for
statistical support (8 → 30 peers matters a lot; 300 → 3,000 barely matters).
The **geometric mean** punishes weakness on *either* axis instead of letting
strength on one compensate.

### Step 4 — two detectors, and their disagreement

| Detector | What it sees | Flagged | Rate |
|---|---|---:|---:|
| Peer robust z | billing vs. same-procedure peers | 4,207 | 2.13% |
| Isolation Forest | multivariate raw features | 1,978 | 1.00% |
| **Both agree** | | **276** | **0.14%** |

Jaccard agreement: **0.047**.

### The result I did not expect

The two methods barely overlap, and finding out why was the most valuable part of
this project.

The Isolation Forest receives raw dollar magnitudes, so it is dominated by
**case-mix**: a heart transplant at $3.1M is genuinely unusual in absolute terms
but perfectly normal *for a heart transplant*. The peer z-score conditions on DRG,
so it detects unusual **billing behaviour**. They are answering different
questions, and a low overlap is the correct result — not a bug to tune away.

The identified fix is to feed the forest DRG-normalised features (ratios against
the national DRG median) so case-mix stops confounding it, while keeping that
computation independent of the peer median/MAD used by the z-score. That is
tracked as the next iteration rather than quietly patched.

### A bug the guards were built to fix

The first ranking was ordered by raw z and returned **eight Washington DC
hospitals at z = 134…192**. That looked like a spectacular finding. It was an
artifact: DC has a single HRR and few hospitals, so its peer groups are small and
homogeneous, MAD approaches zero, and dividing by it inflates z without bound.

Two guards were added, both config-driven and both covered by regression tests:

| Guard | Rule | Rows rejected |
|---|---|---:|
| `thin_dispersion` | `MAD ≥ 5%` of peer median, else z is not meaningful | 4,327 |
| `immaterial_excess` | must also exceed `1.5×` peer median (practical significance) | 92 |

Ranking is now by `relative_excess` (how many times the peer median), not raw z.
The top 10 spans **PA, DC, NJ, NC, KS, TX, FL** instead of one city, and the
maximum effect size is a comprehensible **8.2× peer median**.

Every guard reports why a row was *not* flagged:

| Reason | Rows | % |
|---|---:|---:|
| `within_peer_range` | 183,845 | 93.19% |
| `low_confidence` | 4,764 | 2.42% |
| `thin_dispersion` | 4,327 | 2.19% |
| `high_markup` | 4,025 | 2.04% |
| `low_markup` | 182 | 0.09% |
| `immaterial_excess` | 92 | 0.05% |
| `degenerate_peers` | 48 | 0.02% |

A test asserts **every one of these branches is reachable** — after an earlier
version shipped a `CASE` chain in which `degenerate_peers` was shadowed by a
`robust_z IS NULL` test and could never fire.

---

## Architecture

```
src/claimsight/
├── schema.py    # explicit column spec + two provably-agreeing parsers
├── ingest.py    # CSV -> typed DuckDB view, with a data-quality report
├── peers.py     # hierarchical peer groups + robust statistics
├── scoring.py   # robust z, confidence, guards, Isolation Forest, summary
├── config.py    # typed settings (pydantic-settings, env-overridable)
├── cli.py       # ingest | peers | report | fairness
└── api/         # FastAPI service
scripts/
├── fetch_data.py    # download + structurally validate the CMS file
└── make_sample.py   # rebuild the committed stratified sample (seeded)
tests/
└── fixtures/tiny_claims.csv   # hand-built: one row per edge case
```

`tests/fixtures/tiny_claims.csv` is hand-written rather than sampled so every
branch has a row that exercises it: an 8-row peer group containing a 30× outlier,
an 8-row group where **all markups are identical** (MAD = 0), a singleton DRG
forcing full hierarchy fallback, a `"1,134"` discharge count, zero-padded IDs, and
a corrupt `N/A` money value that must be rejected rather than silently dropped.

### Design decisions worth defending

- **Rejected rows stay inspectable.** `claims_raw` keeps every row; `claims`
  filters to usable ones. A loader that cannot tell you what it dropped cannot be
  trusted. The ingest always emits an `IngestReport`.
- **Results are bound by column name, never position.** An earlier `summarise()`
  indexed a result tuple; adding one column to the `SELECT` silently shifted every
  value. That bug class produces confident, wrong reports, so it is designed out.
- **Idempotent re-scoring.** `score_claims()` drops dependent views first, because
  DuckDB refuses to replace a view others reference — otherwise the CLI could not
  re-run with different thresholds.
- **Sample data is raw, not cleaned.** The committed sample preserves
  `"$1,016,806.46"` and `010033` so the parsing tests can actually fail.
- **Checksums are the wrong validation.** Mirrors of the same dataset differ in
  line endings and row order, so `fetch_data.py` validates *structure* (header,
  197,283 rows, 573 DRGs) and reports the MD5 for information only.

---

## Limitations and honest caveats

Stated plainly, because a project that overclaims is worse than one that is
modest and accurate.

1. **No ground truth.** Nobody has labelled these rows. Nothing here establishes
   that any flagged hospital did anything wrong; the output is a *review queue*,
   not a finding.
2. **Not clinical or legal evidence.** Aggregated public billing data, one fiscal
   year, US Medicare inpatient only. No patient-level data is used or implied.
3. **Peer statistics are unweighted.** Each row is itself an average over
   `total_discharges` cases, so an 11-discharge row is a noisier estimate than a
   4,043-discharge row. This is handled by an explicit confidence term rather
   than by discarding low-volume hospitals — but a discharge-weighted median is
   the better statistic and is not implemented.
4. **Case-mix confounds the Isolation Forest.** Documented above with the
   proposed fix; not yet fixed.
5. **Fairness risk is real and only partly measured.** `make fairness` reports
   flag rates by hospital volume band and state. Small and rural hospitals have
   thinner peer groups and noisier averages, so they are at genuine risk of
   systematic over-flagging. Confidence weighting mitigates this; it does not
   eliminate it, and a production deployment would need an explicit audit.
6. **`markup_ratio` is a crude signal.** Charges are list prices, not payments,
   and hospital chargemasters vary wildly in how they relate to actual revenue.
   A production system would model payments, not charges.

---

## Development

```bash
make lint        # ruff
make format      # ruff format + import sorting
make coverage    # pytest with coverage
make docker-build && make docker-run
```

Python ≥ 3.10. Core dependencies: DuckDB, pandas, NumPy, scikit-learn, FastAPI,
Pydantic v2, Typer, Rich. **No GPU required** — the entire pipeline runs on 2 CPU
cores in about 9 seconds.

### Data provenance

CMS Medicare Provider Charge Data: Inpatient FY2016, published by the US Centers
for Medicare & Medicaid Services. US Government work. `make data` fetches and
structurally validates it; it is never committed. The committed
`data/sample/cms_inpatient_fy2016_sample.csv` (24,370 rows, 4.2 MB) is a seeded
stratified sample retaining all 573 DRGs, all 51 states, and every row flagged on
the full data — built by `scripts/make_sample.py` and reproducible byte-for-byte.
