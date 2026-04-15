"""Tests for the data-fetch script's archive detection.

``make data`` is the first command a reviewer runs, and it was broken in a way
that reported the wrong thing: codeload.github.com serves tarballs from a URL
ending in the *branch name* (``.../tar.gz/refs/heads/master``), so suffix-based
detection classified a 337 MB gzip stream as a plain file, moved it into place as
the "CSV", and the failure surfaced two layers later as
``UnicodeDecodeError: 0x8b at position 1`` -- gzip's second magic byte, read as
text. Detection is now by magic bytes, and these tests pin it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = REPO_ROOT / "scripts" / "fetch_data.py"


@pytest.fixture(scope="module")
def fetch_data():
    """Load scripts/fetch_data.py, which is not an importable package."""
    spec = importlib.util.spec_from_file_location("claimsight_fetch_data", FETCH_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, head: bytes, tail: bytes = b"some content\n") -> Path:
    path.write_bytes(head + tail)
    return path


class TestDetectArchive:
    def test_detects_gzip_by_magic_not_by_name(self, fetch_data, tmp_path: Path) -> None:
        """The actual bug: a gzip file whose name says nothing about compression."""
        gzip_magic = b"\x1f\x8b\x08\x00"
        # Exactly what codeload produces: no useful suffix at all.
        named = fetch_data._detect_archive(_write(tmp_path / "download", gzip_magic))
        assert named == "tar.gz"
        # And a misleading suffix must not override the bytes.
        misleading = fetch_data._detect_archive(_write(tmp_path / "data.csv", gzip_magic))
        assert misleading == "tar.gz"

    def test_detects_zip(self, fetch_data, tmp_path: Path) -> None:
        assert fetch_data._detect_archive(_write(tmp_path / "d", b"PK\x03\x04")) == "zip"
        # cms.gov's real URL ends in .zip, but detection must not depend on that.
        assert fetch_data._detect_archive(_write(tmp_path / "d.bin", b"PK\x03\x04")) == "zip"

    def test_plain_csv_is_raw(self, fetch_data, tmp_path: Path) -> None:
        path = tmp_path / "d"
        path.write_text("DRG Definition,Provider Id\n470 - MAJOR JOINT,010033\n")
        assert fetch_data._detect_archive(path) == "raw"

    def test_an_html_error_page_is_not_an_archive(self, fetch_data, tmp_path: Path) -> None:
        """A mirror returning a 200 with an error page must be classified as raw,
        so validation rejects it on row count instead of on a decode error."""
        path = _write(tmp_path / "d", b"<html><body>rate limited</body></html>")
        assert fetch_data._detect_archive(path) == "raw"

    def test_empty_download_is_reported(self, fetch_data, tmp_path: Path) -> None:
        path = tmp_path / "d"
        path.write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            fetch_data._detect_archive(path)


class TestFetchScriptUsability:
    def test_script_is_importable_and_linted_clean(self) -> None:
        """A reviewer running `make lint` should not trip over scripts/."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(FETCH_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        if result.returncode != 0 and "No module named ruff" in result.stderr:
            pytest.skip("ruff not installed")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_makefile_data_target_points_at_the_script(self) -> None:
        """`make data` is the documented first step; it must invoke this script."""
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert "\ndata: " in makefile or makefile.startswith("data: ")
        assert "scripts/fetch_data.py" in makefile
