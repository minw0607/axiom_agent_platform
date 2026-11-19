"""A job paused before ADR 0016 still resumes: its checkpoint names types by their old
modules (axiom.domain, axiom.agents.base), and the serializer still loads them."""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from axiom.apps.credit_memo.domain import Citation, Claim, MemoDraft, MemoSection
from axiom.core.types import ModelCall, ReviewDecision
from axiom.memory.checkpoint import allowed_types, serializer


def _written_before_the_move(
    monkeypatch: pytest.MonkeyPatch, value: Any, old_modules: dict[type, str]
) -> tuple[str, bytes]:
    """Serialize `value` as the pre-ADR 0016 code did, with each class under its old module."""
    with monkeypatch.context() as m:
        for cls, module in old_modules.items():
            m.setattr(cls, "__module__", module)
        return serializer().dumps_typed(value)


def test_old_module_names_are_allowed() -> None:
    allowed = allowed_types()
    for pair in [
        ("axiom.domain", "MemoDraft"),
        ("axiom.domain", "ReviewDecision"),
        ("axiom.domain", "Document"),
        ("axiom.agents.base", "ModelCall"),
        ("axiom.apps.credit_memo.domain", "MemoDraft"),
        ("axiom.core.types", "ReviewDecision"),
    ]:
        assert pair in allowed


def test_a_checkpoint_written_before_the_move_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = MemoDraft(
        sections=(
            MemoSection(
                heading="Summary",
                claims=(
                    Claim(
                        text="Revenue grew.", citations=(Citation(doc_id="d", locator="line 3"),)
                    ),
                ),
            ),
        ),
        draft_recommendation="refer",
    )
    review = ReviewDecision(decision="revise", reviewer="officer", round=1, comments="more")
    call = ModelCall(
        node="drafter",
        route="draft",
        alias="frontier-a",
        provider="recorded",
        prompt_id="drafter",
        prompt_version="v3",
        prompt_sha256="0" * 64,
        origin="recorded",
        attempt=1,
        fallback_used=False,
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        valid=True,
    )
    state = {"draft": draft, "reviews": [review], "model_calls": [call]}
    old = {
        MemoDraft: "axiom.domain",
        MemoSection: "axiom.domain",
        Claim: "axiom.domain",
        Citation: "axiom.domain",
        ReviewDecision: "axiom.domain",
        ModelCall: "axiom.agents.base",
    }
    blob = _written_before_the_move(monkeypatch, state, old)
    assert b"axiom.domain" in blob[1] and b"axiom.agents.base" in blob[1]
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a blocked type only warns, so make it fail
        loaded = serializer().loads_typed(blob)
    # Without the old names the serializer does not fail: it quietly returns plain dicts,
    # and the job breaks later, on resume. So check the types, not just the values.
    assert type(loaded["draft"]) is MemoDraft
    assert type(loaded["reviews"][0]) is ReviewDecision
    assert type(loaded["model_calls"][0]) is ModelCall
    assert loaded == state
