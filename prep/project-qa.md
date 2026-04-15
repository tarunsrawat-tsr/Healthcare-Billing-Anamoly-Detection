# claimsight — interview Q&A sheet

Everything an interviewer can reasonably ask about this project, with answers you
can actually say out loud. Written to be *drilled*, not read once.

**How to use this:**
1. Memorise §1 (the pitch) and §12 (the numbers). Those two cover 60% of what happens.
2. Then drill §8 — the four failure stories. They are the highest-value answers in
   this document because they are the ones nobody else in the room can give.
3. Everything else is reference. Read a section, close the file, say the answers
   aloud. If you can't say it without looking, you don't know it yet.

Rule for all of these: **answer in 30–60 seconds, then stop.** Interviewers ask
follow-ups when they want more; a three-minute monologue reads as rehearsed.

---

## 1. The 60-second pitch (memorise this verbatim-ish)

> "claimsight ingests the CMS Medicare inpatient charge file — 197,283 rows of
> real published billing data — and flags hospitals whose charges are outliers
> against statistically valid peer groups.
>
> The interesting problem isn't the model, it's that the obvious grouping is
> unusable: DRG × hospital-referral-region gives 58,000 groups, 43% of which are
> a single hospital. A z-score over a group of one is undefined, and nothing
> errors — you just get confident garbage for 43% of rows. So the pipeline walks a
> hierarchy, DRG×HRR → DRG×state → DRG×national, takes the first level with
> enough members, and records which level it used on every output row.
>
> Scoring is a robust median/MAD z-score with guards for degenerate dispersion and
> a practical-significance floor, plus an Isolation Forest as an independent
> second opinion. Everything runs in DuckDB — the full pipeline is about 8 seconds
> on 2 CPU cores — and it's served over FastAPI with a self-contained dashboard.
> 287 tests, CI on three Python versions, Docker, no GPU.
>
> The part I'd most want to talk about is a hypothesis I tested and lost, and a
> nondeterminism bug that the reproducibility work exposed."

That last line is bait. It works — they will ask.

**If they only give you 20 seconds:**

> "An anomaly-detection pipeline over 197k rows of real Medicare billing data.
> DuckDB for the compute, FastAPI for the service, 287 tests. The hard part was
> that the natural peer grouping is 43% singletons, so I built a hierarchy with
> recorded fallback and robust statistics with explicit guards."

---

## 2. The two-minute walkthrough (for "take me through the architecture")

Say it in pipeline order, one sentence per stage, then stop:

1. **Ingest.** CSV → DuckDB with an *explicit* schema. `auto_detect=false`, every
   column declared, everything read as VARCHAR and parsed through tested
   converters. Emits an `IngestReport` with rows read / usable / rejected.
2. **Peers.** Window functions compute median, MAD, p25/p75 per group at three
   levels; a `CASE` picks the finest level with ≥ `min_support` members and writes
   `peer_level` + `peer_n` onto every row.
3. **Score.** Robust z, confidence, and a `CASE` chain that assigns exactly one
   `flag_reason` per row — including the reasons a row was *not* flagged.
4. **Second opinion.** Isolation Forest over case-mix-normalised features,
   materialised into a real DuckDB table and joined back on the claim key.
5. **Serve.** Pipeline runs once at startup; every request is a lookup through a
   per-request cursor. `POST /score` reuses the *same SQL expressions* as the batch
   path so the two cannot disagree.
6. **Operate.** `save` writes a manifest (input SHA-256, config, library versions,
   metrics, quantile profile) plus the review queue as Parquet; `drift` computes PSI
   of a new file against that baseline and exits non-zero on a significant shift.

Then: "Happy to go deeper on any stage — the two I'd pick are the peer hierarchy
and the determinism bug."

---

## 3. Why these technologies (the questions you *will* get)

**Why DuckDB instead of Postgres?**
> The data is a static published file, not a transactional workload. There are no
> concurrent writers, no need for a server process, and the whole query profile is
> analytical: window functions over 197k rows, group-by aggregations, column
> pruning. DuckDB is in-process and columnar, so it reads only the columns a query
> touches and needs zero infrastructure — the pipeline runs from a laptop or a
> container with no database to provision. Postgres would be the right answer the
> moment multiple services needed concurrent transactional access; that's not this.

