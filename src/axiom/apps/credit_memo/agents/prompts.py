"""Versioned prompt files: prompts/<prompt_id>/<version>.md."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_SAFE = re.compile(r"^[a-z_]+$")
_VERSION = re.compile(r"^v\d+$")


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    version: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def load_prompt(prompts_dir: Path, prompt_id: str, version: str) -> Prompt:
    if not _SAFE.fullmatch(prompt_id) or not _VERSION.fullmatch(version):
        raise ValueError(f"bad prompt reference {prompt_id}/{version}")
    return Prompt(prompt_id, version, (prompts_dir / prompt_id / f"{version}.md").read_text())
