# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuration loading, saving and environment overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from blockprobe.core.config import (
    ConfigError,
    load_config,
    render_config,
)


def test_missing_file_produces_documented_defaults(tmp_path: Path) -> None:
    settings = load_config(tmp_path / "missing" / "config.toml")
    assert settings.config.app.debug is False
    assert settings.config.app.language == "en"
    assert settings.config.storage.backend == "sqlite"
    assert settings.config.cache.backend == "embedded"
    assert settings.config.portals.refresh_ttl_hours == 24
    assert settings.config.portals.auto_refresh_on_run is True
    assert settings.config.safety.reset_confirm_word == "RESET"
    assert settings.config.probe.concurrency >= 1
    assert len(settings.config.probe.control_urls) >= 3
    assert all("example.com" not in url for url in settings.config.probe.control_urls)
    assert settings.config.probe.control_every == 10
    assert settings.db_path == settings.data_dir / "blockprobe.sqlite3"


def test_loaded_values_are_applied(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
debug = true
language = "it"

[storage]
path = "{data}"

[portals]
refresh_ttl_hours = 48
auto_refresh_on_run = false

[probe]
concurrency = 4
timeout_seconds = 3
control_urls = ["https://control.example/", "https://control2.example/"]
control_every = 3
""".format(data=str(tmp_path / "data").replace("\\", "/")),
        encoding="utf-8",
    )
    settings = load_config(config_path)
    assert settings.config.app.debug is True
    assert settings.config.app.language == "it"
    assert settings.config.portals.refresh_ttl_hours == 48
    assert settings.config.portals.auto_refresh_on_run is False
    assert settings.config.probe.concurrency == 4
    assert settings.config.probe.control_urls == [
        "https://control.example/",
        "https://control2.example/",
    ]
    assert settings.config.probe.control_every == 3
    assert settings.data_dir == tmp_path / "data"


def test_legacy_single_control_url_still_loads(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[probe]\ncontrol_url = "https://legacy.example/"\n', encoding="utf-8"
    )
    settings = load_config(config_path)
    assert settings.config.probe.control_url == "https://legacy.example/"
    assert settings.config.probe.control_urls == ["https://legacy.example/"]


def test_malformed_config_raises_instead_of_silently_ignoring(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[app\ndebug = true\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_env_data_dir_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[storage]\npath = "{(tmp_path / "fromfile").as_posix()}"\n', encoding="utf-8"
    )
    env_dir = tmp_path / "fromenv"
    monkeypatch.setenv("BLOCKPROBE_DATA_DIR", str(env_dir))
    settings = load_config(config_path)
    assert settings.data_dir == env_dir


def test_env_config_path_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "custom.toml"
    config_path.write_text("[app]\nlanguage = \"it\"\n", encoding="utf-8")
    monkeypatch.setenv("BLOCKPROBE_CONFIG", str(config_path))
    monkeypatch.setenv("BLOCKPROBE_DATA_DIR", str(tmp_path / "data"))
    settings = load_config()
    assert settings.config_path == config_path
    assert settings.config.app.language == "it"
    assert settings.config_dir == tmp_path


def test_debug_environment_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOCKPROBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BLOCKPROBE_DEBUG", "1")
    settings = load_config(tmp_path / "config.toml")
    assert settings.config.app.debug is True


def test_save_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    settings = load_config(config_path)
    settings.config.portals.refresh_ttl_hours = 12
    settings.config.probe.concurrency = 7
    settings.config.app.language = "it"
    settings.save()
    assert config_path.exists()
    reloaded = load_config(config_path)
    assert reloaded.config.portals.refresh_ttl_hours == 12
    assert reloaded.config.probe.concurrency == 7
    assert reloaded.config.app.language == "it"


def test_render_config_contains_every_section() -> None:
    settings = load_config()
    text = render_config(settings.config)
    for section in ("[app]", "[storage]", "[cache]", "[portals]", "[safety]", "[probe]"):
        assert section in text
    assert "language = 'en'" in text
    assert "theme" not in text
    assert "privacy" not in text


def test_unsupported_language_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[app]\nlanguage = "de"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="language"):
        load_config(config_path)


def test_removed_storage_backend_is_rejected_with_a_clear_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[storage]\nbackend = "mongo"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="sqlite"):
        load_config(config_path)


def test_removed_cache_backend_is_rejected_with_a_clear_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[cache]\nbackend = "redis"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="embedded"):
        load_config(config_path)
