# Healthcare Billing Anomaly Detection

A pipeline that flags unusual hospital billing patterns in CMS Medicare data.

It groups hospitals into peer groups (same procedure, similar region), calculates how far their markup ratios deviate from the group, and runs an Isolation Forest as a second detector. Results are served through a FastAPI dashboard.

Dataset: CMS Medicare Provider Charge Data, Inpatient FY2016 (~197k rows, 3,208 hospitals).

## How it works

    Ingest — parse the messy CMS CSV (dollar signs, commas, zero-padded IDs) with explicit converters instead of relying on auto-detection
    Peer groups — group by DRG + HRR → DRG + State → DRG + National, falling back when groups are too small
    Score — robust z-score using median and MAD (not standard deviation, since billing ratios are heavily skewed), weighted by peer group size and hospital volume
    Isolation Forest — multivariate detector using several features at once; kept separate from the peer scorer because they catch different things (Jaccard overlap ~0.02 on the full dataset)
    Drift — PSI-based comparison against a saved baseline to detect distribution shifts in new data

Results (full dataset)
Detector	Flagged
Peer z-score	4,207
Isolation Forest	1,958
Both	136

Flagged ≠ fraud. There's no ground truth — this is a review queue, not a verdict.

## Quickstart

Bash
    make install-dev
    make test          # 287 tests
    make report        # runs the pipeline on the included sample
    make serve         # dashboard at localhost:8000/dashboard, docs at /docs

To use the full dataset:

Bash
    
    make data
    make report

Bash
    
    make save          # saves manifest + outliers to artifacts/runs/<id>/
    make runs          # list previous runs
    make drift         # compare current data against last saved baseline

Project structure
    
    src/claimsight/     Core pipeline (schema, ingest, peers, scoring, drift, CLI, API)
    scripts/            Data download + sampling
    tests/              287 tests including malformed-data edge cases

Tech stack

    Python 3.10+, DuckDB, Pandas, NumPy, Scikit-learn, FastAPI, Pydantic, Typer
    
Limitations

    No ground truth labels — flags need human review
    FY2016 inpatient only, aggregated at hospital/procedure level
    Charges ≠ actual payments
    Small hospitals get noisier peer groups
    Drift is reported, not auto-handled

Data source

    CMS Medicare Provider Charge Data: Inpatient FY2016. Downloaded via make data. A stratified sample is included in the repo for dev/testing.
