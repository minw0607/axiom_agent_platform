"""Role-scoped API keys (X-API-Key). Keys map to a named principal and one role."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

from axiom.api.errors import Problem
from axiom.settings import Settings

Role = Literal["submitter", "reviewer", "auditor"]


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role


def _match(presented: str, keys: dict[str, str]) -> str | None:
    found = None
    for key, name in keys.items():
        # Compare against every key so timing does not reveal which prefix matched.
        if hmac.compare_digest(presented.encode(), key.encode()):
            found = name
    return found


def authenticate(settings: Settings, presented: str | None) -> Principal:
    if not presented:
        raise Problem(
            401, "unauthorized", "An API key is required", "Send the key in the X-API-Key header."
        )
    for role, keys in (
        ("submitter", settings.submitter_keys),
        ("reviewer", settings.reviewer_keys),
        ("auditor", settings.auditor_keys),
    ):
        name = _match(presented, keys)
        if name is not None:
            return Principal(name=name, role=role)  # type: ignore[arg-type]
    raise Problem(401, "unauthorized", "The API key is not valid")


def require(*roles: Role):  # type: ignore[no-untyped-def]
    def dependency(request: Request) -> Principal:
        settings: Settings = request.app.state.settings
        principal = authenticate(settings, request.headers.get("X-API-Key"))
        if principal.role not in roles:
            raise Problem(403, "forbidden", "This key's role cannot perform this action")
        return principal

    return dependency
