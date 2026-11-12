"""Golden set v1: the committed packages and answers match the generator, and the answers
are consistent with the credit policy's rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_synthetic as gen  # noqa: E402

RECOMMENDATIONS = {"approve", "approve_with_conditions", "decline", "refer"}


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("packages")
    review = out / "answers.md"
    old = gen.OUT, gen.GOLDEN_REVIEW
    gen.OUT, gen.GOLDEN_REVIEW = out, review
    try:
        gen.main()
    finally:
        gen.OUT, gen.GOLDEN_REVIEW = old
    return out


def test_the_committed_packages_match_the_generator(regenerated: Path) -> None:
    committed = sorted(p.relative_to(gen.OUT) for p in gen.OUT.rglob("*") if p.is_file())
    fresh = sorted(p.relative_to(regenerated) for p in regenerated.rglob("*") if p.is_file())
    assert [p for p in fresh if p.name != "answers.md"] == committed
    for rel in committed:
        assert (gen.OUT / rel).read_bytes() == (regenerated / rel).read_bytes(), rel


def test_the_review_page_matches_the_generator(regenerated: Path) -> None:
    assert gen.GOLDEN_REVIEW.read_text() == (regenerated / "answers.md").read_text()


def test_there_are_25_packages_each_with_golden_answers() -> None:
    catalog = json.loads((gen.OUT / "catalog.json").read_text())
    assert len(catalog) == 25
    for p in catalog:
        golden = json.loads((gen.OUT / p["package_id"] / "ground_truth.json").read_text())["golden"]
        assert golden["status"] == gen.GOLDEN_STATUS
        assert set(golden["acceptable_recommendations"]) <= RECOMMENDATIONS
        assert golden["key_findings"] and all(k["text"] for k in golden["key_findings"])


def test_the_decline_floor_and_refer_cases_allow_one_answer_only() -> None:
    for pid in ("pkg-04", "pkg-15", "pkg-16"):
        golden = json.loads((gen.OUT / pid / "ground_truth.json").read_text())["golden"]
        assert golden["acceptable_recommendations"] == ["decline"], pid
    for pid in ("pkg-06", "pkg-21", "pkg-22", "pkg-23"):
        golden = json.loads((gen.OUT / pid / "ground_truth.json").read_text())["golden"]
        assert golden["acceptable_recommendations"] == ["refer"], pid


def test_values_exactly_on_a_limit_are_not_breaches() -> None:
    for pid in ("pkg-11", "pkg-13", "pkg-19"):
        golden = json.loads((gen.OUT / pid / "ground_truth.json").read_text())["golden"]
        assert golden["expected_flags"] == [] and golden["acceptable_recommendations"] == [
            "approve"
        ]
        assert any("exactly at the policy" in k["text"] for k in golden["key_findings"])
