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
  **0.023** — they mostly disagree, and [that turned out to be the most
  interesting result in the project](#the-hypothesis-i-tested-and-lost).
- **Every run is attributable.** `make save` writes a manifest (input SHA-256,
  config, library versions, metrics, quantile profile) plus the flagged review
  queue as Parquet. No model is pickled — [and proving that claim is what
  exposed a real determinism bug](#reproducibility-and-the-bug-it-exposed).
- **Drift is measured, not assumed.** `make drift` scores a new file and computes
  the Population Stability Index of seven features against a stored baseline.
  [Its first run caught a limitation of my own demo
  dataset.](#drift-monitoring-and-what-it-caught-on-run-one)
- **Tested.** 297 tests, including hand-built fixtures that encode every dirty-data
  trap in the source file, a test asserting every branch of the flagging `CASE`
  chain is reachable (after one was found to be dead code), and tests that assert
  the documented API example resolves against real data.
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
- **Case-mix normalisation, implemented and then reported honestly.** The forest
  was fed DRG-normalised log-ratios on the hypothesis that it would agree more
  with the peer z-score. Agreement went *down* (Jaccard 0.049 → 0.023). The
  normalisation is still the default, because it is the version that catches the
  one planted anomaly this project has ground truth for — [the full story is
  here](#the-hypothesis-i-tested-and-lost).
- **Degenerate cases are reported, not imputed.** 48 rows sit in peer groups with
  zero dispersion; they are marked `degenerate_peers` and excluded from flagging
  rather than being assigned a fabricated score.

</details>

---

## Quickstart

```bash
make install-dev        # runtime + dev deps
make test               # 297 tests, hermetic (uses the committed sample)
make templates          # compile + self-test the C++ reference in prep/
make report             # full pipeline -> anomaly report on stdout + JSON
make fairness           # flag-rate audit across hospital size bands and states
make serve              # API + dashboard on :8000  (/dashboard, /docs)
```

`make report` works immediately against the committed 24k-row sample. For the
full dataset:

```bash
make data               # fetches + validates the 34 MB CMS file (~1 min)
make report             # now runs on all 197,283 rows
```

The operational loop — score a file, keep the receipt, check the next one against it:

```bash
make save               # score + write artifacts/runs/<run_id>/{manifest.json,outliers.parquet}
make runs               # list saved runs (id, rows, flagged, source sha)
make drift              # PSI of a new file against the latest baseline; exits non-zero on significant shift
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
| Isolation Forest | multivariate profile, DRG-normalised | 1,958 | 0.99% |
| **Both agree** | | **136** | **0.07%** |

Jaccard agreement: **0.0226**.

### The hypothesis I tested and lost

The forest originally received **raw dollar magnitudes**, which made it a
case-mix detector rather than a billing detector: a heart transplant at $3.1M is
genuinely extreme in absolute terms and perfectly normal *for a heart transplant*.
The peer z-score conditions on DRG, so it detects unusual **billing behaviour**.

So I predicted the obvious thing: normalise the forest's inputs against each DRG's
national median and the two detectors will start agreeing, because both would then
be asking about behaviour rather than about how expensive the procedure is.

I implemented it — log-ratios against the DRG's national median, computed with
window functions in the same SQL pass so the normalisation stays independent of
the peer hierarchy — and measured it on all 197,283 rows:

| Forest features | Forest flags | Both agree | Jaccard | Median × peer among forest flags | Share of forest flags ≥ 1.5× |
|---|---:|---:|---:|---:|---:|
| raw magnitudes | 1,954 | 295 | **0.050** | 1.31× | 40.3% |
| case-mix normalised | 1,958 | 136 | **0.023** | 0.96× | 20.4% |

**The hypothesis was wrong.** Agreement did not rise; it halved.

The reason is that normalisation does not remove the forest's reference frame, it
*replaces* it. Raw features ask "is this billing extreme in absolute dollars?"
Normalised features ask "is this hospital extreme for its DRG *nationally*?"
Neither asks the peer z-score's question — "is this hospital extreme among the
hospitals it actually competes with?" Moving the forest from an absolute frame to
a national one moved it further from a *local* statistic, not closer.

The evidence for that reading is in the table: the median forest-flagged row under
normalisation bills **0.96× its peer median** — slightly *below* its local peers.
Those are not broken rows. They are claims that are unusual nationally and
unremarkable locally, which is a different and legitimate thing to surface. The
top three are all Californian (UC Davis, Kaiser Redwood City, Kaiser San Jose):
normalising against a *national* DRG median makes a systematically high-cost
region look anomalous. That is a new confound, not the absence of one.

**I kept the normalised features as the default anyway**, because of the one place
in this project that has ground truth. `tests/fixtures/tiny_claims.csv` plants a
30× outlier inside an 8-hospital peer group:

| Forest features | Planted anomaly flagged? | Its percentile |
|---|---|---:|
| raw magnitudes | **no** | 0.941 — a near miss, under the contamination cut |
| case-mix normalised | **yes** | 1.000 |

So the honest summary is: these two detectors are **complementary, not
redundant**; agreement between them is the wrong thing to optimise; and the choice
of default is defended on the only labelled example available rather than on an
argument about which features sound more principled. Both feature sets are
exported (`RAW_MAGNITUDE_FEATURES`, `CASE_MIX_NORMALIZED_FEATURES`) and one config
field apart, so the comparison is reproducible rather than a claim.

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

## Running it as a system

A pipeline that produces a report once is a script. What follows is what makes it
operable: an HTTP surface, a receipt for every run, and a check on the next file
that arrives.

### API and dashboard

`make serve` starts FastAPI on `:8000`. The pipeline runs **once at startup** (~8s
for 197k rows) and every request is then a lookup against DuckDB views through a
per-request cursor.

| Endpoint | What it returns |
|---|---|
| `GET /dashboard` | self-contained HTML analytics dashboard |
| `GET /health` | version, rows loaded, source file, pipeline seconds |
| `GET /summary` | headline metrics + how often each guard fired |
| `GET /peer-levels` | which rung of the hierarchy served how many rows |
| `GET /outliers` | flagged claims ranked by practical significance (`?state=&min_excess=&agreed_only=`) |
| `GET /providers/{id}` | one hospital's billing profile across every DRG it reported |
| `GET /distribution` | server-side histogram of a whitelisted column, with flags per bucket |
| `GET /reference` | distinct DRG / HRR / state strings, so clients can build valid requests |
| `POST /score` | "would *this* claim be flagged?" — same SQL guards as the batch run |
| `GET /docs` | OpenAPI / Swagger UI |

Three details worth defending:

- **`POST /score` does not reimplement the rules.** It evaluates the *same*
  `flagged_expression()` / `reason_expression()` SQL the batch pipeline uses,
  against a one-row inline relation. A second copy in Python would eventually
  disagree with the first, and then the API would answer "would this be flagged?"
  differently from the report that produced the numbers.
- **The dashboard has no CDN, no webfont and no absolute URL.** One HTML file,
  inline CSS and JS, hand-built SVG charts, every request a relative fetch. It
  renders identically offline, in CI, in a container with no egress and behind a
  reverse proxy, and needs no CORS configuration. `tests/test_dashboard.py`
  asserts all of that against the file's text, so adding a
  `<script src="https://…">` later fails the build.
- **`/distribution` bins in DuckDB, and its column parameter is a whitelist.** The
  column name is interpolated into SQL, so it is validated against a closed set
  (anything else is a 422) rather than sanitised. Bucket counts are asserted to sum
  exactly to the rows binned, and the flagged counts to sum to the summary's — a
  histogram that quietly drops data is worse than no histogram.

The documented `POST /score` example is the most extreme claim in the committed
sample, so a reviewer's first request returns a flagged verdict with a dramatic,
explainable number: **Denton Regional Medical Center (TX), DRG 440 — $53,062
charged against $953 of Medicare payment.** A 55.7× markup, where its 31 Texas
peers median at 9.15× → 6.09× peer excess, robust z 10.7. `tests/test_docs.py`
asserts that the example parses, resolves to a real peer group, and flags. An
earlier example was real but *unremarkable*, and correctly returned "within peer
range" — accurate, and a terrible first impression.

### Reproducibility, and the bug it exposed

`make save` writes `artifacts/runs/<run_id>/`:

- **`manifest.json`** — input path, size and **SHA-256**; the exact scoring config;
  library versions (claimsight, Python, DuckDB, NumPy, scikit-learn); the ingest
  report; peer-level usage; headline metrics; and a quantile profile per monitored
  feature.
- **`outliers.parquet`** — the flagged review queue, worst first, ZSTD-compressed.

**No model is pickled.** The forest is seeded and the peer statistics are pure
aggregates, so the manifest plus the committed data *is* the reproduction recipe; a
pickled artifact would be a second source of truth that could silently disagree
with the code that produced it. That is an argument, and arguments like that
deserve to be tested rather than asserted:

> Two runs of the same code on the same file disagreed by **37 forest flags**
> (1,923 vs 1,960). The cause was not the seed: the fit subsample is drawn
> *positionally* (`rng.choice` over row indices) from a view built out of joins and
> window aggregates, and DuckDB does not guarantee a plan's output order across
> runs or thread schedules. Different arrival order → different 50k subsample →
> different forest.
>
> Fix: `ORDER BY` the unique claim key (`provider_id`, `drg`) before sampling —
> about 0.1s on 197k rows. Three separate processes now produce bit-identical
> scores (an MD5 over all 197,283 rounded score values agrees), and
> `TestForestDeterminism` pins it by fitting the forest on the fixture with rows
> arriving in reverse order and asserting identical output. Remove the `ORDER BY`
> and that test fails.

Without this, every number in the "raw vs normalised" comparison above would have
been unfalsifiable — the run-to-run noise (±37 flags) is the same size as the
effect being measured.

### Drift monitoring, and what it caught on run one

`make drift` scores a new file with the same config and computes the **Population
Stability Index** of seven features against the baseline's stored quantile edges:

```
PSI = Σ (actualᵢ − expectedᵢ) · ln(actualᵢ / expectedᵢ)
```

PSI rather than a KS test because it is bucket-based and therefore explainable to a
non-statistician ("the share of claims in the top charge decile doubled"), and
rather than mean/variance comparison because billing distributions are heavily
skewed — a mean can stay flat while the tail moves substantially, which is exactly
the change that matters here. Bands are conventional (< 0.10 quiet, 0.10–0.25
moderate, > 0.25 significant) and the command **exits non-zero on a significant
shift**, so it can gate a scheduled job.

Nothing is retrained automatically. The honest answer to "what do you do about
drift?" is *tell a human, with a number attached*.

The first real comparison was the committed 24,370-row sample against a full-file
baseline, and it found something I had assumed was fine:

| Feature | PSI | Band |
|---|---:|---|
| `relative_excess` | **0.133** | **moderate shift** |
| `markup_ratio` | 0.063 | quiet |
| `avg_covered_charges` | 0.017 | quiet |
| `non_medicare_share` | 0.003 | quiet |
| `avg_total_payments` | 0.0004 | quiet |
| `total_discharges` | 0.0003 | quiet |

The **raw inputs** are faithfully reproduced by the stratified sample. The
**derived** statistic is not: the flag rate goes 2.13% on the full file → 3.63% on
the sample, a 1.49 pp gap, because sampling thins the peer groups and a thinner
peer group has a noisier median to be compared against. Two consequences, now
documented rather than hidden:

1. Numbers measured on the sample are not numbers about the dataset. Every figure
   in this README is from the full 197,283-row file.
2. A drift monitor earns its keep on the first run if it tells you something you
   did not already know about your own data.

Making PSI say "no change" when nothing changed took two fixes, both pinned by
tests. Quantile edges **collapse on tied integer columns** — `total_discharges` has
a median of 21 and a long left tail of 1s and 2s, so several deciles land on the
same value and the surviving bins are not equal-sized; baseline proportions are
therefore *measured* rather than assumed uniform. And edges are **rounded before
the baseline is counted**, because rounding afterwards moves boundary values into a
neighbouring bin and reports drift where nothing moved. Before both fixes, a file
compared against itself scored PSI 0.0175 — small enough to look plausible, which
is exactly what makes it dangerous.

---

## Architecture

```
src/claimsight/
├── schema.py    # explicit column spec + two provably-agreeing parsers
├── ingest.py    # CSV -> typed DuckDB view, with a data-quality report
├── peers.py     # hierarchical peer groups + robust statistics
├── scoring.py   # robust z, confidence, guards, case-mix normalisation, Isolation Forest
├── registry.py  # run manifests, review-queue Parquet, run-to-run comparison
├── monitor.py   # distribution profiles + PSI drift detection
├── config.py    # typed settings (pydantic-settings, env-overridable)
├── cli.py       # ingest | peers | report | fairness | save | runs | drift
└── api/         # FastAPI service
    ├── main.py                 # routes; /score reuses the batch SQL guards
    ├── deps.py                 # pipeline built once at startup, per-request cursors
    ├── schemas.py              # pydantic contracts (extra="forbid")
    └── static/dashboard.html   # the whole UI: one file, zero external requests
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
- **Manifests store quantile edges, not data.** A run's drift profile is ~9 numbers
  per feature, which is enough to compute PSI against it months later without
  retaining a 34 MB source file or a model blob.
- **A run is identified by timestamp and content hash, not a mutable tag.** Tags get
  moved; `20260916T195206Z` plus the input's SHA-256 cannot be.
- **Cursor boundaries are tested, not assumed.** DuckDB's `con.register()` creates a
  *connection-local* virtual table: a view over it works on the connection and 500s
  on any `con.cursor()`. The API takes a cursor per request, so the forest's scores
  are materialised into a real catalog table — and `TestCursorVisibility` crosses
  that boundary over HTTP with the forest enabled, because the failure was invisible
  to every test that stayed on one connection.

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
4. **The Isolation Forest has no good reference frame.** Case-mix normalisation is
   implemented and is the default, but as documented above it traded an
   absolute-dollars confound for a regional-cost one — the top of the normalised
   forest is three Californian hospitals, because their costs are high against a
   *national* DRG median. Neither feature set asks the peer z-score's question.
   This is a limitation of the second detector, and it is why the forest is
   presented as a second opinion to read alongside the peer statistic rather than
   as half of an ensemble vote.
5. **Fairness risk is real and only partly measured.** `make fairness` reports
   flag rates by hospital volume band and state. Small and rural hospitals have
   thinner peer groups and noisier averages, so they are at genuine risk of
   systematic over-flagging. Confidence weighting mitigates this; it does not
   eliminate it, and a production deployment would need an explicit audit.
6. **`markup_ratio` is a crude signal.** Charges are list prices, not payments,
   and hospital chargemasters vary wildly in how they relate to actual revenue.
   A production system would model payments, not charges.
7. **The committed sample over-flags.** 3.63% on the sample vs 2.13% on the full
   file (PSI 0.133 on `relative_excess`), because stratified sampling thins the
   peer groups and a thin group has a noisier median. The sample is right for tests
   and for exercising every code path; it is wrong for quoting numbers. Every
   figure in this README is from the full 197,283-row file, and `make drift` is
   what tells me so rather than my say-so.
8. **Drift is detected, not handled.** PSI raises a number and a non-zero exit
   code. There is no automatic re-baselining, no threshold adaptation and no
   alerting integration — those need a decision about who is responsible for the
   answer, which is an organisational question this project does not get to skip
   past by writing code.

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
cores in about 8 seconds.

**297 tests, ~1 min, hermetic** (no network, no full dataset, no GPU). They run
against a hand-built 18-row fixture and pin the things that fail silently:

| Suite | Tests | What it protects |
|---|---:|---|
| `test_schema` | 39 | both parsers (Python and DuckDB SQL) agree on the same `Column.expression()` objects |
| `test_fetch_data` | 7 | `make data` detects archives by magic bytes, not URL suffix (a codeload URL ends in the branch name) |
| `test_ingest` | 15 | dirty-data traps: `"$1,016,806.46"`, `"1,134"`, `010033`, corrupt `N/A` money rejected and counted |
| `test_peers` | 12 | hierarchy fallback order and `min_support` behaviour |
| `test_scoring` | 44 | every `flag_reason` branch reachable, dispersion fallback chain, Python twins of the SQL maths, forest determinism under reordered input |
| `test_monitor` | 28 | PSI is 0 against its own profile (tied columns included), finite when a bin empties, bands, localisation |
| `test_registry` | 26 | manifest provenance, review-queue contents and ordering, run comparison, byte-stable re-saves |
| `test_api` | 34 | status codes, validation boundaries, `/score` agrees with the batch pipeline, everything works over HTTP **with the forest enabled** |
| `test_cli` | 24 | commands print real numbers; `drift` exit codes 0 / 1 / 2 |
| `test_dashboard` | 46 | the asset is self-contained, only calls routes that exist, and reads no field the API does not return |
| `test_docs` | 12 | documented examples resolve against real data; the test counts quoted above match the live collection |
| `test_prep` | 10 | the C++ reference in `prep/` compiles warning-free at `-O0`/`-O2` and passes its own asserts; every section the docs cross-reference exists |

Those counts are asserted against the live collection by `test_docs.py`, so a stale
number here fails the build rather than quietly embarrassing anyone.

### Data provenance

CMS Medicare Provider Charge Data: Inpatient FY2016, published by the US Centers
for Medicare & Medicaid Services. US Government work. `make data` fetches and
structurally validates it; it is never committed. The committed
`data/sample/cms_inpatient_fy2016_sample.csv` (24,370 rows, 4.2 MB) is a seeded
stratified sample retaining all 573 DRGs, all 51 states, and every row flagged on
the full data — built by `scripts/make_sample.py` and reproducible byte-for-byte.
