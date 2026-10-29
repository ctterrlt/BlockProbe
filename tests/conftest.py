# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared pytest fixtures: source path, saved pages and a temporary runtime."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blockprobe.core.config import AppConfig, Settings
from blockprobe.core.models import Portal, builtin_adm_portal

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def adm_page1() -> str:
    return fixture_text("adm_page1.html")


@pytest.fixture()
def alt_layout_page() -> str:
    return fixture_text("alt_layout_page.html")


@pytest.fixture()
def adm_portal() -> Portal:
    """The shipped ADM portal definition, as the loader sees it."""
    return builtin_adm_portal()


@pytest.fixture()
def temp_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    config_dir = tmp_path / "config"
    settings = Settings(
        config=AppConfig(),
        config_dir=config_dir,
        config_path=config_dir / "config.toml",
        data_dir=data,
        db_path=data / "blockprobe.sqlite3",
        cache_dir=data / "cache",
        logs_dir=data / "logs",
        backups_dir=data / "backups",
        reports_dir=data / "reports",
        signatures_dir=data / "signatures",
        user_portals_dir=config_dir / "portals",
    )
    settings.ensure_directories()
    return settings
