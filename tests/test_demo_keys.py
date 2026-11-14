"""The keyless demo's generated secrets, and the wrapper that hands them to each container."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from axiom.demo_keys import ROLES, ensure, read, summary

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "with-demo-keys.sh"


def test_first_start_generates_a_secret_and_one_key_per_role(tmp_path: Path) -> None:
    path = tmp_path / "keys.env"
    values, created = ensure(path)
    assert created and set(values) == {"MCP_SHARED_SECRET", *ROLES}
    assert len(values["MCP_SHARED_SECRET"]) == 64
    names = {values[v].partition(":")[0] for v in ROLES}
    assert names == {"rm-demo", "co-demo", "audit-demo"}
    assert read(path) == values
    assert path.stat().st_mode & 0o777 == 0o644


def test_later_starts_keep_the_same_keys(tmp_path: Path) -> None:
    path = tmp_path / "keys.env"
    first, _ = ensure(path)
    again, created = ensure(path)
    assert again == first and not created


def test_an_incomplete_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "keys.env"
    path.write_text("MCP_SHARED_SECRET=abc\n")
    values, created = ensure(path)
    assert created and all(values[v] for v in ROLES)


def test_the_summary_never_shows_a_key_set_in_env(tmp_path: Path) -> None:
    values, _ = ensure(tmp_path / "keys.env")
    text = summary(values, True, {"REVIEWER_API_KEYS": "co-mine:secret-from-env"})
    assert values["SUBMITTER_API_KEYS"].partition(":")[2] in text
    assert values["REVIEWER_API_KEYS"].partition(":")[2] not in text
    assert "secret-from-env" not in text and "set in your .env" in text
    assert values["MCP_SHARED_SECRET"] not in text
    every = dict.fromkeys(ROLES, "x:y")
    assert ".demo/keys.env" not in summary(values, False, every)


def run_wrapper(keys: Path, env: dict[str, str]) -> dict[str, str]:
    out = subprocess.run(
        ["sh", str(WRAPPER), "env"],
        env={"PATH": os.environ["PATH"], "AXIOM_DEMO_KEYS_FILE": str(keys), **env},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.partition("=")[::2] for line in out.splitlines() if "=" in line)


def test_the_wrapper_fills_only_what_env_leaves_empty(tmp_path: Path) -> None:
    keys = tmp_path / "keys.env"
    values, _ = ensure(keys)
    env = run_wrapper(keys, {"REVIEWER_API_KEYS": "co-mine:abc", "SUBMITTER_API_KEYS": ""})
    assert env["REVIEWER_API_KEYS"] == "co-mine:abc"
    assert env["SUBMITTER_API_KEYS"] == values["SUBMITTER_API_KEYS"]
    assert env["MCP_SHARED_SECRET"] == values["MCP_SHARED_SECRET"]


def test_the_wrapper_ignores_lines_that_are_not_plain_settings(tmp_path: Path) -> None:
    keys = tmp_path / "keys.env"
    keys.write_text("# comment\nX=1\nbad name=2\n$(touch pwned)=3\nPATH_X=a=b\n")
    env = run_wrapper(keys, {})
    assert env["X"] == "1" and env["PATH_X"] == "a=b"
    assert not (tmp_path / "pwned").exists() and not Path("pwned").exists()


def test_the_wrapper_runs_the_command_without_a_keys_file(tmp_path: Path) -> None:
    env = run_wrapper(tmp_path / "missing.env", {"A": "1"})
    assert env["A"] == "1" and "MCP_SHARED_SECRET" not in env


def test_every_service_that_needs_a_secret_starts_after_the_keys_exist() -> None:
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    for name in ("api", "worker", "gkn-mcp", "regulus-mcp"):
        svc = compose["services"][name]
        assert svc["command"][:2] == ["sh", "/usr/local/bin/with-demo-keys.sh"], name
        after = svc["depends_on"]["demo-keys"]["condition"]
        assert after == "service_completed_successfully", name
        assert "./.demo:/run/axiom-demo:ro" in svc["volumes"], name
    # Nothing in the stack refuses to start for want of a .env value.
    assert ":?" not in (REPO / "docker-compose.yml").read_text()
