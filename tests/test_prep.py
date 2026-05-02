"""Prep material must not rot.

``prep/cpp-templates.cpp`` exists to be *typed from memory* under interview
pressure. That only works if the reference is correct: a template with a subtle
bug is worse than no template, because the person practising with it will spend
twenty minutes debugging the reference and conclude the fault is theirs.

So the prep material gets the same treatment as the rest of the repo -- the
claims it makes are asserted:

* it compiles with no warnings under ``-Wall -Wextra``;
* every template's self-test passes, at ``-O0`` and ``-O2``;
* every section the companion document points at actually exists.

Two real bugs were found by writing these tests rather than by reading the file:
a top-K comparator that was inverted relative to its own assertion, and an
``assert()`` containing a braced pair -- the macro reads the comma as an argument
separator. Both are documented in the file as gotchas, which is the correct
outcome: the mistake became teaching material instead of a silent error.

Skipped when no C++ compiler is installed, so the suite stays runnable anywhere.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "prep" / "cpp-templates.cpp"
CODE_PRODUCTION = REPO_ROOT / "prep" / "code-production.md"
DSA_PLAN = REPO_ROOT / "prep" / "dsa-plan.md"
SUCCESS_LINE = "all templates compile and pass"

CXX = shutil.which("g++") or shutil.which("clang++")

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


def _build(binary: Path, std: str, optimisation: str) -> subprocess.CompletedProcess[str]:
    """Compile the templates, treating warnings as failures."""
    return subprocess.run(
        [
            CXX,
            f"-std={std}",
            optimisation,
            "-Wall",
            "-Wextra",
            "-o",
            str(binary),
            str(TEMPLATES),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_templates_exist() -> None:
    """Guard the rest of this module against a renamed or deleted file."""
    assert TEMPLATES.is_file(), f"missing {TEMPLATES}"
    assert CODE_PRODUCTION.is_file(), f"missing {CODE_PRODUCTION}"


@pytest.mark.parametrize("optimisation", ["-O0", "-O2"])
def test_templates_compile_and_pass_their_asserts(tmp_path: Path, optimisation: str) -> None:
    """The reference must build clean and every self-test must hold.

    Both optimisation levels: an assert that passes at ``-O0`` and fails at
    ``-O2`` means the code relied on undefined behaviour, which is exactly the
    class of bug this file is supposed to teach people to avoid.
    """
    binary = tmp_path / f"templates{optimisation}"
    built = _build(binary, "c++17", optimisation)
    assert built.returncode == 0, f"compilation failed:\n{built.stderr}"
    assert "error:" not in built.stderr

    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, check=False)
    assert ran.returncode == 0, f"self-tests failed:\n{ran.stderr}"
    assert SUCCESS_LINE in ran.stdout


def test_templates_compile_without_warnings(tmp_path: Path) -> None:
    """`code-production.md` claims a clean build, so a clean build is enforced.

    Kept separate from the correctness test so that a future compiler adding a
    new warning fails one focused check instead of hiding the fact that the
    templates still work.
    """
    built = _build(tmp_path / "warnings", "c++17", "-O0")
    assert built.returncode == 0, built.stderr
    assert built.stderr.strip() == "", f"compiled with warnings:\n{built.stderr}"


def test_templates_also_build_as_cxx20(tmp_path: Path) -> None:
    """Interviewers and judges are increasingly on C++20; the shapes must survive it."""
    binary = tmp_path / "templates20"
    built = _build(binary, "c++20", "-O1")
    assert built.returncode == 0, f"C++20 build failed:\n{built.stderr}"
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, check=False)
    assert SUCCESS_LINE in ran.stdout


def _section_numbers() -> set[int]:
    """Numbered section headers in the template file, e.g. ``// 7. BINARY SEARCH``."""
    text = TEMPLATES.read_text(encoding="utf-8")
    return {int(m) for m in re.findall(r"^// (\d+)\. ", text, flags=re.MULTILINE)}


def test_template_sections_are_contiguous() -> None:
    """Sections are referenced by number throughout the docs, so gaps would mislead."""
    sections = _section_numbers()
    assert sections, "no numbered sections found -- did the header format change?"
    assert sections == set(range(1, max(sections) + 1)), (
        f"section numbers are not contiguous: {sorted(sections)}"
    )


@pytest.mark.parametrize("doc", [CODE_PRODUCTION, DSA_PLAN], ids=lambda p: p.name)
def test_every_referenced_section_exists(doc: Path) -> None:
    """Each ``§N`` pointer in the prose must resolve to a real section.

    The docs cross-reference the template file by section number in three places
    (the shape table, the drill schedule, the sliding-window discussion). A
    renumbered file would leave all three pointing at the wrong code.
    """
    text = doc.read_text(encoding="utf-8")
    referenced = {int(m) for m in re.findall(r"§(\d+)", text)}
    if not referenced:
        pytest.skip(f"{doc.name} does not reference template sections")
    missing = referenced - _section_numbers()
    assert not missing, f"{doc.name} references missing sections: {sorted(missing)}"


def test_documented_compile_command_is_accurate() -> None:
    """The build command shown to the reader must be the one that actually works."""
    documented = "g++ -std=c++17 -O2 -Wall -Wextra -o /tmp/t prep/cpp-templates.cpp"
    assert documented in CODE_PRODUCTION.read_text(encoding="utf-8"), (
        "the compile command in code-production.md changed; re-verify it and update this test"
    )


def test_the_priority_queue_rule_matches_its_own_demo() -> None:
    """The comparator rule is the easiest thing in the file to state backwards.

    It was, once, while this file was being written. The rule claims that
    ``comp(a, b)`` true means *a sorts before b* and that ``top()`` is the element
    that sorts last -- so ``greater<int>`` must put the smallest on top. Asserting
    the wording and the demo live side by side keeps the two honest with each
    other; the executable proof is the heap section's own asserts.
    """
    text = TEMPLATES.read_text(encoding="utf-8")
    assert "top() is the element that sorts LAST" in text
    assert "MIN-heap" in text and "MAX-heap" in text
    # The Dijkstra template must use greater<> and therefore yield a min-heap.
    dijkstra = text.split("vector<int> dijkstra(", 1)[1].split("// ====", 1)[0]
    assert "greater<>" in dijkstra, "Dijkstra should avoid a hand-written comparator"
    assert "if (d > dist[u]) continue" in dijkstra, "stale-entry guard is required"