**Why not Spark / a distributed engine?**
> 34 MB and 197k rows. Spark's driver and JVM startup would cost more than the
> entire pipeline. Distributing this would be a résumé decision, not an engineering
> one. What I did instead is keep the compute *set-based* inside one engine, so the
> same SQL would port to Spark or Postgres without a rewrite — the peer statistics
> are plain window functions, not Python loops.

**Why FastAPI?**
> Pydantic v2 validation at the boundary, generated OpenAPI docs, and dependency
> injection for the pipeline object. The validation matters here: a negative charge
> or a zero-discharge row isn't a scoring edge case, it's an invalid request, and
> saying so at the boundary is cheaper than defending against it in the maths.

**Why a hand-written HTML dashboard instead of Streamlit / React?**
> One file, inline CSS and JS, hand-built SVG charts, zero external requests. It
> renders identically offline, in CI, in a container with no egress, and behind a
> reverse proxy, and it needs no CORS configuration because every fetch is a
> relative URL to the same origin. Streamlit would have added a second server
> process and iframe/host-header friction; React would have added a build step for
> six charts. There's a test that fails the build if anyone adds a CDN reference.

**Why no deep learning?**
> No labels, and the rows are already aggregates — each row is one hospital's
> *average* billing for one procedure group. There are 197k of them and maybe a
> few hundred genuine anomalies, unlabelled. A neural net would need a supervision
> signal that doesn't exist and would give up the one thing that makes the output
> usable: every flag has a reason a human can check. I kept the ML deliberately
> thin and explainable — an Isolation Forest as a second opinion, not as the
> decision.

**Why Isolation Forest specifically?**
> It's unsupervised, it needs no distance metric assumption (billing data is
> heavily skewed, so k-means/Euclidean methods are dominated by scale), it's fast,
> and its output is a single interpretable score I can rank into percentiles. It's
> also *genuinely independent* of the peer statistic — I excluded every
> peer-derived column from its features so it can't just rediscover the z-score.

---

## 4. Data engineering questions

**What's dirty about the data?** (Know all three; they will ask for specifics.)
> Three independent failures of automatic type inference:
> 1. **Money is formatted strings** — `"$1,016,806.46"`. Inference gives VARCHAR, so
>    arithmetic fails and, worse, `ORDER BY` becomes lexicographic. Because `$` is
>    0x24 and `9` is 0x39, every dollar value sorts *below* a bare digit: the
>    string-sorted "maximum" charge was 98,136.70 when the real maximum was
>    $3,104,374 — off by 31×, silently.
> 2. **Sampling-based inference breaks mid-file.** `Total Discharges` infers as
>    BIGINT from the first 20,480 sampled rows, which are all comma-free. Row
>    111,180 contains `"1,134"` and the read hard-fails — or silently drops rows if
>    error tolerance is on. A pipeline that passes on a sample and dies in
>    production.
> 3. **Zero-padded identifiers get eaten.** `Provider Id` (`010033`) and ZIP
>    (`02115`) look numeric; cast to int they lose leading zeros and joins against
>    any other CMS vintage break quietly.
>
> The fix is one rule: declare every column, read everything as VARCHAR, parse
> through tested converters.

**Why two parsers, and how do you keep them in sync?**
> There's a pure-Python parser (used by `POST /score` and unit tests) and a DuckDB
> SQL parser (the 197k-row batch path). Two implementations of the same rule is
> exactly how they drift apart, so both are generated from the *same* `Column`
> objects — one spec, and the test suite runs the identical `Column.expression()`
> through both paths and asserts they agree.

**What happens to bad rows?**
> They're rejected, counted, and stay inspectable. `claims_raw` keeps every row;
> `claims` filters to usable ones, and the `IngestReport` reports read/usable/
> rejected. A loader that can't tell you what it dropped can't be trusted. On the
> FY2016 file exactly one row is rejected — a corrupt `N/A` money value — and the
> fixture has a row that reproduces it.

