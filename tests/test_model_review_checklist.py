"""App 2, step 2a: the synthetic documentation packages and the section checklist.

The generator records where it wrote each section; the app's parser finds them on its own.
The two must agree for every package and every checklist item."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from axiom.apps.model_review.catalog import (
    UnknownPackage,
    list_packages,
    load_ground_truth,
    load_package,
    packages_dir,
)
from axiom.apps.model_review.domain import DocumentationPackage
from axiom.apps.model_review.tools.checklist import check_package, completeness, load_checklist
from axiom.apps.model_review.tools.sections import normalize, sections
from axiom.core.types import Document, UnknownInput
from conftest import SETTINGS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_model_docs as gen  # noqa: E402

FOLDER = packages_dir(SETTINGS.data_dir)
CHECKLIST = load_checklist(SETTINGS.app_config_dir("model_review") / "checklist.yaml")
IDS = [p["package_id"] for p in list_packages(FOLDER)]


def test_the_committed_packages_match_the_generator(tmp_path: Path) -> None:
    old = gen.OUT
    gen.OUT = tmp_path
    try:
        gen.main()
    finally:
        gen.OUT = old
    committed = sorted(p.relative_to(FOLDER) for p in FOLDER.rglob("*") if p.is_file())
    fresh = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert fresh == committed
    for rel in committed:
        assert (FOLDER / rel).read_bytes() == (tmp_path / rel).read_bytes(), rel


@pytest.mark.parametrize("package_id", IDS)
def test_the_checklist_finds_what_the_generator_wrote(package_id: str) -> None:
    truth = load_ground_truth(FOLDER, package_id)
    checks = check_package(load_package(FOLDER, package_id), CHECKLIST)
    assert [c.item_id for c in checks] == [i.item_id for i in CHECKLIST.items]
    for c in checks:
        expected = truth["checks"][c.item_id]
        assert c.outcome == expected["outcome"], c.item_id
        if c.outcome == "missing":
            assert c.doc_id is None and c.first_line is None
        else:
            assert c.doc_id == expected["doc_id"]
            assert [c.first_line, c.last_line] == expected["lines"]
    score = completeness(checks, CHECKLIST)
    assert f"{score.present}/{score.total}" == truth["completeness"]
    assert list(score.critical_gaps) == truth["critical_gaps"]


def test_each_scenario_leaves_the_gap_it_was_built_for() -> None:
    def gaps(package_id: str) -> dict[str, str]:
        checks = check_package(load_package(FOLDER, package_id), CHECKLIST)
        return {c.item_id: c.outcome for c in checks if c.outcome != "present"}

    assert gaps("mdl-01") == {}
    assert gaps("mdl-02") == {"MR-11": "missing", "MR-12": "missing"}
    assert gaps("mdl-03") == {"MR-6": "missing"}
    assert gaps("mdl-04") == {"MR-5": "empty"}  # MR-4 is present but weak: a model's call
    assert gaps("mdl-05") == {"MR-8": "missing", "MR-10": "empty"}
    assert gaps("mdl-06") == {"MR-2": "missing", "MR-7": "missing"}
    full = completeness(check_package(load_package(FOLDER, "mdl-01"), CHECKLIST), CHECKLIST)
    assert full.score == Decimal("1.00") and full.critical_gaps == ()
    two = completeness(check_package(load_package(FOLDER, "mdl-02"), CHECKLIST), CHECKLIST)
    assert two.score == Decimal("0.83") and two.critical_gaps == ("MR-11",)


def test_every_expected_finding_names_a_checklist_item() -> None:
    items = {i.item_id for i in CHECKLIST.items}
    for package_id in IDS:
        expected = load_ground_truth(FOLDER, package_id)["expected_findings"]
        assert set(expected) <= items, package_id


def test_every_item_cites_at_least_one_public_provision() -> None:
    assert CHECKLIST.version == "MR-2026.1"
    for item in CHECKLIST.items:
        assert item.provisions, item.item_id
        assert {p.framework for p in item.provisions} <= {"nist_ai_rmf", "eu_ai_act"}
    assert {i.document for i in CHECKLIST.items} == {
        "development_report",
        "data_description",
        "monitoring_plan",
    }


def doc(text: str) -> Document:
    return Document(doc_id="d", kind="development_report", title="t", text=text)


def test_sections_are_split_by_heading_with_their_line_numbers() -> None:
    found = sections(doc("# Title\n\n## 1. Purpose\nA\n\nB\n## Limits:\n# Annex\nC\n"))
    assert [
        (s.heading, s.heading_line, s.first_line, s.last_line, s.content_lines) for s in found
    ] == [
        ("1. Purpose", 3, 4, 6, 2),
        ("Limits:", 7, 7, 7, 0),  # a top-level heading ends the section
    ]
    assert normalize("3.1) Performance   Testing:") == "performance testing"


def test_a_section_under_another_documents_heading_does_not_count() -> None:
    package = load_package(FOLDER, "mdl-01")
    moved = tuple(
        d.model_copy(update={"kind": "data_description"}) if d.kind == "monitoring_plan" else d
        for d in package.documents
        if d.kind != "data_description"
    )
    checks = check_package(package.model_copy(update={"documents": moved}), CHECKLIST)
    by_id = {c.item_id: c.outcome for c in checks}
    assert by_id["MR-11"] == "missing"  # monitoring sections now sit in the wrong document
    assert by_id["MR-8"] == "missing"


def test_unknown_packages_and_document_kinds_are_refused() -> None:
    for bad in ("pkg-01", "mdl-99", "../mdl-01"):
        with pytest.raises(UnknownPackage):
            load_package(FOLDER, bad)
    assert issubclass(UnknownPackage, UnknownInput)
    with pytest.raises(ValidationError, match="unknown document kinds"):
        DocumentationPackage(
            package_id="mdl-01",
            model_name="m",
            model_type="t",
            documents=(Document(doc_id="d", kind="balance_sheet", title="t", text=""),),
        )


def test_the_catalog_lists_every_package_folder() -> None:
    folders = sorted(p.name for p in FOLDER.iterdir() if p.is_dir())
    assert folders == IDS
    meta = json.loads((FOLDER / "mdl-03" / "package.json").read_text())
    assert [d["kind"] for d in meta["documents"]] == list(gen.DOCUMENTS)
