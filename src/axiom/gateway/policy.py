"""The tool gateway's rules: which caller may call which tool, with which argument values."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

Backend = Literal["gkn", "regulus"]


class GatewayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tools: dict[str, Backend]
    callers: dict[str, dict[str, dict[str, list[Any]]]]

    def model_post_init(self, _: Any) -> None:
        for caller, tools in self.callers.items():
            unknown = sorted(set(tools) - set(self.tools))
            if unknown:
                raise ValueError(f"caller {caller} lists unknown tools: {', '.join(unknown)}")

    def check(self, caller: str, tool: str, arguments: dict[str, Any]) -> str:
        """Why the call is refused, or "" if it is allowed."""
        rules = self.callers.get(caller)
        if rules is None:
            return f"unknown caller {caller}"
        if tool not in rules:
            return f"{caller} may not call {tool}"
        for name, allowed in rules[tool].items():
            if arguments.get(name) not in allowed:
                return f"{caller} may not call {tool} with {name}={arguments.get(name)!r}"
        return ""


def load_policy(path: Path) -> GatewayPolicy:
    return GatewayPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
