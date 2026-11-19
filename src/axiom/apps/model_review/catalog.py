"""Load synthetic model documentation packages and their ground truth from the data folder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from axiom.apps.model_review.domain import DocumentationPackage
from axiom.core.types import Document, UnknownInput

PACKAGE_ID = re.compile(r"^mdl-\d{2}$")


class UnknownPackage(UnknownInput):
    pass


def packages_dir(data_dir: Path) -> Path:
    return data_dir / "synthetic" / "model_docs"


def list_packages(folder: Path) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = json.loads((folder / "catalog.json").read_text())
    return catalog


def load_package(folder: Path, package_id: str) -> DocumentationPackage:
    if not PACKAGE_ID.fullmatch(package_id):
        raise UnknownPackage(package_id)
    root = folder / package_id
    if not (root / "package.json").is_file():
        raise UnknownPackage(package_id)
    meta = json.loads((root / "package.json").read_text())
    return DocumentationPackage(
        package_id=meta["package_id"],
        model_name=meta["model_name"],
        model_type=meta["model_type"],
        documents=tuple(
            Document(
                doc_id=d["doc_id"],
                kind=d["kind"],
                title=d["title"],
                text=(root / "documents" / d["file"]).read_text(),
            )
            for d in meta["documents"]
        ),
    )


def load_ground_truth(folder: Path, package_id: str) -> dict[str, Any]:
    if not PACKAGE_ID.fullmatch(package_id):
        raise UnknownPackage(package_id)
    truth: dict[str, Any] = json.loads((folder / package_id / "ground_truth.json").read_text())
    return truth
