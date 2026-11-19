"""The credit memo app: a commercial term loan package in, a cited draft memo out."""

from axiom.apps.credit_memo import domain
from axiom.core.registry import AppSpec

APP = AppSpec(
    app_id="credit_memo",
    title="Credit memo",
    state_modules=(domain,),
    # Before ADR 0016 the domain types lived in axiom.domain.
    legacy_modules={"axiom.domain": domain},
)
