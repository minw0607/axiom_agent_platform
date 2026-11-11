"""Load synthetic borrower packages and their ground truth from the data folder."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from axiom.domain import (
    BorrowerPackage,
    Document,
    Figure,
    FinancialStatements,
    LoanRequest,
    SourceRef,
)

PACKAGE_ID = re.compile(r"^pkg-\d{2}$")


class UnknownPackage(LookupError):
    pass


def list_packages(packages_dir: Path) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = json.loads((packages_dir / "catalog.json").read_text())
    return catalog


def load_package(packages_dir: Path, package_id: str) -> BorrowerPackage:
    if not PACKAGE_ID.fullmatch(package_id):
        raise UnknownPackage(package_id)
    folder = packages_dir / package_id
    if not (folder / "package.json").is_file():
        raise UnknownPackage(package_id)
    meta = json.loads((folder / "package.json").read_text())
    return BorrowerPackage(
        package_id=meta["package_id"],
        borrower_name=meta["borrower_name"],
        industry=meta["industry"],
        documents=tuple(
            Document(
                doc_id=d["doc_id"],
                kind=d["kind"],
                title=d["title"],
                text=(folder / "documents" / d["file"]).read_text(),
            )
            for d in meta["documents"]
        ),
    )


def _figure(raw: dict[str, Any]) -> Figure:
    return Figure(
        value=Decimal(raw["value"]), source=SourceRef(doc_id=raw["doc_id"], line=raw["line"])
    )


def load_ground_truth(packages_dir: Path, package_id: str) -> dict[str, Any]:
    if not PACKAGE_ID.fullmatch(package_id):
        raise UnknownPackage(package_id)
    truth: dict[str, Any] = json.loads(
        (packages_dir / package_id / "ground_truth.json").read_text()
    )
    return truth


def ground_truth_statements(truth: dict[str, Any]) -> FinancialStatements:
    return FinancialStatements(
        years={
            int(year): {name: _figure(raw) for name, raw in fields.items()}
            for year, fields in truth["years"].items()
        },
        loan=LoanRequest(**{name: _figure(raw) for name, raw in truth["loan"].items()}),
    )
