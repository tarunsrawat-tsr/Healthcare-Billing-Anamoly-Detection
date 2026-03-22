"""Fetch the CMS Medicare Inpatient Provider Charge File (FY2016).

The file is published by the US Centers for Medicare & Medicaid Services and is
US Government work, so there is no licensing obstacle to redistributing it --
but at 34 MB it still does not belong in Git. This script fetches it and
**validates it structurally** before accepting it.

Why structural validation instead of a checksum
----------------------------------------------
The same logical dataset is mirrored in several places (cms.gov, data.cms.gov,
and various public GitHub archives). Mirrors differ in line endings, quoting and
row order, so a byte-level checksum would reject a perfectly good copy of the
right data. What actually matters is that the file has the CMS header, the
expected row count, and parses cleanly under the project's explicit schema.
Those are checked; the MD5 is reported for information only.

Provenance note
---------------
``SOURCES`` is ordered by preference. In the sandbox this project was developed
in, only the GitHub mirror was reachable; the official CMS endpoints are
documented and untested from there. Every attempt is logged so a failure says
which source was tried.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claimsight.ingest import load_claims

EXPECTED_ROWS = 197_283
EXPECTED_MD5 = "278114a8c0b623d2b979e92802a4f661"  # informational, mirror-specific

SOURCES: tuple[tuple[str, str], ...] = (
    (
        "cms.gov (official)",
        "https://www.cms.gov/files/zip/medicare-provider-charge-data-fy2016-inpatient.zip",
    ),
    (
        "github mirror (plotly/datasets)",
        "https://codeload.github.com/plotly/datasets/tar.gz/refs/heads/master",
    ),
)

#: Path of the CSV inside the GitHub mirror tarball.
TARBALL_MEMBER = (
    "datasets-master/dash-sample-apps/dash-medical-provider-charges/data/"
    "CMS_inpatient_2016_data/Inpatient_summary_2016_all/"
    "Medicare_Provider_Charge_Inpatient_DRGALL_FY2016.csv"
)


def _download(url: str, destination: Path, *, timeout: int = 600) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "claimsight/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as fh:
        shutil.copyfileobj(response, fh)


def _extract_member(tarball: Path, member: str, destination: Path) -> None:
    import tarfile

    with tarfile.open(tarball) as archive:
        handle = archive.extractfile(member)
        if handle is None:
            raise KeyError(f"{member} not found in {tarball.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as out:
            shutil.copyfileobj(handle, out)


def validate(path: Path) -> dict[str, object]:
    """Confirm the file really is the CMS inpatient charge file."""
    import duckdb

    _con, report = load_claims(path, duckdb.connect())
    result = {
        "path": str(path),
        "rows_read": report.rows_read,
        "rows_usable": report.rows_usable,
        "rows_rejected": report.rows_rejected,
        "distinct_providers": report.distinct_providers,
        "distinct_drgs": report.distinct_drgs,
        "distinct_hrrs": report.distinct_hrrs,
        "md5": hashlib.md5(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }
    if report.rows_read != EXPECTED_ROWS:
        raise ValueError(
            f"expected {EXPECTED_ROWS:,} rows, got {report.rows_read:,} -- "
            "wrong file or wrong fiscal year"
        )
    if report.distinct_drgs != 573:
        raise ValueError(f"expected 573 DRGs, got {report.distinct_drgs}")
    result["valid"] = True
    return result


def fetch(destination: Path, *, source_index: int | None = None) -> dict[str, object]:
    """Try each source in order until one yields a valid file."""
    candidates = (
        [SOURCES[source_index]] if source_index is not None else list(SOURCES)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for name, url in candidates:
            print(f"trying {name}: {url}", flush=True)
            staged = tmpdir / "download"
            try:
                _download(url, staged)
                if url.endswith((".zip", ".tar.gz", ".tgz")):
                    if url.endswith(".zip"):
                        import zipfile

                        with zipfile.ZipFile(staged) as archive:
                            members = [m for m in archive.namelist() if m.endswith(".csv")]
                            if not members:
                                raise KeyError("no CSV inside the archive")
                            with archive.open(members[0]) as src, destination.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
                    else:
                        _extract_member(staged, TARBALL_MEMBER, destination)
                else:
                    shutil.move(str(staged), str(destination))

                result = validate(destination)
                result["source"] = name
                return result
            except Exception as exc:
                print(f"  failed: {type(exc).__name__}: {exc}", flush=True)
                if destination.exists():
                    destination.unlink()

    raise SystemExit(
        "Could not obtain a valid CMS file from any source.\n"
        "Download it manually from https://www.cms.gov/ (Medicare Provider Charge "
        f"Data: Inpatient, FY2016) and save it to:\n  {destination}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("data/raw/cms_inpatient_fy2016.csv"),
    )
    parser.add_argument(
        "--source",
        type=int,
        default=None,
        help="index into SOURCES (0=cms.gov, 1=github mirror); default tries all",
    )
    args = parser.parse_args()

    if args.destination.exists():
        print(f"already present: {args.destination}; re-validating")
        result = validate(args.destination)
    else:
        result = fetch(args.destination, source_index=args.source)

    for key, value in result.items():
        print(f"{key:>20}: {value}")
    if result["md5"] != EXPECTED_MD5:
        print(
            f"\nnote: md5 differs from the mirror this project was built against "
            f"({EXPECTED_MD5}). That is expected for a different mirror or line "
            f"ending; structural validation above is what matters."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
