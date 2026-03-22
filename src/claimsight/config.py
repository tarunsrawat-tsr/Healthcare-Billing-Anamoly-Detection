"""Typed runtime configuration.

Paths are resolved relative to the repository root so the CLI works from any
working directory. Everything is overridable by environment variable with a
``CLAIMSIGHT_`` prefix, which is what makes the Docker image configurable
without editing code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root: src/claimsight/config.py -> parents[2]
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Filesystem locations and pipeline defaults."""

    model_config = SettingsConfigDict(
        env_prefix="CLAIMSIGHT_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Field(default=REPO_ROOT / "data")
    #: Full CMS file, fetched by ``make data``. Never committed.
    raw_csv: Path = Field(default=REPO_ROOT / "data" / "raw" / "cms_inpatient_fy2016.csv")
    #: Stratified sample, committed, used by CI and the demo.
    sample_csv: Path = Field(
        default=REPO_ROOT / "data" / "sample" / "cms_inpatient_fy2016_sample.csv"
    )
    processed_dir: Path = Field(default=REPO_ROOT / "data" / "processed")
    artifacts_dir: Path = Field(default=REPO_ROOT / "artifacts")

    #: Minimum members before a peer group's statistics are trusted.
    min_support: int = 8

    def resolve_input(self, prefer_full: bool = True) -> Path:
        """Pick the best available claims file.

        Falls back from the full 34 MB download to the committed sample rather
        than failing, because a demo that refuses to run without a manual
        download step is a demo that does not get run. The choice is always
        reported to the caller.
        """
        if prefer_full and self.raw_csv.exists():
            return self.raw_csv
        if self.sample_csv.exists():
            return self.sample_csv
        raise FileNotFoundError(
            f"No claims data found. Looked for:\n"
            f"  full:   {self.raw_csv}\n"
            f"  sample: {self.sample_csv}\n"
            f"Run `make data` to fetch the full file, or `make sample-check` to "
            f"verify the committed sample."
        )


def get_settings() -> Settings:
    """Construct settings. A function rather than a module-level singleton so
    tests can point it at a temporary directory via environment variables."""
    return Settings()