**Is the pipeline idempotent?**
> Yes. `score_claims()` drops dependent views first, because DuckDB refuses to
> replace a view that others reference — without that, the CLI couldn't be re-run
> with different thresholds. Runs are identified by timestamp plus the input's
> SHA-256, not a mutable tag, so re-running can't overwrite history.

**How would this handle a monthly file / a backfill?**
> That's what the registry and drift monitor are for. A new month's file is scored
> with the same config, saved as a new run, and drift-checked against the previous
> baseline; PSI exits non-zero on a significant shift, which is the gate for a
> human deciding whether the thresholds still make sense. Nothing retrains
> automatically — the honest answer to "what do you do about drift" is *tell a
> human, with a number attached*.

**How would you scale it 100×?**
> Three changes, in order: partition by fiscal year and run years in parallel
> (they're independent); move the peer statistics to incremental aggregation if the
> input became append-only rather than a published snapshot; and only then consider
> a distributed engine. The SQL is already set-based, so the port is mechanical.
> What I would *not* do is reach for Spark first — the bottleneck at 100× is I/O
> and memory, not CPU.

---

## 5. SQL / DBMS questions this project invites

These double as DBMS revision, because they're all real things I had to solve.

**Which window functions, and why?**
> `median(x) OVER (PARTITION BY drg, hrr)` and the MAD via a second pass, plus
> `quantile_cont` for p25/p75. Case-mix normalisation uses national DRG medians as
> window functions so a row can be compared to its DRG nationwide without a
> self-join. DuckDB returns `median() OVER (...)` as a Decimal, so it needs an
> explicit cast before float arithmetic — I hit that.

**View vs materialised table — where did it matter?**
> Two places. The forest's scores *must* be a real table: `con.register()` creates
> a **connection-local** virtual table, and a view built on it works on that
> connection but fails on any `con.cursor()` with "iforest_scores does not exist".
> The API takes a cursor per request, so that distinction was the difference
> between a working service and a 500 on every scored endpoint. Elsewhere I keep
> views, because the pipeline is a DAG that gets re-run with different thresholds
> and materialising everything would waste I/O.

**Why bind results by column name, not position?**
> An earlier `summarise()` indexed a result tuple. Adding one column to the SELECT
> silently shifted every value — the report was confidently wrong. That bug class
> is designed out now: results are bound by name.

**Indexes?**
> DuckDB uses zone maps and min/max pruning on columnar storage rather than B-tree
> indexes; the win comes from column pruning and predicate pushdown. If this were
> Postgres I'd index `(drg, hrr)` and `(provider_id)` and consider a BRIN index on
> the year-partitioned table.

**Transactions / ACID — relevant here?**
> Partly. The pipeline is read-only after startup, so isolation levels don't bite.
> But the registry writes a manifest *and* a Parquet file, which is two writes with
> no transaction — so a crash between them leaves a run directory without a
> manifest. `list_runs()` treats that as "not a run" and skips it, and there's a
> test for a corrupt manifest. That's the deliberate choice: fail-safe on read
> rather than pretend I have atomicity I don't.

**Normalisation?**
> The source is one row per provider × DRG with `(provider_id, drg)` as a natural
> key — I verified it's unique across all 197,283 rows, which is why I could use it
> as the join key and the deterministic sort order. It's 3NF-ish but with derived
> columns (markup ratio) denormalised in, because this is an analytical table, not
> an OLTP one.

---

## 6. Statistics / ML questions

**Why median and MAD instead of mean and standard deviation?**
> Markup ratios run 0.26 → 55.7 with a median of 4.75. With a mean/std, one extreme
> hospital inflates the standard deviation for its whole peer group and *masks the
> outliers I'm looking for* — the breakdown point of the mean is 0. MAD's is 50%.
> I scale MAD by 1.4826 so it's comparable to a standard deviation under normality.

**What if MAD is zero?**
> Fallback chain: MAD → IQR/1.349 → NULL. If both are zero the row is marked
> `dispersion_degenerate` and *excluded* from flagging rather than assigned a
> fabricated score. 48 rows on the full file. A Python twin of that chain exists
> for `POST /score`, and it delegates to the same `dispersion_scale` helper — an
> earlier version returned None where the SQL fell back to IQR, so the API and the
> batch disagreed. Tests pin the twins to the SQL.

**Why z > 3.5?**
> Conservative on purpose: at ~197k rows, a 3.0 threshold flags routine variation.
> But statistical significance at this sample size is cheap, so z alone isn't
> enough — a flag must *also* clear a practical-significance floor of 1.5× the peer
> median and a confidence floor of 0.25. 4,025 rows flag as high markup; 92 are
> rejected as statistically unusual but immaterial.

**Why the confidence term, and that exact formula?**
> `support = peer_n/(peer_n+20)`, `volume = discharges/(discharges+50)`,
> `confidence = sqrt(support × volume)`. Each saturates fast then flattens, which
> matches how statistical support actually works — 8 → 30 peers matters enormously,
> 300 → 3,000 barely matters. The *geometric* mean punishes weakness on either axis
> instead of letting strength on one compensate. Low-volume hospitals get
> down-weighted, **not deleted** — deleting them would systematically remove small
> and rural providers from the analysis, which is a fairness failure disguised as
> data cleaning.

**There are no labels. How do you know it works?**
> Three things, and I'm careful not to claim a fourth:
> 1. **Structural validity** — every flag carries a checkable reason, so a domain
>    expert can audit a sample rather than trust a score.
> 2. **One piece of actual ground truth** — the test fixture plants a 30× outlier
>    inside an 8-hospital peer group, and I use it to choose between feature sets.
> 3. **Two independent detectors** and their *disagreement* reported rather than
>    averaged away.
>
> What I explicitly cannot report is precision or recall. Anyone who gives you an
> accuracy number on this dataset has invented labels.

**What's the Isolation Forest actually doing?**
> Builds 150 random isolation trees on a 50k subsample; anomalies need fewer random
> splits to isolate, so path length is the score. `contamination=0.01` sets the
> threshold at the 99th percentile of scores. I negate `score_samples` so larger =
> more anomalous (matching the z-score's direction) and rank into percentiles so
> the output reads as "more anomalous than 99.2% of claims". Seeded, so it's
> reproducible — though see §8, because the seed wasn't the only thing that needed
> fixing.

**What is PSI and why that instead of a KS test?**
> Population Stability Index: `Σ (actualᵢ − expectedᵢ) · ln(actualᵢ/expectedᵢ)` over
> baseline-defined bins. Conventional bands: < 0.10 quiet, 0.10–0.25 moderate,
> > 0.25 significant. I chose it over KS because it's bucket-based and therefore
> *explainable to a non-statistician* — "the share of claims in the top charge
> decile doubled" — and over mean/variance comparison because billing distributions
> are heavily skewed: a mean can stay flat while the tail moves substantially, and
> the tail is exactly what this project cares about.

**What did the drift monitor find on its first real run?**
> Something about my own demo data. The committed 24,370-row stratified sample
> reproduces the raw inputs faithfully — PSI ≤ 0.06 on charges, payments,
> discharges — but the *derived* statistic drifts: PSI 0.133 on `relative_excess`,
> and the flag rate goes 2.13% → 3.63%, a 1.49 percentage-point gap. Because
> sampling thins the peer groups, and a thinner group has a noisier median to be
> compared against. Consequence: every number I quote is from the full file, and
> the sample is for tests and code paths, not for measuring anything.

---

## 7. System design / production questions

**How does the service handle concurrency?**
> The pipeline runs once at startup and the connection is shared read-only. DuckDB
> connections aren't safe to share across threads for concurrent writes, so each
> request takes its own `cursor()`. All post-startup operations are reads. Under
> real load I'd put a pool of cursors behind the app or run multiple workers with a
> pre-built database file rather than rebuilding in each process.

**Startup takes 8 seconds. Is that acceptable?**
> For a service over a static published file, yes — it trades 8 seconds once for
> single-millisecond lookups forever. I'd change it if the data became streaming:
> then you'd persist the scored database to disk (or Parquet) and memory-map it, so
> restarts don't recompute.

**What's the latency of a request?**
> Lookups against a materialised view over 197k rows: single-digit milliseconds for
> `/summary` and `/outliers`, and `/distribution` does its binning in DuckDB and
> returns ~40 numbers instead of shipping 197k values to a browser.

**How do you prevent SQL injection?**
> Two layers. All *values* are bound parameters — never interpolated. The one place
> a name enters SQL is `/distribution`'s column parameter, which is a closed
> whitelist validated by a `Literal` type at the boundary: anything else is a 422
> before a query is built. There's a test parametrised over injection strings.

**What's missing for production?**
> Auth and audit logging on the review queue (it's a list of named hospitals —
> access control is not optional); alerting integration on drift; a discharge-
> weighted peer median, since each row is itself an average over `total_discharges`
> cases and an 11-discharge row is a noisier estimate than a 4,043-discharge one;
> and a human-in-the-loop workflow that records what a reviewer *decided*, which is
> how you'd eventually get labels.

**How would you get labels?**
> From the reviewers. Every flag goes into a queue; if the tool records dispositions
> (confirmed / dismissed / escalated), after a few months you have weak labels and
> can start measuring precision and tuning thresholds against outcomes instead of
> against my priors. That's the actual path from "statistically unusual" to
> "validated", and it's a workflow question before it's a modelling question.

---

## 8. The four failure stories (highest-value answers — drill these)

Each is 45 seconds. Structure: **what I believed → what I measured → what it
actually meant → what I changed.** The fourth beat is what makes it a story about
engineering rather than a confession.

### 8.1 The hypothesis I tested and lost

> "The Isolation Forest originally got raw dollar amounts, so it was really a
> case-mix detector: a $3.1M heart transplant is extreme in absolute terms and
> completely normal *for a heart transplant*. I predicted that normalising its
> inputs against each DRG's national median would make it agree more with the peer
> z-score, since both would then be measuring behaviour rather than how expensive
> the procedure is.
>
> I implemented it — log-ratios against national DRG medians, computed with window
> functions in the same SQL pass, deliberately independent of the peer hierarchy —
> and measured it on all 197k rows. Agreement didn't rise. It **halved**: Jaccard
> 0.050 → 0.023.
>
> The reason is that normalisation doesn't remove a reference frame, it *replaces*
> one. Raw asks 'extreme in absolute dollars?' Normalised asks 'extreme for this
> DRG *nationally*?' Neither asks the peer z-score's question, which is 'extreme
> among the hospitals you actually compete with?' Moving the forest from an
> absolute frame to a national one moved it *further* from a local statistic.
>
> The evidence is in the numbers: the median forest-flagged row under normalisation
> bills 0.96× its peer median — slightly *below* its local peers. And the top three
> are all Californian, because normalising against a national median makes a
> high-cost region look anomalous. That's a new confound, not the absence of one.
>
> I kept the normalised features as the default anyway, because of the one place in
> the project with ground truth: the fixture's planted 30× outlier is *missed* by
> the raw forest (percentile 0.941, just under the contamination cut) and *caught*
> by the normalised one (1.000). So the honest conclusion is that the detectors are
> complementary, not redundant — and agreement between them was the wrong metric to
> optimise."

**Likely follow-ups:**
- *"So which one should a reviewer trust?"* → The peer z-score for "unusual billing
  behaviour", the forest for "unusual profile nationally for this procedure". They
  overlap on 136 rows, and those are where I'd start a review, but I'd never present
  the forest alone.
- *"Would you tune contamination to increase overlap?"* → No. That would be fitting
  one detector to another's opinion. There's no ground truth to justify it, and it
  would destroy the independence that makes the second opinion worth having.
- *"How do you know the normalised version is better if agreement went down?"* → I
  don't, on the full data — there's nothing to check against. I know it on the one
  labelled example, and I'm explicit that n=1 is weak evidence. That's why both
  feature sets are exported and one config field apart.

### 8.2 The Washington DC artifact

> "My first ranking was ordered by raw z-score and returned eight Washington DC
> hospitals at z = 134 to 192. It looked like a spectacular finding. It was an
> artifact: DC has a single HRR and few hospitals, so its peer groups are small and
> homogeneous, MAD approaches zero, and dividing by a near-zero scale inflates z
> without bound.
>
> Two guards, both config-driven, both covered by regression tests: a
> thin-dispersion rule requiring MAD ≥ 5% of the peer median, and a
> practical-significance floor requiring the flag to also exceed 1.5× the peer
> median. And I changed the ranking to `relative_excess` — how many times the peer
> median — rather than raw z.
>
> The top ten now spans seven states instead of one city, and the maximum effect
> size is a comprehensible 8.2× the peer median. The lesson I took: never rank by a
> statistic with a data-dependent denominator without guarding the denominator."

### 8.3 The determinism bug (best story for an engineering role)

> "I'd written in the run manifest that no model artifact is persisted, because the
> pipeline is deterministic — the forest is seeded and the peer statistics are pure
> aggregates, so the manifest plus the committed data is the whole reproduction
> recipe. That's an *argument*, and I decided it deserved to be tested rather than
> asserted.
>
> Two runs of the same code on the same file disagreed by **37 forest flags** —
> 1,923 versus 1,960.
>
> The seed was never the problem. The fit subsample is drawn *positionally* —
> `rng.choice` over row indices — from a view built out of joins and window
> aggregates, and DuckDB doesn't guarantee a plan's output order across runs or
> thread schedules. Different arrival order, different 50k subsample, different
> forest. I fixed it with an `ORDER BY` on the unique claim key before sampling,
> about a tenth of a second on 197k rows. Three separate processes now produce
> bit-identical scores — I check an MD5 over all 197,283 rounded score values — and
> there's a test that fits the forest with rows arriving in reverse order and
> asserts identical output. Remove the `ORDER BY` and that test fails; I verified
> that it does.
>
> The part that matters: without this, the raw-versus-normalised comparison I just
> described would have been **unfalsifiable**. Run-to-run noise was ±37 flags, and
> the effect I was measuring was 159. I would have been reading noise."

**Likely follow-up:** *"How did you find it?"* → Because the live service and my
earlier notebook run disagreed, and I chose to treat that as a bug rather than as
"probably fine". Then I measured it three times instead of twice.

### 8.4 The fabricated example (short, but tells them you check your own docs)

> "The OpenAPI example for `POST /score` used a DRG string I'd written from memory:
> `'470 - MAJOR JOINT REPLACEMENT W/O CC/MCC'`. The real string is `'470 - MAJOR
> JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY W/O MCC'`. Nothing failed —
> the schema validated, the tests passed, and the API returned a perfectly
> reasonable 'no peer group found'. A reviewer clicking *Try it out* in Swagger
> would have been told their first request matched nothing and concluded the
> service was broken.
>
> So there's now a test file whose whole job is documentation integrity: the
> documented example must parse, must resolve to a real peer group, and must return
> a flagged verdict; and every DRG code quoted in the README must exist in the
> committed data. The example is now a real row — the most extreme claim in the
> sample — so a reviewer's first request returns a dramatic, explainable number.
>
> The general rule I took from it: examples and fixtures must use real dataset
> values, and something has to *test* them, because fabricated reference data is
> invisible to type checkers."

---

## 9. Testing, CI, DevOps

**What do 287 tests actually cover?**
> They run in about a minute, hermetically — no network, no full dataset, no GPU — and
> they pin the things that fail *silently*: both parsers agreeing on the same
> column spec; every dirty-data trap; every branch of the flagging `CASE` chain
> being reachable; the Python twins of the SQL maths; forest determinism under
> reordered input; PSI returning exactly zero against its own profile; the manifest
> carrying real provenance; API status codes and validation boundaries; CLI exit
> codes; the dashboard being self-contained; and the documented examples resolving
> against real data.

**Tell me about a test that found a real bug.**
> The cursor-visibility one. `con.register()` in DuckDB creates a connection-local
> virtual table; a view over it works on that connection and 500s on any
> `con.cursor()`. Every unit test stayed on one connection, so the whole suite was
> green while *every scored endpoint in the API returned 500*. The fix was to
> materialise the forest's scores into a real catalog table, and the test now
> crosses the cursor boundary over HTTP with the forest enabled. That's why the API
> tests build the app twice — with and without the forest.

**And one that found a bug in a metric?**
> PSI. The property that matters most is that a file compared against itself
> reports zero drift — otherwise the alarm trains people to ignore it. It reported
> 0.0175. Two causes: quantile edges *collapse* on tied integer columns
> (`total_discharges` has a median of 21 and a long tail of 1s and 2s), so the
> surviving bins aren't equal-sized and the "expected is uniform" assumption was
> wrong; and I was rounding the edges *after* counting the baseline, which moved
> boundary values into a neighbouring bin. Both fixed, both tested.

**And one that found a bug in the first command a reviewer runs?**
> `make data` was broken and reported the wrong thing. It decided whether a download
> was an archive from the URL suffix, but codeload.github.com serves tarballs from a
> URL ending in the *branch name* — `.../tar.gz/refs/heads/master`. So a 337 MB gzip
> stream was classified as a plain file, moved into place as the "CSV", and the
> failure surfaced two layers later as `UnicodeDecodeError: 0x8b at position 1` —
> gzip's second magic byte, read as text. Detection is now by magic bytes, which also
> catches a mirror that returns a 200 with an HTML error page. I only found it
> because the sandbox I was working in lost its data directory and I had to re-fetch.

**What's your coverage philosophy?**
> Branch coverage on, but I don't chase a percentage. I chase *silent failure
> modes*: wrong numbers that don't raise. Those get tests even when they're awkward
> — like fitting a forest twice with reordered input.

**CI?**
> GitHub Actions, matrix over Python 3.10/3.11/3.12: ruff, pytest with coverage,
> then a CLI smoke test that runs the whole operational loop (report → save → runs →
> drift) against the committed sample. A second job boots the real service and curls
> it — `/health`, `/summary`, `/outliers`, `/dashboard`, `/distribution`, the
> whitelist 422 — and asserts the dashboard HTML contains no external URL.
> Superseded runs are cancelled via a concurrency group.

**Docker?**
> Multi-stage build so the runtime image has no build toolchain, a non-root user,
> and a `HEALTHCHECK` against `/health`. The dashboard HTML is declared as package
> data so it ships in the wheel — there's a test asserting that, because a missing
> asset is a 503 you only find in production.

---

## 10. Trap questions and honest answers

**"Isn't this just a fancy GROUP BY?"**
> The peer statistics are a GROUP BY, yes, and I'd rather say that than dress it up.
> The work is in what makes the grouping *valid* — 43% of the natural groups are
> singletons, so the hierarchy and the recorded fallback are the actual contribution
> — and in the guards that stop a correct-looking query from producing confident
> nonsense. Plus everything around it: explicit schema, provenance, drift, tests,
> service.

**"So which hospitals are committing fraud?"**
> None as far as this project can say, and I'd push back on the framing. There are no
> labels, the data is aggregated public averages, and a flag means "statistically
> unusual relative to peers, worth a human looking at it". Charges are list prices,
> not revenue, and chargemasters vary wildly. Every surface in the project repeats
> that caveat, including the API's root endpoint, because that's the one most likely
> to be read without the README open.

**"Is your flagging fair to small hospitals?"**
> It's a real risk and it's only partly mitigated. Small and rural hospitals have
> thinner peer groups and noisier per-discharge averages, so a naive scorer
> systematically over-flags them. The confidence term down-weights them instead of
> deleting them — deleting would remove them from the analysis entirely, which is
> worse. `make fairness` reports flag rates by volume band and state so the risk is
> measurable rather than assumed away. What I *haven't* done is the discharge-weighted
> peer median, which is the better statistic, or a formal fairness audit. I'd want
> both before anyone used this operationally.

**"Why should I believe your numbers?"**
> Because they're reproducible and I've told you where they're weak. `make data`
> fetches and structurally validates the file; `make save` writes the input's
> SHA-256 and library versions into a manifest; three processes produce bit-identical
> forest scores. And the drift monitor is what tells me the committed sample
> over-flags relative to the full file, rather than my say-so.

**"What would you do differently?"**
> Measure the raw-vs-normalised forest comparison *before* writing the narrative
> about why normalisation was obviously right — I had the explanation drafted in my
> head and the data contradicted it. Second, add the ORDER BY on day one: I wrote
> "deterministic" in a manifest before I'd tested determinism. Third, discharge-
> weighted peer statistics from the start.

**"What's the hardest technical problem here?"** — pick *one*, don't list three:
> The peer hierarchy. Not the SQL — the realisation that the obvious grouping is
> unusable for 43% of rows and that the failure is *silent*. Everything else follows
> from deciding that "which level did this score come from, and how much should you
> trust it" has to be part of the output rather than hidden inside it.

**"If you had two more weeks?"**
> Discharge-weighted peer medians; a reviewer disposition workflow, which is the
> only realistic route to labels; drift alerting wired to something; and a
> multi-year comparison — FY2016 vs FY2015 would turn the drift monitor from a
> self-check into an actual finding about how billing behaviour moved.

---

## 11. Questions to ask them

Pick two, and make them specific to what they said:

- "How does your team decide whether a data-quality problem gets fixed in the
  pipeline or pushed back to the source?"
- "What does code review look like for analytical code, where a wrong number doesn't
  raise an exception?"
- "Where's the boundary between the data team and the service team on something like
  this?"
- "What would make someone successful here in the first six months?"

---

## 12. Numbers cheat sheet (memorise the bold ones)

| | |
|---|---|
| Rows | **197,283** |
| Providers / DRGs / HRRs / states | 3,208 / **573** / 306 / 51 |
| File size | 34 MB CSV, fetched not committed |
| Full pipeline time | **~8 s on 2 CPU cores** |
| `DRG × HRR` groups | 58,441 — **43% singletons**, 80% under 5 members, median size 2 |
| Peer hierarchy usage | `drg_state` **46.0%**, `drg_hrr` **41.1%**, `drg_national` **12.9%** |
| Median group size by level | 31 / 15 / 236 |
| Peer-flagged | **4,207 (2.13%)**, across 670 providers |
| Forest-flagged | **1,958 (0.99%)** |
| Both agree | **136 (0.07%)** — Jaccard **0.0226** |
| Forest: raw vs normalised | 1,954 / Jaccard 0.050 → 1,958 / **Jaccard 0.023** |
| Forest-flagged median × peer | raw 1.31× → normalised **0.96×** |
| Planted fixture anomaly | raw **missed** (pct 0.941) → normalised **caught** (1.000) |
| Thresholds | z > **3.5**, excess ≥ **1.5×**, confidence ≥ **0.25**, min_support **8**, MAD ≥ **5%** of median |
| Confidence | `sqrt(peer_n/(peer_n+20) × discharges/(discharges+50))`, median **0.412** |
| MAD scale | **1.4826**; fallback MAD → IQR/1.349 → NULL |
| Guard rejections | thin_dispersion 4,327 · immaterial_excess 92 · degenerate_peers 48 |
| Max effect size | **8.2× peer median** (Brandywine Hospital, PA, DRG 883) |
| Max raw z (the artifact) | **191.7** — ranked low now, on purpose |
| Demo claim | Denton Regional TX, DRG 440: **$53,062 charged vs $953 Medicare = 55.7×**, 31 TX peers at 9.15× → **6.09× excess**, z 10.7 → flagged |
| Determinism bug | **37 flags** of run-to-run noise (1,923 vs 1,960), fixed by `ORDER BY provider_id, drg` |
| Drift, sample vs full | `relative_excess` PSI **0.133** (moderate); flag rate **2.13% → 3.63%** (+1.49 pp) |
| PSI bands | < **0.10** quiet · 0.10–**0.25** moderate · > 0.25 significant → **exit code 1** |
| Tests | **287**, ~1 min, hermetic; CI matrix Python **3.10/3.11/3.12** |
| Isolation Forest | 150 trees, contamination 0.01, 50k subsample, `random_state=42` |
| Stack | DuckDB · pandas · NumPy · scikit-learn · FastAPI · Pydantic v2 · Typer · Rich |

**Three sentences to have ready if your mind goes blank:**
1. "The interesting problem was that the obvious peer grouping is 43% singletons, and
   that failure is silent."
2. "I keep the ML thin because there are no labels, and every flag has to carry a
   reason a human can check."
3. "The thing I'm proudest of is a hypothesis I tested and lost, and a determinism
   bug that only showed up because I decided to test a claim I'd written in a
   manifest."
