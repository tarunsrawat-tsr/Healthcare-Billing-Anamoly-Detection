# ClaimSight

A data pipeline for finding unusual billing patterns.

The project uses peer-based statistics and Isolation Forest to identify hospitals whose billing patterns are significantly different from similar hospitals.

## What this project does

ClaimSight takes the CMS Medicare Provider Charge Data for FY2016 and:

- Loads and validates the raw CSV data.
- Groups hospitals into comparable peer groups.
- Calculates billing ratios and robust statistical scores.
- Flags unusually high billing patterns.
- Runs a second anomaly detector using Isolation Forest.
- Exposes the results through a FastAPI service.
- Stores run information so results can be reproduced.
- Compares new data with previous data using drift detection.

The full dataset contains **197,283 rows from 3,208 hospitals**.

## How it works

The main idea is to compare a hospital with hospitals that perform the same procedure in a similar region.

For example, comparing the billing for a heart procedure with the billing for a knee replacement would not be very useful. Instead, ClaimSight first groups claims using:

```text
DRG + HRR
    ↓
DRG + State
    ↓
DRG + National
```

If there are not enough hospitals in the first group, the system moves to the next level.

For each claim, the pipeline records which peer level was used.

### Billing ratio

The basic signal is:

```text
markup_ratio = average covered charges / average Medicare payments
```

A robust z-score is then calculated using the peer group's median and MAD:

```text
z = (markup_ratio - peer_median) / (1.4826 × MAD)
```

MAD is used instead of standard deviation because the billing ratios are heavily skewed.

### Confidence

The score also considers how much data is available:

```text
support = peer_count / (peer_count + 20)

volume = discharges / (discharges + 50)

confidence = sqrt(support × volume)
```

This prevents small peer groups and low-volume hospitals from being treated the same as well-supported cases.

### Second detector

The project also uses an Isolation Forest.

It looks at several features at once rather than only comparing a hospital with its peer group.

The two detectors are intentionally kept separate because they often identify different cases. In the full dataset their Jaccard overlap is about **0.023**.

## Data

The project uses:

**CMS Medicare Provider Charge Data — Inpatient, FY2016**

| | |
|---|---:|
| Rows | 197,283 |
| Hospitals | 3,208 |
| DRGs | 573 |
| HRRs | 306 |
| States | 51 |
| Dataset size | ~34 MB |

Each row represents a hospital and procedure group and contains information such as charges, payments, and discharge volume.

The original dataset is not committed to the repository. A smaller sample is included for testing and development.

## Why the ingestion is explicit

The CMS CSV has a few data-format issues that make automatic type detection unreliable.

For example:

```text
"$1,016,806.46"
"1,134"
"010033"
```

Money values contain `$` and commas, discharge counts can contain commas, and provider IDs contain leading zeros.

ClaimSight therefore reads the raw columns explicitly and converts them using tested parsers instead of relying on automatic CSV type inference.

This also makes malformed values easier to detect rather than silently dropping them.

## Quickstart

Install the project:

```bash
make install-dev
```

Run the tests:

```bash
make test
```

Run the analysis:

```bash
make report
```

Start the API and dashboard:

```bash
make serve
```

The dashboard is available at:

```text
http://localhost:8000/dashboard
```

API documentation:

```text
http://localhost:8000/docs
```

### Using the full dataset

The repository includes a smaller sample so the project can run without downloading the full dataset.

To download the full CMS dataset:

```bash
make data
make report
```

The full pipeline processes approximately 197k rows in a few seconds on a 2-core CPU.

## Saving and comparing runs

A run can be saved with:

```bash
make save
```

This creates:

```text
artifacts/
└── runs/
    └── <run_id>/
        ├── manifest.json
        └── outliers.parquet
```

The manifest contains information such as the input file hash, configuration, package versions, and summary statistics.

You can list previous runs with:

```bash
make runs
```

## Drift detection

ClaimSight can compare a new dataset with a stored baseline.

```bash
make drift
```

It uses Population Stability Index (PSI) to check whether monitored feature distributions have changed.

The system does not automatically retrain or change the model when drift is detected. It reports the change so that it can be investigated.

## API

The main endpoints are:

| Endpoint | Description |
|---|---|
| `/dashboard` | Web dashboard |
| `/health` | Service and pipeline status |
| `/summary` | Overall statistics |
| `/peer-levels` | Peer-group usage |
| `/outliers` | Flagged claims |
| `/providers/{id}` | Billing profile for a hospital |
| `/distribution` | Feature distribution |
| `/reference` | DRG, HRR and state values |
| `/score` | Score a single claim |
| `/docs` | Swagger/OpenAPI documentation |

The `/score` endpoint uses the same scoring rules as the batch pipeline so that the API and offline results do not use different logic.

## Project structure

```text
src/claimsight/
├── schema.py       # Data schema and parsing
├── ingest.py       # Data loading and validation
├── peers.py        # Peer-group construction
├── scoring.py      # Anomaly scoring
├── registry.py     # Run artifacts and manifests
├── monitor.py      # Drift detection
├── config.py       # Configuration
├── cli.py          # Command-line interface
└── api/
    ├── main.py
    ├── deps.py
    ├── schemas.py
    └── static/
        └── dashboard.html

scripts/
├── fetch_data.py
└── make_sample.py

tests/
└── fixtures/
    └── tiny_claims.csv
```

## Testing

The project currently has **287 tests** covering:

- Data parsing
- Data validation
- Peer-group selection
- Statistical scoring
- Isolation Forest behaviour
- Drift detection
- API endpoints
- CLI commands
- Dashboard behaviour
- Reproducibility

The tests also include deliberately malformed data such as comma-formatted numbers, zero-padded IDs, and invalid money values.

```bash
make test
```

## Reproducibility

The Isolation Forest uses a fixed seed, but that alone was not enough to guarantee identical results.

The input rows were being sampled based on their position. Since database query order can change, the same data could produce a different sample and therefore slightly different forest results.

The pipeline now orders rows by a stable claim key before sampling. This makes repeated runs deterministic.

## Results

On the full dataset:

| Detector | Flagged |
|---|---:|
| Peer robust z-score | 4,207 |
| Isolation Forest | 1,958 |
| Both | 136 |

The detectors do not agree very often. This suggests that they are looking at different types of unusual behaviour rather than simply producing duplicate results.

The project also found that normalising Isolation Forest features by national DRG medians did **not** increase agreement with the peer-based detector. Instead, the two approaches captured different reference frames.

## Limitations

There are several important limitations:

1. **There is no labelled ground truth.** A flagged hospital is not necessarily doing anything wrong.
2. The dataset covers only **FY2016 Medicare inpatient claims**.
3. The data is aggregated at the hospital/procedure level rather than being patient-level data.
4. Charges are not the same as actual payments or revenue.
5. Small hospitals can have smaller peer groups and noisier statistics.
6. The Isolation Forest and peer-based detector use different reference frames.
7. The included sample produces somewhat different flag rates from the full dataset because smaller peer groups are created after sampling.
8. Drift is detected but not automatically handled.

For these reasons, the output should be treated as a **review queue for further investigation**, not as evidence of fraud.

## Development

Useful commands:

```bash
make lint
make format
make coverage
make docker-build
make docker-run
```

Main technologies:

- Python 3.10+
- DuckDB
- Pandas
- NumPy
- Scikit-learn
- FastAPI
- Pydantic
- Typer
- Rich

No GPU is required.

## Data source

The project uses the CMS Medicare Provider Charge Data: Inpatient FY2016.

The full dataset is downloaded by the project when running:

```bash
make data
```

A smaller stratified sample is included in the repository for development and testing.
