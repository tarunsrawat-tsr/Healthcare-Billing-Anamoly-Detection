# claimsight - task runner
#
# Everything a reviewer needs to reproduce this project is one `make` call away.
# Run `make help` for the list.

PYTHON      ?= python3
PIP         := $(PYTHON) -m pip
DATA_FULL   := data/raw/cms_inpatient_fy2016.csv
DATA_SAMPLE := data/sample/cms_inpatient_fy2016_sample.csv
REPORT_JSON := artifacts/report.json
DRIFT_JSON  := artifacts/drift.json

.DEFAULT_GOAL := help
.PHONY: help install install-dev lint format typecheck test coverage \
        data sample report save runs drift fairness serve docker-build \
        docker-run clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime dependencies
	$(PIP) install -e .

install-dev:  ## Install runtime + dev dependencies
	$(PIP) install -e ".[dev]"

lint:  ## Ruff check
	$(PYTHON) -m ruff check src tests scripts

format:  ## Ruff format + import sorting
	$(PYTHON) -m ruff format src tests scripts
	$(PYTHON) -m ruff check --fix src tests scripts

test:  ## Run the test suite
	$(PYTHON) -m pytest

coverage:  ## Run tests with a coverage report
	$(PYTHON) -m pytest --cov=claimsight --cov-report=term-missing


data:  ## Fetch the full 34 MB CMS file (not committed)
	$(PYTHON) scripts/fetch_data.py --destination $(DATA_FULL)

sample:  ## Rebuild the committed stratified sample from the full file
	@test -f $(DATA_FULL) || { echo "run 'make data' first"; exit 1; }
	$(PYTHON) scripts/make_sample.py $(DATA_FULL) $(DATA_SAMPLE)

report:  ## Full pipeline -> anomaly report on stdout + JSON
	@mkdir -p artifacts
	$(PYTHON) -m claimsight.cli report --json $(REPORT_JSON) $(if $(wildcard $(DATA_FULL)),$(DATA_FULL),$(DATA_SAMPLE))

save:  ## Score the data and persist a run (manifest + review queue + drift profile)
	@mkdir -p artifacts
	$(PYTHON) -m claimsight.cli report --save $(if $(wildcard $(DATA_FULL)),$(DATA_FULL),$(DATA_SAMPLE))

runs:  ## List saved runs in the artifact registry
	$(PYTHON) -m claimsight.cli runs

drift:  ## PSI drift check of the current file against the latest saved run
	@mkdir -p artifacts
	$(PYTHON) -m claimsight.cli drift --json $(DRIFT_JSON) \
	  $(if $(wildcard $(DATA_FULL)),$(DATA_FULL),$(DATA_SAMPLE))

fairness:  ## Audit flag rates across hospital size bands and states
	$(PYTHON) -m claimsight.cli fairness $(if $(wildcard $(DATA_FULL)),$(DATA_FULL),$(DATA_SAMPLE))

serve:  ## Run the API + dashboard locally (http://localhost:8000/dashboard)
	@echo "  dashboard  -> http://localhost:8000/dashboard"
	@echo "  api docs   -> http://localhost:8000/docs"
	$(PYTHON) -m uvicorn claimsight.api.main:app --host 0.0.0.0 --port 8000 --reload

docker-build:  ## Build the container image
	docker build -t claimsight:latest .

docker-run:  ## Run the container, exposing the API on :8000
	docker run --rm -p 8000:8000 claimsight:latest

clean:  ## Remove generated artifacts and caches
	rm -rf artifacts data/processed .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
