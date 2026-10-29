# SPDX-License-Identifier: AGPL-3.0-or-later
"""CLI wiring tests: the terminal and the UI share the same actions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blockprobe.cli import app
from blockprobe.services import reset_runtime

runner = CliRunner()


def test_cli_defers_actions_import_on_python_315_plus() -> None:
    import subprocess

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import blockprobe.cli; "
            "assert ('blockprobe.ui.actions' in sys.modules) "
            "== (sys.version_info < (3, 15))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "config.toml"
    # The CLI tests assert the English source strings; the Italian default is
    # covered by test_i18n.py and test_config.py.
    config_path.write_text('[app]\nlanguage = "en"\n', encoding="utf-8")
    monkeypatch.setenv("BLOCKPROBE_CONFIG", str(config_path))
    monkeypatch.setenv("BLOCKPROBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BLOCKPROBE_DEBUG", raising=False)
    monkeypatch.delenv("GS_DEBUG", raising=False)
    reset_runtime()
    yield tmp_path
    reset_runtime()


def test_version_and_self_check(cli_env: Path) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "BlockProbe" in result.output

    result = runner.invoke(app, ["self-check"])
    assert result.exit_code == 0
    assert "SQLite" in result.output


def test_consent_flow_gates_network_commands(cli_env: Path) -> None:
    result = runner.invoke(app, ["consent", "status"])
    assert result.exit_code == 0
    assert "not been accepted" in result.output

    result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 1
    assert "Consent required" in result.output

    result = runner.invoke(app, ["consent", "accept"], input="not yet\n")
    assert result.exit_code == 1
    assert "not recorded" in result.output

    result = runner.invoke(app, ["consent", "accept"], input="I AGREE\n")
    assert result.exit_code == 0
    assert "Consent recorded" in result.output

    result = runner.invoke(app, ["consent", "status"])
    assert "is recorded" in result.output


def test_consent_can_be_accepted_non_interactively(cli_env: Path) -> None:
    result = runner.invoke(app, ["consent", "accept", "--yes"])
    assert result.exit_code == 0
    assert "Consent recorded" in result.output


def test_custom_list_commands(cli_env: Path) -> None:
    result = runner.invoke(app, ["custom", "add", "example.com"])
    assert result.exit_code == 0
    assert "example.com" in result.output

    result = runner.invoke(app, ["custom", "list"])
    assert result.exit_code == 0
    assert "example.com" in result.output

    result = runner.invoke(app, ["custom", "remove", "example.com"])
    assert result.exit_code == 0
    assert "removed" in result.output.lower()

    result = runner.invoke(app, ["custom", "remove", "example.com"])
    assert result.exit_code == 1


def test_report_without_a_run_fails_honestly(cli_env: Path) -> None:
    result = runner.invoke(app, ["report", "-f", "markdown"])
    assert result.exit_code == 1
    assert "No scan" in result.output


def test_scan_with_nothing_selected_fails_honestly(cli_env: Path) -> None:
    runner.invoke(app, ["consent", "accept", "--yes"])
    result = runner.invoke(app, ["scan", "--custom-only", "--no-refresh", "--wait"])
    assert result.exit_code == 1
    assert "No websites" in result.output


def test_scan_rejects_conflicting_options(cli_env: Path) -> None:
    result = runner.invoke(
        app, ["scan", "--custom-only", "--portal", "some-portal"]
    )
    assert result.exit_code == 1
    assert "not both" in result.output


def test_unknown_resume_run_is_refused(cli_env: Path) -> None:
    result = runner.invoke(app, ["scan", "--resume", "does-not-exist"])
    assert result.exit_code == 1


def test_portals_and_diagnostics(cli_env: Path) -> None:
    result = runner.invoke(app, ["portals", "list"])
    assert result.exit_code == 0
    assert "ADM" in result.output

    result = runner.invoke(app, ["diagnose"])
    assert result.exit_code == 0
    assert "Operating system" in result.output


def test_signature_capture_requires_consent(cli_env: Path) -> None:
    result = runner.invoke(app, ["signature", "capture", "example.com"])
    assert result.exit_code == 1
    assert "Consent required" in result.output


def test_help_lists_the_command_groups(cli_env: Path) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("consent", "schedule", "signature", "portals", "custom", "logs"):
        assert name in result.output


def test_schedule_status_is_read_only(cli_env: Path) -> None:
    result = runner.invoke(app, ["schedule", "status"])
    assert result.exit_code == 0
    assert "Scheduled scan" in result.output
    assert "never asks for administrator rights" in result.output

