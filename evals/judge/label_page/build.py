"""Render the labelling page from the calibration set, without how each item was built.

    uv run python evals/judge/label_page/build.py <output.html>

The page is published as a private claude.ai artifact that saves each label to its own
store; the labels are exported from there to evals/judge/v1/labels.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SET = HERE.parent / "v1" / "calibration.json"


def render() -> str:
    items = json.loads(SET.read_text())["items"]
    blind = [{k: v for k, v in i.items() if k != "built_as"} for i in items]
    page = (HERE / "template.html").read_text()
    return page.replace("__ITEMS__", json.dumps(blind).replace("</", "<\\/"))


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(render())
